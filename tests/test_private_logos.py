import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from xcmate_epg.private_logos import verify_provider_logos


def png():
    def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\x00\x00\x00'))+chunk(b'IEND',b'')


class PrivateLogoTests(unittest.TestCase):
    def test_bound_logo_verified_but_no_feed_mapping_invented(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); files=root/'files'; files.mkdir(); data=png(); sha=hashlib.sha256(data).hexdigest()
            (files/(sha+'.png')).write_bytes(data)
            inventory={'records':[{'cache_key':'fixture','sha256':sha,'width':1,'height':1}],
                       'provider_bindings':[{'provider_id':'fixture-provider','cache_key':'fixture','sha256':sha,'evidence':'explicit backup binding'}]}
            path=root/'inventory.json'; path.write_text(json.dumps(inventory))
            result=verify_provider_logos(path,files,root/'private')
            self.assertEqual(result['verified_provider_logos'],1)
            self.assertEqual(result['xmltv_logos_exported'],0)
            record=json.loads((root/'private/provider-logo-metadata.jsonl').read_text())
            self.assertFalse(record['feed_mapping_confirmed'])
            self.assertTrue(record['verified'])

    def test_crc_and_manifest_dimension_mismatch_do_not_count(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); data=png(); sha=hashlib.sha256(data).hexdigest(); (root/(sha+'.png')).write_bytes(data)
            path=root/'inventory.json'; path.write_text(json.dumps({'records':[{'cache_key':'f','sha256':sha,'width':2,'height':1}],
                'provider_bindings':[{'provider_id':'f','cache_key':'f','sha256':sha,'evidence':'explicit'}]}))
            result=verify_provider_logos(path,root,root/'out')
            self.assertEqual(result['verified_provider_logos'],0)
            self.assertEqual(result['invalid_logo_records'],1)


if __name__=='__main__': unittest.main()
