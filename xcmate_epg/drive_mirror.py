"""Rotate an owner-only Drive mirror of an independently verified GitHub export."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from xml.parsers.expat import ExpatError

from .full_export import verify_archive
from .github_smoke import COMMIT
from .private_delivery import MAX_GZIP, MAX_XML, SHA, XMLTV
from .private_preview import private_location
from .runner_state import _lock, _protected

FOLDER = 'XCMate EPG Live-Mirror'
POINTER = 'mirror-manifest.json'
MANAGED = 'xcmate-live-mirror-v1'
FOLDER_MIME = 'application/vnd.google-apps.folder'
ID = re.compile(r'[A-Za-z0-9_-]{1,256}')
CLEANUP_AGE = timedelta(hours=48)


def payload_properties(revision):
    return {'xcmateMirror': MANAGED, 'kind': 'payload', 'revision': revision}


def _name(kind, revision):
    return f'{kind}-{revision}.xml.gz'


def _identity(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value)


def _utc(value):
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError('DRIVE_TIMEZONE_REQUIRED')
    return moment.astimezone(timezone.utc)


def _generation(github, source):
    digest = github.get('sha256')
    size = github.get('bytes')
    if (github.get('status') != 'PRIVATE_READY' or github.get('schema_version') != 3
            or github.get('file') != XMLTV or github.get('revision') != digest
            or not _identity(digest, SHA) or type(size) is not int or not 0 < size <= MAX_GZIP
            or not _identity(github.get('payload_ref'), COMMIT) or not _identity(source, SHA)):
        raise ValueError('DRIVE_GITHUB_GENERATION_REQUIRED')
    _utc(github.get('generated_at_utc'))


def _entry(drive, file_id, owner, folder=None):
    if not _identity(file_id, ID):
        raise ValueError('DRIVE_INVALID_ID')
    row = drive.metadata(file_id)
    if (row.get('id') != file_id or row.get('trashed') is not False
            or row.get('shared') is not False or row.get('ownedByMe') is not True
            or (folder is not None and row.get('parents') != [folder])
            or drive.owner(file_id) != owner):
        raise ValueError('DRIVE_OWNER_ONLY_REQUIRED')
    return row


def _payload(row):
    props = row.get('appProperties') or {}
    digest = props.get('revision')
    if props.get('xcmateMirror') != MANAGED or props.get('kind') != 'payload' or not _identity(digest, SHA):
        return None
    if row.get('name') not in (XMLTV, _name('.incoming', digest), _name('lastgood', digest)):
        return None
    return digest


def _pointer(drive, folder, owner, rows):
    entries = [row for row in rows if row.get('name') == POINTER]
    if not entries:
        return None, None
    if len(entries) != 1:
        raise ValueError('DRIVE_MULTIPLE_POINTERS')
    file_id = entries[0]['id']
    row = _entry(drive, file_id, owner, folder)
    if row.get('name') != POINTER or row.get('appProperties') != POINTER_PROPERTIES:
        raise ValueError('DRIVE_UNMANAGED_POINTER')
    with drive.download(file_id) as stream:
        body = stream.read(32769)
    if len(body) > 32768:
        raise ValueError('DRIVE_POINTER_TOO_LARGE')
    value = json.loads(body)
    if not isinstance(value, dict) or not _identity(value.get('file_id'), ID):
        raise ValueError('DRIVE_INVALID_POINTER')
    _generation(dict(value, file=XMLTV, status=value.get('github_status'),
                     payload_ref=value.get('github_payload_ref')), value.get('source_generation'))
    _utc(value.get('mirror_verified_at_utc'))
    return file_id, value


POINTER_PROPERTIES = {'xcmateMirror': MANAGED, 'kind': 'manifest'}


def _verify_file(drive, row, manifest, scratch):
    size = manifest['bytes']
    if str(row.get('size')) != str(size) or _payload(row) != manifest['sha256']:
        raise ValueError('DRIVE_MEDIA_MISMATCH')
    digest = hashlib.sha256()
    read = 0
    deadline = time.monotonic() + 180
    with tempfile.TemporaryDirectory(prefix='.mirror-readback-', dir=scratch) as directory:
        path = Path(directory) / XMLTV
        with drive.download(row['id']) as source, path.open('xb') as destination:
            while chunk := source.read(min(65536, size - read + 1)):
                if time.monotonic() > deadline:
                    raise ValueError('DRIVE_READBACK_TIMEOUT')
                read += len(chunk)
                if read > size:
                    raise ValueError('DRIVE_MEDIA_MISMATCH')
                digest.update(chunk)
                destination.write(chunk)
        if read != size or digest.hexdigest() != manifest['sha256']:
            raise ValueError('DRIVE_MEDIA_MISMATCH')
        try:
            verify_archive(path, max_xml_bytes=MAX_XML)
        except (ExpatError, EOFError, OSError, ValueError):
            raise ValueError('DRIVE_XMLTV_READBACK_FAILED') from None


def _current(drive, folder, owner):
    rows = drive.children(folder)
    if any(row.get('name') == XMLTV and _payload(row) is None for row in rows):
        raise ValueError('DRIVE_UNMANAGED_LIVE_FILE')
    return rows, _pointer(drive, folder, owner, rows)


def _remove_expired(drive, folder, owner, installed, moment, scratch):
    rows, (_, pointer) = _current(drive, folder, owner)
    if pointer != installed:
        raise ValueError('DRIVE_POINTER_CHANGED')
    current = _entry(drive, installed['file_id'], owner, folder)
    if current.get('name') != XMLTV or _payload(current) != installed['sha256']:
        raise ValueError('DRIVE_CURRENT_NOT_INSTALLED')
    expired_files = []
    for listed in rows:
        digest = _payload(listed)
        if digest is None or listed['id'] == current['id']:
            continue
        row = _entry(drive, listed['id'], owner, folder)
        if _payload(row) != digest:
            raise ValueError('DRIVE_ROTATION_FILE_CHANGED')
        # Only tagged, owned files in the exact mirror directory are eligible.
        expired = row['name'] == _name('.incoming', digest) and _utc(row['createdTime']) <= moment - CLEANUP_AGE
        if row['name'] in (XMLTV, _name('lastgood', digest)) or expired:
            expired_files.append(row)
    if expired_files:
        _verify_file(drive, current, installed, scratch)
    for row in expired_files:
        _, (_, active) = _current(drive, folder, owner)
        if active != installed:
            raise ValueError('DRIVE_POINTER_CHANGED')
        checked = _entry(drive, row['id'], owner, folder)
        if checked.get('name') != row['name'] or _payload(checked) != _payload(row):
            raise ValueError('DRIVE_ROTATION_FILE_CHANGED')
        if row['name'] == XMLTV:
            # A prior incomplete rotation can leave a second visible name.
            # Move it back to staging without removing its file ID or bytes.
            drive.patch(row['id'], {'name': _name('.incoming', _payload(row))})
        else:
            drive.patch(row['id'], {'trashed': True})
    return [row['id'] for row in expired_files if row['name'] != XMLTV]


def sync(drive, folder, archive, github, source_generation, *, lock_store, now=None):
    """Rotate under the same protected filesystem lock for every producer."""
    folder = str(folder)
    if not _identity(folder, ID):
        raise ValueError('DRIVE_INVALID_ID')
    store = _protected(private_location(lock_store))
    lock_id = hashlib.sha256(folder.encode()).hexdigest()
    with _lock(store, f'.drive-mirror-{lock_id}.lock'):
        return _sync(drive, folder, archive, github, source_generation, now)


def _sync(drive, folder, archive, github, source_generation, now):
    _generation(github, source_generation)
    moment = _utc(now if now is not None else datetime.now(timezone.utc))
    path = Path(archive)
    with path.open('rb') as stream:
        content = stream.read(github['bytes'] + 1)
    digest = github['sha256']
    if len(content) != github['bytes'] or hashlib.sha256(content).hexdigest() != digest:
        raise ValueError('DRIVE_MEDIA_MISMATCH')
    owner = drive.owner(folder)
    target = _entry(drive, folder, owner)
    if target.get('name') != FOLDER or target.get('mimeType') != FOLDER_MIME:
        raise ValueError('DRIVE_WRONG_MIRROR_FOLDER')
    rows, (pointer_id, before) = _current(drive, folder, owner)
    old = None
    if before is not None:
        old = _entry(drive, before['file_id'], owner, folder)
        _verify_file(drive, old, before, path.parent)
    changed = before is None or before['sha256'] != digest
    if changed:
        candidate = None
        for listed in rows:
            if _payload(listed) != digest or (old and old['id'] == listed['id']):
                continue
            row = _entry(drive, listed['id'], owner, folder)
            try:
                _verify_file(drive, row, github, path.parent)
            except ValueError as error:
                if str(error) != 'DRIVE_MEDIA_MISMATCH':
                    raise
                continue
            candidate = row
            break
        if candidate is None:
            metadata = {'name': _name('.incoming', digest), 'parents': [folder],
                        'mimeType': 'application/gzip', 'appProperties': payload_properties(digest)}
            uploaded = drive.upload(metadata, content)
            candidate = _entry(drive, uploaded['id'], owner, folder)
            _verify_file(drive, candidate, github, path.parent)
        _, (observed_id, observed) = _current(drive, folder, owner)
        if (observed_id, observed) != (pointer_id, before):
            raise ValueError('DRIVE_POINTER_CHANGED')
        candidate = _entry(drive, candidate['id'], owner, folder)
        if _payload(candidate) != digest:
            raise ValueError('DRIVE_ROTATION_FILE_CHANGED')
        if candidate['name'] != XMLTV:
            drive.patch(candidate['id'], {'name': XMLTV})
        if old is not None and old['name'] == XMLTV:
            old = _entry(drive, old['id'], owner, folder)
            if _payload(old) != before['sha256']:
                raise ValueError('DRIVE_ROTATION_FILE_CHANGED')
            if old['name'] == XMLTV:
                drive.patch(old['id'], {'name': _name('lastgood', before['sha256'])})
        installed = {
            'schema_version': 3, 'revision': digest, 'sha256': digest, 'bytes': github['bytes'],
            'file_id': candidate['id'], 'source_generation': source_generation,
            'github_payload_ref': github['payload_ref'], 'github_status': github['status'],
            'generated_at_utc': github['generated_at_utc'], 'mirror_verified_at_utc': moment.isoformat(),
        }
        metadata = {'name': POINTER, 'parents': [folder], 'mimeType': 'application/json',
                    'appProperties': POINTER_PROPERTIES}
        drive.write_pointer(metadata, (json.dumps(installed, sort_keys=True) + '\n').encode(), pointer_id)
    else:
        installed = before
        # A failed pointer write may have renamed the active file. Its
        # pointer and independently checked bytes still identify LastGood.
        if old['name'] != XMLTV:
            _, (observed_id, observed) = _current(drive, folder, owner)
            if (observed_id, observed) != (pointer_id, before):
                raise ValueError('DRIVE_POINTER_CHANGED')
            _entry(drive, old['id'], owner, folder)
            drive.patch(old['id'], {'name': XMLTV})
    removed = _remove_expired(drive, folder, owner, installed, moment, path.parent)
    return {'status': 'DRIVE_MIRROR_VERIFIED', 'drive_file_id': installed['file_id'],
            'drive_sha256': digest, 'github_sha256': digest, 'changed': changed,
            'last_mirror_utc': moment.isoformat(), 'removed_file_ids': removed}
