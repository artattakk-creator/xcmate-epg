"""Bounded, explicitly authorized metadata enrichment for one private event.

No environment credentials are read. Input source artwork remains private until a
concrete image has passed the same verifier used for provider artwork.
"""
from collections import Counter
import copy
from datetime import date
import hashlib
import html
import json
import re
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from . import artwork, pipeline
from .fetch import http_get
from .image_validation import validate_image
from .xmltv import IMAGE_HOSTS, public_image


_METADATA_TTL = 30 * 86400
_API_BODY_LIMIT = 8 * 1024 * 1024
_MAX_BULK_EPISODES = 50_000
_MAX_CAST = 100
_MAX_EPISODE_SUMMARY = 32_768


def source_episode_marker(programme):
    """Detect episode scope without trusting source provider IDs for matching."""
    for node in programme.findall('episode-num'):
        if node.get('system') not in {'tmdb', 'tvmaze'}:
            return True
        parts = (node.text or '').strip().split('/')
        if len(parts) >= 4 and parts[0] == 'tv':
            return True
    return False


def parse_xmltv_ns(value):
    """Return known one-based season/episode, validating all XMLTV_NS fields.

    XMLTV indexes are zero-based; optional totals count items. Unknown season
    or episode cannot identify a provider episode. An omitted/unknown part is
    harmless, but any supplied index/total must still be valid.
    """
    if not isinstance(value, str) or len(value) > 256:
        return None
    fields = value.split('.')
    if len(fields) == 2:
        fields.append('')
    if len(fields) != 3:
        return None
    result = []
    for index, field in enumerate(fields):
        match = re.fullmatch(r'\s*([0-9]+)?\s*(?:/\s*([0-9]+)\s*)?', field)
        if not match:
            return None
        number, total = (int(n) if n is not None else None for n in match.groups())
        if index < 2 and number is None:
            return None
        if total is not None and (total <= 0 or (number is not None and number >= total)):
            return None
        result.append(number)
    return result[0] + 1, result[1] + 1


class _Unavailable(ValueError):
    pass


class _Cache:
    """Reuse only values produced under this adapter's identity guarantees.

    Legacy entries stay private and intact, but their original format does not
    prove current exact-title/year/episode checks, so they cannot make matches.
    """
    def __init__(self, owner, episode_year=None):
        self.owner = owner
        self.episode_year = episode_year

    def cached(self, key):
        found, value = self.owner.state.cached(json.dumps(['live-artwork-v3', key]))
        self.owner.counts['metadata_cache_hits'] += bool(found)
        if found and value and self.episode_year is not None:
            identity = json.loads(key)
            if identity[0] == 'tv':
                extra_key = json.dumps(['live-tmdb-episode-v1', value['id'], identity[3], identity[4]])
                extra_found, extra = self.owner.state.cached(extra_key)
                if not extra_found:
                    return False, None
                if not self.owner._tmdb_episode_identity(extra.get('detail'), identity[3], identity[4]):
                    return False, None
                if not self.owner._episode_year_matches(extra['detail'].get('air_date'), self.episode_year):
                    return True, None
        return found, value

    def cache(self, key, value, ttl):
        self.owner.state.cache(json.dumps(['live-artwork-v3', key]), value, ttl)


class _Metadata:
    """Keep the existing enrichment/cache path, with counted client operations."""
    def __init__(self, owner, client, episode_year=''):
        self.owner, self.client = owner, client
        self.episode_year = episode_year
        self.matches = {}

    def _search(self, kind, title, year):
        if kind == 'tv':
            show = self.owner._tmdb_show(title)
            hits = [show] if show else []
        else:
            hits = self.owner._client(self.client, 'search', title, year)
        if not isinstance(hits, list):
            raise _Unavailable('metadata unavailable')
        names = ('title', 'original_title') if kind == 'movie' else ('name', 'original_name')
        date = 'release_date' if kind == 'movie' else 'first_air_date'
        candidates = {hit['id'] for hit in hits if isinstance(hit, dict)
                      and type(hit.get('id')) is int and hit['id'] > 0
                      and artwork.title_key(title) in [artwork.title_key(hit.get(n)) for n in names]
                      and str(hit.get(date) or '')[:4] == str(year)}
        if len(candidates) > 1:
            self.owner.counts['ambiguous'] += 1
        if len(candidates) == 1:
            identifier = next(iter(candidates))
            self.matches[identifier] = (title, str(year), next(hit for hit in hits if isinstance(hit, dict) and hit.get('id') == identifier))
        return hits

    def search(self, title, year): return self._search('movie', title, year)
    def search_series(self, title, year): return self._search('tv', title, year)
    def movie(self, identifier):
        detail = self.owner._client(self.client, 'movie', identifier)
        title, year, hit = self.matches[identifier]
        if (not isinstance(detail, dict) or type(detail.get('id')) is not int
                or detail['id'] != identifier):
            raise _Unavailable('metadata mismatch')
        if (detail.get('title') and artwork.title_key(detail['title']) not in
                {artwork.title_key(title), artwork.title_key(hit.get('title')),
                 artwork.title_key(hit.get('original_title'))}):
            raise _Unavailable('metadata mismatch')
        if (detail.get('original_title') and hit.get('original_title')
                and artwork.title_key(detail['original_title']) != artwork.title_key(hit['original_title'])):
            raise _Unavailable('metadata mismatch')
        if detail.get('release_date') and str(detail['release_date'])[:4] != year:
            raise _Unavailable('metadata mismatch')
        return detail

    def episode(self, identifier, season, number):
        detail = self.owner._client(self.client, 'episode', identifier, season, number)
        if not self.owner._tmdb_episode_identity(detail, season, number):
            raise _Unavailable('metadata mismatch')
        if not self.owner._episode_year_matches(detail.get('air_date'), self.episode_year):
            raise _Unavailable('episode date unavailable or mismatched')
        show = self.matches.get(identifier, ('', '', {}))[2]
        key = json.dumps(['live-tmdb-episode-v1', identifier, season, number])
        self.owner.state.cache(key, {'show': show, 'detail': detail}, 30 * 86400)
        return detail


