"""Verified, rotating snapshots on an owner-only persistent runner volume."""
import argparse
from contextlib import contextmanager
import ctypes
from datetime import datetime, timedelta, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile

from .daily import _read_state
from .export import atomic
from .full_export import read_private_json, read_run_manifest, _verify_run_bundle
from .private_preview import private_location, private_root
from .streaming import file_sha

SHA256 = re.compile(r'[0-9a-f]{64}')
MAX_BYTES = 8 * 1024**3
MAX_FILES = 10_000
VERIFY_METADATA_BYTES = 8 * 1024**2
DATABASES = ('stream.sqlite', 'artwork.sqlite')
STATE_FILES = ('daily-state.json', 'run-manifest.json', *DATABASES)


def _protected(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('STATE_UNSAFE_PATH')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('STATE_PROTECTED_DIRECTORY_REQUIRED')
    return path.resolve()


@contextmanager
def _lock(root, name):
    fd = os.open(root / name, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('STATE_UNSAFE_PATH')
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('STATE_BUSY') from None
        yield


def _now(value):
    moment = value or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError('STATE_TIMEZONE_REQUIRED')
    return moment.astimezone(timezone.utc)


def _safe_relative(name):
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('STATE_UNSAFE_PATH')
    return path


def _file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('STATE_UNSAFE_PATH')
    return info


def _files(root):
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('STATE_UNSAFE_PATH')
        if not path.is_dir():
            yield path, _file(path)


def _inventory(root):
    files = {}
    remaining = MAX_BYTES
    for path, info in _files(root):
        if len(files) >= MAX_FILES or info.st_size > remaining:
            raise ValueError('STATE_SNAPSHOT_LIMIT')
        digest, size = file_sha(path, remaining)
        files[path.relative_to(root).as_posix()] = {'sha256': digest, 'bytes': size}
        remaining -= size
    return files


def _key(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _copy(source, destination):
    _file(source)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with source.open('rb') as input_file, destination.open('xb') as output:
        os.fchmod(output.fileno(), 0o600)
        shutil.copyfileobj(input_file, output, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())


def _sync_directory(path):
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _install_restore(candidate, target):
    # The Linux runner uses a no-replace rename so even a concurrent empty
    # destination is not overwritten. Unsupported filesystems fail closed.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise ValueError('STATE_NOREPLACE_UNAVAILABLE')
    at_fdcwd, rename_noreplace = -100, 1
    if rename(at_fdcwd, os.fsencode(candidate), at_fdcwd, os.fsencode(target), rename_noreplace):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ValueError('STATE_RESTORE_TARGET_EXISTS')
        raise OSError(error, 'STATE_RESTORE_RENAME_FAILED')


def _copy_tree(source, target):
    for path, _ in _files(source):
        _copy(path, target / path.relative_to(source))


def _verified_run(root):
    _file(root / 'run-manifest.json')
    run = read_run_manifest(root)
    for name in run['files']:
        _safe_relative(name)
    _verify_run_bundle(root / run['directory'], run['files'], lambda: None)
    return run


def _check_state(root):
    for filename in STATE_FILES:
        _file(root / filename)
    state = _read_state(root / 'daily-state.json')
    if not state.get('last_validated_reference') or state['failures']:
        raise ValueError('STATE_LAST_GOOD_REQUIRED')
    run = _verified_run(root)
    for path in (root / filename for filename in DATABASES):
        for suffix in ('-wal', '-journal'):
            journal = path.with_name(path.name + suffix)
            if journal.exists() and _file(journal).st_size:
                raise ValueError('STATE_SQLITE_JOURNAL_ACTIVE')
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
                if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('STATE_SQLITE_INTEGRITY')
        except sqlite3.Error:
            raise ValueError('STATE_SQLITE_INTEGRITY') from None
    return run


def _profiles(root):
    directory = root / 'profiles'
    if directory.is_dir():
        for entry in directory.iterdir():
            if entry.is_dir() and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', entry.name):
                yield entry


def _stage_paths(root, run):
    # Snapshot the active generation and current source validators, rather than
    # accumulating every historical run and every previously fetched body.
    for filename in STATE_FILES:
        yield root / filename
    audit = root / 'drive-mirror-audit.json'
    if audit.exists():
        yield audit
    for path, _ in _files(root / run['directory']):
        yield path
    for profile in _profiles(root):
        with _lock(profile, 'profile.lock'):
            pointer = profile / 'run-manifest.json'
            if pointer.exists():
                current = _verified_run(profile)
                yield pointer
                for path, _ in _files(profile / current['directory']):
                    yield path
    quality = read_private_json(root / run['directory'] / 'quality-report.json')
    for source in quality['sources']:
        source_id = source['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', source_id):
            raise ValueError('STATE_INVALID_SOURCE_ID')
        cache = root / 'snapshots'
        index = cache / (source_id + '.json')
        if not index.exists():
            continue  # A local-input source has no HTTP validator.
        item = read_private_json(index, 65536)
        identity, digest = item['identity'], item['sha256']
        if not SHA256.fullmatch(identity) or not SHA256.fullmatch(digest):
            raise ValueError('STATE_INVALID_SOURCE_SNAPSHOT')
        name = f'{source_id}-{identity}-{digest}.bin'
        body = cache / name
        if (item['file'] != name or _file(body).st_size != item['compressed_bytes']
                or file_sha(body, MAX_BYTES) != (digest, item['compressed_bytes'])):
            raise ValueError('STATE_INVALID_SOURCE_SNAPSHOT')
        yield index
        yield body


def _stage(root, target, run):
    remaining = MAX_BYTES
    for count, path in enumerate(_stage_paths(root, run), start=1):
        remaining -= _file(path).st_size
        if count > MAX_FILES or remaining < 0:
            raise ValueError('STATE_SNAPSHOT_LIMIT')
        _copy(path, target / path.relative_to(root))


def _metadata(folder, identity):
    _protected(folder)
    if {path.name for path in folder.iterdir()} != {'snapshot.json', 'state'}:
        raise ValueError('STATE_INVALID_SNAPSHOT_LAYOUT')
    _file(folder / 'snapshot.json')
    metadata = read_private_json(folder / 'snapshot.json', VERIFY_METADATA_BYTES)
    files = metadata.get('files')
    if metadata.get('schema_version') != 1 or not isinstance(files, dict) or _key(files) != identity:
        raise ValueError('STATE_INVALID_DESCRIPTOR')
    created = datetime.fromisoformat(metadata['created_at'])
    if created.tzinfo is None:
        raise ValueError('STATE_INVALID_DESCRIPTOR')
    return files, created


def _verify(folder, identity):
    files, _ = _metadata(folder, identity)
    state = _protected(folder / 'state')
    if _inventory(state) != files:
        raise ValueError('STATE_HASH_MISMATCH')
    result = _check_state(state)
    for profile in _profiles(state):
        _verified_run(profile)
    return result


def _pointers(store):
    path = store / 'latest.json'
    _file(path)
    value = read_private_json(path, 4096)
    if (value.get('schema_version') != 1
            or not isinstance(value.get('current'), str) or not SHA256.fullmatch(value['current'])
            or (value.get('previous') is not None
                and (not isinstance(value['previous'], str) or not SHA256.fullmatch(value['previous'])
                     or value['previous'] == value['current']))):
        raise ValueError('STATE_INVALID_POINTER')
    return value


def save(workspace, store, *, now=None):
    """Preserve a validated generation before atomically updating the backup pointer."""
    root, store = _protected(workspace), _protected(private_location(store))
    if root == store or root in store.parents or store in root.parents:
        raise ValueError('STATE_OVERLAPPING_ROOTS')
    # Enforce the repository-ignore protection and reject links before reading inputs.
    root = private_root(root)
    with _lock(root, 'daily.lock'), _lock(store, '.runner-state.lock'):
        snapshots = store / 'snapshots'
        snapshots.mkdir(mode=0o700, exist_ok=True)
        _protected(snapshots)
        try:
            current = _pointers(store)['current']
        except FileNotFoundError:
            current = None
        if current:
            _verify(snapshots / current, current)
        run = _check_state(root)
        with tempfile.TemporaryDirectory(prefix='.stage-', dir=store) as directory:
            candidate = Path(directory)
            state = candidate / 'state'
            state.mkdir(mode=0o700)
            _stage(root, state, run)
            files = _inventory(state)
            identity = _key(files)
            if identity == current:
                return identity
            descriptor = {'schema_version': 1, 'created_at': _now(now).isoformat(), 'files': files}
            atomic(candidate / 'snapshot.json', json.dumps(descriptor, sort_keys=True).encode())
            _verify(candidate, identity)
            for directory, _, _ in os.walk(candidate, topdown=False):
                _sync_directory(directory)
            final = snapshots / identity
            if final.exists():
                _verify(final, identity)
            else:
                os.rename(candidate, final)
                _sync_directory(snapshots)
            pointer = {'schema_version': 1, 'current': identity, 'previous': current}
            atomic(store / 'latest.json', json.dumps(pointer, sort_keys=True).encode())
            _sync_directory(store)
            return identity


def restore(store, destination, *, previous=False):
    """Rebuild in an empty private path with independent hash and SQLite checks."""
    store = _protected(private_location(store))
    requested = Path(destination).absolute()
    target = private_location(_protected(requested.parent) / requested.name)
    if target == store or store in target.parents:
        raise ValueError('STATE_OVERLAPPING_ROOTS')
    if target.exists() or target.is_symlink():
        raise ValueError('STATE_RESTORE_TARGET_EXISTS')
    with _lock(store, '.runner-state.lock'):
        version = _pointers(store)['previous' if previous else 'current']
        if version is None:
            raise ValueError('STATE_NO_PREVIOUS_SNAPSHOT')
        snapshot = _protected(store / 'snapshots') / version
        run = _verify(snapshot, version)
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=target.parent) as directory:
            candidate = Path(directory) / 'state'
            candidate.mkdir(mode=0o700)
            _copy_tree(snapshot / 'state', candidate)
            _check_state(candidate)
            expected, _ = _metadata(snapshot, version)
            if _inventory(candidate) != expected:
                raise ValueError('STATE_HASH_MISMATCH')
            for name in run['files']:
                _copy(candidate / run['directory'] / name, candidate / name)
            for profile in _profiles(candidate):
                profile_run = _verified_run(profile)
                for name in profile_run['files']:
                    _copy(profile / profile_run['directory'] / name, profile / name)
            for directory, _, _ in os.walk(candidate, topdown=False):
                _sync_directory(directory)
            _install_restore(candidate, target)
            _sync_directory(target.parent)
        return version


def prune(store, *, now=None, retention=timedelta(days=7)):
    """Remove expired managed snapshots only when both rollback points verify."""
    store = _protected(private_location(store))
    if retention <= timedelta(0):
        raise ValueError('STATE_INVALID_RETENTION')
    with _lock(store, '.runner-state.lock'):
        pointer = _pointers(store)
        snapshots = _protected(store / 'snapshots')
        kept = {pointer['current'], pointer.get('previous')}
        if None in kept:
            return 0
        for identity in kept:
            _verify(snapshots / identity, identity)
        cutoff = _now(now) - retention
        expired = []
        for folder in snapshots.iterdir():
            if folder.name not in kept and SHA256.fullmatch(folder.name):
                _, created = _metadata(folder, folder.name)
                if created < cutoff:
                    _verify(folder, folder.name)
                    expired.append(folder)
        for folder in expired:
            shutil.rmtree(folder)
        _sync_directory(snapshots)
        return len(expired)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('save').add_argument('workspace', type=Path)
    restore_parser = actions.add_parser('restore')
    restore_parser.add_argument('destination', type=Path)
    restore_parser.add_argument('--previous', action='store_true')
    actions.add_parser('prune')
    options = parser.parse_args()
    try:
        if options.action == 'save':
            value = save(options.workspace, options.store)
        elif options.action == 'restore':
            value = restore(options.store, options.destination, previous=options.previous)
        else:
            value = prune(options.store)
    except (KeyError, OSError, TypeError, ValueError, sqlite3.Error):
        parser.exit(1, 'XCMATE_RUNNER_STATE_BLOCKED\n')
    print(json.dumps({'action': options.action, 'result': value}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
