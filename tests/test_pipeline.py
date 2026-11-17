import tempfile
import unittest
from pathlib import Path
from xcmate_epg.pipeline import aggregate
from xcmate_epg.xmltv import parse
from xcmate_epg.state import State
from test_agent import XML

class PipelineTests(unittest.TestCase):
    def test_metadata_details_logo_and_export_pipeline(self):
        class Metadata:
            calls=0
            def search(self,title,year):
                return [{'id':7,'title':'Demo','release_date':'2020-01-01'}]
            def movie(self,identifier):
                self.calls+=1
                return {'id':7,'title':'Demo','original_title':'Original','original_language':'en','overview':'Synopsis','genres':[{'name':'Drama'}],'vote_average':8.2,'poster_path':'/poster.jpg'}
            def search_series(self,*args): return []
            def episode(self,*args): raise AssertionError()
        metadata=Metadata()
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as state:
            tree,report=aggregate([(1,parse(XML,'a'))],state,metadata,
                logos={'a:one':'https://image.tmdb.org/t/p/w500/logo.png'})
            self.assertEqual(tree.findtext('programme/desc'),'Synopsis')
            self.assertEqual(tree.findtext('programme/title[@lang="en"]'),'Original')
            self.assertEqual(tree.findtext('programme/star-rating/value'),'8.2/10')
            self.assertEqual(tree.find('channel/icon').get('src'),'https://image.tmdb.org/t/p/w500/logo.png')
            self.assertEqual(report['conflicts'],0)
            aggregate([(1,parse(XML,'a'))],state,metadata)
            self.assertEqual(metadata.calls,1)

    def test_export_orders_xmltv_children_and_removes_source_metadata(self):
        from xml.etree import ElementTree as ET
        from xcmate_epg.export import publish
        import gzip
        with tempfile.TemporaryDirectory() as d:
            root=parse(XML,'a')
            root.set('source-info-name','internal processing label')
            programme=root.find('programme')
            ET.SubElement(programme,'title',{'lang':'en'}).text='Original'
            result=publish(root,Path(d))
            exported=ET.fromstring(gzip.decompress((Path(d)/result['file']).read_bytes()))
            self.assertNotIn('source-info-name',exported.attrib)
            self.assertEqual([n.tag for n in exported.find('programme')][:2],['title','title'])
