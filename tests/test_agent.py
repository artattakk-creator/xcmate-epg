import gzip
import json
import tempfile
import unittest
from pathlib import Path
from xcmate_epg.xmltv import parse, merge, normalize_name
from xcmate_epg.state import State
from xcmate_epg.fetch import refresh, Response
from xcmate_epg.artwork import choose, image_url, enrich
from xcmate_epg.export import publish

XML = b'<tv><channel id="one"><display-name>Demo HD</display-name></channel><programme channel="one" start="20261117090000 +0100" stop="20261117100000 +0100"><title>Demo</title><date>2020</date><category>movie</category></programme></tv>'

class AgentTests(unittest.TestCase):
    def test_parse_and_source_isolation(self):
        a, b = parse(XML, 'a'), parse(XML, 'b')
        root, conflicts = merge([(1,a),(2,b)])
        self.assertEqual(len(root.findall('channel')), 2)
        self.assertEqual(len(root.findall('programme')), 2)
        self.assertNotEqual(a.find('channel').get('id'), b.find('channel').get('id'))
        self.assertEqual(normalize_name('Demo HD'), 'demo')

    def test_bad_xml(self):
        for data in [b'', b'<tv/>', b'<!DOCTYPE tv><tv/>', XML.replace(b'+0100',b'+2500'), XML.replace(b' +0100',b''), XML.replace(b'100000',b'080000'), XML.replace(b'</tv>', b'<channel id="one"/></tv>'), XML.replace(b'channel="one"',b'channel="missing"')]:
            with self.subTest(data=data), self.assertRaises(ValueError): parse(data,'a')

    def test_gzip_and_limits(self):
        self.assertEqual(parse(gzip.compress(XML),'a').tag, 'tv')
        for data in [b'\x1f\x8bbroken', gzip.compress(b' ' * 1000)]:
            with self.assertRaises(ValueError): parse(data,'a',limit=500)

    def test_confirmed_alias_and_priority(self):
        a = parse(XML,'a',{'a:one':'canonical'})
        b = parse(XML.replace(b'>Demo<', b'>Other<'),'b',{'b:one':'canonical'})
        root, conflicts = merge([(2,b),(1,a)])
        self.assertEqual(len(root.findall('programme')),1)
        self.assertEqual(root.findtext('programme/title'),'Demo')
        self.assertEqual(conflicts,1)

    def test_persistence_304_and_invalid_lastgood(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'state.sqlite'
            with State(p) as state:
                self.assertEqual(refresh(state,'a',lambda h:Response(200,XML,{'ETag':'v1'}))[1],'changed')
            with State(p) as state:
                def unchanged(headers):
                    self.assertEqual(headers['If-None-Match'],'v1')
                    return Response(304,b'',{})
                self.assertEqual(refresh(state,'a',unchanged)[1],'unchanged')
                self.assertEqual(refresh(state,'a',lambda h:Response(200,b'bad',{'ETag':'v2'}))[1],'lastgood')
                self.assertEqual(state.source('a')['etag'],'v1')
                self.assertEqual(refresh(state,'a',lambda h:Response(200,XML,{}))[1],'unchanged')
                def timeout(h): raise TimeoutError()
                self.assertEqual(refresh(state,'a',timeout)[1],'lastgood')

    def test_retry_and_missing(self):
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as s:
            calls=[]
            def fetch(h):
                calls.append(h)
                return Response(429 if len(calls)<3 else 200,XML,{})
            self.assertEqual(refresh(s,'a',fetch,sleep=lambda n:None)[1],'changed')
            self.assertEqual(len(calls),3)
            with self.assertRaises(ValueError): refresh(s,'b',lambda h:Response(304,b'',{}))

    def test_artwork_matching(self):
        hit={'id':7,'title':'Demo','release_date':'2020-01-01','poster_path':'/p.jpg'}
        self.assertEqual(choose('Demo',2020,[hit])['id'],7)
        self.assertIsNone(choose('Demo',2020,[hit,dict(hit,id=8)]))
        self.assertIsNone(choose('Demo',2021,[hit]))
        self.assertIsNone(image_url(None))
        self.assertEqual(image_url('/p.jpg'),'https://image.tmdb.org/t/p/w500/p.jpg')
        for bad in ['https://evil.invalid/x','//evil.invalid/x','/p.jpg?api_key=hidden']:
            with self.assertRaises(ValueError): image_url(bad)

    def test_artwork_cache(self):
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as s:
            calls=[]
            def search(title,year):
                calls.append(title)
                return [{'id':7,'title':'Demo','release_date':'2020-01-01'}]
            root=parse(XML,'a')
            enrich(root,s,search)
            enrich(root,s,search)
            self.assertEqual(calls,['Demo'])
            self.assertEqual(root.findtext('programme/episode-num[@system="tmdb"]'),'movie/7')
            self.assertIsNone(root.find('programme/icon'))

    def test_export_integrity_and_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            root=parse(XML,'a')
            manifest=publish(root,Path(d))
            data=(Path(d)/manifest['file']).read_bytes()
            self.assertEqual(gzip.decompress(data)[:3],b'<?x')
            self.assertEqual(publish(root,Path(d)),manifest)
            before=(Path(d)/'manifest.json').read_bytes()
            root.find('programme').set('stop','bad')
            with self.assertRaises(ValueError): publish(root,Path(d))
            self.assertEqual((Path(d)/'manifest.json').read_bytes(),before)

    def test_public_image_policy(self):
        for url in ['http://image.tmdb.org/a','https://image.tmdb.org/a?token=x','https://private.invalid/x']:
            data=XML.replace(b'</programme>', f'<icon src="{url}"/></programme>'.encode())
            with self.assertRaises(ValueError): parse(data,'a')
