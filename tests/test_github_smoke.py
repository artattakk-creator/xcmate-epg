import base64
import hashlib
import unittest

from xcmate_epg import github_smoke


class GitHubSmokeTests(unittest.TestCase):
    def test_reader_retrieves_exact_immutable_commit(self):
        fixture = b'XCMATE_PRIVATE_UPLOAD_SMOKE_OK\nRun: 42\n'
        revision = 'a' * 40
        path = '/contents/tests/agent-upload-smoke.txt'
        calls = []

        def request(token, method, route, data=None):
            calls.append((token, method, route))
            if route == '':
                return 200, {'private': True}
            if token is None:
                return 404, {}
            if method == 'PUT':
                self.assertEqual(data['branch'], 'main')
                self.assertEqual(base64.b64decode(data['content']), fixture)
                return 201, {'commit': {'sha': revision}}
            if token == 'publisher-binding':
                return 404, {}
            return 200, {'content': base64.b64encode(fixture).decode()}

        evidence = github_smoke.probe('publisher-binding', 'reader-binding', '42', request=request)
        self.assertEqual(calls[-1], ('reader-binding', 'GET', path + '?ref=' + revision))
        self.assertEqual(evidence['sha256'], hashlib.sha256(fixture).hexdigest())
        self.assertEqual(evidence['commit'], revision)

    def test_anonymous_unexpected_response_keeps_actual_status_and_forbids_write(self):
        def request(token, method, route, data=None):
            self.assertEqual(method, 'GET')
            return (200, {'private': True}) if token else (403, {})

        with self.assertRaisesRegex(ValueError, 'ANONYMOUS_CONTENTS_HTTP_403'):
            github_smoke.probe('publisher-binding', 'reader-binding', '42', request=request)

    def test_same_or_absent_bindings_do_not_contact_the_repository(self):
        def request(*args):
            self.fail('no network when bindings are invalid')

        for publisher, reader in (('same', 'same'), ('value', ''), ('', 'value')):
            with self.subTest(publisher=bool(publisher), reader=bool(reader)):
                with self.assertRaisesRegex(ValueError, 'GITHUB_BINDINGS'):
                    github_smoke.probe(publisher, reader, '42', request=request)

    def test_unrelated_file_is_preserved(self):
        def request(token, method, route, data=None):
            self.assertEqual(method, 'GET')
            if route == '':
                return 200, {'private': True}
            if token is None:
                return 404, {}
            return 200, {'sha': 'a' * 40, 'content': base64.b64encode(b'unrelated').decode()}

        with self.assertRaisesRegex(ValueError, 'UNRELATED_SMOKE_FIXTURE'):
            github_smoke.probe('publisher-binding', 'reader-binding', '42', request=request)
