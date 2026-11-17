"""Bounded, explicitly authorized metadata enrichment for one private event.

No environment credentials are read. Input source artwork remains private until a
concrete image has passed the same verifier used for provider artwork.
"""
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
        if not isinstance(detail, dict):
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
        self.counts = {key: 0 for key in (
            'programmes_considered', 'programmes_eligible', 'programmes_matched',
            'unique_identities_considered', 'unique_identities_matched', 'ambiguous',
            'api_attempts', 'api_successes', 'api_errors', 'api_budget_exhausted',
            'image_attempts', 'image_successes', 'image_errors', 'image_budget_exhausted',
            'image_cache_hits', 'metadata_cache_hits', 'verified_images',
            'episode_year_unproven', 'episode_year_mismatched', 'show_year_unavailable')}
        self.identities = set()
        self.matched_identities = set()
        self.last_request = None
        self.blocked_hosts = set()
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
        host = urlsplit(url).hostname
        if host not in {'api.themoviedb.org', 'api.tvmaze.com'} or host in self.blocked_hosts:
            raise _Unavailable('metadata unavailable')
        return self._request(url, headers, host)

    def _request(self, url, headers, host):
        if self.counts['api_attempts'] >= self.max_requests:
            self.counts['api_budget_exhausted'] += 1
            raise _Unavailable('metadata budget exhausted')
        self._pace()
        self.counts['api_attempts'] += 1
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
            if not isinstance(response.body, bytes) or len(response.body) > 8 * 1024 * 1024:
                raise _Unavailable('metadata unavailable')
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            self.counts['api_errors'] += 1
            raise _Unavailable('metadata unavailable') from None
        self.counts['api_successes'] += 1
        return response

    def _client(self, client, method, *args):
        if isinstance(client, (artwork.TMDB, artwork.TVMaze)):
            try:
                return getattr(client, method)(*args)
            except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
                raise _Unavailable('metadata unavailable') from None
        # Explicit injected clients without a transport are counted per operation.
        if self.counts['api_attempts'] >= self.max_requests:
            self.counts['api_budget_exhausted'] += 1
            raise _Unavailable('metadata budget exhausted')
        self._pace()
        self.counts['api_attempts'] += 1
        try:
            result = getattr(client, method)(*args)
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            self.counts['api_errors'] += 1
            raise _Unavailable('metadata unavailable') from None
        self.counts['api_successes'] += 1
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

    def _tvmaze_show(self, title, year):
        key = json.dumps(['live-tvmaze-show-v2', artwork.title_key(title), year])
        found, value = self.state.cached(key)
        if found:
            self.counts['metadata_cache_hits'] += 1
            return value
        try:
            hits = self._client(self.tvmaze, 'search_series', title, int(year) if year else None)
            candidates = {}
            for hit in hits:
                if (not isinstance(hit, dict) or type(hit.get('id')) is not int or hit['id'] <= 0
                        or artwork.title_key(hit.get('name')) != artwork.title_key(title)
                        or (year and str(hit.get('premiered') or hit.get('first_air_date') or '')[:4] != year)):
                    continue
                candidates[hit['id']] = hit
            value = None
            if len(candidates) > 1:
                self.counts['ambiguous'] += 1
            if len(candidates) == 1:
                identifier = next(iter(candidates))
                detail = self._tvmaze_json(f'https://api.tvmaze.com/shows/{identifier}')
                if (detail.get('id') != identifier or artwork.title_key(detail.get('name')) != artwork.title_key(title)
                        or (year and str(detail.get('premiered') or '')[:4] != year)):
                    raise _Unavailable('metadata mismatch')
                value = detail
            self.state.cache(key, value, 30 * 86400 if value else 86400)
            return value
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            return None

    def _tvmaze_lookup(self, title, year, episode):
        detail = self._tvmaze_show(title, '' if episode else year)
        if not detail:
            return None
        if not episode:
            return {'show': detail, 'episode': None}
        season, number = episode
        identifier = detail['id']
        key = json.dumps(['live-tvmaze-episode-v2', identifier, season, number])
        found, ep = self.state.cached(key)
        if found:
            self.counts['metadata_cache_hits'] += 1
        else:
            try:
                ep = self._tvmaze_json(f'https://api.tvmaze.com/shows/{identifier}/episodebynumber?season={season}&number={number}')
                if (ep.get('season') != season or ep.get('number') != number
                        or type(ep.get('id')) is not int or ep['id'] <= 0):
                    raise _Unavailable('metadata mismatch')
                self.state.cache(key, ep, 30 * 86400)
            except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
                return None
        if not self._episode_year_matches(ep.get('airdate'), year):
            return None
        return {'show': detail, 'episode': ep}

    def _tvmaze_json(self, url):
        data = json.loads(self._api_transport(url, {}).body)
        if not isinstance(data, dict):
            raise _Unavailable('metadata unavailable')
        return data

    def _record(self, programme, kind, url, provider, identifier, evidence):
        return {'type': kind, 'url': url, 'provider': provider, 'provider_id': identifier,
                'verified': False, 'attribution': ('TMDB: https://www.themoviedb.org/' if provider == 'tmdb' else
                    'TVMaze (CC BY-SA): https://www.tvmaze.com/' if provider == 'tvmaze' else 'original source'),
                'evidence': evidence, 'channel': programme.get('channel'),
                'start': programme.get('start'), 'stop': programme.get('stop')}

    def enrich(self, channel, programme):
        self.counts['programmes_considered'] += 1
        title = programme.findtext('title', '')
        year = programme.findtext('date', '')[:4]
        year = year if re.fullmatch(r'\d{4}', year) else ''
        ns_values = [n.text for n in programme.findall('episode-num[@system="xmltv_ns"]')]
        parsed = [parse_xmltv_ns(value) for value in ns_values]
        episode = parsed[0] if parsed and parsed[0] is not None and all(p == parsed[0] for p in parsed) else None
        categories = {artwork.title_key(c.text) for c in programme.findall('category')}
        movie = bool(categories & {'movie', 'film', 'spielfilm'}) and not any(
            n.get('system') not in {'tmdb', 'tvmaze'} for n in programme.findall('episode-num'))
        show = bool(episode or categories & {'series', 'serie', 'tv series', 'tv show', 'fernsehserie'})
        eligible = bool((movie and year) or show)
        identity = (artwork.title_key(title), year, episode, 'movie' if movie else 'tv' if show else 'unknown')
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
        # Calling aggregate also exercises existing movie/episode positive/negative caches.
        result, _ = pipeline.aggregate([('bounded-event', tree)], _Cache(self, year if episode else None), metadata)
        enriched = result.find('programme')
        matched = False
        tmdb_identity = enriched.findtext('episode-num[@system="tmdb"]', '')
        if tmdb_identity:
            parts = tmdb_identity.split('/')
            if len(parts) in (2, 4) and parts[0] in ('movie', 'tv') and parts[1].isdigit():
                identifier = int(parts[1])
                matched = True
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
        if not matched and show and not episode and not source_ns and self.tmdb is not None:
            hit = self._tmdb_show(title, year)
            detail = self._tmdb_series_detail(title, hit, year) if hit else None
            if detail:
                matched = True
                identifier = detail['id']
                ET.SubElement(enriched, 'episode-num', {'system': 'tmdb'}).text = 'tv/' + str(identifier)
                self._tmdb_series_metadata(enriched, detail)
                records.extend(self._tmdb_images(enriched, detail, identifier,
                    {'title': 'unicode_normalized_exact', 'year': year or None,
                     'series_premiere': detail.get('first_air_date'), 'series_detail_identity': 'exact',
                     'episode': None}))
        if matched and len(tmdb_identity.split('/')) == 4:
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
        if not matched and show and self.authorize_tvmaze:
            value = self._tvmaze_lookup(title, year, episode)
            if value:
                matched = True
                detail, ep = value['show'], value['episode']
                identifier = detail['id']
                if enriched.find('episode-num[@system="tvmaze"]') is None:
                    ET.SubElement(enriched, 'episode-num', {'system': 'tvmaze'}).text = 'tv/' + str(identifier) + (f'/{episode[0]}/{episode[1]}' if episode else '')
                summary = detail.get('summary')
                if not enriched.findtext('desc') and isinstance(summary, str):
                    text = html.unescape(re.sub('<[^>]*>', '', summary)).strip()
                    if text:
                        ET.SubElement(enriched, 'desc').text = text
                for genre in detail.get('genres', []) if isinstance(detail.get('genres', []), list) else []:
                    if isinstance(genre, str) and genre.strip() and genre not in [c.text for c in enriched.findall('category')]:
                        ET.SubElement(enriched, 'category').text = genre
                rating = detail.get('rating')
                rating = rating.get('average') if isinstance(rating, dict) else None
                if enriched.find('star-rating') is None and type(rating) in (int, float) and 0 < rating <= 10:
                    node = ET.SubElement(enriched, 'star-rating', {'system': 'TVMaze'})
                    ET.SubElement(node, 'value').text = str(rating) + '/10'
                for kind, media in (('poster', detail), ('episode_still', ep)):
                    image = media.get('image') if media else None
                    url = image.get('original') if isinstance(image, dict) else None
                    if isinstance(url, str):
                        records.append(self._record(enriched, kind, url, 'tvmaze', identifier,
                            {'title': 'unicode_normalized_exact', 'year': year or None,
                             'episode': list(episode) if episode else None, 'episode_id': ep.get('id') if ep else None,
                             'episode_airdate': ep.get('airdate') if ep else None,
                             'episode_year_validation': ('exact' if year else 'not_supplied') if ep else None}))
        for icon in enriched.findall('icon'):
            enriched.remove(icon)
        records.extend(feed)
        for record in records:
            if record['provider'] == 'feed' and not self.config.get('allow_feed_images', False):
                continue
            record.update(self.verify_media(record['url']))
            if record['verified']:
                ET.SubElement(enriched, 'icon', {'src': record['url']})
        if matched:
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
