from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from xcmate_epg import daily, runner_state
from xcmate_epg.full_export import read_run_manifest
from xcmate_epg.full_fetch import SnapshotCache
from test_full_fetch import Reply
from test_private_delivery import SOURCE, VerifiedFixture


class RunnerStateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / 'workspace'
        self.store = self.base / 'persistent'
        self.store.mkdir(mode=0o700)
        self.now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        self.config = {'sources': [], 'horizon': {'reference_time': self.now.isoformat()}}
        self.rights = {}
        for index in range(3):
            path = self.base / f'source-{index}.xml'
            path.write_text(SOURCE)
            identifier = f's{index}'
            self.config['sources'].append({'id': identifier, 'path': str(path)})
            self.rights[identifier] = {'input_sha256': hashlib.sha256(str(path).encode()).hexdigest(),
                                       'evidence': 'local synthetic fixture'}

    def cycle(self, days):
        if days:
            path = Path(self.config['sources'][0]['path'])
            path.write_text(SOURCE.replace('Test Event', f'Test Event {days}'))
        result = daily.run(self.config, self.root, self.rights, now=self.now + timedelta(days=days),
                           metadata=VerifiedFixture())
        self.assertEqual(result['cycle_status'], 'VALIDATED')
        return read_run_manifest(self.root)['generation']

    def test_restart_from_verified_snapshot(self):
        generation = self.cycle(0)
        snapshot = runner_state.save(self.root, self.store, now=self.now)
        cold = self.base / 'fresh-runner'
        self.assertEqual(runner_state.restore(self.store, cold), snapshot)
        self.assertEqual(read_run_manifest(cold)['generation'], generation)
        self.assertEqual((cold / 'daily-state.json').read_bytes(), (self.root / 'daily-state.json').read_bytes())
        self.assertEqual((cold / 'full/manifest.json').read_bytes(), (self.root / 'full/manifest.json').read_bytes())
        for filename in ('stream.sqlite', 'artwork.sqlite'):
            with sqlite3.connect((cold / filename).as_uri() + '?mode=ro', uri=True) as database:
                self.assertEqual(database.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
        result = daily.run(self.config, cold, self.rights, now=self.now + timedelta(hours=23),
                           metadata=VerifiedFixture())
        self.assertEqual(result['cycle_status'], 'NOT_DUE')

    def test_rotation_keeps_current_and_previous(self):
        identities = []
        for days in (0, 1, 9):
            self.cycle(days)
            identities.append(runner_state.save(self.root, self.store, now=self.now + timedelta(days=days)))
        first, second, third = [(self.store / 'snapshots' / key) for key in identities]
        self.assertEqual(runner_state.prune(self.store, now=self.now + timedelta(days=9)), 1)
        self.assertFalse(first.exists())
        self.assertTrue(second.is_dir())
        self.assertTrue(third.is_dir())
        self.assertEqual(runner_state.restore(self.store, self.base / 'fallback', previous=True), identities[1])

    def test_current_corruption_blocks_prune_and_preserves_last_good(self):
        self.cycle(0)
        previous = runner_state.save(self.root, self.store, now=self.now)
        self.cycle(1)
        current = runner_state.save(self.root, self.store, now=self.now + timedelta(days=1))
        (self.store / 'snapshots' / current / 'state' / 'daily-state.json').write_text('{}')
        cold = self.base / 'fresh-runner'
        with self.assertRaisesRegex(ValueError, 'STATE_HASH_MISMATCH'):
            runner_state.restore(self.store, cold)
        self.assertFalse(cold.exists())
        with self.assertRaisesRegex(ValueError, 'STATE_HASH_MISMATCH'):
            runner_state.prune(self.store, now=self.now + timedelta(days=15))
        self.assertEqual(runner_state.restore(self.store, cold, previous=True), previous)
        with self.assertRaisesRegex(ValueError, 'STATE_RESTORE_TARGET_EXISTS'):
            runner_state.restore(self.store, cold, previous=True)

    def test_bad_database_does_not_publish_snapshot(self):
        self.cycle(0)
        runner_state.save(self.root, self.store, now=self.now)
        pointer = (self.store / 'latest.json').read_bytes()
        (self.root / 'stream.sqlite').write_bytes(b'broken SQLite')
        with self.assertRaisesRegex(ValueError, 'STATE_SQLITE_INTEGRITY'):
            runner_state.save(self.root, self.store, now=self.now + timedelta(days=1))
        self.assertEqual((self.store / 'latest.json').read_bytes(), pointer)

    def test_failed_daily_attempt_does_not_advance_snapshot_pointers(self):
        self.cycle(0)
        runner_state.save(self.root, self.store, now=self.now)
        pointer = (self.store / 'latest.json').read_bytes()
        state = self.root / 'daily-state.json'
        value = json.loads(state.read_text())
        state.write_text(json.dumps(dict(value, failures=1)))
        with self.assertRaisesRegex(ValueError, 'STATE_LAST_GOOD_REQUIRED'):
            runner_state.save(self.root, self.store, now=self.now + timedelta(days=1))
        self.assertEqual((self.store / 'latest.json').read_bytes(), pointer)

    def test_prune_refuses_unrelated_files_in_expired_snapshot(self):
        identities = []
        for days in (0, 1, 9):
            self.cycle(days)
            identities.append(runner_state.save(self.root, self.store, now=self.now + timedelta(days=days)))
        unrelated = self.store / 'snapshots' / identities[0] / 'operator-file'
        unrelated.write_text('preserve')
        with self.assertRaisesRegex(ValueError, 'STATE_INVALID_SNAPSHOT_LAYOUT'):
            runner_state.prune(self.store, now=self.now + timedelta(days=9))
        self.assertEqual(unrelated.read_text(), 'preserve')

    def test_restore_never_overwrites_a_target_created_during_install(self):
        self.cycle(0)
        runner_state.save(self.root, self.store, now=self.now)
        cold = self.base / 'fresh-runner'
        install = runner_state._install_restore

        def race(candidate, target):
            target.mkdir()
            return install(candidate, target)

        with patch.object(runner_state, '_install_restore', race):
            with self.assertRaisesRegex(ValueError, 'STATE_RESTORE_TARGET_EXISTS'):
                runner_state.restore(self.store, cold)
        self.assertEqual(list(cold.iterdir()), [])

    def test_repo_trackable_state_paths_are_rejected(self):
        self.cycle(0)
        runner_state.save(self.root, self.store, now=self.now)
        public = self.base / 'tracking'
        public.mkdir(mode=0o700)
        subprocess.run(['git', 'init', str(public)], check=True, capture_output=True)
        unsafe = public / 'unsafe'
        unsafe.mkdir(mode=0o700)
        with self.assertRaisesRegex(ValueError, 'UNSAFE_PRIVATE_ROOT'):
            runner_state.save(self.root, unsafe, now=self.now)
        with self.assertRaisesRegex(ValueError, 'UNSAFE_PRIVATE_ROOT'):
            runner_state.restore(self.store, public / 'new')

    def test_fresh_runner_reuses_http_validators(self):
        for source in self.config['sources']:
            url = f"https://fixture.invalid/{source['id']}.xml"
            del source['path']
            source['url'] = url
            self.rights[source['id']]['input_sha256'] = hashlib.sha256(url.encode()).hexdigest()
        calls = []

        def fixture(request, timeout):
            validator = request.get_header('If-none-match')
            calls.append(validator)
            if validator:
                raise HTTPError(request.full_url, 304, '', {}, None)
            return Reply(SOURCE.encode())

        def cache(*args, **kwargs):
            return SnapshotCache(*args, **kwargs, opener=fixture)

        with patch('xcmate_epg.full_preview.SnapshotCache', cache):
            result = daily.run(self.config, self.root, self.rights, now=self.now,
                               metadata=VerifiedFixture(), authorize_network=True)
            self.assertEqual(result['cycle_status'], 'VALIDATED')
            runner_state.save(self.root, self.store, now=self.now)
            cold = self.base / 'fresh-runner'
            runner_state.restore(self.store, cold)
            result = daily.run(self.config, cold, self.rights, now=self.now + timedelta(days=1),
                               metadata=VerifiedFixture(), authorize_network=True)
        self.assertEqual(calls, [None] * 3 + ['generation-1'] * 3)
        self.assertEqual([row['http_status'] for row in result['report']['sources']], [304] * 3)
