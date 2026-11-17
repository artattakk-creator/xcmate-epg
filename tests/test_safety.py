import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from xcmate_epg.artwork import TMDB, choose_series, enrich_episode
from xcmate_epg.fetch import Response
from xcmate_epg.state import State
from xcmate_epg.export import publish
from xcmate_epg.xmltv import parse
from xcmate_epg.cli import run
from test_agent import XML

class SafetyTests(unittest.TestCase):
    def test_export_rejects_sensitive_text(self):
        for sensitive in ['https://private.invalid/feed','Bearer hidden','password=hidden']:
            root=parse(XML,'a')
            ET.SubElement(root.find('programme'),'desc').text=sensitive
            with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError): publish(root,Path(d))

    def test_manifest_commit_survives_interruption(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            first=publish(parse(XML,'a'),p)
            real_replace=__import__('os').replace
            def fail_manifest(src,dst):
                if Path(dst).name=='manifest.json': raise OSError('simulated interruption')
                real_replace(src,dst)
            with patch('xcmate_epg.export.os.replace',side_effect=fail_manifest), self.assertRaises(OSError):
                publish(parse(XML.replace(b'>Demo<',b'>Changed<'),'a'),p)
            self.assertEqual(json.loads((p/'manifest.json').read_text()),first)
            self.assertTrue((p/first['file']).is_file())

    def test_tmdb_authorization_and_errors(self):
        def transport(url,headers):
            self.assertEqual(headers,{'Authorization':'Bearer test-placeholder'})
            self.assertNotIn('test-placeholder',url)
            return Response(200,b'{"results":[]}',{})
        self.assertEqual(TMDB('test-placeholder',transport).search('Demo',2020),[])
        for status in [401,403,429,500]:
            with self.assertRaisesRegex(ValueError,'metadata lookup failed'):
                TMDB('test-placeholder',lambda u,h:Response(status,b'',{})).search('Demo',2020)

    def test_series_episode(self):
        hit={'id':9,'name':'Demo','first_air_date':'2020-01-01'}
        self.assertEqual(choose_series('Demo',2020,[hit])['id'],9)
        self.assertIsNone(choose_series('Demo',2020,[hit,dict(hit,id=10)]))
        root=parse(XML.replace(b'<date>2020</date>',b'<date>2020</date><category>series</category><episode-num system="xmltv_ns">0.1.</episode-num>'),'a')
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'state') as s:
            def episode(series,season,number):
                self.assertEqual((series,season,number),(9,1,2))
                return {'id':10,'season_number':1,'episode_number':2,'name':'Episode','still_path':'/still.jpg'}
            enrich_episode(root,s,lambda t,y:[hit],episode)
            self.assertEqual(root.findtext('programme/episode-num[@system="tmdb"]'),'tv/9/1/2')
            self.assertEqual(root.find('programme/icon').get('src'),'https://image.tmdb.org/t/p/w500/still.jpg')

    def test_synthetic_cli_and_cache_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            first=run(p/'state.sqlite',p/'out')
            self.assertEqual(first['programmes'],1)
            self.assertEqual(run(p/'other.sqlite',p/'out'),first)

    def test_invalid_config_blocks_network(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            cfg=p/'cfg.json'
            cfg.write_text(json.dumps({'sources':[{'id':'a','url':'https://private.invalid/feed'}]}))
            with patch('xcmate_epg.cli.http_get') as fetch, self.assertRaises(ValueError):
                run(p/'s',p/'o',cfg)
            fetch.assert_not_called()
