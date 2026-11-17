import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xcmate_epg.state import State
from xcmate_epg.fetch import refresh,Response
from xcmate_epg.export import publish
from xcmate_epg.xmltv import parse
from test_agent import XML

class IncrementalTests(unittest.TestCase):
    def test_unchanged_source_skips_parsing(self):
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as state:
            refresh(state,'a',lambda h:Response(200,XML,{}))
            with patch('xcmate_epg.fetch.parse',side_effect=AssertionError('unnecessary parse')):
                self.assertEqual(refresh(state,'a',lambda h:Response(304,b'',{}))[1],'unchanged')
                self.assertEqual(refresh(state,'a',lambda h:Response(200,XML,{}))[1],'unchanged')

    def test_unchanged_export_does_not_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            root=parse(XML,'a')
            first=publish(root,Path(d))
            with patch('xcmate_epg.export.atomic',side_effect=AssertionError('unnecessary write')):
                self.assertEqual(publish(root,Path(d)),first)

    def test_alias_change_rebuilds_normalized_snapshot(self):
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as state:
            refresh(state,'a',lambda h:Response(200,XML,{}))
            root,status=refresh(state,'a',lambda h:Response(304,b'',{}),{'a:one':'canonical'})
            self.assertEqual(root.find('channel').get('id'),'canonical')

    def test_source_threshold_preserves_previous_manifest(self):
        import json
        from xcmate_epg.cli import run
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            first=run(p/'s',p/'out')
            (p/'good.xml').write_bytes(XML)
            config=p/'config.json'
            config.write_text(json.dumps({'minimum_sources':2,'sources':[{'id':'good','path':str(p/'good.xml')},{'id':'missing','path':str(p/'bad.xml')}]}))
            with self.assertRaises(ValueError): run(p/'s',p/'out',config)
            self.assertEqual(json.loads((p/'out/manifest.json').read_text()),first)
