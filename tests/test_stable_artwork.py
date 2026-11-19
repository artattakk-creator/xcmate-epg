import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from xcmate_epg.full_preview import run_full


class Candidates:
    def __init__(self, records):
        self.records = records

    def verify_media(self, url):
        return {'verified': url.endswith('/manual.png'), 'width': 640, 'height': 360}

    def enrich(self, channel, programme):
        return programme, self.records

    def summary(self):
        return {}


def candidate(provider, role, path, width=640, height=360, verified=True):
    return dict(provider=provider, type=role, url='https://image.tmdb.org/' + path,
                verified=verified, width=width, height=height,
                evidence={'title': 'unicode_normalized_exact'})


class StableArtworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'source.xml'
        self.config = {
            'sources': [{'id': 'fake', 'path': str(self.source)}],
            'horizon': {'reference_time': '2026-11-19T12:00:00+00:00'},
            'artwork': {'stable_selection': True},
            'mapping': {
                'catalog': [{'provider_id': 'fixture', 'name': 'Fixture'}],
                'confirmations': [{'provider_id': 'fixture', 'qualified_id': 'fake:one',
                                   'evidence': 'synthetic confirmation'}],
            },
        }

    def render(self, records, start='120000', description='first', metadata=None):
        self.source.write_text(
            f'<tv><channel id="one"><display-name>Fixture</display-name></channel>'
            f'<programme channel="one" start="20261119{start} +0000" stop="20261119140000 +0000">'
            f'<title>Test Event</title><desc>{description}</desc></programme></tv>')
        output = self.root / 'private'
        report = run_full(self.config, output, metadata=metadata or Candidates(records))
        entry = json.loads((output / 'artwork-selection.jsonl').read_text())
        return output, report, entry

    def test_refresh_restart_and_late_preferred_provider_keep_first_selection(self):
        maze = candidate('tvmaze', 'backdrop', 'maze.png')
        tmdb = candidate('tmdb', 'backdrop', 'tmdb.png')
        output, _, first = self.render([maze])
        self.assertEqual(first['decisions']['backdrop']['selected_provider'], 'tvmaze')
        self.assertEqual(first['device_channels'], ['provider:fixture'])
        _, _, replay = self.render([tmdb, maze], description='changed metadata')
        self.assertEqual(replay, first)
        programme = ET.fromstring(gzip.decompress((output / 'full/xcmate-epg.xml.gz').read_bytes()))
        self.assertEqual([x.get('src') for x in programme.findall('programme/icon')], [maze['url']])
        _, _, changed = self.render([maze, tmdb], start='130000')
        self.assertNotEqual(changed['event_key'], first['event_key'])
        self.assertEqual(changed['decisions']['backdrop']['selected_provider'], 'tmdb')

    def test_role_order_safety_local_fallback_and_coverage(self):
        records = [
            candidate('tmdb', 'backdrop', 'invalid.png', verified=False),
            candidate('tvmaze', 'poster', 'portrait.png', 360, 640),
            candidate('tvmaze', 'episode_still', 'still.png'),
            candidate('tmdb', 'backdrop', 'wrong-format.png', 360, 640),
        ]
        output, report, entry = self.render(records)
        self.assertEqual(entry['decisions']['backdrop']['selected_role'], 'episode_still')
        self.assertEqual(entry['decisions']['poster']['selected_role'], 'poster')
        self.assertTrue((output / entry['offline']['local_ref']).is_file())
        self.assertEqual(report['artwork']['selected_by_provider']['tvmaze'], 3)

    def test_empty_refresh_cannot_erase_a_winner(self):
        _, _, first = self.render([candidate('tmdb', 'poster', 'poster.png', 360, 640)])
        _, _, second = self.render([])
        self.assertEqual(first, second)

    def test_placeholder_is_frozen_and_not_programme_artwork(self):
        _, report, first = self.render([])
        self.assertEqual(first['decisions']['backdrop']['selected_provider'], 'placeholder')
        self.assertEqual(report['artwork']['programmes_with_verified_artwork'], 0)
        _, _, second = self.render([candidate('tmdb', 'backdrop', 'late.png')])
        self.assertEqual(first, second)

    def test_confirmed_manual_image_replaces_only_its_role(self):
        tmdb = candidate('tmdb', 'backdrop', 'winner.png')
        _, _, previous = self.render([tmdb])
        self.config['artwork']['manual_images'] = {
            previous['event_key']: {'backdrop': {
                'url': 'https://image.tmdb.org/manual.png',
                'rights_reviewed': True, 'evidence': 'synthetic manual confirmation',
            }},
        }
        _, _, result = self.render([])
        self.assertEqual(result['decisions']['backdrop']['selected_provider'], 'manual')
        self.assertEqual(result['decisions']['poster'], previous['decisions']['poster'])
        _, _, replay = self.render([])
        self.assertEqual(replay, result)

    def test_confirmed_defect_allows_only_one_automatic_replacement(self):
        tmdb = candidate('tmdb', 'backdrop', 'winner.png')
        xmltv = candidate('feed', 'unclassified_source_image', 'original.png')
        _, _, previous = self.render([tmdb, xmltv])
        self.config['artwork']['confirmed_invalid_images'] = {
            previous['event_key']: {'backdrop': {
                'selected_url_sha256': hashlib.sha256(tmdb['url'].encode()).hexdigest(),
                'evidence': 'synthetic confirmed corrupt response',
            }},
        }
        _, _, replaced = self.render([tmdb, xmltv])
        self.assertEqual(replaced['decisions']['backdrop']['selected_provider'], 'xmltv')
        report = self.config['artwork']['confirmed_invalid_images'][previous['event_key']]['backdrop']
        report['selected_url_sha256'] = hashlib.sha256(xmltv['url'].encode()).hexdigest()
        _, _, replay = self.render([])
        self.assertEqual(replay, replaced)

    def test_canonical_schedule_title_survives_confirmed_lookup_alias(self):
        _, _, previous = self.render([])
        self.config['artwork']['manual_titles'] = {
            previous['event_key']: {'title': 'Confirmed Alias', 'evidence': 'synthetic identity'},
        }

        class AliasLookup(Candidates):
            def enrich(self, channel, programme):
                return programme, self.records if programme.findtext('title') == 'Confirmed Alias' else []

        output, _, result = self.render([], metadata=AliasLookup([candidate('tmdb', 'backdrop', 'alias.png')]))
        self.assertEqual(result['event_key'], previous['event_key'])
        self.assertEqual(result['decisions']['backdrop']['reason'], 'manual_title_match')
        xml = gzip.decompress((output / 'full/xcmate-epg.xml.gz').read_bytes())
        self.assertEqual(ET.fromstring(xml).findtext('programme/title'), 'Test Event')

    def test_verified_local_logo_requires_confirmed_channel_binding(self):
        from image_fixtures import png
        data = png(64, 64)
        sha = hashlib.sha256(data).hexdigest()
        (self.root / (sha + '.png')).write_bytes(data)
        inventory = self.root / 'logos.json'
        inventory.write_text(json.dumps({
            'records': [{'cache_key': 'test', 'sha256': sha, 'width': 64, 'height': 64}],
            'provider_bindings': [{'cache_key': 'test', 'provider_id': 'fixture',
                                   'sha256': sha, 'evidence': 'synthetic binding'}],
        }))
        self.config['provider_logos'] = {'inventory_path': str(inventory), 'files_root': str(self.root)}
        output, report, result = self.render([])
        choice = result['decisions']['backdrop']
        self.assertEqual(choice['selected_provider'], 'logo')
        self.assertEqual((output / choice['local_ref']).read_bytes(), data)
        self.assertEqual(report['artwork']['programmes_with_verified_artwork'], 0)
        self.config['mapping']['confirmations'] = []
        _, _, revoked = self.render([])
        self.assertEqual(revoked['decisions']['backdrop']['reason'], 'logo_no_longer_confirmed')
        _, _, unmapped = self.render([], start='130000')
        self.assertEqual(unmapped['decisions']['backdrop']['selected_provider'], 'placeholder')

    def test_failed_publish_cannot_freeze_an_unpublished_candidate(self):
        with patch('xcmate_epg.full_preview.publish_run', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.render([candidate('tvmaze', 'backdrop', 'never-published.png')])
        _, _, result = self.render([candidate('tmdb', 'backdrop', 'published.png')])
        self.assertEqual(result['decisions']['backdrop']['selected_provider'], 'tmdb')

    def test_real_enricher_uses_tvmaze_when_tmdb_identity_has_no_image(self):
        from image_fixtures import png
        from test_live_gallery import background, CDN
        from test_tmdb_integration import TMDBFixture
        from xcmate_epg.artwork import TMDB
        from xcmate_epg.fetch import Response
        from xcmate_epg.live_artwork import ArtworkEnricher
        from xcmate_epg.state import State

        fixture = TMDBFixture()
        fixture.show.update(poster_path=None, backdrop_path=None)
        show = {'id': 7, 'name': 'Example', 'premiered': '2010-02-03'}

        def transport(url, headers):
            parts = urlsplit(url)
            if parts.hostname == 'api.themoviedb.org':
                return fixture(url, headers)
            if parts.hostname == 'static.tvmaze.com':
                self.assertEqual(headers, {})
                return Response(200, png(640, 360), {'Content-Type': 'image/png'})
            if parts.hostname == 'api.tvmaze.com':
                routes = {'/search/shows': [{'show': show}], '/shows/7': show,
                          '/shows/7/images': [background()]}
                return Response(200, json.dumps(routes[parts.path]).encode(), {})
            self.fail('unexpected synthetic endpoint')

        self.source.write_text(
            '<tv><channel id="one"><display-name>Fixture</display-name></channel>'
            '<programme channel="one" start="20261119120000 +0000" stop="20261119140000 +0000">'
            '<title>Example</title><date>2010</date><category>Series</category></programme></tv>')
        output = self.root / 'private'
        with State(self.root / 'fixture-cache.sqlite') as state:
            adapter = ArtworkEnricher(state, {'request_interval': 0, 'authorize_tvmaze': True,
                                      'tvmaze_gallery': True}, tmdb=TMDB(managed_http=True),
                                      transport=transport)
            run_full(self.config, output, metadata=adapter)
            entry = json.loads((output / 'artwork-selection.jsonl').read_text())
            self.assertEqual(entry['decisions']['backdrop']['selected_url'], CDN + 'background-31.png')
            self.assertEqual(adapter.summary()['image_attempts'], 1)
            receipts = (output / 'artwork-metadata.jsonl').read_bytes()
            state.db.execute('DELETE FROM artwork')
            state.db.commit()
            run_full(self.config, output, metadata=adapter)
            self.assertEqual(adapter.summary()['image_attempts'], 1)
            self.assertEqual(json.loads((output / 'artwork-selection.jsonl').read_text()), entry)
            self.assertEqual((output / 'artwork-metadata.jsonl').read_bytes(), receipts)

    def test_duplicate_schedule_variants_share_one_exported_decision(self):
        self.source.write_text(
            '<tv><channel id="one"><display-name>Fixture</display-name></channel>'
            '<programme channel="one" start="20261119120000 +0000" stop="20261119140000 +0000">'
            '<title>Test Event</title><desc>first</desc></programme>'
            '<programme channel="one" start="20261119120000 +0000" stop="20261119140000 +0000">'
            '<title>Test Event</title><desc>second</desc></programme></tv>')

        class VariantLookup(Candidates):
            def enrich(self, channel, programme):
                path = programme.findtext('desc') + '.png'
                return programme, [candidate('tmdb', 'backdrop', path)]

        output = self.root / 'private'
        run_full(self.config, output, metadata=VariantLookup([]))
        choice = json.loads((output / 'artwork-selection.jsonl').read_text())['decisions']['backdrop']
        xml = ET.fromstring(gzip.decompress((output / 'full/xcmate-epg.xml.gz').read_bytes()))
        programmes = xml.findall('programme')
        self.assertEqual(len(programmes), 2)
        self.assertEqual([p.find('icon').get('src') for p in programmes], [choice['selected_url']] * 2)

    def test_invalid_manual_reference_stays_quarantined_on_replay(self):
        _, _, first = self.render([])
        url = 'https://image.tmdb.org/manual.png'
        key = first['event_key']
        self.config['artwork']['manual_images'] = {key: {'backdrop': {
            'url': url, 'rights_reviewed': True, 'evidence': 'synthetic manual confirmation',
        }}}
        _, _, manual = self.render([])
        self.assertEqual(manual['decisions']['backdrop']['selected_provider'], 'manual')
        self.config['artwork']['confirmed_invalid_images'] = {key: {'backdrop': {
            'selected_url_sha256': hashlib.sha256(url.encode()).hexdigest(),
            'evidence': 'synthetic confirmed corrupt file',
        }}}
        _, _, replacement = self.render([candidate('tvmaze', 'backdrop', 'safe.png')])
        self.assertEqual(replacement['decisions']['backdrop']['selected_provider'], 'tvmaze')
        _, _, replay = self.render([])
        self.assertEqual(replay, replacement)
