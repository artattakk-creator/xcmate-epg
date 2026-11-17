"""Synthetic gallery and explicit provider-resolution fallback contracts."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from image_fixtures import png
from test_live_artwork import event
from xcmate_epg.fetch import Response
from xcmate_epg.live_artwork import ArtworkEnricher
from xcmate_epg.state import State


CDN = 'https://static.tvmaze.com/synthetic/'
GALLERY_KEY = json.dumps(['live-tvmaze-gallery-v1', 7])


def background(identifier=31, *, main=True, width=1920, height=1080):
    return {'id': identifier, 'type': 'background', 'main': main, 'resolutions': {
        'original': {'url': CDN + f'background-{identifier}.png', 'width': width, 'height': height},
        'medium': {'url': CDN + f'background-{identifier}-medium.png', 'width': 640, 'height': 360}}}


class GalleryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = State(temporary.name + '/state.sqlite')
        self.addCleanup(self.state.db.close)
        self.show = {'id': 7, 'name': 'Example', 'premiered': '2020-01-01'}
        self.episode = {'id': 9, 'season': 1, 'number': 2, 'type': 'regular', 'airdate': '2020-02-03'}
        self.routes = {'/search/shows': [{'show': self.show}], '/shows/7': self.show,
                       '/shows/7/episodebynumber': self.episode, '/shows/7/images': [background()]}
        self.requests = []
        self.responses = {}

    def transport(self, url, headers):
        self.requests.append(url)
        if url in self.responses:
            response = self.responses[url]
            if isinstance(response, Exception):
                raise response
            return response
        parts = urlsplit(url)
        if parts.hostname == 'static.tvmaze.com':
            return Response(200, png(4, 2), {'Content-Type': 'image/png'})
        self.assertEqual(parts.hostname, 'api.tvmaze.com')
        self.assertIn(parts.path, self.routes)
        body = self.routes[parts.path]
        return body if isinstance(body, Response) else Response(200, json.dumps(body).encode(), {})

    def adapter(self, **config):
        return ArtworkEnricher(self.state, {'request_interval': 0, 'authorize_tvmaze': True,
                              'tvmaze_gallery': True, **config}, transport=self.transport)

    def enrich(self, adapter=None, *, episode=False):
        return (adapter or self.adapter()).enrich(*event(episode=episode, category='series'))

    def test_verified_backdrop_keeps_series_scope_and_episode_identity(self):
        adapter = self.adapter()
        channel, programme = event(episode=True, category='series')
        ET.SubElement(programme, 'desc').text = 'Existing description'
        ET.SubElement(programme, 'credits').append(ET.Element('actor'))
        result, records = adapter.enrich(channel, programme)
        self.assertEqual([r['type'] for r in records], ['backdrop'])
        record = records[0]
        self.assertTrue(record['verified'])
        self.assertEqual((record['provider'], record['provider_id'], record['scope']), ('tvmaze', 7, 'series'))
        self.assertEqual(record['evidence']['episode'], [1, 2])
        self.assertEqual(record['evidence']['episode_id'], 9)
        self.assertEqual(record['evidence']['gallery_image_id'], 31)
        self.assertEqual(record['evidence']['resolution'], 'original')
        self.assertEqual(result.find('icon').get('src'), CDN + 'background-31.png')
        self.assertEqual(result.findtext('desc'), 'Existing description')
        self.assertIsNotNone(result.find('credits/actor'))
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7/1/2')
        self.assertEqual(adapter.summary()['tvmaze_api_attempts'], 4)
        self.assertEqual(adapter.summary()['image_successes'], 1)
        self.assertEqual(adapter.last_metadata['scope'], 'episode')

    def test_flag_defaults_off_and_does_not_fetch_gallery_or_medium(self):
        self.show['image'] = {'original': CDN + 'poster.png', 'medium': CDN + 'poster-medium.png'}
        self.responses[CDN + 'poster.png'] = Response(404, b'', {})
        adapter = ArtworkEnricher(self.state, {'request_interval': 0, 'authorize_tvmaze': True},
                                 transport=self.transport)
        result, records = self.enrich(adapter)
        self.assertIsNone(result.find('icon'))
        self.assertEqual([r['url'] for r in records], [CDN + 'poster.png'])
        self.assertNotIn('https://api.tvmaze.com/shows/7/images', self.requests)
        self.assertNotIn(CDN + 'poster-medium.png', self.requests)

    def test_original_valid_never_fetches_or_emits_medium(self):
        self.show['image'] = {'original': CDN + 'poster.png', 'medium': CDN + 'poster-medium.png'}
        self.episode['image'] = {'original': CDN + 'episode.png', 'medium': CDN + 'episode-medium.png'}
        result, records = self.enrich(episode=True)
        self.assertEqual(len(result.findall('icon')), 3)
        self.assertEqual([r['type'] for r in records], ['poster', 'episode_still', 'backdrop'])
        self.assertFalse(any('medium' in u for u in self.requests))

    def test_original_shared_across_roles_keeps_typed_records_with_one_xml_icon(self):
        self.show['image'] = {'original': CDN + 'background-31.png'}
        result, records = self.enrich()
        self.assertEqual([r['type'] for r in records], ['poster', 'backdrop'])
        self.assertTrue(all(r['verified'] for r in records))
        self.assertEqual([node.get('src') for node in result.findall('icon')],
                         [CDN + 'background-31.png'])

    def test_medium_shared_across_roles_keeps_typed_records_with_one_xml_icon(self):
        self.show['image'] = {'original': CDN + 'bad-poster.png', 'medium': CDN + 'background-31-medium.png'}
        for filename in ('bad-poster.png', 'background-31.png'):
            self.responses[CDN + filename] = Response(404, b'', {})
        result, records = self.enrich()
        verified = [record for record in records if record['verified']]
        self.assertEqual([r['type'] for r in verified], ['poster', 'backdrop'])
        self.assertEqual(len(records), 4)
        self.assertEqual([node.get('src') for node in result.findall('icon')],
                         [CDN + 'background-31-medium.png'])

    def test_bad_original_retained_and_explicit_medium_verified_for_every_type(self):
        self.show['image'] = {'original': CDN + 'poster.png', 'medium': CDN + 'poster-medium.png'}
        self.episode['image'] = {'original': CDN + 'episode.png', 'medium': CDN + 'episode-medium.png'}
        for filename in ('poster.png', 'episode.png', 'background-31.png'):
            self.responses[CDN + filename] = Response(200, b'not an image', {'Content-Type': 'image/png'})
        adapter = self.adapter()
        result, records = self.enrich(adapter, episode=True)
        self.assertEqual({r['type'] for r in records if r['verified']}, {'poster', 'episode_still', 'backdrop'})
        self.assertEqual(len(records), 6)
        self.assertEqual(len(result.findall('icon')), 3)
        for record in records:
            self.assertEqual(record['verified'], 'medium' in record['url'])
            if record['verified']:
                self.assertEqual(record['evidence']['resolution'], 'medium')
                original = record['url'].replace('-medium.png', '.png')
                self.assertEqual(record['evidence']['fallback_from_sha256'],
                                 hashlib.sha256(original.encode('utf-8')).hexdigest())
                self.assertNotIn('fallback_from', record['evidence'])
                if record['type'] == 'backdrop':
                    self.assertEqual(record['evidence']['provider_dimensions'], {'width': 640, 'height': 360})
        self.assertEqual(adapter.summary()['image_attempts'], 6)
        self.requests.clear()
        replay = self.adapter(max_requests=0, max_image_requests=0)
        cached, cached_records = self.enrich(replay, episode=True)
        self.assertEqual(ET.tostring(cached), ET.tostring(result))
        self.assertEqual(cached_records, records)
        self.assertEqual(self.requests, [])
        self.assertEqual(replay.summary()['api_attempts'], 0)
        self.assertEqual(replay.summary()['image_attempts'], 0)
        self.assertTrue(self.state.cached(GALLERY_KEY)[0])

    def test_selection_prefers_main_then_bounded_area_then_lowest_id(self):
        self.routes['/shows/7/images'] = [background(41, main=False, width=4000), background(35, width=2000),
                                       background(32, width=2000), background(30, width=1000)]
        _, records = self.enrich()
        self.assertEqual([r['url'] for r in records], [CDN + 'background-32.png'])

    def test_wrong_type_duplicate_ids_and_unsafe_urls_abstain_affected_images(self):
        wrong = dict(background(1), type='poster')
        duplicate = background(2)
        unsafe = background(3)
        unsafe['resolutions']['original']['url'] = 'https://foreign.invalid/unapproved.png'
        unsafe['resolutions']['medium']['url'] = CDN + 'unapproved.png?token=synthetic'
        self.routes['/shows/7/images'] = [wrong, duplicate, dict(duplicate, type='banner'), unsafe,
                                       background(4, main=False)]
        _, records = self.enrich()
        self.assertEqual([r['url'] for r in records], [CDN + 'background-4.png'])
        self.assertFalse(any('foreign.invalid' in u or '?' in u and 'token=' in u for u in self.requests))

    def test_missing_and_null_images_never_fabricate_resolutions(self):
        self.show['image'] = {'original': CDN + 'poster.png'}
        self.episode['image'] = None
        self.responses[CDN + 'poster.png'] = Response(404, b'', {})
        self.routes['/shows/7/images'] = [None, {}, {'id': True}, {'id': 0},
            dict(background(2), resolutions=None), dict(background(3), resolutions={'original': None}),
            background(4, width=0), background(5, height=3000)]
        result, records = self.enrich(episode=True)
        self.assertIsNone(result.find('icon'))
        self.assertEqual([r['url'] for r in records], [CDN + 'poster.png'])
        self.assertFalse(any('medium' in u for u in self.requests))

    def test_gallery_requires_verified_show_and_episode_binding(self):
        self.routes['/shows/7'] = dict(self.show, name='Different')
        result, records = self.enrich()
        self.assertEqual(records, [])
        self.assertIsNone(result.find('icon'))
        self.assertNotIn('https://api.tvmaze.com/shows/7/images', self.requests)

    def test_invalid_medium_does_not_become_an_additional_typed_record(self):
        self.show['image'] = {'original': CDN + 'poster.png',
                              'medium': 'https://static.tvmaze.com/medium.png?auth=synthetic'}
        self.episode['image'] = {'original': CDN + 'episode.png', 'medium': CDN + 'bad-medium.png'}
        for filename in ('poster.png', 'episode.png', 'background-31.png',
                         'bad-medium.png', 'background-31-medium.png'):
            self.responses[CDN + filename] = Response(404, b'', {})
        result, records = self.enrich(episode=True)
        self.assertIsNone(result.find('icon'))
        self.assertEqual(len(records), 3)
        self.assertFalse(any(r['evidence']['resolution'] == 'medium' for r in records))
        self.assertFalse(any('auth=' in url for url in self.requests))

    def test_unsafe_originals_keep_only_hash_provenance_in_valid_medium_evidence(self):
        self.routes['/shows/7/images'] = []
        originals = ('https://synthetic-user:synthetic-pass@static.tvmaze.com/poster.png',
                     'https://static.tvmaze.com/episode.png?token=synthetic-credential')
        self.show['image'] = {'original': originals[0], 'medium': CDN + 'poster-medium.png'}
        self.episode['image'] = {'original': originals[1], 'medium': CDN + 'episode-medium.png'}
        result, records = self.enrich(episode=True)
        self.assertEqual(len(result.findall('icon')), 2)
        self.assertEqual([r['url'] for r in records if not r['verified']], list(originals))
        for record, original in zip((r for r in records if r['verified']), originals):
            self.assertEqual(record['evidence']['fallback_from_sha256'],
                             hashlib.sha256(original.encode('utf-8')).hexdigest())
            self.assertNotIn('fallback_from', record['evidence'])
        self.assertFalse(any(original in json.dumps(r['evidence'])
                             for original in originals for r in records))
        self.assertFalse(any(original in self.requests for original in originals))

    def test_full_renderer_sanitizes_failed_original_and_preserves_medium_hash(self):
        from xcmate_epg.full_preview import run_full

        original = 'https://synthetic-user:synthetic-pass@static.tvmaze.com/poster.png?token=synthetic-credential'
        self.show['image'] = {'original': original, 'medium': CDN + 'poster-medium.png'}
        self.routes['/shows/7/images'] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.xml'
            tree = ET.Element('tv')
            tree.extend(event(category='series'))
            source.write_bytes(ET.tostring(tree))
            config = {'sources': [{'id': 'synthetic', 'path': str(source)}],
                      'horizon': {'reference_time': '2020-01-01T00:00:00+00:00',
                                  'past_days': 1, 'future_days': 1}}
            report = run_full(config, root / 'output', metadata=self.adapter())
            self.assertEqual(report['full']['programmes'], 1)
            payload = (root / 'output' / 'artwork-metadata.jsonl').read_text()
            self.assertNotIn(original, payload)
            self.assertNotIn('synthetic-pass', payload)
            self.assertNotIn('synthetic-credential', payload)
            records = [json.loads(line) for line in payload.splitlines()]
            self.assertEqual(len(records), 2)
            self.assertNotIn('url', records[0])
            self.assertEqual(records[0]['error'], 'UNSAFE_ARTWORK_REFERENCE')
            self.assertTrue(records[1]['verified'])
            self.assertEqual(records[1]['url'], CDN + 'poster-medium.png')
            self.assertEqual(records[1]['evidence']['fallback_from_sha256'],
                             hashlib.sha256(original.encode('utf-8')).hexdigest())
            self.assertNotIn(original, self.requests)

    def test_transient_poster_and_episode_original_recover_without_metadata_refetch(self):
        self.show['image'] = {'original': CDN + 'poster.png', 'medium': CDN + 'poster-medium.png'}
        self.episode['image'] = {'original': CDN + 'episode.png', 'medium': CDN + 'episode-medium.png'}
        for filename in ('poster.png', 'episode.png'):
            self.responses[CDN + filename] = Response(503, b'', {})
        _, records = self.enrich(episode=True)
        self.assertEqual(len([r for r in records if r['verified']]), 3)
        self.responses.clear()
        adapter = self.adapter(max_requests=0)
        result, records = self.enrich(adapter, episode=True)
        self.assertEqual([r['type'] for r in records], ['poster', 'episode_still', 'backdrop'])
        self.assertEqual(len(result.findall('icon')), 3)
        self.assertTrue(all(r['verified'] for r in records))
        self.assertEqual(adapter.summary()['api_attempts'], 0)

    def test_gallery_metadata_has_positive_and_empty_ttls(self):
        self.enrich()
        row = self.state.db.execute('SELECT expires FROM artwork WHERE key=?', (GALLERY_KEY,)).fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row[0] - time.time(), 30 * 86400, delta=5)
        self.state.db.execute('DELETE FROM artwork WHERE key=?', (GALLERY_KEY,))
        self.routes['/shows/7/images'] = []
        self.enrich()
        row = self.state.db.execute('SELECT expires FROM artwork WHERE key=?', (GALLERY_KEY,)).fetchone()
        self.assertAlmostEqual(row[0] - time.time(), 86400, delta=5)
        self.requests.clear()
        self.enrich(self.adapter(max_requests=0, max_image_requests=0))
        self.assertEqual(self.requests, [])

    def test_gallery_api_budget_exhaustion_does_not_cache_absence(self):
        adapter = self.adapter(max_requests=2)
        _, records = self.enrich(adapter)
        self.assertEqual(records, [])
        self.assertGreater(adapter.summary()['api_budget_exhausted'], 0)
        self.assertFalse(self.state.cached(GALLERY_KEY)[0])
        _, records = self.enrich()
        self.assertTrue(records[0]['verified'])

    def test_image_budget_failure_keeps_metadata_and_recovers(self):
        adapter = self.adapter(max_image_requests=0)
        result, records = self.enrich(adapter)
        self.assertIsNone(result.find('icon'))
        self.assertTrue(records)
        self.assertFalse(any(r['verified'] for r in records))
        self.assertTrue(self.state.cached(GALLERY_KEY)[0])
        self.assertEqual(adapter.summary()['image_attempts'], 0)
        self.assertTrue(self.enrich()[1][0]['verified'])

    def test_transient_gallery_errors_and_images_recover_next_adapter(self):
        for response in (Response(503, b'', {}), Response(401, b'', {}), Response(429, b'', {})):
            with self.subTest(status=response.status):
                self.state.db.execute('DELETE FROM artwork')
                self.routes['/shows/7/images'] = response
                self.assertEqual(self.enrich()[1], [])
                self.assertFalse(self.state.cached(GALLERY_KEY)[0])
                self.routes['/shows/7/images'] = [background()]
                self.responses[CDN + 'background-31.png'] = TimeoutError()
                self.responses[CDN + 'background-31-medium.png'] = Response(503, b'', {})
                _, records = self.enrich()
                self.assertTrue(records)
                self.assertFalse(any(r['verified'] for r in records))
                self.responses.clear()
                self.assertTrue(self.enrich()[1][0]['verified'])

    def test_gallery_bounds_reject_oversized_list_and_body_without_negative_cache(self):
        for payload in ([background()] * 1001, Response(200, b' ' * (8 * 1024 * 1024 + 1), {})):
            self.routes['/shows/7/images'] = payload
            self.assertEqual(self.enrich()[1], [])
            self.assertFalse(self.state.cached(GALLERY_KEY)[0])
        self.routes['/shows/7/images'] = [background()]
        self.assertTrue(self.enrich()[1][0]['verified'])


if __name__ == '__main__':
    unittest.main()
