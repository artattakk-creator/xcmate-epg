"""One private XCMate cycle for a separately approved daily runner."""
import argparse
import copy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit

from .export import atomic
from .full_preview import run_full
from .private_delivery import publish_delivery
from .private_preview import private_root

INTERVAL = timedelta(days=1)
RETRY_START_SECONDS = 300
RETRY_MAX_SECONDS = 6 * 3600


def _check_rights(config, permissions, *, authorize_network, replay):
    sources = config.get('sources')
    if not isinstance(sources, list) or len(sources) != 3 or not isinstance(permissions, dict):
        raise ValueError('THREE_APPROVED_SOURCES_REQUIRED')
    seen = set()
    inputs = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('INVALID_DAILY_SOURCE')
        identifier = source.get('id')
        if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier)
                or identifier in seen or identifier == 'provider'):
            raise ValueError('INVALID_DAILY_SOURCE')
        seen.add(identifier)
        if ('url' in source) == ('path' in source) or 'channel_ids' in source:
            raise ValueError('INVALID_DAILY_SOURCE')
        if source.get('url') and not (authorize_network or replay):
            raise ValueError('NETWORK_NOT_AUTHORIZED')
        value = source.get('url', source.get('path'))
        if not isinstance(value, str) or not value.strip():
            raise ValueError('INVALID_DAILY_SOURCE')
        if 'url' in source:
            parts = urlsplit(value)
            if (parts.scheme != 'https' or not parts.hostname or parts.port not in (None, 443)
                    or parts.username or parts.password or parts.query or parts.fragment):
                raise ValueError('INVALID_DAILY_SOURCE')
        identity = value if 'url' in source else str(Path(value).absolute())
        if identity in inputs:
            raise ValueError('THREE_DISTINCT_SOURCES_REQUIRED')
        inputs.add(identity)
        expected = hashlib.sha256(identity.encode()).hexdigest()
        approval = permissions.get(identifier, {})
        if (not isinstance(approval, dict) or approval.get('input_sha256') != expected
                or not isinstance(approval.get('evidence'), str) or not approval['evidence'].strip()):
            raise ValueError('SOURCE_RIGHTS_NOT_APPROVED')


def _read_state(path):
    if not path.is_file():
        return {}
    try:
        with path.open(encoding='utf-8') as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError()
        state = json.loads(data)
        if state.get('schema_version') != 1 or type(state.get('failures')) is not int or state['failures'] < 0:
            raise ValueError()
        for name in ('next_attempt', 'last_validated_reference'):
            if state.get(name) is not None and datetime.fromisoformat(state[name]).tzinfo is None:
                raise ValueError()
        return state
    except (AttributeError, ValueError, TypeError):
        raise ValueError('INVALID_DAILY_STATE') from None


def run(config, workspace, permissions, *, now=None, authorize_network=False,
        replay=False, metadata=None):
    """Run one guarded V2 cycle; replay never promotes an export or advances its due date."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('DAILY_TIMEZONE_REQUIRED')
    now = now.astimezone(timezone.utc)
    if replay and authorize_network:
        raise ValueError('REPLAY_REQUIRES_OFFLINE')
    _check_rights(config, permissions, authorize_network=authorize_network, replay=replay)
    root = private_root(workspace)
    with (root / 'daily.lock').open('a') as lock:
        (root / 'daily.lock').chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'cycle_status': 'ALREADY_RUNNING'}
        return _run_locked(config, root, now, authorize_network, replay, metadata)


def _run_locked(config, root, now, authorize_network, replay, metadata):
    state_path = root / 'daily-state.json'
    state = _read_state(state_path)
    due = datetime.fromisoformat(state['next_attempt']) if state.get('next_attempt') else None
    if not replay and due and now < due:
        return {'cycle_status': 'NOT_DUE', 'next_attempt': due.isoformat()}
    effective = copy.deepcopy(config)
    horizon = effective.setdefault('horizon', {})
    if replay and state.get('last_validated_reference'):
        horizon['reference_time'] = state['last_validated_reference']
    else:
        horizon.setdefault('reference_time', now.isoformat())
    artwork = effective.setdefault('artwork', {})
    artwork.update(stable_selection=True, incremental_events=True, upgrade_fallbacks=True)
    try:
        report = run_full(effective, root, authorize_network=authorize_network, metadata=metadata, replay=replay)
    except (ValueError, OSError, KeyError, sqlite3.Error):
        report = {'status': 'CYCLE_FAILED', 'error': 'PRIVATE_FULL_CYCLE_FAILED'}
    ok = report['status'] not in ('SOURCE_BLOCKED', 'CYCLE_FAILED')
    if replay:
        return {'cycle_status': 'OFFLINE_REPLAY' if ok else report['status'], 'report': report}
    failures = 0 if ok else min(30, state.get('failures', 0) + 1)
    delay = INTERVAL if ok else timedelta(seconds=min(RETRY_MAX_SECONDS, RETRY_START_SECONDS * 2**(failures - 1)))
    next_attempt = (now + delay).isoformat()
    state.update(schema_version=1, failures=failures, next_attempt=next_attempt,
                 last_attempt=now.isoformat(), last_status=report['status'])
    if ok:
        state['last_validated_reference'] = horizon['reference_time']
    atomic(state_path, (json.dumps(state, sort_keys=True) + '\n').encode())
    return {'cycle_status': 'VALIDATED' if ok else report['status'],
            'next_attempt': next_attempt, 'report': report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--source-rights', type=Path)
    parser.add_argument('--private-root', type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--authorize-network', action='store_true')
    modes.add_argument('--offline-replay', action='store_true')
    modes.add_argument('--prepare-local-delivery', type=Path, metavar='APPROVAL')
    args = parser.parse_args()
    try:
        if args.prepare_local_delivery:
            result = publish_delivery(args.private_root,
                                      approval=json.loads(args.prepare_local_delivery.read_text()))
            fields = ('status', 'reason_code', 'changed')
        else:
            if args.config is None or args.source_rights is None:
                parser.error('--config and --source-rights are required for a cycle')
            result = run(json.loads(args.config.read_text()), args.private_root,
                         json.loads(args.source_rights.read_text()),
                         authorize_network=args.authorize_network, replay=args.offline_replay)
            fields = ('cycle_status', 'next_attempt')
        print(json.dumps({key: result[key] for key in fields if key in result}))
        return int(result.get('cycle_status') in ('SOURCE_BLOCKED', 'CYCLE_FAILED'))
    except (ValueError, OSError, KeyError, sqlite3.Error):
        print('XCMATE_PRIVATE_DAILY_BLOCKED', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
