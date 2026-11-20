import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from xcmate_epg import github_delivery, private_delivery
from xcmate_epg.full_export import read_run_manifest
from xcmate_epg.full_preview import run_full
from test_private_delivery import SOURCE, LOGO, VerifiedFixture


class GitHubDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / 'work'
        self.remote = self.base / 'remote.git'
        self.dates = {'reference_time': '2026-11-19T12:00:00+00:00'}
        self.git(self.base, 'init', '--bare', '--initial-branch=main', str(self.remote))
        seed = self.base / 'seed'
        seed.mkdir()
        self.git(seed, 'init', '--initial-branch=main')
        (seed / 'README.md').write_text('Preserved private repository file')
        self.pending = {'schema_version': 1, 'file': 'xcmate-epg.xml.gz', 'status': 'PRIVATE_PENDING'}
        (seed / 'manifest.json').write_text(json.dumps(self.pending))
        self.git(seed, 'add', '.')
        self.git(seed, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Seed')
        self.git(seed, 'push', str(self.remote), 'main')
        self.git_seed = self.git(self.remote, 'rev-parse', 'main').decode().strip()
        self.sources = []
        for index in range(3):
            path = self.base / f'feed-{index}.xml'
            path.write_text(SOURCE)
            self.sources.append({'id': f's{index}', 'path': str(path)})
        config = {'sources': self.sources, 'horizon': self.dates,
                  'artwork': {'stable_selection': True, 'confirmed_source_logos': [{
                      'qualified_id': 's1:one', 'url': LOGO, 'rights_reviewed': True,
                      'evidence': 'fixture', 'attribution': 'TMDB'}]},
                  'mapping': {'catalog_complete': True, 'catalog': [{'provider_id': 'fixture', 'name': 'Fixture'}],
                              'confirmations': [{'provider_id': 'fixture', 'qualified_id': 's1:one', 'evidence': 'test'}]}}
        run_full(config, self.root, metadata=VerifiedFixture())
        self.approval = {'generation': read_run_manifest(self.root)['generation'],
                         'gates': {name:'synthetic test' for name in private_delivery.GATES}}
        self.reads = []

    def git(self, directory, *args):
        return subprocess.check_output(['git', '-C', str(directory), *args], stderr=subprocess.DEVNULL)

    def readback(self, commit, path):
        self.reads.append((commit, path))
        if path == 'xcmate-epg.xml.gz' and len(self.reads) == 1:
            self.assertEqual(self.git(self.remote, 'rev-parse', 'main').decode().strip(), self.git_seed)
        return io.BytesIO(self.git(self.remote, 'show', f'{commit}:{path}'))

    def publish(self, approval):
        if not os.environ.get('XCMATE_XMLTV_DTD'):
            self.skipTest('Pinned offline XMLTV DTD required')
        return github_delivery.publish(self.root, approval, _remote=str(self.remote), _read=self.readback)

    def test_readback_happens_before_manifest_and_keeps_other_files(self):
        manifest = self.publish(self.approval)
        self.assertEqual(manifest['status'], 'PRIVATE_READY')
        self.assertEqual(json.loads(self.git(self.remote, 'show', 'main:manifest.json'))['revision'], manifest['sha256'])
        self.assertEqual(self.git(self.remote, 'show', 'main:README.md'), b'Preserved private repository file')
        self.assertEqual(self.git(self.remote, 'rev-list', '--count', manifest['payload_ref']).strip(), b'1')
        self.assertEqual(self.git(self.remote, 'ls-tree', '--name-only', manifest['payload_ref']).strip(), b'xcmate-epg.xml.gz')
        self.assertEqual(self.reads[0], (manifest['payload_ref'], 'xcmate-epg.xml.gz'))
        self.assertFalse(self.publish(self.approval)['changed'])
        self.assertFalse(manifest['staging_retained'])
        self.assertFalse(self.git(self.remote, 'for-each-ref', 'refs/heads/xcmate-manifest-stage'))

    def test_checksum_mismatch_preserves_pending_manifest(self):
        self.readback = lambda *args: io.BytesIO(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'REMOTE_PAYLOAD_READBACK_MISMATCH'):
            self.publish(self.approval)
        self.assertEqual(json.loads(self.git(self.remote, 'show', 'main:manifest.json')), self.pending)

    def test_missing_approval_performs_no_remote_read_or_write(self):
        self.assertEqual(self.publish(None)['status'], 'PRIVATE_PENDING')
        self.assertFalse(self.reads)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main').decode().strip(), self.git_seed)

    def test_unreadable_staging_manifest_never_advances_main(self):
        readback = self.readback
        self.readback = lambda commit, path: io.BytesIO(b'corrupt') if path == 'manifest.json' else readback(commit, path)
        with self.assertRaisesRegex(ValueError, 'REMOTE_MANIFEST_READBACK_MISMATCH'):
            self.publish(self.approval)
        self.assertEqual(json.loads(self.git(self.remote, 'show', 'main:manifest.json')), self.pending)

    def test_modified_local_archive_never_reaches_remote(self):
        stage = github_delivery._Repository.commit_payload

        def modify(repo, ref, archive, manifest):
            archive.write_bytes(b'corrupted')
            return stage(repo, ref, archive, manifest)

        with patch.object(github_delivery._Repository, 'commit_payload', modify):
            with self.assertRaisesRegex(ValueError, 'LOCAL_PAYLOAD_CHANGED_BEFORE_PUSH'):
                self.publish(self.approval)
        self.assertFalse(self.git(self.remote, 'for-each-ref', 'refs/heads/xcmate-payload'))
        self.assertEqual(json.loads(self.git(self.remote, 'show', 'main:manifest.json')), self.pending)

    def test_cdn_redirect_drops_authorization_and_refuses_other_hosts(self):
        handler = github_delivery._DownloadRedirect()
        initial = Request('https://api.github.com/', headers={'Authorization': 'fixture'})
        redirected = handler.redirect_request(
            initial, None, 302, '', {}, 'https://objects.githubusercontent.com/fixture')
        self.assertIsNone(redirected.get_header('Authorization'))
        with self.assertRaisesRegex(ValueError, 'PRIVATE_GITHUB_UNTRUSTED_REDIRECT'):
            handler.redirect_request(initial, None, 302, '', {}, 'https://example.invalid/fixture')
