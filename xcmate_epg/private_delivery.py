"""Prepare and verify the Kodi V2 contract in a local, private Git repository."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile

from . import dtd_validation
from .full_export import (
    read_full_archive, read_private_json, read_run_manifest,
    verify_archive, verify_run_member,
)
from .private_preview import private_root
from .streaming import Limits, memory_guard
from .xmltv import IMAGE_HOSTS, timestamp
from .xmltv_images import IMAGE_COUNTS, STANDARD_ROLES, image_attributes, image_url

MANIFEST = 'manifest.json'
XMLTV = 'xcmate-epg.xml.gz'
REF = 'refs/heads/main'
MAX_GZIP = 48 * 1024 * 1024
MAX_XML = 768 * 1024 * 1024
MAX_ARTWORK = 768 * 1024 * 1024
MAX_RECORD = 128 * 1024
MAX_RECORDS = 3_000_000
SHA = re.compile(r'[a-f0-9]{64}')
PROVIDERS = {'tmdb', 'tvmaze', 'xmltv', 'source_declared'}
GATES = {'restore', 'sources', 'mapping', 'artwork', 'delivery', 'client', 'rights', 'release'}
SECRETS = re.compile(r'Bearer\s|(?:password|token|api[_-]?key|username)\s*[:=]|github_pat_', re.I)


def _read_bundle(root, approval):
    try:
        run = read_run_manifest(root)
    except FileNotFoundError:
        return None
    if (not isinstance(approval, dict) or approval.get('generation') != run['generation']
            or not isinstance(approval.get('gates'), dict)
            or not all(isinstance(approval['gates'].get(gate), str)
                       and approval['gates'][gate].strip() for gate in GATES)):
        return None
    return root / run['directory'], run['files']


def _quality(report):
    sources = report.get('sources', [])
    if not isinstance(sources, list) or not all(isinstance(row, dict) for row in sources):
        raise ValueError('INCOMPLETE_DELIVERY_QUALITY')
    source_ids = {row.get('id') for row in sources if isinstance(row.get('id'), str)}
    approved_sources = (len(sources) == len(source_ids) >= 3
                        and all(isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_-]+', key)
                                for key in source_ids)
                        and 'provider' not in source_ids
                        and report.get('source_coverage_complete') is True
                        and report.get('real_sources_successful') == report.get('configured_sources') == len(sources)
                        and all(row.get('successful') is True and type(row.get('exported_programmes')) is int
                                and row['exported_programmes'] > 0
                                for row in sources))
    mapping = report.get('mapping', {})
    if (not approved_sources or mapping.get('catalog_complete') is not True
            or not mapping.get('mapped_channels_with_programmes', 0)
            or not report.get('device', {}).get('programmes', 0)
            or not report.get('artwork', {}).get('programmes_with_verified_artwork', 0)
            or not report.get('artwork', {}).get('logos_present', 0)):
        raise ValueError('INCOMPLETE_DELIVERY_QUALITY')
    return {row['id']: row['exported_programmes'] for row in sources}


def _reference(channel, start, stop, url):
    times = (int(timestamp(start).timestamp()), int(timestamp(stop).timestamp())) if start else (0, 0)
    return channel, *times, url


def _record(raw, sources):
    if not isinstance(raw, dict):
        raise ValueError('INVALID_ARTWORK_RECORD')
    role, url, channel = (raw.get(key) for key in ('type', 'url', 'channel'))
    provider, attribution, digest = (raw.get(key) for key in ('provider', 'attribution', 'sha256'))
    if (not isinstance(channel, str) or len(channel) > 512 or channel.partition(':')[0] not in sources
            or not channel.partition(':')[2] or not isinstance(role, str)
            or role not in (*STANDARD_ROLES, 'channel_logo')
            or not isinstance(provider, str) or provider not in PROVIDERS
            or raw.get('verified') is not True or not isinstance(url, str) or SECRETS.search(url)
            or not isinstance(attribution, str) or not attribution.strip() or len(attribution) > 512
            or SECRETS.search(attribution)
            or not isinstance(digest, str) or not SHA.fullmatch(digest)
            or raw.get('mime_type') not in ('image/jpeg', 'image/png', 'image/webp')
            or any(type(raw.get(key)) is not int or raw[key] <= 0 for key in ('width', 'height', 'size_bytes'))):
        raise ValueError('INVALID_ARTWORK_RECORD')
    image_url(url, IMAGE_HOSTS)
    if role == 'channel_logo':
        return (*_reference(channel, '', '', url), role, '')
    start, stop = raw.get('start'), raw.get('stop')
    if (not isinstance(start, str) or not isinstance(stop, str)
            or timestamp(start) >= timestamp(stop)
            or raw.get('source') not in (None, channel.partition(':')[0])
            or provider == 'source_declared'):
        raise ValueError('INVALID_ARTWORK_EVENT')
    attrs = image_attributes(dict(raw, selected_role=role, selected_provider=provider))
    return (*_reference(channel, start, stop, url), attrs['type'], json.dumps(attrs, sort_keys=True))


def _verify_delivery_xmltv(xmltv, audit, stage, sources):
    limits = Limits()
    image_counts = dict.fromkeys(IMAGE_COUNTS, 0)
    programmes_by_source = Counter()
    earliest = latest = None
    seen_channels = 0
    with sqlite3.connect(stage / 'artwork-audit.sqlite') as db:
        db.execute("""CREATE TABLE reference (
            channel TEXT, start INTEGER, stop INTEGER, url TEXT, role TEXT, attrs TEXT,
            seen INTEGER DEFAULT 0, PRIMARY KEY (channel, start, stop, url, role, attrs)
        ) WITHOUT ROWID""")
        with audit.open('rb') as source:
            records = 0
            while line := source.readline(MAX_RECORD + 1):
                records += 1
                if len(line) > MAX_RECORD or records > MAX_RECORDS:
                    raise ValueError('ARTWORK_RECORD_LIMIT')
                memory_guard(limits)
                db.execute('INSERT OR IGNORE INTO reference VALUES (?, ?, ?, ?, ?, ?, 0)',
                           _record(json.loads(line), sources))

        def on_channel(channel):
            nonlocal seen_channels
            seen_channels += 1
            if (len(channel) > 512 or channel.partition(':')[0] not in sources
                    or not channel.partition(':')[2] or seen_channels > 100_000):
                raise ValueError('UNIVERSAL_CHANNEL_NOT_SOURCE_QUALIFIED')

        def on_programme(channel):
            programmes_by_source[channel.partition(':')[0]] += 1

        def on_times(start, stop):
            nonlocal earliest, latest
            earliest = start if earliest is None else min(earliest, start)
            latest = stop if latest is None else max(latest, stop)

        def on_icon(channel, start, stop, url):
            identity = _reference(channel, start, stop, url)
            if start:
                image_counts['programme_icon'] += 1
                result = db.execute('SELECT 1 FROM reference WHERE channel=? AND start=? AND stop=? AND url=?',
                                    identity).fetchone()
            else:
                image_counts['channel_logo'] += 1
                result = db.execute("""UPDATE reference SET seen=1
                    WHERE channel=? AND start=? AND stop=? AND url=? AND role='channel_logo'""", identity).rowcount
            if not result:
                raise ValueError('ARTWORK_XMLTV_REFERENCE_MISMATCH')

        def on_image(channel, start, stop, url, attrs):
            role = attrs['type']
            image_counts[role] += 1
            arguments = (*_reference(channel, start, stop, url), role, json.dumps(attrs, sort_keys=True))
            updated = db.execute("""UPDATE reference SET seen=1
                WHERE channel=? AND start=? AND stop=? AND url=? AND role=? AND attrs=?""", arguments)
            if not updated.rowcount:
                raise ValueError('ARTWORK_XMLTV_REFERENCE_MISMATCH')

        counts = verify_archive(xmltv, max_xml_bytes=MAX_XML, memory_check=lambda: memory_guard(limits),
                                on_channel=on_channel, on_programme=on_programme, on_icon=on_icon,
                                on_image=on_image, on_times=on_times)
        if programmes_by_source != sources or counts['programmes'] > 3_000_000:
            raise ValueError('DELIVERY_SOURCE_COUNT_MISMATCH')
        if db.execute('SELECT 1 FROM reference WHERE seen=0 LIMIT 1').fetchone():
            raise ValueError('ARTWORK_XMLTV_REFERENCE_MISMATCH')
        if not image_counts['channel_logo'] or not any(image_counts[role] for role in ('poster', 'backdrop', 'still')):
            raise ValueError('INCOMPLETE_DELIVERY_ARTWORK')
    window = {'start_utc': earliest.astimezone(timezone.utc).isoformat(),
              'stop_utc': latest.astimezone(timezone.utc).isoformat()}
    return counts, image_counts, window


class _LocalDelivery:
    """Only local Git plumbing. The final ref is the sole publication point."""
    def __init__(self, root, stage):
        self.path = root / 'delivery.git'
        self.path.mkdir(mode=0o700, exist_ok=True)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GIT_INDEX_FILE=str(stage / 'index'),
                        GIT_AUTHOR_NAME='XCMate EPG Agent', GIT_AUTHOR_EMAIL='epg@example.invalid',
                        GIT_COMMITTER_NAME='XCMate EPG Agent', GIT_COMMITTER_EMAIL='epg@example.invalid')
        if not (self.path / 'HEAD').exists():
            self.git('init', '--bare', '--object-format=sha1', '--initial-branch=main', '--template=')

    def git(self, *args, stdin=None, check=True):
        result = subprocess.run(['git', '-c', f'core.hooksPath={os.devnull}', '--git-dir',
                                 str(self.path), *args], input=stdin, capture_output=True, env=self.env)
        if check and result.returncode:
            raise ValueError('LOCAL_DELIVERY_GIT_FAILED')
        return result

    def current(self):
        result = self.git('rev-parse', '--verify', REF, check=False)
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def blob(self, path):
        return self.git('hash-object', '-w', '--no-filters', str(path)).stdout.decode().strip()

    def commit(self, blobs, parent, message):
        tree_data = ''.join(f'100644 blob {blob}\t{name}\n' for name, blob in sorted(blobs.items()))
        tree = self.git('mktree', stdin=tree_data.encode()).stdout.decode().strip()
        args = ['-p', parent] if parent else []
        return self.git('commit-tree', tree, *args, '-m', message).stdout.decode().strip()

    def check_payload(self, commit, descriptor):
        name = descriptor['file']
        process = subprocess.Popen(['git', '--git-dir', str(self.path), 'cat-file', 'blob', f'{commit}:{name}'],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        digest = hashlib.sha256()
        size = 0
        with process.stdout as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                size += len(chunk)
                digest.update(chunk)
        if process.wait() or size != descriptor['bytes'] or digest.hexdigest() != descriptor['sha256']:
            raise ValueError('LOCAL_DELIVERY_READBACK_MISMATCH')

    def install(self, revision, previous):
        self.git('update-ref', REF, revision, previous or '0' * 40)


def publish_delivery(workspace, *, approval=None, dtd_path=None):
    """Bind one approved immutable run to a verified offline V2 delivery ref."""
    pending = {'schema_version': 3, 'file': XMLTV, 'status': 'PRIVATE_PENDING',
               'reason_code': 'PRODUCTION_DATA_NOT_APPROVED'}
    if approval is None:
        return pending
    root = private_root(workspace)
    selected = _read_bundle(root, approval)
    if selected is None:
        return pending
    bundle, checksums = selected
    report = read_private_json(verify_run_member(bundle, checksums, 'quality-report.json', 1024 * 1024)[0])
    sources = _quality(report)
    xmltv, full = read_full_archive(bundle, checksums, MAX_GZIP)
    audit = verify_run_member(bundle, checksums, 'artwork-metadata.jsonl', MAX_ARTWORK)[0]
    # libxml2 sees only the pinned local grammar and independently verifies the
    # structure. Expat then rejects supplied DTD declarations and audits counts.
    dtd_validation.validate(xmltv, dtd_path, max_bytes=MAX_XML)
    with tempfile.TemporaryDirectory(prefix='.delivery-', dir=root) as directory:
        stage = Path(directory)
        counts, images, window = _verify_delivery_xmltv(xmltv, audit, stage, sources)
        if any(full.get(key) != counts[key] for key in ('channels', 'programmes', 'xml_bytes')):
            raise ValueError('UNIVERSAL_COUNT_MISMATCH')
        manifest = {'schema_version': 3, 'status': 'PRIVATE_READY', 'file': XMLTV,
                    'bytes': full['bytes'], 'sha256': full['sha256'], 'revision': full['sha256'],
                    'channel_count': counts['channels'], 'programme_count': counts['programmes'],
                    'source_count': len(sources), 'image_counts': images, 'window': window}
        delivery = _LocalDelivery(root, stage)
        previous = delivery.current()
        if previous:
            before = json.loads(delivery.git('show', f'{previous}:{MANIFEST}').stdout)
            if all(before.get(key) == value for key, value in manifest.items()):
                delivery.check_payload(before['payload_ref'], manifest)
                return dict(before, changed=False)
        blobs = {XMLTV: delivery.blob(xmltv)}
        payload = delivery.commit(blobs, previous, 'Prepare verified XMLTV delivery')
        delivery.check_payload(payload, manifest)
        manifest.update(payload_ref=payload, generated_at_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        manifest_path = stage / MANIFEST
        manifest_bytes = (json.dumps(manifest, sort_keys=True) + '\n').encode()
        manifest_path.write_bytes(manifest_bytes)
        blobs[MANIFEST] = delivery.blob(manifest_path)
        revision = delivery.commit(blobs, payload, 'Commit verified XMLTV manifest')
        if delivery.git('show', f'{revision}:{MANIFEST}').stdout != manifest_bytes:
            raise ValueError('LOCAL_DELIVERY_READBACK_MISMATCH')
        delivery.install(revision, previous)
        return dict(manifest, changed=True)
