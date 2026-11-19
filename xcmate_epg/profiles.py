"""Create separate device XMLTV profiles from a committed universal archive."""
import argparse
from collections import defaultdict
import copy
import fcntl
import gzip
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from xml.etree import ElementTree as ET

from .channel_mapping import build_mapping, persist_mapping
from .export import atomic
from .full_export import (
    publish_run, publish_stream, read_full_archive, read_private_json, read_run_manifest,
    verify_archive, verify_run_member,
)
from .private_preview import private_root
from .streaming import Limits, memory_guard

def _records(archive, kind):
    with gzip.open(archive, 'rb') as stream:
        depth = 0
        root = None
        for event, node in ET.iterparse(stream, events=('start', 'end')):
            if event == 'start':
                depth += 1
                if depth == 1:
                    root = node
                continue
            if depth == 2:
                if node.tag == kind:
                    yield node
                elif kind == 'channel' and node.tag == 'programme':
                    return
                root.remove(node)
            depth -= 1


def derive_profile(workspace, profile_id, mapping_input):
    """Derive an independent local profile without visiting any network source."""
    if not isinstance(profile_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', profile_id):
        raise ValueError('INVALID_PROFILE_ID')
    if not isinstance(mapping_input, dict):
        raise ValueError('INVALID_PROFILE_CATALOG')
    root = private_root(workspace)
    run = read_run_manifest(root)
    limits = Limits()
    guard = lambda: memory_guard(limits)
    bundle = root / run['directory']
    archive, full = read_full_archive(bundle, run['files'], limits.max_compressed_bytes)
    quality = read_private_json(verify_run_member(bundle, run['files'], 'quality-report.json', 1024 * 1024)[0])
    priorities = {row['id']: row.get('priority', 100) for row in quality['sources']}
    verified = verify_archive(archive, memory_check=guard)
    if any(full.get(key) != verified[key] for key in ('channels', 'programmes', 'xml_bytes')):
        raise ValueError('UNIVERSAL_COUNT_MISMATCH')

    channels = {}
    for node in _records(archive, 'channel'):
        channels[node.get('id')] = node
        guard()
    channel_rows = [{'qualified_id': key, 'name': node.findtext('display-name', ''),
                     'source': key.partition(':')[0], 'priority': priorities.get(key.partition(':')[0], 100)}
                    for key, node in channels.items()]
    mapping = build_mapping(mapping_input.get('catalog', []), mapping_input.get('confirmations', []),
                            channel_rows)
    mapping['catalog_complete'] = mapping_input.get('catalog_complete') is True
    identities = defaultdict(list)
    for row in mapping['entries']:
        if row['status'] == 'confirmed':
            identities[row['qualified_id']].append(f"provider:{row['provider_id']}")

    def device_channels():
        for source, targets in identities.items():
            for identifier in targets:
                node = copy.deepcopy(channels[source])
                node.set('id', identifier)
                yield node

    def device_programmes():
        for node in _records(archive, 'programme'):
            for identifier in identities.get(node.get('channel'), ()):
                selected = copy.deepcopy(node)
                selected.set('channel', identifier)
                yield selected

    profiles = root / 'profiles'
    profiles.mkdir(mode=0o700, exist_ok=True)
    target = profiles / profile_id
    target.mkdir(mode=0o700, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.profile-', dir=target))
    try:
        profile = publish_stream(device_channels(), device_programmes(), stage / 'device',
                                 status='PARTIAL_COVERAGE', view='device', memory_check=guard)
        persist_mapping(mapping, stage / 'mapping-review.json')
        report = {'status': 'PROFILE_DERIVED', 'universal_generation': run['generation'],
                  'universal_sha256': full['sha256'], 'mapping': mapping['summary'],
                  'channel_count': profile['channels'], 'programme_count': profile['programmes']}
        atomic(stage / 'quality-report.json', (json.dumps(report, sort_keys=True) + '\n').encode())
        lock_path = target / 'profile.lock'
        with lock_path.open('a') as lock:
            lock_path.chmod(0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            publish_run(stage, target, memory_check=guard)
        return report
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', required=True, type=Path)
    parser.add_argument('--profile-id', required=True)
    parser.add_argument('--mapping', required=True, type=Path)
    args = parser.parse_args()
    try:
        report = derive_profile(args.private_root, args.profile_id, read_private_json(args.mapping, 16 * 1024 * 1024))
    except (OSError, ValueError, KeyError, TypeError):
        print('PRIVATE_PROFILE_DERIVATION_BLOCKED', file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in ('status', 'channel_count', 'programme_count')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
