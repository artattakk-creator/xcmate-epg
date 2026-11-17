import json
import tempfile
import unittest
from pathlib import Path
from xcmate_epg.cli import run
from xcmate_epg.xmltv import parse,merge
from xcmate_epg.artwork import choose
from test_agent import XML

class ResilienceTests(unittest.TestCase):
    def test_missing_source_does_not_discard_good_source(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            (p/'good.xml').write_bytes(XML)
            config=p/'config.json'
            config.write_text(json.dumps({'sources':[{'id':'missing','path':str(p/'missing.xml')},{'id':'good','path':str(p/'good.xml')}]}))
            self.assertEqual(run(p/'s',p/'out',config)['channels'],1)

    def test_overlap_conflict_uses_priority(self):
        a=parse(XML,'a',{'a:one':'c'})
        b=parse(XML.replace(b'090000',b'093000').replace(b'100000',b'103000'),'b',{'b:one':'c'})
        root,conflicts=merge([(1,a),(2,b)])
        self.assertEqual(len(root.findall('programme')),1)
        self.assertEqual(conflicts,1)

    def test_invalid_api_candidates_are_not_matches(self):
        for hits in [[None],[{'id':1,'title':'Demo','release_date':None}],[{'id':True,'title':'Demo','release_date':'2020-01-01'}]]:
            self.assertIsNone(choose('Demo',2020,hits))

    def test_invalid_deflate_preserves_snapshot(self):
        from xcmate_epg.state import State
        from xcmate_epg.fetch import refresh,Response
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as state:
            refresh(state,'a',lambda h:Response(200,XML,{}))
            result,status=refresh(state,'a',lambda h:Response(200,bytes.fromhex('1f8b080000000000000006'),{}))
            self.assertEqual(status,'lastgood')
            self.assertEqual(result.findtext('programme/title'),'Demo')

    def test_nonstring_image_path_is_validation_error(self):
        from xcmate_epg.artwork import image_url
        with self.assertRaises(ValueError): image_url(42)
