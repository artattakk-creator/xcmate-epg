import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from xcmate_epg.full_export import publish_stream, verify_archive


def channel(): return ET.fromstring('<channel id="s:one"><display-name>Fixture</display-name></channel>')
def event(): return ET.fromstring('<programme channel="s:one" start="20261117100000 +0100" stop="20261117110000 +0100"><title>Fixture</title><desc>Preserve</desc></programme>')


class FullExportTests(unittest.TestCase):
    def test_deterministic_verified_export_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            first=publish_stream([channel()],[event()],root,status='ARTWORK_BLOCKED',view='full')
            before=(root/'manifest.json').stat().st_mtime_ns
            second=publish_stream([channel()],[event()],root,status='ARTWORK_BLOCKED',view='full')
            self.assertEqual(first,second)
            self.assertEqual((root/'manifest.json').stat().st_mtime_ns,before)
            self.assertEqual(hashlib.sha256((root/'xcmate-epg.xml.gz').read_bytes()).hexdigest(),first['sha256'])
            self.assertEqual(verify_archive(root/'xcmate-epg.xml.gz')['programmes'],1)

    def test_failed_export_preserves_manifest_and_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            first=publish_stream([channel()],[event()],root,status='PARTIAL_COVERAGE',view='full')
            before=(root/'manifest.json').read_bytes()
            with self.assertRaises(ValueError):
                publish_stream([channel()],[event()],root,status='PARTIAL_COVERAGE',view='full',max_xml_bytes=10)
            self.assertEqual((root/'manifest.json').read_bytes(),before)
            self.assertTrue((root/first['file']).is_file())

    def test_missing_channel_and_secret_bearing_image_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); bad=event(); bad.set('channel','missing')
            with self.assertRaises(ValueError): publish_stream([channel()],[bad],root)
            bad=event(); ET.SubElement(bad,'icon',{'src':'https://image.tmdb.org/t/p/w500/a.jpg?token=fixture'})
            with self.assertRaises(ValueError): publish_stream([channel()],[bad],root)
            self.assertFalse((root/'manifest.json').exists())

    def test_private_empty_device_view_is_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            r=publish_stream([],[],Path(d),view='device',status='PARTIAL_COVERAGE')
            self.assertEqual((r['channels'],r['programmes']),(0,0))
            self.assertEqual(r['status'],'PARTIAL_COVERAGE')
            with self.assertRaises(ValueError): publish_stream([],[],Path(d)/'full',view='full')

    def test_output_exceeds_old_tree_limit_with_bounded_records(self):
        with tempfile.TemporaryDirectory() as d:
            def rows():
                for i in range(60):
                    p=event(); p.find('desc').text='x'*(600*1024); ET.SubElement(p,'sub-title').text=str(i)
                    yield p
            result=publish_stream([channel()],rows(),Path(d))
            self.assertGreater(result['xml_bytes'],32*1024*1024)
            self.assertEqual(result['programmes'],60)

    def test_manifest_failure_restores_current_aliases(self):
        from xcmate_epg.export import atomic
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); publish_stream([channel()],[event()],root)
            before={name:(root/name).read_bytes() for name in ('manifest.json','xcmate-epg.xml.gz','xcmate-epg.xml.gz.sha256')}
            changed=event(); changed.find('desc').text='Changed'
            def fail_manifest(path,data):
                if path==root/'manifest.json': raise OSError('synthetic commit failure')
                return atomic(path,data)
            with patch('xcmate_epg.full_export.atomic',side_effect=fail_manifest),self.assertRaises(OSError):
                publish_stream([channel()],[changed],root)
            for name,payload in before.items(): self.assertEqual((root/name).read_bytes(),payload,name)


if __name__=='__main__': unittest.main()
