"""Final live safety regressions with synthetic metadata and in-memory state."""
import copy
import json
import unittest
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from image_fixtures import PNG
from test_live_artwork import MovieClient, event
from xcmate_epg.fetch import Response
from xcmate_epg.live_artwork import ArtworkEnricher
from xcmate_epg.state import State


SHOW = {'id': 7, 'name': 'Example', 'premiered': '2020-01-01'}
EPISODE = {'id': 12, 'season': 1, 'number': 2, 'type': 'regular',
           'airdate': '2020-02-03', 'summary': 'Episode plot'}
SEARCH_KEY = json.dumps(['live-tvmaze-search-v1', 'example'])
TITLE_KEY = json.dumps(['live-tvmaze-show-v2', 'example', '2020'])
GALLERY_KEY = json.dumps(['live-tvmaze-gallery-v1', 7])
EPISODE_KEY = json.dumps(['live-tvmaze-episode-v2', 7, 1, 2])
BACKDROP = 'https://static.tvmaze.com/synthetic/final-background.png'
BACKGROUND = {'id': 31, 'type': 'background', 'resolutions': {
    'original': {'url': BACKDROP, 'width': 1920, 'height': 1080}}}


class FinalLiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.state = State(':memory:')
        self.addCleanup(self.state.db.close)
        self.routes = {'/search/shows': [{'show': SHOW}], '/shows/7': SHOW,
                       '/shows/7/episodes': [EPISODE],
                       '/shows/7/episodebynumber': EPISODE,
                       '/shows/7/cast': [], '/shows/7/images': [BACKGROUND]}
        self.requests = []

    def transport(self, url, headers):
        parts = urlsplit(url)
        self.requests.append(parts.path)
        if parts.hostname in ('static.tvmaze.com', 'image.tmdb.org'):
            return Response(200, PNG, {'Content-Type': 'image/png'})
        self.assertEqual(parts.hostname, 'api.tvmaze.com')
        self.assertIn(parts.path, self.routes)
        body = self.routes[parts.path]
        return body if isinstance(body, Response) else Response(200, json.dumps(body).encode(), {})

    def adapter(self, *, tmdb=None, **config):
        return ArtworkEnricher(self.state, {'request_interval': 0, 'authorize_tvmaze': True,
            'tvmaze_bulk_metadata': True, **config}, tmdb=tmdb, transport=self.transport)

    def clear(self):
        self.state.db.execute('DELETE FROM artwork')
        self.requests.clear()

    def series(self, *, episode=False):
        return event(episode=episode, category='series')

    def movie_client(self):
        client = MovieClient()
        client.movie_credits = lambda identifier: {'id': identifier, 'cast': [{'name': 'Movie Actor'}]}
        return client

    def test_source_episode_veto_blocks_live_and_cached_tmdb_movie_lookup(self):
        # Removing untrusted provider IDs must not erase their episode-scope veto.
        for warm in (False, True):
            for system in ('tmdb', 'tvmaze'):
                for mixed_categories in (False, True):
                    with self.subTest(warm=warm, system=system, mixed=mixed_categories):
                        self.clear()
                        client = self.movie_client()
                        if warm:
                            self.adapter(tmdb=client, tmdb_credits=True).enrich(*event())
                        before = client.calls
                        channel, programme = event()
                        if mixed_categories:
                            ET.SubElement(programme, 'category').text = 'Series'
                        ET.SubElement(programme, 'episode-num', system=system).text = 'tv/123/1/2'
                        ET.SubElement(programme, 'credits').append(ET.Element('director'))
                        original = ET.tostring(programme)
                        adapter = self.adapter(tmdb=client, tmdb_credits=True)
                        result, records = adapter.enrich(channel, programme)
                        self.assertEqual(ET.tostring(programme), original)
                        self.assertEqual(result.findtext(f'episode-num[@system="{system}"]'), 'tv/123/1/2')
                        self.assertEqual(len(result.findall('episode-num')), 1)
                        self.assertIsNone(result.find('desc'))
                        self.assertIsNone(result.find('credits/actor'))
                        self.assertIsNotNone(result.find('credits/director'))
                        self.assertEqual(records, [])
                        self.assertIsNone(adapter.last_metadata)
                        self.assertEqual(client.calls, before)
                        self.assertEqual(adapter.summary()['programmes_matched'], 0)

    def test_independent_numeric_episode_survives_movie_and_provider_episode_markers(self):
        for system in ('tmdb', 'tvmaze'):
            with self.subTest(system=system):
                self.clear()
                channel, programme = event(episode=True)
                ET.SubElement(programme, 'episode-num', system=system).text = 'tv/123/9/8'
                result, _ = self.adapter(tmdb=self.movie_client()).enrich(channel, programme)
                self.assertEqual(result.findtext('episode-num[@system="tmdb"]'), 'tv/2/1/2')
                self.assertIn('tv/123/9/8', [node.text for node in result.findall('episode-num')])
                self.assertEqual(result.findtext('episode-num[@system="xmltv_ns"]'), '0.1.')

    def test_series_and_movie_source_ids_still_allow_independent_movie_lookup(self):
        for system in ('tmdb', 'tvmaze'):
            for source in ('tv/123', 'movie/123'):
                with self.subTest(system=system, source=source):
                    self.clear()
                    channel, programme = event()
                    ET.SubElement(programme, 'episode-num', system=system).text = source
                    result, _ = self.adapter(tmdb=self.movie_client()).enrich(channel, programme)
                    self.assertEqual(result.findtext('episode-num[@system="tmdb"]'), 'movie/1')
                    self.assertIn(source, [node.text for node in result.findall('episode-num')])

    def test_bulk_requires_explicit_regular_type_after_unavailable_index(self):
        missing_type = {key: value for key, value in EPISODE.items() if key != 'type'}
        for cached in (False, True):
            with self.subTest(cached=cached):
                self.clear()
                self.routes['/shows/7/episodes'] = Response(503, b'', {})
                self.routes['/shows/7/episodebynumber'] = missing_type
                if cached:
                    self.state.cache(EPISODE_KEY, missing_type, 30 * 86400)
                adapter = self.adapter()
                result, _ = adapter.enrich(*self.series(episode=True))
                self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
                self.assertIsNone(result.find('desc'))
                self.assertIsNone(adapter.last_metadata)

    def test_typed_fallback_still_matches_after_unavailable_bulk_index(self):
        for cached in (False, True):
            with self.subTest(cached=cached):
                self.clear()
                self.routes['/shows/7/episodes'] = Response(503, b'', {})
                if cached:
                    self.state.cache(EPISODE_KEY, EPISODE, 30 * 86400)
                result, _ = self.adapter().enrich(*self.series(episode=True))
                self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7/1/2')
                self.assertEqual(result.findtext('desc'), 'Episode plot')

    def test_legacy_mode_keeps_missing_type_fallback_compatibility(self):
        self.routes['/shows/7/episodebynumber'] = {k: v for k, v in EPISODE.items() if k != 'type'}
        result, _ = self.adapter(tvmaze_bulk_metadata=False).enrich(*self.series(episode=True))
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7/1/2')
        self.assertNotIn('/shows/7/episodes', self.requests)

    def test_malformed_search_response_is_retryable_on_new_adapter(self):
        for bad_show in ({}, {'id': 7}, {'id': True, 'name': 'Example'}, {'id': 7, 'name': ' '}):
            with self.subTest(bad_show=bad_show):
                self.clear()
                self.routes['/search/shows'] = [{'show': bad_show}]
                result, _ = self.adapter().enrich(*self.series())
                self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
                self.assertFalse(self.state.cached(SEARCH_KEY)[0])
                self.assertFalse(self.state.cached(TITLE_KEY)[0])
                self.routes['/search/shows'] = [{'show': SHOW}]
                result, _ = self.adapter().enrich(*self.series())
                self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7')
                self.assertEqual(self.requests.count('/search/shows'), 2)

    def test_cached_malformed_search_bypasses_legacy_title_negative(self):
        self.state.cache(SEARCH_KEY, [{'first_air_date': ''}], 30 * 86400)
        self.state.cache(TITLE_KEY, None, 86400)
        result, _ = self.adapter().enrich(*self.series())
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7')
        self.assertEqual(self.requests.count('/search/shows'), 1)
        self.assertEqual(self.state.cached(SEARCH_KEY)[1][0]['id'], 7)

    def test_mixed_search_retains_independently_verified_match_without_raw_cache(self):
        for bad_row in ({'show': {}}, {'error': 'temporarily unavailable'}):
            with self.subTest(bad_row=bad_row):
                self.clear()
                self.routes['/search/shows'] = [bad_row, {'show': SHOW}]
                result, _ = self.adapter().enrich(*self.series())
                self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7')
                self.assertFalse(self.state.cached(SEARCH_KEY)[0])
                self.assertEqual(self.requests.count('/shows/7'), 1)

    def test_mixed_search_cannot_cache_absence_for_unproven_rows(self):
        self.routes['/search/shows'] = [{'show': dict(SHOW, name='Different')}, {'show': {}}]
        result, _ = self.adapter().enrich(*self.series())
        self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
        self.assertFalse(self.state.cached(SEARCH_KEY)[0])
        self.assertFalse(self.state.cached(TITLE_KEY)[0])

    def test_cached_mixed_search_retries_then_uses_independent_valid_detail(self):
        self.state.cache(SEARCH_KEY, [{'first_air_date': ''}, dict(SHOW, first_air_date='2020-01-01')], 300)
        self.routes['/search/shows'] = Response(503, b'', {})
        result, _ = self.adapter().enrich(*self.series())
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7')
        self.assertEqual(self.requests.count('/search/shows'), 1)
        self.assertEqual(self.requests.count('/shows/7'), 1)

    def test_valid_empty_search_preserves_negative_cache(self):
        self.routes['/search/shows'] = []
        self.adapter().enrich(*self.series())
        self.adapter(max_requests=0).enrich(*self.series())
        self.assertEqual(self.requests, ['/search/shows'])
        self.assertEqual(self.state.cached(SEARCH_KEY), (True, []))
        self.assertEqual(self.state.cached(TITLE_KEY), (True, None))

    def test_malformed_gallery_response_is_retryable_on_new_adapter(self):
        for bad_row in ({'error': 'temporarily unavailable'},
                        {'id': 31, 'type': 'background'},
                        {'id': 31, 'type': [], 'resolutions': {}}):
            with self.subTest(bad_row=bad_row):
                self.clear()
                self.routes['/shows/7/images'] = [bad_row]
                _, records = self.adapter(tvmaze_gallery=True).enrich(*self.series())
                self.assertEqual(records, [])
                self.assertFalse(self.state.cached(GALLERY_KEY)[0])
                self.routes['/shows/7/images'] = [BACKGROUND]
                _, records = self.adapter(tvmaze_gallery=True).enrich(*self.series())
                self.assertEqual([r['url'] for r in records if r['verified']], [BACKDROP])
                self.assertEqual(self.requests.count('/shows/7/images'), 2)

    def test_cached_malformed_gallery_is_revalidated_and_refetched(self):
        self.state.cache(GALLERY_KEY, [{'error': 'temporarily unavailable'}], 30 * 86400)
        _, records = self.adapter(tvmaze_gallery=True).enrich(*self.series())
        self.assertEqual([r['url'] for r in records if r['verified']], [BACKDROP])
        self.assertEqual(self.requests.count('/shows/7/images'), 1)
        self.assertEqual(self.state.cached(GALLERY_KEY)[1], [BACKGROUND])

    def test_valid_gallery_empty_nontarget_and_null_types_remain_cacheable(self):
        for rows in ([], [dict(BACKGROUND, type='poster')], [dict(BACKGROUND, type=None)]):
            with self.subTest(rows=rows):
                self.clear()
                self.routes['/shows/7/images'] = rows
                _, records = self.adapter(tvmaze_gallery=True).enrich(*self.series())
                self.assertEqual(records, [])
                self.assertEqual(self.state.cached(GALLERY_KEY), (True, rows))
                self.adapter(tvmaze_gallery=True, max_requests=0).enrich(*self.series())
                self.assertEqual(self.requests.count('/shows/7/images'), 1)

    def test_mixed_gallery_uses_valid_row_without_caching_malformed_list(self):
        self.routes['/shows/7/images'] = [{'error': 'temporarily unavailable'}, BACKGROUND]
        _, records = self.adapter(tvmaze_gallery=True).enrich(*self.series())
        self.assertEqual([r['url'] for r in records if r['verified']], [BACKDROP])
        self.assertFalse(self.state.cached(GALLERY_KEY)[0])

    def test_cached_mixed_gallery_retries_and_retains_valid_conflict_proof(self):
        for conflict, expected in (({'error': 'temporarily unavailable'}, [BACKDROP]),
                                   ({'id': 31, 'error': 'temporarily unavailable'}, [])):
            with self.subTest(conflict=conflict):
                self.clear()
                self.state.cache(GALLERY_KEY, [conflict, BACKGROUND], 300)
                self.routes['/shows/7/images'] = Response(503, b'', {})
                _, records = self.adapter(tvmaze_gallery=True).enrich(*self.series())
                self.assertEqual([r['url'] for r in records if r['verified']], expected)
                self.assertEqual(self.requests.count('/shows/7/images'), 1)

    def test_malformed_gallery_duplicate_id_still_disqualifies_valid_candidate(self):
        for conflicting in ({'id': 31, 'error': 'temporarily unavailable'},
                            dict(BACKGROUND, type=None), dict(BACKGROUND, type='poster')):
            for rows in ([BACKGROUND, conflicting], [conflicting, BACKGROUND]):
                with self.subTest(rows=rows):
                    self.clear()
                    unrelated = copy.deepcopy(BACKGROUND)
                    unrelated['id'] = 32
                    unrelated['resolutions']['original']['url'] = BACKDROP.replace('final-', 'other-')
                    self.routes['/shows/7/images'] = rows + [unrelated]
                    _, records = self.adapter(tvmaze_gallery=True).enrich(*self.series())
                    self.assertEqual([r['evidence']['gallery_image_id'] for r in records], [32])
                    self.assertNotIn('/synthetic/final-background.png', self.requests)

    def test_search_auth_transient_and_budget_failures_never_cache_absence(self):
        for status in (401, 403, 429, 503):
            with self.subTest(status=status):
                self.clear()
                self.routes['/search/shows'] = Response(status, b'', {})
                self.adapter().enrich(*self.series())
                self.assertFalse(self.state.cached(SEARCH_KEY)[0])
                self.assertFalse(self.state.cached(TITLE_KEY)[0])
                self.routes['/search/shows'] = [{'show': SHOW}]
                result, _ = self.adapter().enrich(*self.series())
                self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7')
        self.clear()
        self.adapter(max_requests=0).enrich(*self.series())
        self.assertFalse(self.state.cached(SEARCH_KEY)[0])
        self.assertFalse(self.state.cached(TITLE_KEY)[0])


if __name__ == '__main__':
    unittest.main()
