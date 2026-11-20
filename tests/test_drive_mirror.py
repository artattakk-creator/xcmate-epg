from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from xcmate_epg import drive_mirror, runner_state


class FakeDrive:
    def __init__(self):
        self.folder = 'fixture-folder'
        self.rows = {}
        self.media = {}
        self.uploads = 0
        self.operations = []
        self.fail_upload = False
        self.corrupt_pending = False
        self.block_pointer = False
        self.now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
        self.rows[self.folder] = {
            'id': self.folder, 'name': 'XCMate EPG Live-Mirror', 'mimeType': 'application/vnd.google-apps.folder',
            'shared': False, 'ownedByMe': True, 'trashed': False,
        }

    def add(self, metadata, data):
        file_id = f'fixture-{len(self.rows)}'
        row = dict(metadata, id=file_id, shared=False, ownedByMe=True, trashed=False, size=str(len(data)),
                   createdTime=self.now.isoformat(), mimeType='application/gzip')
        self.rows[file_id] = row
        self.media[file_id] = data
        return dict(row)

    def metadata(self, file_id):
        return dict(self.rows[file_id])

    def owner(self, file_id):
        return 'fixture-owner'

    def children(self, folder):
        return [dict(row) for row in self.rows.values()
                if folder in row.get('parents', []) and not row['trashed']]

    def download(self, file_id):
        if self.corrupt_pending and self.rows[file_id]['name'].startswith('.incoming-'):
            return io.BytesIO(b'corrupted')
        return io.BytesIO(self.media[file_id])

    def upload(self, metadata, payload):
        if self.fail_upload:
            raise ValueError('DRIVE_RESUMABLE_HTTP_403')
        self.uploads += 1
        self.operations.append(('upload',))
        return self.add(metadata, payload)

    def patch(self, file_id, metadata):
        self.operations.append(('patch', file_id, metadata))
        self.rows[file_id].update(metadata)

    def write_pointer(self, metadata, data, file_id):
        if self.block_pointer:
            raise ValueError('DRIVE_POINTER_HTTP_429')
        self.operations.append(('pointer', file_id))
        if file_id is None:
            return self.add(metadata, data)
        self.media[file_id] = data
        self.rows[file_id]['size'] = str(len(data))
        return self.metadata(file_id)


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.drive = FakeDrive()
        self.base = tempfile.TemporaryDirectory()
        self.addCleanup(self.base.cleanup)
        self.archive = Path(self.base.name) / 'xcmate-epg.xml.gz'

    def generation(self, title):
        payload = gzip.compress(f'<tv><channel id="a"><display-name>A</display-name></channel>'
                                f'<programme channel="a" start="20261120120000 +0000" '
                                f'stop="20261120130000 +0000"><title>{title}</title></programme></tv>'.encode(), mtime=0)
        self.archive.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        return {'status': 'PRIVATE_READY', 'schema_version': 3, 'file': self.archive.name,
                'revision': digest, 'sha256': digest, 'bytes': len(payload),
                'generated_at_utc': self.drive.now.isoformat(), 'payload_ref': 'a' * 40}

    def sync(self, manifest):
        return drive_mirror.sync(self.drive, self.drive.folder, self.archive, manifest, 'b' * 64,
                                 lock_store=self.base.name, now=self.drive.now)

    def test_two_rotations_keep_a_single_live_file_with_matching_hash(self):
        first = self.sync(self.generation('First'))
        second_manifest = self.generation('Second')
        second = self.sync(second_manifest)
        self.assertEqual(second['drive_sha256'], second_manifest['sha256'])
        self.assertIn(first['drive_file_id'], second['removed_file_ids'])
        self.assertTrue(self.drive.rows[first['drive_file_id']]['trashed'])
        self.assertEqual([row['name'] for row in self.drive.children(self.drive.folder)].count(self.archive.name), 1)
        self.assertLess(next(i for i, row in enumerate(self.drive.operations) if row[0] == 'pointer' and row[1]),
                        next(i for i, row in enumerate(self.drive.operations) if row[0] == 'patch' and row[2].get('trashed')))
        self.assertEqual(hashlib.sha256(self.drive.media[second['drive_file_id']]).hexdigest(), second_manifest['sha256'])

    def test_same_hash_does_not_reupload_or_rename(self):
        manifest = self.generation('Unchanged')
        first = self.sync(manifest)
        before = self.drive.operations[:]
        retry = self.sync(manifest)
        self.assertEqual(first['drive_file_id'], retry['drive_file_id'])
        self.assertFalse(retry['changed'])
        self.assertEqual(self.drive.uploads, 1)
        self.assertEqual(before, self.drive.operations)

    def test_upload_or_corrupt_readback_preserves_last_good(self):
        first = self.sync(self.generation('First'))
        next_manifest = self.generation('Second')
        self.drive.fail_upload = True
        with self.assertRaisesRegex(ValueError, 'DRIVE_RESUMABLE_HTTP_403'):
            self.sync(next_manifest)
        self.drive.fail_upload = False
        self.drive.corrupt_pending = True
        with self.assertRaisesRegex(ValueError, 'DRIVE_MEDIA_MISMATCH'):
            self.sync(next_manifest)
        row = self.drive.rows[first['drive_file_id']]
        self.assertFalse(row['trashed'])
        self.assertEqual(row['name'], self.archive.name)

    def test_interrupted_pointer_write_recovers_without_second_upload(self):
        first = self.sync(self.generation('First'))
        manifest = self.generation('Second')
        self.drive.block_pointer = True
        with self.assertRaisesRegex(ValueError, 'DRIVE_POINTER_HTTP_429'):
            self.sync(manifest)
        self.assertFalse(self.drive.rows[first['drive_file_id']]['trashed'])
        self.drive.block_pointer = False
        self.assertEqual(self.sync(manifest)['drive_sha256'], manifest['sha256'])
        self.assertEqual(self.drive.uploads, 2)

    def test_same_hash_recovers_the_old_live_name_after_failed_new_pointer(self):
        first_manifest = self.generation('First')
        first = self.sync(first_manifest)
        self.drive.block_pointer = True
        with self.assertRaisesRegex(ValueError, 'DRIVE_POINTER_HTTP_429'):
            self.sync(self.generation('Second'))
        self.generation('First')
        retry = self.sync(first_manifest)
        self.assertFalse(retry['changed'])
        self.assertEqual(retry['drive_file_id'], first['drive_file_id'])
        self.assertEqual(self.drive.rows[first['drive_file_id']]['name'], self.archive.name)
        self.assertEqual(self.drive.uploads, 2)

    def test_shared_target_blocks_all_writes(self):
        manifest = self.generation('Current')
        self.drive.rows[self.drive.folder]['shared'] = True
        with self.assertRaisesRegex(ValueError, 'DRIVE_OWNER_ONLY_REQUIRED'):
            self.sync(manifest)
        self.assertFalse(self.drive.operations)

    def test_same_folder_rotation_is_serialized_before_any_remote_write(self):
        manifest = self.generation('Current')
        lock_id = hashlib.sha256(self.drive.folder.encode()).hexdigest()
        with runner_state._lock(Path(self.base.name), f'.drive-mirror-{lock_id}.lock'):
            with self.assertRaisesRegex(ValueError, 'STATE_BUSY'):
                self.sync(manifest)
        self.assertFalse(self.drive.operations)

    def test_invalid_gzip_verified_hash_still_cannot_replace_last_good(self):
        first = self.sync(self.generation('First'))
        manifest = self.generation('Invalid')
        invalid = b'not a gzip file'
        self.archive.write_bytes(invalid)
        digest = hashlib.sha256(invalid).hexdigest()
        with self.assertRaisesRegex(ValueError, 'DRIVE_XMLTV_READBACK_FAILED'):
            self.sync(dict(manifest, bytes=len(invalid), revision=digest, sha256=digest))
        self.assertFalse(self.drive.rows[first['drive_file_id']]['trashed'])
        self.assertEqual(self.drive.rows[first['drive_file_id']]['name'], self.archive.name)

    def test_unknown_uploads_and_other_folders_are_preserved(self):
        manifest = self.generation('Current')
        self.sync(manifest)
        unknown = self.drive.add({'name': '.incoming-' + 'd' * 64 + '.xml.gz',
                                  'parents': [self.drive.folder]}, b'operator-backup')
        external = self.drive.add({'name': '.incoming-' + 'e' * 64 + '.xml.gz',
                                   'parents': ['not-the-mirror']}, b'other-folder')
        stale = self.drive.add({'name': '.incoming-' + 'f' * 64 + '.xml.gz', 'parents': [self.drive.folder],
                                'appProperties': drive_mirror.payload_properties('f' * 64)}, b'old-staging')
        self.drive.now += timedelta(days=3)
        result = self.sync(manifest)
        self.assertEqual(result['removed_file_ids'], [stale['id']])
        self.assertFalse(self.drive.rows[unknown['id']]['trashed'])
        self.assertFalse(self.drive.rows[external['id']]['trashed'])

    def test_ineligible_manifest_or_changed_archive_never_mutates_drive(self):
        manifest = self.generation('First')
        with self.assertRaisesRegex(ValueError, 'DRIVE_GITHUB_GENERATION_REQUIRED'):
            self.sync(dict(manifest, status='OWNER_ONLY_EXPERIMENTAL'))
        self.archive.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'DRIVE_MEDIA_MISMATCH'):
            self.sync(manifest)
        self.assertFalse(self.drive.operations)
