"""Stage an approved Schema-3 payload and verify it before advancing GitHub main."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .full_export import read_full_archive, read_private_json, read_run_manifest
from .github_smoke import API, COMMIT, request
from .private_delivery import MANIFEST, MAX_GZIP, REF, XMLTV, publish_delivery

REMOTE = 'https://github.com/artattakk-creator/xcmate-epg-private.git'


class _DownloadRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (target.scheme != 'https' or target.username or target.password or target.port not in (None, 443)
                or target.hostname not in ('objects.githubusercontent.com', 'release-assets.githubusercontent.com')):
            raise ValueError('PRIVATE_GITHUB_UNTRUSTED_REDIRECT')
        return Request(newurl, method='GET', headers={'User-Agent': 'xcmate-private-delivery/1'})


class _Repository:
    def __init__(self, directory, remote, token):
        self.directory = directory
        self.remote = remote
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
                        GIT_AUTHOR_NAME='XCMate EPG Agent', GIT_AUTHOR_EMAIL='epg@example.invalid',
                        GIT_COMMITTER_NAME='XCMate EPG Agent', GIT_COMMITTER_EMAIL='epg@example.invalid')
        if token:
            basic = base64.b64encode(('x-access-token:' + token).encode()).decode()
            self.env.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0=f'http.{remote}.extraHeader',
                            GIT_CONFIG_VALUE_0='Authorization: Basic ' + basic,
                            GIT_CONFIG_KEY_1='http.followRedirects', GIT_CONFIG_VALUE_1='false')
        self.git('init', '--object-format=sha1', '--template=')

    def git(self, *args, content=None):
        try:
            result = subprocess.run(['git', '-c', 'credential.helper=', '-c', f'core.hooksPath={os.devnull}',
                                     '-C', str(self.directory), *args], input=content, capture_output=True,
                                    env=self.env, timeout=180)
        except subprocess.TimeoutExpired:
            raise ValueError('PRIVATE_GITHUB_GIT_TIMEOUT') from None
        if result.returncode:
            raise ValueError('PRIVATE_GITHUB_GIT_OPERATION_FAILED')
        return result.stdout

    def ref(self, ref):
        line = self.git('ls-remote', '--refs', self.remote, ref).decode().splitlines()
        if not line:
            return None
        parts = line[0].split('\t')
        if len(line) != 1 or len(parts) != 2 or parts[1] != ref or not COMMIT.fullmatch(parts[0]):
            raise ValueError('INVALID_PRIVATE_GITHUB_REF')
        return parts[0]

    def push(self, commit, ref):
        self.git('push', self.remote, f'{commit}:{ref}')

    def clear_stage(self, commit, ref):
        self.git('push', f'--force-with-lease={ref}:{commit}', self.remote, f':{ref}')

    def commit_payload(self, tag, archive, manifest):
        with archive.open('rb') as stream:
            content = stream.read(manifest['bytes'] + 1)
        if (len(content), hashlib.sha256(content).hexdigest()) != (manifest['bytes'], manifest['sha256']):
            raise ValueError('LOCAL_PAYLOAD_CHANGED_BEFORE_PUSH')
        blob = self.git('hash-object', '-w', '--stdin', content=content).strip()
        tree = self.git('mktree', content=b'100644 blob ' + blob + b'\t' + XMLTV.encode() + b'\n').strip()
        payload = self.git('commit-tree', tree.decode(), '-m', 'Stage a verified XCMate XMLTV payload').decode().strip()
        self.push(payload, tag)
        return payload

    def commit_manifest(self, parent, content):
        self.git('read-tree', parent)
        blob = self.git('hash-object', '-w', '--stdin', content=content).decode().strip()
        self.git('update-index', '--add', '--cacheinfo', f'100644,{blob},{MANIFEST}')
        tree = self.git('write-tree').decode().strip()
        return self.git('commit-tree', tree, '-p', parent, '-m', 'Activate verified XCMate XMLTV revision').decode().strip()


def _read_remote(reader, commit, path):
    headers = {'Authorization': 'Bearer ' + reader, 'Accept': 'application/vnd.github.raw+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'xcmate-private-delivery/1'}
    url = f'{API}/contents/{path}?ref={commit}'
    try:
        return build_opener(_DownloadRedirect()).open(Request(url, headers=headers), timeout=30)
    except HTTPError as error:
        raise ValueError(f'PRIVATE_GITHUB_READBACK_HTTP_{error.code}') from None
    except (URLError, OSError):
        raise ValueError('PRIVATE_GITHUB_READBACK_CONNECTION_FAILED') from None


def _check_access(publisher, reader):
    if not publisher or not reader or hmac.compare_digest(publisher, reader):
        raise ValueError('GITHUB_BINDINGS_REQUIRE_DISTINCT_READER_AND_PUBLISHER')
    for role, token in (('PUBLISHER', publisher), ('READER', reader)):
        status, data = request(token, 'GET', '')
        if status != 200 or data.get('private') is not True:
            raise ValueError(f'{role}_PRIVATE_REPO_HTTP_{status}')
    status, _ = request(None, 'GET', f'/contents/{MANIFEST}')
    if status != 404:
        raise ValueError(f'ANONYMOUS_MANIFEST_HTTP_{status}')


def _payload_readback(read, manifest):
    digest = hashlib.sha256()
    size = 0
    with read(manifest['payload_ref'], XMLTV) as response:
        for chunk in iter(lambda: response.read(65536), b''):
            size += len(chunk)
            if size > manifest['bytes']:
                raise ValueError('REMOTE_PAYLOAD_READBACK_MISMATCH')
            digest.update(chunk)
    if (size, digest.hexdigest()) != (manifest['bytes'], manifest['sha256']):
        raise ValueError('REMOTE_PAYLOAD_READBACK_MISMATCH')


def _manifest_readback(read, commit, contents):
    with read(commit, MANIFEST) as response:
        if response.read(len(contents) + 1) != contents:
            raise ValueError('REMOTE_MANIFEST_READBACK_MISMATCH')


def _publish(repo, read, manifest, archive):
    repo.git('fetch', '--no-tags', '--depth=1', repo.remote, REF)
    current_commit = repo.git('rev-parse', 'FETCH_HEAD').decode().strip()
    raw = repo.git('show', f'{current_commit}:{MANIFEST}')
    try:
        current = json.loads(raw)
    except ValueError:
        raise ValueError('INVALID_REMOTE_MANIFEST') from None
    if (not isinstance(current, dict) or current.get('file') != XMLTV
            or current.get('status') not in ('PRIVATE_PENDING', 'PRIVATE_READY')):
        raise ValueError('INVALID_REMOTE_MANIFEST')
    if current['status'] == 'PRIVATE_READY':
        payload = current.get('payload_ref')
        if (current.get('schema_version') != 3 or not isinstance(payload, str) or not COMMIT.fullmatch(payload)):
            raise ValueError('INVALID_REMOTE_MANIFEST')
        compared = {key: value for key, value in manifest.items() if key not in ('payload_ref', 'generated_at_utc')}
        if all(current.get(key) == value for key, value in compared.items()):
            _payload_readback(read, current)
            _manifest_readback(read, current_commit, raw)
            if repo.ref(REF) != current_commit:
                raise ValueError('REMOTE_MANIFEST_REF_CHANGED')
            return dict(current, changed=False)

    ref = f"refs/heads/xcmate-payload/{manifest['sha256']}"
    payload = repo.ref(ref)
    if payload is None:
        payload = repo.commit_payload(ref, archive, manifest)
    manifest['payload_ref'] = payload
    _payload_readback(read, manifest)
    contents = (json.dumps(manifest, sort_keys=True) + '\n').encode()
    commit = repo.commit_manifest(current_commit, contents)
    stage = f'refs/heads/xcmate-manifest-stage/{commit}'
    repo.push(commit, stage)
    _manifest_readback(read, commit, contents)
    # The normal fast-forward push rejects competing manifest writers.
    repo.push(commit, REF)
    _manifest_readback(read, commit, contents)
    if repo.ref(REF) != commit:
        raise ValueError('REMOTE_MANIFEST_REF_CHANGED')
    try:
        repo.clear_stage(commit, stage)
        staging_retained = False
    except ValueError:
        staging_retained = True
    return dict(manifest, changed=True, manifest_commit=commit, staging_retained=staging_retained)


def publish(workspace, approval, *, publisher='', reader='', _remote=None, _read=None):
    """Use the existing generation-scoped local rights, artwork and XMLTV gates."""
    root = Path(workspace).absolute()
    manifest = publish_delivery(root, approval=approval)
    if manifest['status'] != 'PRIVATE_READY':
        return manifest
    manifest.pop('changed', None)
    if _remote is None:
        _check_access(publisher, reader)
    run = read_run_manifest(root)
    if run['generation'] != approval['generation']:
        raise ValueError('PUBLISH_GENERATION_CHANGED')
    archive, full = read_full_archive(root / run['directory'], run['files'], MAX_GZIP)
    if full['sha256'] != manifest['sha256'] or full['bytes'] != manifest['bytes']:
        raise ValueError('PUBLISH_GENERATION_CHANGED')

    def read(commit, name):
        return _read_remote(reader, commit, name)

    with tempfile.TemporaryDirectory(prefix='.github-delivery-', dir=root) as temp:
        repo = _Repository(Path(temp), _remote or REMOTE, publisher)
        return _publish(repo, _read or read, manifest, archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-root', type=Path, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = publish(args.private_root, read_private_json(args.approval),
                         publisher=os.environ.get('XCMATE_EPG_PRIVATE_WRITE_TOKEN', ''),
                         reader=os.environ.get('XCMATE_EPG_PRIVATE_READ_TOKEN', ''))
    except (KeyError, OSError, TypeError, ValueError):
        parser.exit(1, 'XCMATE_PRIVATE_GITHUB_DELIVERY_BLOCKED\n')
    print(json.dumps({key: result[key] for key in ('status', 'sha256', 'manifest_commit', 'changed', 'staging_retained')
                      if key in result}, sort_keys=True))
    return int(result['status'] != 'PRIVATE_READY')


if __name__ == '__main__':
    raise SystemExit(main())
