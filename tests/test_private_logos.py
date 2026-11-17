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

class PixelStreamRegressions(unittest.TestCase):
    def test_crc_correct_invalid_pixels_and_illegal_headers_are_not_verified(self):
        def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        header=struct.pack('>IIBBBBB',1,1,8,2,0,0,0)
        cases=[(header,b'not zlib data'),(header,zlib.compress(b'\x05\x00\x00\x00')),
               (header,zlib.compress(b'\x00')),(header,zlib.compress(b'\x00'*1000000)),
               (struct.pack('>IIBBBBB',1,1,3,2,0,0,0),zlib.compress(b'\x00\x00\x00\x00'))]
        for ihdr,pixels in cases:
            with self.subTest(header=ihdr,pixels=len(pixels)),tempfile.TemporaryDirectory() as d:
                root=Path(d); data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'IDAT',pixels)+chunk(b'IEND',b'')
                sha=hashlib.sha256(data).hexdigest(); (root/(sha+'.png')).write_bytes(data)
                inventory={'records':[{'cache_key':'f','sha256':sha,'width':1,'height':1}],
                    'provider_bindings':[{'provider_id':'f','cache_key':'f','sha256':sha,'evidence':'explicit'}]}
                path=root/'inventory.json'; path.write_text(json.dumps(inventory))
                result=verify_provider_logos(path,root,root/'out')
                self.assertEqual(result['verified_provider_logos'],0)
                self.assertEqual(result['invalid_logo_records'],1)
                self.assertFalse(list((root/'out/assets/logos').glob('*.png')))

    def test_common_png_modes_adam7_and_multiple_idat_are_supported(self):
        from xcmate_epg.private_logos import _png_dimensions
        def chunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        for color,depth,pixel_bytes in ((0,1,1),(0,16,2),(2,8,3),(2,16,6),(3,4,1),(4,8,2),(6,8,4),(6,16,8)):
            for interlace in (0,1):
                with self.subTest(color=color,depth=depth,interlace=interlace):
                    ihdr=struct.pack('>IIBBBBB',1,1,depth,color,0,0,interlace)
                    compressed=zlib.compress(b'\0'+b'\0'*pixel_bytes)
                    palette=chunk(b'PLTE',b'\0\0\0') if color==3 else b''
                    data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+palette+chunk(b'IDAT',compressed[:3])+chunk(b'IDAT',compressed[3:])+chunk(b'IEND',b'')
                    self.assertEqual(_png_dimensions(data),(1,1))
