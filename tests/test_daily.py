from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from xcmate_epg import daily
from xcmate_epg.full_fetch import SnapshotCache
from test_full_fetch import Reply
from test_private_delivery import SOURCE, VerifiedFixture


class DailyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'private'
        self.now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        urls = [f'https://feed.example.invalid/{i}.xml' for i in range(3)]
        self.config = {'sources': [{'id': f's{i}', 'url': url} for i, url in enumerate(urls)],
                       'artwork': {'stable_selection': True}}
        self.permissions = {f's{i}': {
            'input_sha256': hashlib.sha256(url.encode()).hexdigest(), 'evidence': 'synthetic review',
        } for i, url in enumerate(urls)}
        self.calls = []
        self.failure = None
        self.changing = False

    def open_fixture(self, request, timeout):
        url = request.full_url
        self.calls.append((url, request.get_header('If-none-match')))
        if self.failure and '/0.xml' in url:
            raise HTTPError(url, self.failure, '', {}, None)
        if request.get_header('If-none-match') and not (self.changing and '/0.xml' in url):
            raise HTTPError(url, 304, '', {}, None)
        text = SOURCE
        if self.changing and '/0.xml' in url:
            text = SOURCE.replace('Test Event', 'Updated Event')
        return Reply(text.encode())

    def cycle(self, after=0, **options):
        options.setdefault('authorize_network', True)
        options.setdefault('metadata', VerifiedFixture())
        return daily.run(self.config, self.root, self.permissions,
                         now=self.now+timedelta(seconds=after), **options)

    def test_permission_is_checked_for_all_sources_before_transport(self):
        self.config['sources'][2]['url'] += '/changed'
        with patch.object(SnapshotCache, 'fetch', side_effect=AssertionError('network reached')):
            with self.assertRaisesRegex(ValueError, 'SOURCE_RIGHTS_NOT_APPROVED'):
                self.cycle()
        self.assertFalse(self.root.exists())

    def test_unauthorized_source_on_first_run_has_no_last_good(self):
        cache = SnapshotCache
        def fixture_cache(*args, **kwargs):
            return cache(*args, opener=self.open_fixture, **kwargs)
        for status in (401, 403):
            with self.subTest(status=status), patch('xcmate_epg.full_preview.SnapshotCache', fixture_cache):
                self.root = self.root.parent / f'first-http-{status}'
                self.failure = status
                result = self.cycle()
                self.assertEqual(result['cycle_status'], 'SOURCE_BLOCKED')
                self.assertEqual(result['report']['sources'][0]['http_status'], status)
                self.assertFalse((self.root / 'run-manifest.json').exists())

    def test_daily_restart_304_then_one_source_delta(self):
        cache = SnapshotCache
        def fixture_cache(*args, **kwargs):
            return cache(*args, opener=self.open_fixture, **kwargs)
        with patch('xcmate_epg.full_preview.SnapshotCache', fixture_cache):
            first = self.cycle()
            self.assertEqual(first['cycle_status'], 'VALIDATED')
            self.assertEqual(first['report']['full']['programmes'], 3)
            self.assertEqual(self.cycle(86399)['cycle_status'], 'NOT_DUE')
            self.assertEqual(len(self.calls), 3)
            unchanged = self.cycle(86400)
            self.assertEqual([row['http_status'] for row in unchanged['report']['sources']], [304, 304, 304])
            self.assertEqual(unchanged['report']['full']['sha256'], first['report']['full']['sha256'])
            self.changing = True
            updated = self.cycle(172800)
        self.assertEqual([row['cache_status'] for row in updated['report']['sources']],
                         ['changed', 'unchanged', 'unchanged'])
        self.assertNotEqual(updated['report']['full']['sha256'], first['report']['full']['sha256'])

    def test_only_changed_event_enters_expensive_enrichment(self):
        processed = []

        class CountedFixture(VerifiedFixture):
            def enrich(self, channel, event):
                processed.append(event.get('channel'))
                return super().enrich(channel, event)

        cache = SnapshotCache
        def fixture_cache(*args, **kwargs):
            return cache(*args, opener=self.open_fixture, **kwargs)
        with patch('xcmate_epg.full_preview.SnapshotCache', fixture_cache):
            first = self.cycle(metadata=CountedFixture())
            self.assertEqual(processed, ['s0:one', 's1:one', 's2:one'])
            processed.clear()
            second = self.cycle(86400, metadata=CountedFixture())
            self.assertEqual(processed, [])
            self.assertEqual(second['report']['full']['sha256'], first['report']['full']['sha256'])
            self.changing = True
            third = self.cycle(172800, metadata=CountedFixture())
            self.assertEqual(processed, ['s0:one'])
        original_source, updated_source = first['report']['sources'][0], third['report']['sources'][0]
        self.assertNotEqual(original_source['sha256'], updated_source['sha256'])
        self.assertEqual(original_source['channel_fingerprint'], updated_source['channel_fingerprint'])
        self.assertEqual(original_source['time_fingerprint'], updated_source['time_fingerprint'])

    def test_daily_placeholder_can_upgrade_when_a_verified_image_arrives(self):
        class NoPictures(VerifiedFixture):
            def enrich(self, channel, event):
                return event, []

        cache = SnapshotCache
        def fixture_cache(*args, **kwargs):
            return cache(*args, opener=self.open_fixture, **kwargs)
        with patch('xcmate_epg.full_preview.SnapshotCache', fixture_cache):
            first = self.cycle(metadata=NoPictures())
            second = self.cycle(86400)
        self.assertEqual(first['report']['artwork']['programmes_with_verified_artwork'], 0)
        self.assertEqual(second['report']['artwork']['programmes_with_verified_artwork'], 3)

    def test_source_failure_keeps_last_good_and_backoff_survives_restart(self):
        cache = SnapshotCache
        def fixture_cache(*args, **kwargs):
            return cache(*args, opener=self.open_fixture, **kwargs)
        with patch('xcmate_epg.full_preview.SnapshotCache', fixture_cache):
            self.cycle()
            manifest = self.root / 'run-manifest.json'
            before = manifest.read_bytes()
            self.failure = 429
            failed = self.cycle(86400)
            self.assertEqual(failed['cycle_status'], 'SOURCE_BLOCKED')
            self.assertEqual(failed['report']['sources'][0]['http_status'], 429)
            self.assertEqual(len(self.calls), 6)
            self.assertEqual(self.cycle(86500)['cycle_status'], 'NOT_DUE')
            self.assertEqual(len(self.calls), 6)
            self.assertEqual(manifest.read_bytes(), before)
            self.failure = None
            self.assertEqual(self.cycle(86700)['cycle_status'], 'VALIDATED')

    def test_offline_replay_uses_verified_snapshots_without_changing_live_manifest(self):
        cache = SnapshotCache
        def fixture_cache(*args, **kwargs):
            return cache(*args, opener=self.open_fixture, **kwargs)
        with patch('xcmate_epg.full_preview.SnapshotCache', fixture_cache):
            initial = self.cycle()
            before = (self.root / 'run-manifest.json').read_bytes()
            checkpoint = (self.root / 'daily-state.json').read_bytes()
            replay = self.cycle(120, replay=True, authorize_network=False)
        self.assertEqual(replay['cycle_status'], 'OFFLINE_REPLAY')
        self.assertEqual(replay['report']['full']['sha256'], initial['report']['full']['sha256'])
        self.assertEqual(len(self.calls), 3)
        self.assertEqual((self.root / 'run-manifest.json').read_bytes(), before)
        self.assertEqual((self.root / 'daily-state.json').read_bytes(), checkpoint)
