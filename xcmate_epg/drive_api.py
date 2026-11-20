"""Narrow Google Drive v3 transport for the owner-only live mirror."""
import json
import os
import re
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from .fetch import NoRedirect

FILES = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
TOKEN = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/drive.file'
ID = re.compile(r'[A-Za-z0-9_-]{1,256}')
FIELDS = 'id,name,mimeType,parents,shared,ownedByMe,trashed,size,createdTime,appProperties'
RETRYABLE = {408, 429, 500, 502, 503, 504}
CHUNK = 8 * 1024**2


class DriveFailure(ValueError):
    def __init__(self, status=None, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__('DRIVE_CONNECTION_FAILED' if status is None else f'DRIVE_HTTP_{status}')

    @property
    def retryable(self):
        return self.status is None or self.status in RETRYABLE


def _url(endpoint, **params):
    return endpoint + '?' + urlencode(params)


def _id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('DRIVE_INVALID_ID')
    return value


def _json(response):
    with response:
        body = response.read(2 * 1024**2 + 1)
    if len(body) > 2 * 1024**2:
        raise ValueError('DRIVE_RESPONSE_LIMIT')
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError('DRIVE_INVALID_RESPONSE')
    return value


def _open(opener, request, expected, timeout=25):
    try:
        response = opener(request, timeout=timeout)
    except HTTPError as error:
        response = error
        status = error.code
    except (URLError, OSError):
        raise DriveFailure() from None
    else:
        status = response.status
    if status not in expected:
        after = response.headers.get('Retry-After')
        response.close()
        raise DriveFailure(status, after)
    return response


def from_environment(environment=None):
    env = os.environ if environment is None else environment
    names = ('XCMATE_DRIVE_CLIENT_ID', 'XCMATE_DRIVE_CLIENT_SECRET', 'XCMATE_DRIVE_REFRESH_TOKEN')
    if any(not env.get(name) for name in names):
        raise ValueError('DRIVE_OAUTH_BINDINGS_REQUIRED')
    form = {'client_id': env[names[0]], 'client_secret': env[names[1]],
            'refresh_token': env[names[2]], 'grant_type': 'refresh_token'}
    request = Request(TOKEN, data=urlencode(form).encode(), method='POST',
                      headers={'Content-Type': 'application/x-www-form-urlencoded'})
    result = _json(_open(build_opener(NoRedirect).open, request, {200}))
    if result.get('token_type', '').lower() != 'bearer':
        raise ValueError('DRIVE_INVALID_TOKEN_RESPONSE')
    if 'scope' in result and result['scope'].split() != [SCOPE]:
        raise ValueError('DRIVE_FILE_SCOPE_REQUIRED')
    return Drive(result.get('access_token'))


class Drive:
    def __init__(self, token, *, opener=None, sleep=time.sleep):
        if not isinstance(token, str) or not re.fullmatch(r'[!-~]{1,4096}', token):
            raise ValueError('DRIVE_INVALID_AUTH_BINDING')
        self.token = token
        self.open = opener or build_opener(NoRedirect).open
        self.sleep = sleep

    def _wait(self, error, attempt):
        after = error.retry_after
        self.sleep(min(10, int(after)) if after and after.isdecimal() and len(after) <= 8 else 2 ** attempt)

    def _call(self, method, url, *, body=None, headers=None, expected=(200,), retry=True):
        headers = dict(headers or {}, Authorization='Bearer ' + self.token,
                       **{'User-Agent': 'xcmate-owner-only-mirror/1'})
        request = Request(url, data=body, method=method, headers=headers)
        attempts = 3 if retry else 1
        for attempt in range(attempts):
            try:
                return _open(self.open, request, expected)
            except DriveFailure as error:
                if not error.retryable or attempt + 1 == attempts:
                    raise
                self._wait(error, attempt)

    def metadata(self, file_id):
        return _json(self._call('GET', _url(f'{FILES}/{_id(file_id)}', fields=FIELDS)))

    def owner(self, file_id):
        endpoint = f'{FILES}/{_id(file_id)}/permissions'
        value = _json(self._call('GET', _url(endpoint, fields='nextPageToken,permissions(id,type,role)', pageSize=100)))
        rows = value.get('permissions')
        if (value.get('nextPageToken') or not isinstance(rows, list) or len(rows) != 1
                or rows[0].get('type') != 'user' or rows[0].get('role') != 'owner'):
            raise ValueError('DRIVE_OWNER_ONLY_REQUIRED')
        return _id(rows[0].get('id'))

    def children(self, folder):
        fields = f'nextPageToken,incompleteSearch,files({FIELDS})'
        options = {'q': f"'{_id(folder)}' in parents and trashed = false", 'fields': fields, 'pageSize': 100}
        rows = []
        for _ in range(20):
            page = _json(self._call('GET', _url(FILES, **options)))
            if page.get('incompleteSearch'):
                raise ValueError('DRIVE_INCOMPLETE_LIST')
            rows.extend(page.get('files', ()))
            token = page.get('nextPageToken')
            if not token:
                return rows
            options['pageToken'] = token
        raise ValueError('DRIVE_LIST_LIMIT')

    def patch(self, file_id, fields):
        return _json(self._call('PATCH', _url(f'{FILES}/{_id(file_id)}', fields=FIELDS),
                                body=json.dumps(fields).encode(), headers={'Content-Type': 'application/json'}))

    def download(self, file_id):
        return self._call('GET', _url(f'{FILES}/{_id(file_id)}', alt='media'))

    def upload(self, metadata, payload):
        size = len(payload)
        if not size:
            raise ValueError('DRIVE_EMPTY_MEDIA')
        headers = {'Content-Type': 'application/json; charset=UTF-8', 'X-Upload-Content-Type': 'application/gzip',
                   'X-Upload-Content-Length': str(size)}
        with self._call('POST', _url(UPLOAD, uploadType='resumable', fields=FIELDS),
                        body=json.dumps(metadata).encode(), headers=headers, retry=False) as response:
            session = response.headers.get('Location', '')
        target = urlsplit(session)
        if (target.scheme != 'https' or target.netloc != 'www.googleapis.com'
                or target.path != '/upload/drive/v3/files' or not target.query or target.fragment):
            raise ValueError('DRIVE_UNTRUSTED_UPLOAD_SESSION')
        offset, recoveries = 0, 0
        while offset < size:
            end = min(offset + CHUNK, size)
            try:
                response = self._chunk(session, payload[offset:end], f'bytes {offset}-{end - 1}/{size}', retry=False)
            except DriveFailure as error:
                if not error.retryable or recoveries >= 3:
                    raise
                self._wait(error, recoveries)
                recoveries += 1
                response = self._chunk(session, b'', f'bytes */{size}', retry=True)
            status = response.status if not isinstance(response, HTTPError) else response.code
            if status in (200, 201):
                return _json(response)
            with response:
                received = response.headers.get('Range')
            match = re.fullmatch(r'bytes=0-([0-9]{1,12})', received) if received else None
            if received and not match:
                raise ValueError('DRIVE_INVALID_UPLOAD_RANGE')
            position = int(match[1]) + 1 if match else 0
            if position < offset or position > end or position >= size:
                raise ValueError('DRIVE_INVALID_UPLOAD_RANGE')
            if position == offset:
                if recoveries >= 3:
                    raise ValueError('DRIVE_UPLOAD_NO_PROGRESS')
                recoveries += 1
            offset = position
        raise ValueError('DRIVE_UPLOAD_UNCONFIRMED')

    def _chunk(self, session, content, length_range, *, retry):
        return self._call('PUT', session, body=content,
                          headers={'Content-Type': 'application/gzip', 'Content-Range': length_range,
                                   'Content-Length': str(len(content))},
                          expected=(200, 201, 308), retry=retry)

    def write_pointer(self, metadata, payload, file_id):
        if file_id is not None:
            return _json(self._call('PATCH', _url(f'{UPLOAD}/{_id(file_id)}', uploadType='media', fields=FIELDS),
                                    body=payload, headers={'Content-Type': 'application/json'}))
        boundary = uuid.uuid4().hex
        marker = b'--' + boundary.encode()
        body = (marker + b'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'
                + json.dumps(metadata).encode() + b'\r\n' + marker
                + b'\r\nContent-Type: application/json\r\n\r\n' + payload + b'\r\n' + marker + b'--\r\n')
        return _json(self._call('POST', _url(UPLOAD, uploadType='multipart', fields=FIELDS), body=body,
                                headers={'Content-Type': f'multipart/related; boundary={boundary}'},
                                expected=(200, 201), retry=False))
