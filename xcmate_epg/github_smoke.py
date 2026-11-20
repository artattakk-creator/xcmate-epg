"""Check private GitHub REST delivery using an isolated synthetic fixture."""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .fetch import NoRedirect

API = 'https://api.github.com/repos/artattakk-creator/xcmate-epg-private'
FIXTURE = '/contents/tests/agent-upload-smoke.txt'
PREFIX = b'XCMATE_PRIVATE_UPLOAD_SMOKE_OK\n'
COMMIT = re.compile(r'[0-9a-f]{40}')


def request(token, method, route, data=None):
    headers = {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'xcmate-private-smoke/2',
    }
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    body = None
    if data is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(data).encode()
    try:
        with build_opener(NoRedirect).open(
                Request(API + route, method=method, headers=headers, data=body), timeout=20) as response:
            encoded = response.read(65537)
            if len(encoded) > 65536:
                raise ValueError('GITHUB_SMOKE_RESPONSE_TOO_LARGE')
            return response.status, json.loads(encoded)
    except HTTPError as error:
        return error.code, {}
    except (URLError, OSError, ValueError):
        raise ValueError('GITHUB_SMOKE_RESPONSE_FAILED') from None


def _contents(data):
    try:
        return base64.b64decode(''.join(data['content'].splitlines()), validate=True)
    except (AttributeError, binascii.Error, KeyError, TypeError, ValueError):
        raise ValueError('GITHUB_SMOKE_INVALID_ENCODING') from None


def _private(token, request, role):
    status, body = request(token, 'GET', '')
    if status != 200 or body.get('private') is not True:
        raise ValueError(f'{role}_PRIVATE_REPO_HTTP_{status}')


def probe(publisher, reader, run_number, *, request=request):
    if not publisher or not reader or hmac.compare_digest(publisher, reader):
        raise ValueError('GITHUB_BINDINGS_REQUIRE_DISTINCT_READER_AND_PUBLISHER')
    if not re.fullmatch(r'[0-9]{1,20}', run_number):
        raise ValueError('GITHUB_INVALID_RUN_NUMBER')
    _private(publisher, request, 'PUBLISHER')
    _private(reader, request, 'READER')
    status, _ = request(None, 'GET', FIXTURE)
    if status != 404:
        raise ValueError(f'ANONYMOUS_CONTENTS_HTTP_{status}')

    fixture = PREFIX + f'Run: {run_number}\n'.encode()
    body = {'message': 'test(private): immutable synthetic delivery smoke',
            'content': base64.b64encode(fixture).decode(), 'branch': 'main'}
    status, current = request(publisher, 'GET', FIXTURE)
    if status == 200:
        sha = current.get('sha')
        if (not isinstance(sha, str) or not COMMIT.fullmatch(sha)
                or not _contents(current).startswith(PREFIX)):
            raise ValueError('UNRELATED_SMOKE_FIXTURE')
        body['sha'] = sha
    elif status != 404:
        raise ValueError(f'PUBLISHER_FIXTURE_HTTP_{status}')

    status, response = request(publisher, 'PUT', FIXTURE, body)
    if status not in (200, 201):
        raise ValueError(f'PUBLISHER_WRITE_HTTP_{status}')
    commit = response.get('commit', {}).get('sha')
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError('GITHUB_SMOKE_INVALID_COMMIT')
    status, result = request(reader, 'GET', f'{FIXTURE}?ref={commit}')
    if status != 200:
        raise ValueError(f'READER_READBACK_HTTP_{status}')
    if _contents(result) != fixture:
        raise ValueError('READER_READBACK_MISMATCH')
    return {'status': 'SYNTHETIC_PRIVATE_READBACK_PASS', 'commit': commit,
            'bytes': len(fixture), 'sha256': hashlib.sha256(fixture).hexdigest()}


def main():
    try:
        result = probe(os.environ.get('XCMATE_EPG_PRIVATE_WRITE_TOKEN', ''),
                       os.environ.get('XCMATE_EPG_PRIVATE_READ_TOKEN', ''),
                       os.environ.get('GITHUB_RUN_NUMBER', ''))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
