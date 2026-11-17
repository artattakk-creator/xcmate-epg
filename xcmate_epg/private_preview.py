"""One-shot private XMLTV preview. No demo fallback, uploads, schedule or secret import."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import sys
from urllib.parse import urlsplit
import zlib
from xml.etree import ElementTree as ET

from .export import atomic, publish
from .fetch import http_get
from .state import State
from .xmltv import IMAGE_HOSTS, LIMIT, merge, parse, timestamp

PREVIEW_STATUS = 'PRIVATE_LIVE_PREVIEW_DO_NOT_PUBLISH'


def private_root(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path,*path.parents)):
        raise ValueError('UNSAFE_PRIVATE_ROOT')
    path = path.resolve()
    for ancestor in (path, *path.parents):
        if (ancestor / '.git').exists():
            repository = subprocess.run(['git', '-C', str(ancestor), 'rev-parse', '--show-toplevel'],
                                        capture_output=True, text=True)
            if repository.returncode or Path(repository.stdout.strip()).resolve() != ancestor:
                raise ValueError('UNSAFE_PRIVATE_ROOT')
            result = subprocess.run(['git', '-C', str(ancestor), 'check-ignore', '-q', str(path)],
                                    capture_output=True)
            if result.returncode:
                raise ValueError('UNSAFE_PRIVATE_ROOT')
            break
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    for directory,dirs,files in os.walk(path,followlinks=False):
        for name in dirs+files:
            entry=Path(directory)/name
            info=entry.lstat()
            if (entry.is_symlink() or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1)):
                raise ValueError('UNSAFE_PRIVATE_ROOT')
            entry.chmod(0o700 if entry.is_dir() else 0o600)
    return path


def measure(data):
    """Measure only within the original 32 MiB limits; no unlimited decompression."""
    report = {'compressed_bytes': len(data), 'compression': 'gzip' if data.startswith(b'\x1f\x8b') else 'none'}
    if len(data) > LIMIT:
        raise ValueError('COMPRESSED_LIMIT_REQUIRES_SUBSET')
    try:
        if report['compression'] == 'gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                xml = stream.read(LIMIT + 1)
        else:
            xml = data
        report['decompressed_bytes'] = len(xml)
        if len(xml) > LIMIT:
            raise ValueError('DECOMPRESSED_LIMIT_REQUIRES_SUBSET')
        xml.decode('utf-8-sig')
        report['encoding'] = 'UTF-8'
        report['doctype_present'] = b'<!DOCTYPE' in xml.upper()
        # The existing parser rejects all DTD/entity declarations.
        return xml, report
    except (OSError, EOFError, zlib.error, UnicodeError):
        raise ValueError('INVALID_GZIP_OR_ENCODING') from None


def _read_local(path):
    with Path(path).open('rb') as stream:
        return stream.read(LIMIT + 1)


def _source(state, source, hosts, config_fingerprint):
    key = source['id']
    # A changed URL/path/host policy cannot reuse validators or a different feed's LastGood.
    fingerprint = hashlib.sha256(json.dumps([source, sorted(hosts), config_fingerprint], sort_keys=True).encode()).hexdigest()
    cache_key = key + '_' + fingerprint
    old = state.source(cache_key)
    stats = {'id': key, 'http_status': None, 'cache_status': None}
    try:
        if 'url' in source:
            headers = {'User-Agent': 'XCMateEPG/0.1 private-one-shot-preview'}
            if old and old['etag']: headers['If-None-Match'] = old['etag']
            if old and old['modified']: headers['If-Modified-Since'] = old['modified']
            response = http_get(source['url'], headers)
            stats['http_status'] = response.status
            if response.status == 304:
                if not old: raise ValueError('304_WITHOUT_SNAPSHOT')
                data = bytes(old['body'])
            elif response.status == 200:
                data = response.body
            else:
                raise ValueError('HTTP_SOURCE_FAILURE')
            validators = {k.lower(): v for k, v in response.headers.items()}
            if response.status == 304:
                validators.setdefault('etag', old['etag'])
                validators.setdefault('last-modified', old['modified'])
        else:
            data = _read_local(source['path'])
            validators = {}
        xml, dimensions = measure(data)
        stats.update(dimensions)
        root = parse(xml, key, image_hosts=hosts)
        stats['input_channels'] = len(root.findall('channel'))
        stats['input_programmes'] = len(root.findall('programme'))
        wanted = source.get('channel_ids')
        if wanted is not None:
            if not isinstance(wanted, list) or not wanted or any(not isinstance(v,str) for v in wanted):
                raise ValueError('INVALID_SUBSET')
            ids = {key + ':' + channel for channel in wanted}
            for node in list(root):
                if (node.tag == 'channel' and node.get('id') not in ids
                        or node.tag == 'programme' and node.get('channel') not in ids):
                    root.remove(node)
            # Validate again before promoting a subset to LastGood.
            from .xmltv import validate
            validate(root, hosts)
        sha = hashlib.sha256(data).hexdigest()
        state.save_source(cache_key, data, sha, validators.get('etag'), validators.get('last-modified'), root, fingerprint)
        stats['cache_status'] = 'unchanged' if old and old['sha'] == sha else 'changed'
        stats['successful'] = True
        return root, stats
    except (ValueError, OSError, TimeoutError) as error:
        allowed = {'COMPRESSED_LIMIT_REQUIRES_SUBSET','DECOMPRESSED_LIMIT_REQUIRES_SUBSET','INVALID_GZIP_OR_ENCODING','304_WITHOUT_SNAPSHOT','HTTP_SOURCE_FAILURE','INVALID_SUBSET'}
        stats['error'] = str(error) if str(error) in allowed else 'NETWORK_OR_INPUT_VALIDATION_FAILED'
        stats['successful'] = False
        if old and old['tree'] is not None:
            root = ET.fromstring(old['tree'])
            from .xmltv import validate
            validate(root, hosts)
            stats['cache_status'] = 'lastgood'
            return root, stats
        stats['cache_status'] = 'missing'
        return None, stats


def run_preview(config, root, *, authorize_network=False):
    sources = config.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('LIVE_INPUT_REQUIRED')
    ids = [source.get('id') for source in sources]
    if len(set(ids)) != len(ids) or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',v) for v in ids):
        raise ValueError('INVALID_SOURCE_IDS')
    for source in sources:
        if ('path' in source) == ('url' in source): raise ValueError('ONE_INPUT_PER_SOURCE_REQUIRED')
        if 'url' in source and not authorize_network: raise ValueError('NETWORK_NOT_AUTHORIZED')
        if 'url' in source:
            url = urlsplit(source['url'])
            if url.scheme != 'https' or url.username or url.password or url.query or url.fragment:
                raise ValueError('CREDENTIAL_FREE_HTTPS_REQUIRED')
    hosts = IMAGE_HOSTS | set(config.get('approved_image_hosts', []))
    previous_umask = os.umask(0o077)
    try:
        return _run(config, private_root(root), hosts)
    finally:
        os.umask(previous_umask)


def _run(config, root, hosts):
    trees, reports = [], []
    with State(root / 'state.sqlite') as state:
        for source in config['sources']:
            tree, stats = _source(state, source, hosts, PREVIEW_STATUS)
            reports.append(stats)
            if tree is not None: trees.append((source.get('priority', 100), tree))
        threshold = config.get('minimum_sources', 1)
        if type(threshold) is not int or threshold < 1 or len(trees) < threshold:
            atomic(root / 'blocked-report.json', json.dumps({'status':'LIVE_INPUT_BLOCKED','sources':reports,'real_sources_successful':sum(r['successful'] for r in reports),'channels':0,'programmes':0,'artwork_matched':0,'logos_present':0,'time_range':None,'size_bytes':0,'sha256':None,'artwork_status':'PENDING'}, indent=2).encode())
            raise ValueError('LIVE_INPUT_BLOCKED')
        tree, conflicts = merge(trees, image_hosts=hosts)
        manifest = publish(tree, root / 'output', status=PREVIEW_STATUS, image_hosts=hosts)
        archive = root / 'output' / 'xcmate-epg.xml.gz'
        payload = archive.read_bytes()
        if hashlib.sha256(payload).hexdigest() != manifest['sha256']:
            raise ValueError('OUTPUT_CHECKSUM_FAILED')
        parse(payload, 'verification', image_hosts=hosts)
        programmes = tree.findall('programme')
        starts = [timestamp(p.get('start')) for p in programmes]
        stops = [timestamp(p.get('stop')) for p in programmes]
        report = {'status':'PRIVATE_EPG_CREATED','real_sources_successful':sum(r['successful'] for r in reports),
                  'channels':manifest['channels'],'programmes':manifest['programmes'],
                  'time_range':{'start':min(starts).isoformat(),'stop':max(stops).isoformat()},
                  'size_bytes':len(payload),'sha256':manifest['sha256'],
                  'channel_map_confirmed':0,'artwork_status':'PENDING','artwork_matched':0,
                  'logos_present':sum(len(c.findall('icon')) for c in tree.findall('channel')),
                  'programme_images_present':sum(len(p.findall('icon')) for p in programmes),
                  'ambiguous':conflicts,'errors':sum(not r['successful'] for r in reports),
                  'sources':reports,'input_kind':'configured_xmltv_no_demo_fallback'}
        atomic(root / 'quality-report.json', (json.dumps(report,indent=2)+'\n').encode())
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--private-root', type=Path, required=True)
    parser.add_argument('--authorize-network', action='store_true')
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        report = run_preview(config,args.private_root,authorize_network=args.authorize_network)
        print(json.dumps({key:report[key] for key in ('status','channels','programmes','size_bytes','sha256','artwork_status')}))
        return 0
    except (OSError,ValueError,TypeError,KeyError):
        print('LIVE_INPUT_BLOCKED: input unavailable or validation failed; previous output retained',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
