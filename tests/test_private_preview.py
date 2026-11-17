"""Offline regressions only. These fixtures never count as live acceptance."""
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from xcmate_epg.private_preview import run_preview

XML=b'<tv><channel id="one"><display-name>Fixture</display-name><icon src="https://logos.example.invalid/logo.png"/></channel><programme channel="one" start="20261117090000 +0100" stop="20261117100000 +0100"><title>Fixture</title><icon src="https://images.example.invalid/programme.jpg"/></programme></tv>'

class PrivatePreviewTests(unittest.TestCase):
    def setup_case(self,d):
        root=Path(d)
        source=root/'source.xml.gz'
        source.write_bytes(gzip.compress(XML,mtime=0))
        config={'sources':[{'id':'s1','path':str(source)}], 'approved_image_hosts':['logos.example.invalid','images.example.invalid']}
        return root,source,config

    def test_real_mode_requires_input_and_never_uses_demo(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'LIVE_INPUT_REQUIRED'): run_preview({},Path(d)/'private')
            self.assertFalse((Path(d)/'private/manifest.json').exists())

    def test_local_import_counts_images_and_private_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            report=run_preview(config,p/'private')
            self.assertEqual((report['channels'],report['programmes'],report['logos_present'],report['programme_images_present']),(1,1,1,1))
            self.assertEqual(report['status'],'PRIVATE_EPG_CREATED')
            self.assertEqual(report['artwork_status'],'PENDING')
            archive=p/'private/output/xcmate-epg.xml.gz'
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),report['sha256'])
            manifest=json.loads((p/'private/output/manifest.json').read_text())
            self.assertEqual(manifest['status'],'PRIVATE_LIVE_PREVIEW_DO_NOT_PUBLISH')
            self.assertNotIn('example.invalid',json.dumps(manifest))
            self.assertEqual(archive.stat().st_mode & 0o777,0o600)

    def test_unchanged_and_offline_lastgood(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            first=run_preview(config,p/'private')
            second=run_preview(config,p/'private')
            self.assertEqual(second['sha256'],first['sha256'])
            self.assertEqual(second['sources'][0]['cache_status'],'unchanged')
            source.write_bytes(b'broken')
            fallback=run_preview(config,p/'private')
            self.assertEqual(fallback['sha256'],first['sha256'])
            self.assertEqual(fallback['sources'][0]['cache_status'],'lastgood')
            self.assertEqual(fallback['real_sources_successful'],0)

    def test_invalid_data_no_output(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            source.write_bytes(b'not xml')
            with self.assertRaisesRegex(ValueError,'LIVE_INPUT_BLOCKED'): run_preview(config,p/'private')
            self.assertFalse((p/'private/output/manifest.json').exists())

    def test_unsafe_icons_are_not_copied(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            source.write_bytes(XML.replace(b'logo.png',b'logo.png?token=fixture'))
            with self.assertRaisesRegex(ValueError,'LIVE_INPUT_BLOCKED'): run_preview(config,p/'private')

    def test_output_cannot_be_inside_git_outside_ignored_work(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            (p/'.git').mkdir()
            with self.assertRaisesRegex(ValueError,'UNSAFE_PRIVATE_ROOT'): run_preview(config,p/'tracked-output')

    def test_report_never_includes_remote_input_address(self):
        from unittest.mock import patch
        from xcmate_epg.fetch import Response
        with tempfile.TemporaryDirectory() as d:
            config={'sources':[{'id':'s1','url':'https://feed.example.invalid/private.xml.gz'}], 'approved_image_hosts':['logos.example.invalid','images.example.invalid']}
            with patch('xcmate_epg.private_preview.http_get',return_value=Response(200,gzip.compress(XML),{})):
                report=run_preview(config,Path(d)/'private',authorize_network=True)
            self.assertNotIn('feed.example.invalid',json.dumps(report))
            self.assertEqual(report['sources'][0]['http_status'],200)

    def test_network_requires_explicit_flag(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'NETWORK_NOT_AUTHORIZED'):
                run_preview({'sources':[{'id':'s1','url':'https://feed.example.invalid/input'}]},Path(d)/'private')

    def test_live_request_identifies_xcmate_client(self):
        from unittest.mock import patch
        from xcmate_epg.fetch import Response
        with tempfile.TemporaryDirectory() as d:
            config={'sources':[{'id':'s1','url':'https://feed.example.invalid/input'}], 'approved_image_hosts':['logos.example.invalid','images.example.invalid']}
            def transport(url, headers):
                self.assertEqual(headers.get('User-Agent'), 'XCMateEPG/0.1 private-one-shot-preview')
                return Response(200, gzip.compress(XML), {})
            with patch('xcmate_epg.private_preview.http_get',side_effect=transport):
                report=run_preview(config,Path(d)/'private',authorize_network=True)
            self.assertEqual(report['real_sources_successful'],1)

    def test_icon_attributes_cannot_bypass_secret_scan(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            source.write_bytes(XML.replace(b'logo.png"/',b'logo.png" token="fixture"/'))
            with self.assertRaisesRegex(ValueError,'LIVE_INPUT_BLOCKED'): run_preview(config,p/'private')

    def test_private_output_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            (p/'private').mkdir()
            (p/'outside').mkdir()
            (p/'private/output').symlink_to(p/'outside',target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'UNSAFE_PRIVATE_ROOT'): run_preview(config,p/'private')
            self.assertFalse((p/'outside/manifest.json').exists())

    def test_oversized_merged_output_keeps_previous_manifest(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.setup_case(d)
            first=run_preview(config,p/'private')
            second=p/'second.xml'
            second.write_bytes(XML.replace(b'</programme>',b'<desc>'+b'x'*1000+b'</desc></programme>'))
            config['sources'].append({'id':'s2','path':str(second)})
            with patch('xcmate_epg.export.LIMIT',1200,create=True), self.assertRaises(ValueError):
                run_preview(config,p/'private')
            manifest=json.loads((p/'private/output/manifest.json').read_text())
            self.assertEqual(manifest['sha256'],first['sha256'])

    def test_304_keeps_http_validators_for_next_run(self):
        from unittest.mock import patch
        from xcmate_epg.fetch import Response
        with tempfile.TemporaryDirectory() as d:
            config={'sources':[{'id':'s1','url':'https://feed.example.invalid/input'}], 'approved_image_hosts':['logos.example.invalid','images.example.invalid']}
            def transport(url,headers):
                if 'If-None-Match' not in headers: return Response(200,gzip.compress(XML),{'ETag':'revision-1','Last-Modified':'Tue, 17 Nov 2026 08:00:00 GMT'})
                self.assertEqual(headers['If-None-Match'],'revision-1')
                return Response(304,b'',{})
            with patch('xcmate_epg.private_preview.http_get',side_effect=transport) as fetch:
                for _ in range(3): run_preview(config,Path(d)/'private',authorize_network=True)
                self.assertEqual(fetch.call_args.args[1]['If-None-Match'],'revision-1')
