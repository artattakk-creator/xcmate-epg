"""Bounded, explicitly authorized metadata enrichment for one private event.

No environment credentials are read. Input source artwork remains private until a
concrete image has passed the same verifier used for provider artwork.
"""
import copy
import hashlib
import html
import json
import re
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from . import artwork, pipeline
from .fetch import http_get
from .xmltv import IMAGE_HOSTS, public_image


class _Unavailable(ValueError):
    pass


class _Cache:
    """Reuse only values produced under this adapter's identity guarantees.

    Legacy entries stay private and intact, but their original format does not
    prove current exact-title/year/episode checks, so they cannot make matches.
    """
    def __init__(self, owner):
        self.owner = owner

    def cached(self, key):
        found, value = self.owner.state.cached(json.dumps(['live-artwork-v2', key]))
        self.owner.counts['metadata_cache_hits'] += bool(found)
        return found, value

    def cache(self, key, value, ttl):
        self.owner.state.cache(json.dumps(['live-artwork-v2', key]), value, ttl)


class _Metadata:
    """Keep the existing enrichment/cache path, with counted client operations."""
    def __init__(self, owner, client):
        self.owner, self.client = owner, client
        self.matches = {}

    def _search(self, kind, title, year):
        hits = self.owner._client(self.client, 'search' if kind == 'movie' else 'search_series', title, year)
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
        if (not isinstance(detail, dict) or detail.get('season_number') != season
                or detail.get('episode_number') != number):
            raise _Unavailable('metadata mismatch')
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
            'image_cache_hits', 'metadata_cache_hits', 'verified_images')}
        self.identities = set()
        self.matched_identities = set()
        self.last_request = None
        self.blocked_hosts = set()
        if isinstance(tmdb, artwork.TMDB):
            # A separate client avoids mutating the caller's reusable client.
            self.tmdb = artwork.TMDB(tmdb._token, transport=self._api_transport, sleep=lambda _: None)
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
        key = json.dumps(['verified-image-v1', url])
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
            signatures = {'image/png': lambda b: b.startswith(b'\x89PNG\r\n\x1a\n'),
                          'image/jpeg': lambda b: b.startswith(b'\xff\xd8\xff'),
                          'image/webp': lambda b: b.startswith(b'RIFF') and b[8:12] == b'WEBP'}
            if (any(k.lower() == 'location' for k in response.headers)
                    or getattr(response, 'url', url) != url
                    or response.status != 200 or not isinstance(body, bytes) or len(body) > 8 * 1024 * 1024
                    or mime not in signatures or not signatures[mime](body)):
                raise ValueError()
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

    def _tvmaze_lookup(self, title, year, episode):
        key = json.dumps(['live-tvmaze-v1', artwork.title_key(title), year, episode])
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
                value = {'show': detail, 'episode': None}
                if episode:
                    season, number = episode
                    ep = self._tvmaze_json(f'https://api.tvmaze.com/shows/{identifier}/episodebynumber?season={season}&number={number}')
                    if (ep.get('season') != season or ep.get('number') != number
                            or type(ep.get('id')) is not int or ep['id'] <= 0):
                        raise _Unavailable('metadata mismatch')
                    value['episode'] = ep
            self.state.cache(key, value, 30 * 86400 if value else 86400)
            return value
        except (ValueError, TimeoutError, OSError, TypeError, AttributeError):
            return None

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
        ns = programme.findtext('episode-num[@system="xmltv_ns"]', '')
        match = re.fullmatch(r'(\d+)\.(\d+)\.', ns)
        episode = tuple(int(n) + 1 for n in match.groups()) if match else None
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
        feed = []
        for icon in clean.findall('icon'):
            clean.remove(icon)
            record = self._record(clean, 'unclassified_source_image', icon.get('src', ''), 'feed', None,
                                  {'source': 'original programme icon', 'identity_verified': False})
            feed.append(record)
        tree.append(clean)
        # Calling aggregate also exercises existing movie/episode positive/negative caches.
        result, _ = pipeline.aggregate([('bounded-event', tree)], _Cache(self),
                                      _Metadata(self, self.tmdb) if self.tmdb is not None else None)
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
                for kind, field in (('poster', 'poster_path'), ('backdrop', 'backdrop_path')):
                    try:
                        url = artwork.image_url(series.get(field))
                    except ValueError:
                        continue
                    if url:
                        records.append(self._record(enriched, kind, url, 'tmdb', identifier,
                            {'title': 'unicode_normalized_exact', 'year': year, 'episode': list(episode)}))
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
                             'episode': list(episode) if episode else None, 'episode_id': ep.get('id') if ep else None}))
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
        return enriched, records

    def summary(self):
        report = dict(self.counts)
        report.update(tmdb_status='configured' if self.tmdb is not None else 'not_configured',
                      tvmaze_status='authorized' if self.authorize_tvmaze else 'not_authorized',
                      artwork_status='verified' if self.counts['verified_images'] else 'zero_verified_images')
        return report