class ArtworkEnricher:
    def __init__(self, state, config, tmdb=None, tvmaze=None, transport=None):
        self.state = state
        self.config = dict(config)
        self.transport = transport or http_get
        self.max_requests = self._limit('max_requests', 200)
        self.max_images = self._limit('max_image_requests', 100)
        self.interval = max(0, float(self.config.get('request_interval', .55)))
        self.hosts = set(self.config.get('approved_image_hosts', IMAGE_HOSTS))
        # Caller may narrow approved hosts, but cannot silently extend the policy.
        if not self.hosts <= IMAGE_HOSTS:
            raise ValueError('image host policy violation')
        self.tmdb = tmdb
        self.tvmaze = tvmaze
        self.authorize_tvmaze = self.config.get('authorize_tvmaze', False) is True
        self.tvmaze_bulk_metadata = self.config.get('tvmaze_bulk_metadata') is True
        self.tvmaze_gallery = self.config.get('tvmaze_gallery') is True
        self.tmdb_credits = self.config.get('tmdb_credits') is True
        self.counts = {key: 0 for key in (
            'programmes_considered', 'programmes_eligible', 'programmes_matched',
            'unique_identities_considered', 'unique_identities_matched', 'ambiguous',
            'api_attempts', 'api_successes', 'api_errors', 'api_budget_exhausted',
            'tmdb_api_attempts', 'tmdb_api_successes', 'tmdb_api_errors',
            'tvmaze_api_attempts', 'tvmaze_api_successes', 'tvmaze_api_errors',
            'image_attempts', 'image_successes', 'image_errors', 'image_budget_exhausted',
            'image_cache_hits', 'metadata_cache_hits', 'verified_images',
            'episode_year_unproven', 'episode_year_mismatched', 'show_year_unavailable',
            'programmes_with_added_credits', 'actors_added')}
        self.identities = set()
        self.matched_identities = set()
        self.last_request = None
        self.blocked_hosts = set()
        self.last_metadata = None
        self._episode_index = (None, None)
        self._failed_bulk_shows = set()
        self._failed_casts = set()
        if isinstance(tmdb, artwork.TMDB):
            # A separate client avoids mutating the caller's reusable client.
            self.tmdb = tmdb.clone(transport=self._api_transport, sleep=lambda _: None)
        if self.authorize_tvmaze and (tvmaze is None or isinstance(tvmaze, artwork.TVMaze)):
            self.tvmaze = artwork.TVMaze(transport=self._api_transport)

    def _limit(self, key, default):
        value = self.config.get(key, default)
        if type(value) is not int or value < 0:
            raise ValueError('invalid request budget')
        return value

    def _pace(self):
        if self.last_request is not None:
            remaining = self.interval - (time.monotonic() - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()

    def _api_transport(self, url, headers):
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443)
                or parts.fragment or host not in {'api.themoviedb.org', 'api.tvmaze.com'}
                or host in self.blocked_hosts):
            raise _Unavailable('metadata unavailable')
        return self._request(url, headers, host)

    def _start_api_request(self, provider):
        if self.counts['api_attempts'] >= self.max_requests:
            self.counts['api_budget_exhausted'] += 1
            raise _Unavailable('metadata budget exhausted')
        self._pace()
        self.counts['api_attempts'] += 1
        self.counts[f'{provider}_api_attempts'] += 1

    def _record_api_result(self, provider, result):
        self.counts[f'api_{result}'] += 1
        self.counts[f'{provider}_api_{result}'] += 1

    def _request(self, url, headers, host):
        provider = 'tmdb' if host == 'api.themoviedb.org' else 'tvmaze'
        self._start_api_request(provider)
        try:
            response = self.transport(url, headers)
            if response.status != 200:
                # Stop this host for this run on authentication/rate-limit errors.
                # Never cache these errors as a negative metadata lookup.
                if response.status in (401, 403, 429):
                    self.blocked_hosts.add(host)
                if response.status == 429:
                    retry = next((v for k, v in response.headers.items() if k.lower() == 'retry-after'), '0')
                    try:
                        allowance = min(5., max(0., float(retry)))
                    except (ValueError, TypeError):
                        allowance = 0
                    if allowance:
                        time.sleep(allowance)
                raise _Unavailable('metadata unavailable')
            if (not isinstance(response.body, bytes) or len(response.body) > _API_BODY_LIMIT
                    or getattr(response, 'url', url) != url
                    or any(k.lower() == 'location' for k in response.headers)):
                raise _Unavailable('metadata unavailable')
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            self._record_api_result(provider, 'errors')
            raise _Unavailable('metadata unavailable') from None
        self._record_api_result(provider, 'successes')
        return response

    def _client(self, client, method, *args):
        if isinstance(client, (artwork.TMDB, artwork.TVMaze)):
            try:
                return getattr(client, method)(*args)
            except (ValueError, TimeoutError, OSError, TypeError, AttributeError, RecursionError):
                raise _Unavailable('metadata unavailable') from None
        # Explicit injected clients without a transport are counted per operation.
        provider = 'tmdb' if client is self.tmdb else 'tvmaze'
        self._start_api_request(provider)
        try:
            result = getattr(client, method)(*args)
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            self._record_api_result(provider, 'errors')
            raise _Unavailable('metadata unavailable') from None
        self._record_api_result(provider, 'successes')
        return result

    def verify_media(self, url):
        """Verify an explicitly supplied approved CDN image; never follow redirects."""
        failure = {'verified': False, 'sha256': None, 'size_bytes': 0, 'mime_type': None}
        try:
            public_image(url, self.hosts)
            parts = urlsplit(url)
            if not parts.path or parts.path == '/' or any(c.isspace() for c in url):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            return dict(failure, error='image_policy')
        key = json.dumps(['verified-image-v2-structure', url])
        found, result = self.state.cached(key)
        if found:
            self.counts['image_cache_hits'] += 1
            self.counts['verified_images'] += bool(result.get('verified'))
            return result
        if self.config.get('verify_images', True) is not True:
            return dict(failure, error='verification_disabled')
        if self.counts['image_attempts'] >= self.max_images:
            self.counts['image_budget_exhausted'] += 1
            return dict(failure, error='image_budget')
        self._pace()
        self.counts['image_attempts'] += 1
        transient = False
        try:
            response = self.transport(url, {})
            transient = response.status in (401, 403, 429) or response.status >= 500
            mime = next((v for k, v in response.headers.items() if k.lower() == 'content-type'), '').split(';')[0].strip().lower()
            body = response.body
            if (any(k.lower() == 'location' for k in response.headers)
                    or getattr(response, 'url', url) != url or response.status != 200):
                raise ValueError()
            validate_image(body, mime)
            result = {'verified': True, 'sha256': hashlib.sha256(body).hexdigest(),
                      'size_bytes': len(body), 'mime_type': mime}
            self.counts['image_successes'] += 1
            self.counts['verified_images'] += 1
        except (TimeoutError, OSError):
            transient = True
            result = dict(failure, error='image_unavailable')
            self.counts['image_errors'] += 1
        except (ValueError, TypeError, AttributeError):
            result = dict(failure, error='image_rejected')
            self.counts['image_errors'] += 1
        if not transient:
            self.state.cache(key, result, 30 * 86400 if result['verified'] else 86400)
        return result

    def _episode_year_matches(self, airdate, year):
        if not year:
            return True
        if not isinstance(airdate, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', airdate):
            self.counts['episode_year_unproven'] += 1
            return False
        try:
            date.fromisoformat(airdate)
        except ValueError:
            self.counts['episode_year_unproven'] += 1
            return False
        if airdate[:4] != year:
            self.counts['episode_year_mismatched'] += 1
            return False
        return True

    @staticmethod
    def _tmdb_episode_identity(detail, season, number):
        return (isinstance(detail, dict) and type(detail.get('id')) is int and detail['id'] > 0
                and type(detail.get('season_number')) is int and detail['season_number'] == season
                and type(detail.get('episode_number')) is int and detail['episode_number'] == number)

    def _tmdb_show(self, title, year=''):
        """A programme episode year cannot constrain the series premiere."""
        key = json.dumps(['live-tmdb-show-v1', artwork.title_key(title)] + ([year] if year else []))
        found, hit = self.state.cached(key)
        if found:
            self.counts['metadata_cache_hits'] += 1
            return hit
        try:
            hits = self._client(self.tmdb, 'search_series', title, year)
            if not isinstance(hits, list):
                raise _Unavailable('metadata unavailable')
            candidates = {h['id']: h for h in hits if isinstance(h, dict)
                          and type(h.get('id')) is int and h['id'] > 0
                          and (not year or str(h.get('first_air_date') or '')[:4] == year)
                          and artwork.title_key(title) in
                          {artwork.title_key(h.get('name')), artwork.title_key(h.get('original_name'))}}
            if len(candidates) > 1:
                self.counts['ambiguous'] += 1
            hit = next(iter(candidates.values())) if len(candidates) == 1 else None
            self.state.cache(key, hit, 30 * 86400 if hit else 86400)
            return hit
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            return None

    def _tmdb_series_detail(self, title, hit, year=''):
        """Verify detail identity independently of a search result or episode."""
        if not callable(getattr(self.tmdb, 'series', None)):
            return None
        key = json.dumps(['live-tmdb-series-detail-v1', hit['id'], artwork.title_key(title),
                          hit.get('name'), hit.get('original_name'), hit.get('first_air_date'), year])
        found, detail = self.state.cached(key)
        if found:
            self.counts['metadata_cache_hits'] += 1
            return detail
        try:
            detail = self._client(self.tmdb, 'series', hit['id'])
            valid = (isinstance(detail, dict) and type(detail.get('id')) is int
                     and detail['id'] == hit['id'] and artwork.title_key(title) in
                     {artwork.title_key(detail.get('name')), artwork.title_key(detail.get('original_name'))})
            if valid:
                for field in ('name', 'original_name'):
                    if hit.get(field) and artwork.title_key(detail.get(field)) != artwork.title_key(hit[field]):
                        valid = False
                premiere = str(detail.get('first_air_date') or '')
                if ((year and premiere[:4] != year) or
                        (hit.get('first_air_date') and premiere != hit['first_air_date'])):
                    valid = False
                if premiere:
                    try:
                        if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', premiere):
                            raise ValueError()
                        date.fromisoformat(premiere)
                    except ValueError:
                        valid = False
            detail = detail if valid else None
            self.state.cache(key, detail, 30 * 86400 if detail else 86400)
            return detail
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            # Authentication, transport and budget failures must remain retryable.
            return None

    def _tmdb_season(self, identifier, season):
        if not callable(getattr(self.tmdb, 'season', None)):
            return None
        key = json.dumps(['live-tmdb-season-v1', identifier, season])
        found, detail = self.state.cached(key)
        if found:
            self.counts['metadata_cache_hits'] += 1
            return detail
        try:
            detail = self._client(self.tmdb, 'season', identifier, season)
            valid = (isinstance(detail, dict) and type(detail.get('season_number')) is int
                     and detail['season_number'] == season)
            detail = detail if valid else None
            self.state.cache(key, detail, 30 * 86400 if detail else 86400)
            return detail
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            return None

    def _tmdb_series_metadata(self, programme, detail, *, episode=False):
        if not episode and not (programme.findtext('desc') or '').strip():
            overview = detail.get('overview')
            if isinstance(overview, str) and overview.strip():
                ET.SubElement(programme, 'desc', {'lang': 'de'}).text = overview
        original, language = detail.get('original_name'), detail.get('original_language')
        if (isinstance(original, str) and original.strip() and isinstance(language, str) and language.strip()
                and original not in [n.text for n in programme.findall('title')]
                and not any(n.get('lang') == language for n in programme.findall('title')[1:])):
            ET.SubElement(programme, 'title', {'lang': language}).text = original
        rating = detail.get('vote_average')
        if not episode and programme.find('star-rating') is None and type(rating) in (int, float) and 0 < rating <= 10:
            node = ET.SubElement(programme, 'star-rating', {'system': 'TMDB'})
            ET.SubElement(node, 'value').text = str(rating) + '/10'
        for genre in detail.get('genres', []) if isinstance(detail.get('genres'), list) else []:
            name = genre.get('name') if isinstance(genre, dict) else None
            if isinstance(name, str) and name.strip() and name not in [n.text for n in programme.findall('category')]:
                ET.SubElement(programme, 'category', {'lang': 'de'}).text = name
        premiere = detail.get('first_air_date')
        if not episode and programme.find('date') is None and isinstance(premiere, str):
            try:
                date.fromisoformat(premiere)
            except ValueError:
                return
            ET.SubElement(programme, 'date').text = premiere[:4]

    def _tmdb_images(self, programme, detail, identifier, evidence, scope=None):
        records = []
        for kind, field in (('poster', 'poster_path'), ('backdrop', 'backdrop_path')):
            try:
                url = artwork.image_url(detail.get(field))
            except ValueError:
                continue
            if url:
                record = self._record(programme, kind, url, 'tmdb', identifier, dict(evidence))
                if scope:
                    record['scope'] = scope
                records.append(record)
        return records

    def _cached_metadata(self, key):
        found, value = self.state.cached(key)
        self.counts['metadata_cache_hits'] += bool(found)
        return found, value

    @staticmethod
    def _tvmaze_show_matches(show, title, year, identifier=None):
        if (not isinstance(show, dict) or type(show.get('id')) is not int or show['id'] <= 0
                or (identifier is not None and show['id'] != identifier)
                or not title or artwork.title_key(show.get('name')) != title):
            return False
        if not year:
            return True
        premiere = show.get('premiered')
        if not isinstance(premiere, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', premiere):
            return False
        try:
            return str(date.fromisoformat(premiere).year).zfill(4) == year
        except ValueError:
            return False

    def _tvmaze_search(self, title, normalized):
        key = json.dumps(['live-tvmaze-search-v1', normalized])
        found, hits = self._cached_metadata(key)
        if not found:
            hits = self._client(self.tvmaze, 'search_series', title, None)
            if not isinstance(hits, list) or not all(isinstance(hit, dict) for hit in hits):
                raise _Unavailable('metadata unavailable')
            self.state.cache(key, hits, _METADATA_TTL if hits else 86400)
        return hits

    def _tvmaze_show_detail(self, hit, title, year):
        identifier = hit['id']
        key = json.dumps(['live-tvmaze-show-detail-v1', identifier])
        found, detail = self._cached_metadata(key)
        if not found:
            detail = self._tvmaze_json(f'https://api.tvmaze.com/shows/{identifier}', dict)
        if (not self._tvmaze_show_matches(detail, title, year, identifier)
                or (hit.get('premiered') and detail.get('premiered') != hit['premiered'])):
            raise _Unavailable('metadata mismatch')
        if not found:
            self.state.cache(key, detail, _METADATA_TTL)
        return detail

    def _tvmaze_show(self, title, year):
        normalized = artwork.title_key(title)
        key = json.dumps(['live-tvmaze-show-v2', normalized, year])
        found, value = self._cached_metadata(key)
        if found and (value is None or self._tvmaze_show_matches(value, normalized, year)):
            return value
        try:
            hits = self._tvmaze_search(title, normalized)
            candidates = {hit['id']: hit for hit in hits
                          if self._tvmaze_show_matches(hit, normalized, year)}
            value = None
            if len(candidates) > 1:
                self.counts['ambiguous'] += 1
            if len(candidates) == 1:
                value = self._tvmaze_show_detail(next(iter(candidates.values())), normalized, year)
            self.state.cache(key, value, _METADATA_TTL if value else 86400)
            return value
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            return None

    @staticmethod
    def _tvmaze_episode_identity(episode, season, number):
        return (isinstance(episode, dict) and type(episode.get('id')) is int and episode['id'] > 0
                and episode.get('type', 'regular') == 'regular'
                and type(episode.get('season')) is int and episode['season'] == season
                and type(episode.get('number')) is int and episode['number'] == number)

    @staticmethod
    def _index_tvmaze_episodes(rows):
        if len(rows) > _MAX_BULK_EPISODES:
            raise _Unavailable('metadata unavailable')
        episode_ids, coordinates = Counter(), Counter()
        for row in rows:
            if not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0:
                raise _Unavailable('metadata unavailable')
            season, number = row.get('season'), row.get('number')
            if any(n is not None and (type(n) is not int or n < 0) for n in (season, number)):
                raise _Unavailable('metadata unavailable')
            episode_ids[row['id']] += 1
            if season and number:
                coordinates[season, number] += 1

        # Specials and unknown or unnumbered episodes cannot match, but still
        # count as conflicts when they share an ID or a complete coordinate.
        index = {}
        for row in rows:
            season, number = row.get('season'), row.get('number')
            if not season or not number or row.get('type') != 'regular':
                continue
            coordinate = f'{season}/{number}'
            if episode_ids[row['id']] > 1 or coordinates[season, number] > 1:
                index[coordinate] = None
                continue
            episode = {key: row[key] for key in
                ('id', 'season', 'number', 'type', 'airdate', 'image', 'rating') if key in row}
            for key, limit in (('name', 512), ('summary', _MAX_EPISODE_SUMMARY)):
                value = row.get(key)
                if isinstance(value, str) and len(value) <= limit:
                    episode[key] = value
            index[coordinate] = episode
        return index

    def _tvmaze_episode_index(self, identifier):
        # One in-memory index is enough for sequential lookups for a show.
        # Other successful show indexes remain on disk for resumed visits.
        active, index = self._episode_index
        if active == identifier:
            return index
        if identifier in self._failed_bulk_shows:
            return None
        self._episode_index = (None, None)
        key = json.dumps(['live-tvmaze-episodes-v2', identifier])
        found, index = self._cached_metadata(key)
        if not found:
            try:
                rows = self._tvmaze_json(f'https://api.tvmaze.com/shows/{identifier}/episodes', list)
                index = self._index_tvmaze_episodes(rows)
                if len(json.dumps(index)) > _API_BODY_LIMIT:
                    raise _Unavailable('metadata unavailable')
            except _Unavailable:
                self._failed_bulk_shows.add(identifier)
                return None
            self.state.cache(key, index, _METADATA_TTL)
        self._episode_index = (identifier, index)
        return index

    def _tvmaze_lookup(self, title, year, episode):
        detail = self._tvmaze_show(title, '' if episode else year)
        if not detail:
            return None
        if not episode:
            return {'show': detail, 'episode': None}
        season, number = episode
        identifier = detail['id']
        key = json.dumps(['live-tvmaze-episode-v2', identifier, season, number])
        index = self._tvmaze_episode_index(identifier) if self.tvmaze_bulk_metadata else None
        found, cached = self._cached_metadata(key)
        if index is not None:
            coordinate = f'{season}/{number}'
            ep = index.get(coordinate)
            if ep is None:
                self.counts['ambiguous'] += coordinate in index
                return None
        elif found:
            ep = cached
        else:
            try:
                ep = self._tvmaze_json(
                    f'https://api.tvmaze.com/shows/{identifier}/episodebynumber?season={season}&number={number}', dict)
            except _Unavailable:
                return None
        if (not self._tvmaze_episode_identity(ep, season, number)
                or not self._episode_year_matches(ep.get('airdate'), year)):
            return None
        if not found or cached != ep:
            self.state.cache(key, ep, _METADATA_TTL)
        return {'show': detail, 'episode': ep}

    def _tvmaze_json(self, url, expected_type):
        try:
            data = json.loads(self._api_transport(url, {}).body)
        except (ValueError, RecursionError):
            raise _Unavailable('metadata unavailable') from None
        if not isinstance(data, expected_type):
            raise _Unavailable('metadata unavailable')
        return data

    def _tvmaze_gallery_image(self, identifier):
        """Select one provider-declared background for an already verified show.

        Dimensions only rank bounded landscape candidates; the concrete URL
        still needs the normal image proof before it can appear in XMLTV.
        """
        key = json.dumps(['live-tvmaze-gallery-v1', identifier])
        found, rows = self._cached_metadata(key)
        if not found:
            try:
                rows = self._tvmaze_json(f'https://api.tvmaze.com/shows/{identifier}/images', list)
                if len(rows) > 1000:
                    raise _Unavailable('metadata unavailable')
            except _Unavailable:
                return None
            self.state.cache(key, rows, _METADATA_TTL if rows else 86400)
        if not isinstance(rows, list) or len(rows) > 1000:
            return None
        identifiers = Counter(row['id'] for row in rows if isinstance(row, dict)
                              and type(row.get('id')) is int and row['id'] > 0)
        candidates = []
        for row in rows:
            if (not isinstance(row, dict) or type(row.get('id')) is not int or row['id'] <= 0
                    or identifiers[row['id']] != 1 or row.get('type') != 'background'):
                continue
            resolutions = row.get('resolutions')
            original = resolutions.get('original') if isinstance(resolutions, dict) else None
            if not self._tvmaze_gallery_resolution(original):
                continue
            medium = resolutions.get('medium')
            candidates.append({'id': row['id'], 'main': row.get('main') is True,
                'original': original,
                'medium': medium if self._tvmaze_gallery_resolution(medium) else None})
        return min(candidates, key=lambda image: (not image['main'],
            -image['original']['width'] * image['original']['height'], image['id'])) if candidates else None

    def _tvmaze_gallery_resolution(self, resolution):
        if not isinstance(resolution, dict):
            return False
        width, height = resolution.get('width'), resolution.get('height')
        if (type(width) is not int or type(height) is not int
                or not 0 < height < width <= 16384 or width * height > 16 * 1024 * 1024):
            return False
        try:
            public_image(resolution.get('url'), self.hosts)
        except (ValueError, TypeError, AttributeError):
            return False
        return True

    @staticmethod
    def _credit_text(value, limit):
        if not isinstance(value, str):
            return None
        text = value.strip()
        if (not 0 < len(text) <= limit or not text.isprintable()
                or any(token in text.casefold() for token in ('<', '>', '://', 'www.'))):
            return None
        return text

    def _credit_actors(self, provider, scope, identifier):
        key = json.dumps(['live-credits-v1', provider, scope, identifier])
        found, actors = self._cached_metadata(key)
        if found:
            return actors
        if key in self._failed_casts:
            return []
        try:
            if provider == 'tvmaze':
                rows = self._tvmaze_json(f'https://api.tvmaze.com/shows/{identifier}/cast', list)
            else:
                method = 'movie_credits' if scope == 'movie' else 'series_credits'
                if not callable(getattr(self.tmdb, method, None)):
                    return []
                data = self._client(self.tmdb, method, identifier)
                if (not isinstance(data, dict) or type(data.get('id')) is not int
                        or data['id'] != identifier or not isinstance(data.get('cast'), list)):
                    raise _Unavailable('metadata mismatch')
                rows = data['cast']
        except _Unavailable:
            self._failed_casts.add(key)
            return []

        actors, seen = [], set()
        for row in rows[:_MAX_CAST]:
            if not isinstance(row, dict):
                continue
            if provider == 'tvmaze':
                person, role = row.get('person'), row.get('character')
                name = person.get('name') if isinstance(person, dict) else None
                character = role.get('name') if isinstance(role, dict) else None
            else:
                name, character = row.get('name'), row.get('character')
                roles = row.get('roles')
                if not character and isinstance(roles, list):
                    character = next((role.get('character') for role in roles[:10]
                                      if isinstance(role, dict) and self._credit_text(role.get('character'), 200)), None)
            name = self._credit_text(name, 160)
            character = self._credit_text(character, 200)
            normalized = artwork.title_key(name)
            if not name or not any(char.isalpha() for char in name) or normalized in seen:
                continue
            seen.add(normalized)
            actor = {'name': name}
            if character:
                actor['character'] = character
            actors.append(actor)
        self.state.cache(key, actors, _METADATA_TTL if actors else 86400)
        return actors

    def _programme_credits(self, programme, provider, scope, identifier):
        cast_scope = 'movie' if scope == 'movie' else 'series'
        actors = self._credit_actors(provider, cast_scope, identifier)
        if not actors:
            return None
        evidence = {'provider': provider, 'provider_id': identifier, 'scope': cast_scope,
                    'applied_to_xmltv': False, 'actors': actors}
        # A series cast listing cannot establish an actor's appearance in a
        # particular episode. Keep its provenance without changing XML credits.
        if scope == 'episode':
            return evidence
        credits = programme.find('credits')
        seen = {artwork.title_key(node.text) for node in programme.findall('credits/actor')}
        added = [actor for actor in actors if artwork.title_key(actor['name']) not in seen]
        if not added:
            return evidence
        if credits is None:
            preceding = {'title', 'sub-title', 'desc'}
            position = next((i for i, node in enumerate(programme) if node.tag not in preceding), len(programme))
            credits = ET.Element('credits')
            programme.insert(position, credits)
        position = next((i for i, node in enumerate(credits) if node.tag not in {'director', 'actor'}), len(credits))
        for actor in added:
            attributes = {'role': actor['character']} if 'character' in actor else {}
            node = ET.Element('actor', attributes)
            node.text = actor['name']
            credits.insert(position, node)
            position += 1
        self.counts['programmes_with_added_credits'] += 1
        self.counts['actors_added'] += len(added)
        return dict(evidence, applied_to_xmltv=True, actors=added)

    def _record(self, programme, kind, url, provider, identifier, evidence):
        return {'type': kind, 'url': url, 'provider': provider, 'provider_id': identifier,
                'verified': False, 'attribution': ('TMDB: https://www.themoviedb.org/' if provider == 'tmdb' else
                    'TVMaze (CC BY-SA): https://www.tvmaze.com/' if provider == 'tvmaze' else 'original source'),
                'evidence': evidence, 'channel': programme.get('channel'),
                'start': programme.get('start'), 'stop': programme.get('stop')}

    def enrich(self, channel, programme):
        self.last_metadata = None
        self.counts['programmes_considered'] += 1
        title = programme.findtext('title', '')
        year = programme.findtext('date', '')[:4]
        year = year if re.fullmatch(r'\d{4}', year) else ''
        ns_values = [n.text for n in programme.findall('episode-num[@system="xmltv_ns"]')]
        parsed = [parse_xmltv_ns(value) for value in ns_values]
        episode = parsed[0] if parsed and parsed[0] is not None and all(p == parsed[0] for p in parsed) else None
        categories = {artwork.title_key(c.text) for c in programme.findall('category')}
        has_source_episode = source_episode_marker(programme)
        movie = bool(categories & {'movie', 'film', 'spielfilm'}) and not has_source_episode
        show = bool(episode or categories & {'series', 'serie', 'tv series', 'tv show', 'fernsehserie'})
        standalone_series = show and not has_source_episode
        eligible = bool((movie and year) or show)
        identity = (artwork.title_key(title), year, episode, has_source_episode,
                    'movie' if movie else 'tv' if show else 'unknown')
        if identity not in self.identities:
            self.identities.add(identity)
            self.counts['unique_identities_considered'] += 1
        if eligible:
            self.counts['programmes_eligible'] += 1
        tree = ET.Element('tv')
        clean_channel = copy.deepcopy(channel)
        for icon in clean_channel.findall('icon'):
            clean_channel.remove(icon)
        tree.append(clean_channel)
        clean = copy.deepcopy(programme)
        records = []
        # Input IDs remain source metadata. Only IDs generated by the controlled
        # lookup/cache path below can establish a match or suppress fallback.
        source_ids = [n for n in clean.findall('episode-num')
                      if n.get('system') in {'tmdb', 'tvmaze'}]
        for node in source_ids:
            clean.remove(node)
        source_ns = clean.findall('episode-num[@system="xmltv_ns"]')
        for node in source_ns:
            clean.remove(node)
        if episode:
            ET.SubElement(clean, 'episode-num', {'system': 'xmltv_ns'}).text = f'{episode[0]-1}.{episode[1]-1}.'
        elif source_ns:
            # A malformed/conflicting source identity must not reach the old
            # strict parser or accidentally qualify the event as a movie.
            ET.SubElement(clean, 'episode-num', {'system': 'xmltv_ns'})
        metadata = _Metadata(self, self.tmdb, year) if self.tmdb is not None else None
        source_dates = None
        if episode and self.tmdb is not None:
            hit = self._tmdb_show(title)
            series_year = str(hit.get('first_air_date') or '')[:4] if hit else ''
            if hit and re.fullmatch(r'[0-9]{4}', series_year):
                # Bridge only the existing series-premiere filter, using the
                # actual matched show's year. _Metadata independently checks
                # the original programme year against the episode air_date.
                source_dates = clean.findall('date')
                for node in source_dates:
                    clean.remove(node)
                ET.SubElement(clean, 'date').text = series_year
            else:
                if hit:
                    self.counts['show_year_unavailable'] += 1
                metadata = None
        feed = []
        for icon in clean.findall('icon'):
            clean.remove(icon)
            record = self._record(clean, 'unclassified_source_image', icon.get('src', ''), 'feed', None,
                                  {'source': 'original programme icon', 'identity_verified': False})
            feed.append(record)
        tree.append(clean)
        # Only explicit alternatives belonging to the same provider record may
        # be tried. Keep failed originals as private evidence in records.
        resolution_fallbacks = {}
        # Calling aggregate also exercises existing movie/episode positive/negative caches.
        result, _ = pipeline.aggregate([('bounded-event', tree)], _Cache(self, year if episode else None), metadata)
        enriched = result.find('programme')
        provider = scope = identifier = None
        tmdb_identity = enriched.findtext('episode-num[@system="tmdb"]', '')
        if tmdb_identity:
            parts = tmdb_identity.split('/')
            if len(parts) in (2, 4) and parts[0] in ('movie', 'tv') and parts[1].isdigit():
                identifier = int(parts[1])
                provider = 'tmdb'
                scope = 'movie' if parts[0] == 'movie' else 'episode'
                for index, icon in enumerate(enriched.findall('icon')):
                    kind = 'episode_still' if parts[0] == 'tv' else 'poster' if index == 0 else 'backdrop'
                    # Existing enrichment emits only present paths; derive exact movie field below.
                    if parts[0] == 'movie':
                        key = json.dumps(['movie', artwork.title_key(title), year, 'de-DE'])
                        found, hit = _Cache(self).cached(key)
                        if found and hit and icon.get('src') == artwork.image_url(hit.get('backdrop_path')):
                            kind = 'backdrop'
                    records.append(self._record(enriched, kind, icon.get('src', ''), 'tmdb', identifier,
                        {'title': 'unicode_normalized_exact', 'year': year, 'episode': list(episode) if episode else None}))
        if provider is None and standalone_series and self.tmdb is not None:
            hit = self._tmdb_show(title, year)
            detail = self._tmdb_series_detail(title, hit, year) if hit else None
            if detail:
                provider, scope = 'tmdb', 'series'
                identifier = detail['id']
                ET.SubElement(enriched, 'episode-num', {'system': 'tmdb'}).text = 'tv/' + str(identifier)
                self._tmdb_series_metadata(enriched, detail)
                records.extend(self._tmdb_images(enriched, detail, identifier,
                    {'title': 'unicode_normalized_exact', 'year': year or None,
                     'series_premiere': detail.get('first_air_date'), 'series_detail_identity': 'exact',
                     'episode': None}))
        if provider == 'tmdb' and scope == 'episode':
            identifier = int(tmdb_identity.split('/')[1])
            key = json.dumps(['live-tmdb-episode-v1', identifier, *episode]) if episode else ''
            found, extra = self.state.cached(key)
            if found and extra:
                detail, series = extra['detail'], extra['show']
                if not enriched.findtext('desc') and isinstance(detail.get('overview'), str) and detail['overview'].strip():
                    ET.SubElement(enriched, 'desc', {'lang': 'de'}).text = detail['overview']
                if not enriched.findtext('sub-title') and isinstance(detail.get('name'), str) and detail['name'].strip():
                    ET.SubElement(enriched, 'sub-title', {'lang': 'de'}).text = detail['name']
                original, language = series.get('original_name'), series.get('original_language')
                if (isinstance(original, str) and isinstance(language, str)
                        and original not in [t.text for t in enriched.findall('title')]):
                    ET.SubElement(enriched, 'title', {'lang': language}).text = original
                rating = detail.get('vote_average')
                if enriched.find('star-rating') is None and type(rating) in (int, float) and 0 < rating <= 10:
                    node = ET.SubElement(enriched, 'star-rating', {'system': 'TMDB'})
                    ET.SubElement(node, 'value').text = str(rating) + '/10'
                for record in records:
                    if record['provider'] == 'tmdb':
                        record['evidence']['episode_id'] = detail.get('id')
                        record['evidence']['episode_airdate'] = detail.get('air_date')
                        record['evidence']['episode_year_validation'] = 'exact' if year else 'not_supplied'
                evidence = {'title': 'unicode_normalized_exact', 'year': year, 'episode': list(episode),
                            'episode_id': detail.get('id'), 'episode_airdate': detail.get('air_date'),
                            'episode_year_validation': 'exact' if year else 'not_supplied'}
                verified_series = self._tmdb_series_detail(title, series)
                if verified_series:
                    self._tmdb_series_metadata(enriched, verified_series, episode=True)
                    records.extend(self._tmdb_images(enriched, verified_series, identifier,
                        dict(evidence, series_detail_identity='exact',
                             series_premiere=verified_series.get('first_air_date')), scope='series'))
                else:
                    # Preserve artwork already proven by the unique exact show
                    # search and verified episode, even if optional detail fails.
                    records.extend(self._tmdb_images(enriched, series, identifier, evidence))
                season = self._tmdb_season(identifier, episode[0])
                if season:
                    season_records = self._tmdb_images(enriched, {'poster_path': season.get('poster_path')},
                        identifier, dict(evidence, season_number=episode[0], season_id=season.get('id'),
                                         season_identity='exact'), scope='season')
                    records.extend(season_records)
        if provider is None and self.authorize_tvmaze and (episode or standalone_series):
            value = self._tvmaze_lookup(title, year, episode)
            if value:
                provider, scope = 'tvmaze', 'episode' if episode else 'series'
                detail, ep = value['show'], value['episode']
                identifier = detail['id']
                if enriched.find('episode-num[@system="tvmaze"]') is None:
                    ET.SubElement(enriched, 'episode-num', {'system': 'tvmaze'}).text = 'tv/' + str(identifier) + (f'/{episode[0]}/{episode[1]}' if episode else '')
                plot = ep if ep is not None and self.tvmaze_bulk_metadata else detail
                summary = plot.get('summary')
                if not enriched.findtext('desc') and isinstance(summary, str) and len(summary) <= _MAX_EPISODE_SUMMARY:
                    text = html.unescape(re.sub('<[^>]*>', '', summary)).strip()
                    if text and all(char.isprintable() or char in '\r\n\t' for char in text):
                        ET.SubElement(enriched, 'desc').text = text
                if ep is not None and self.tvmaze_bulk_metadata and not enriched.findtext('sub-title'):
                    name = self._credit_text(ep.get('name'), 512)
                    if name:
                        ET.SubElement(enriched, 'sub-title').text = name
                for genre in detail.get('genres', []) if isinstance(detail.get('genres', []), list) else []:
                    if isinstance(genre, str) and genre.strip() and genre not in [c.text for c in enriched.findall('category')]:
                        ET.SubElement(enriched, 'category').text = genre
                rating = (ep if ep is not None else detail).get('rating')
                rating = rating.get('average') if isinstance(rating, dict) else None
                if enriched.find('star-rating') is None and type(rating) in (int, float) and 0 < rating <= 10:
                    node = ET.SubElement(enriched, 'star-rating', {'system': 'TVMaze'})
                    ET.SubElement(node, 'value').text = str(rating) + '/10'
                evidence = {'title': 'unicode_normalized_exact', 'year': year or None,
                            'episode': list(episode) if episode else None, 'episode_id': ep.get('id') if ep else None,
                            'episode_airdate': ep.get('airdate') if ep else None,
                            'episode_year_validation': ('exact' if year else 'not_supplied') if ep else None}
                for kind, media in (('poster', detail), ('episode_still', ep)):
                    image = media.get('image') if media else None
                    url = image.get('original') if isinstance(image, dict) else None
                    if isinstance(url, str):
                        record = self._record(enriched, kind, url, 'tvmaze', identifier, dict(evidence))
                        if self.tvmaze_gallery:
                            record['scope'] = 'series' if kind == 'poster' else 'episode'
                            record['evidence']['resolution'] = 'original'
                            medium = image.get('medium')
                            if isinstance(medium, str) and medium != url:
                                resolution_fallbacks[id(record)] = {'url': medium}
                        records.append(record)
                if self.tvmaze_gallery:
                    image = self._tvmaze_gallery_image(identifier)
                    if image:
                        gallery_evidence = dict(evidence, gallery_image_id=image['id'], resolution='original',
                            gallery_endpoint=f'https://api.tvmaze.com/shows/{identifier}/images',
                            provider_dimensions={key: image['original'][key] for key in ('width', 'height')})
                        record = self._record(enriched, 'backdrop', image['original']['url'],
                                              'tvmaze', identifier, gallery_evidence)
                        record['scope'] = 'series'
                        records.append(record)
                        if image['medium'] and image['medium']['url'] != record['url']:
                            resolution_fallbacks[id(record)] = image['medium']
        if provider:
            self.last_metadata = {'provider': provider, 'provider_id': identifier, 'scope': scope}
            if ((provider == 'tvmaze' and self.tvmaze_bulk_metadata)
                    or (provider == 'tmdb' and self.tmdb_credits)):
                credits = self._programme_credits(enriched, provider, scope, identifier)
                if credits:
                    self.last_metadata['credits'] = credits
                    for record in records:
                        if record['provider'] == provider:
                            record['evidence']['credits'] = credits
        for icon in enriched.findall('icon'):
            enriched.remove(icon)
        records.extend(feed)
        fallback_records = []
        emitted_urls = set()
        for record in records:
            if record['provider'] == 'feed' and not self.config.get('allow_feed_images', False):
                continue
            record.update(self.verify_media(record['url']))
            if record['verified']:
                if not self.tvmaze_gallery or record['url'] not in emitted_urls:
                    ET.SubElement(enriched, 'icon', {'src': record['url']})
                    emitted_urls.add(record['url'])
            elif id(record) in resolution_fallbacks:
                resolution = resolution_fallbacks[id(record)]
                evidence = dict(record['evidence'], resolution='medium',
                    fallback_from_sha256=hashlib.sha256(record['url'].encode('utf-8')).hexdigest())
                if 'provider_dimensions' in evidence:
                    evidence['provider_dimensions'] = {key: resolution[key] for key in ('width', 'height')}
                medium = self._record(enriched, record['type'], resolution['url'],
                    record['provider'], record['provider_id'],
                    evidence)
                medium['scope'] = record['scope']
                medium.update(self.verify_media(medium['url']))
                if medium['verified']:
                    fallback_records.append(medium)
                    if medium['url'] not in emitted_urls:
                        ET.SubElement(enriched, 'icon', {'src': medium['url']})
                        emitted_urls.add(medium['url'])
        records.extend(fallback_records)
        if provider:
            self.counts['programmes_matched'] += 1
            if identity not in self.matched_identities:
                self.matched_identities.add(identity)
                self.counts['unique_identities_matched'] += 1
        existing_ids = {(n.get('system'), n.text) for n in enriched.findall('episode-num')}
        for node in source_ids:
            if (node.get('system'), node.text) not in existing_ids:
                enriched.append(node)
        for node in enriched.findall('episode-num[@system="xmltv_ns"]'):
            enriched.remove(node)
        enriched.extend(source_ns)
        if source_dates is not None:
            for node in enriched.findall('date'):
                enriched.remove(node)
            enriched.extend(source_dates)
        return enriched, records

    def summary(self):
        report = dict(self.counts)
        report.update(tmdb_status='configured' if self.tmdb is not None else 'not_configured',
                      tmdb_live_status=getattr(self, 'tmdb_live_status',
                          'TMDB_RUNTIME_BOUND' if self.tmdb is not None else 'TMDB_LIVE_PENDING'),
                      tvmaze_status='authorized' if self.authorize_tvmaze else 'not_authorized',
                      artwork_status='verified' if self.counts['verified_images'] else 'zero_verified_images')
        return report
