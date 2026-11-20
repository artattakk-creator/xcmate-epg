from email.message import Message
import io
import json
import unittest
from urllib.error import HTTPError, URLError

from xcmate_epg import drive_api


class Reply(io.BytesIO):
    def __init__(self, data=b'', status=200, **headers):
        super().__init__(data)
        self.status = status
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name.replace('_', '-')] = value


class DriveAPITests(unittest.TestCase):
    def test_resumable_upload_queries_progress_after_connection_failure(self):
        begin = drive_api.UPLOAD + '?uploadType=resumable'
        session = drive_api.UPLOAD + '?uploadType=resumable&upload_id=fixture'
        ranges = []

        def opener(request, timeout):
            if request.get_method() == 'POST':
                self.assertIn(begin, request.full_url)
                return Reply(Location=session)
            self.assertEqual(request.full_url, session)
            self.assertEqual(request.get_method(), 'PUT')
            ranges.append(request.get_header('Content-range'))
            if len(ranges) == 1:
                raise URLError('simulated connection failure')
            if len(ranges) == 2:
                headers = Message()
                headers['Range'] = 'bytes=0-2'
                raise HTTPError(session, 308, '', headers, io.BytesIO())
            return Reply(json.dumps({'id': 'fixture-file'}).encode(), status=201)

        client = drive_api.Drive('local-test-binding', opener=opener, sleep=lambda seconds: None)
        self.assertEqual(client.upload({'name': 'test'}, b'abcdef'), {'id': 'fixture-file'})
        self.assertEqual(ranges, ['bytes 0-5/6', 'bytes */6', 'bytes 3-5/6'])

    def test_non_google_session_is_rejected_before_token_is_forwarded(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.get_method())
            return Reply(Location='https://example.invalid/receive')

        client = drive_api.Drive('local-test-binding', opener=opener)
        with self.assertRaisesRegex(ValueError, 'DRIVE_UNTRUSTED_UPLOAD_SESSION'):
            client.upload({'name': 'test'}, b'fixture')
        self.assertEqual(calls, ['POST'])

    def test_429_honors_bounded_retry_and_403_is_terminal(self):
        calls = []
        waits = []
        headers = Message()
        headers['Retry-After'] = '120'

        def opener(request, timeout):
            calls.append(request.get_method())
            status = 429 if len(calls) == 1 else 403
            raise HTTPError(request.full_url, status, '', headers, io.BytesIO())

        client = drive_api.Drive('local-test-binding', opener=opener, sleep=waits.append)
        with self.assertRaisesRegex(ValueError, 'DRIVE_HTTP_403'):
            client.metadata('folder')
        self.assertEqual(calls, ['GET', 'GET'])
        self.assertEqual(waits, [10])
