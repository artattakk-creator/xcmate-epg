import tempfile
import unittest
from pathlib import Path
from xcmate_epg.artwork import TMDB, TVMaze, enrich
from xcmate_epg.fetch import Response
from xcmate_epg.state import State
from xcmate_epg.xmltv import parse
from test_agent import XML

class MetadataTests(unittest.TestCase):
    def test_unknown_media_never_movie_matched(self):
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as state:
            def forbidden(t,y): self.fail('unknown media searched as movie')
            enrich(parse(XML.replace(b'<category>movie</category>',b''),'a'),state,forbidden)

    def test_positive_negative_cache_across_parsed_trees(self):
        with tempfile.TemporaryDirectory() as d, State(Path(d)/'s') as state:
            calls=[]
            def search(t,y): calls.append(t); return []
            enrich(parse(XML,'a'),state,search)
            enrich(parse(XML,'a'),state,search)
            self.assertEqual(len(calls),1)

    def test_tmdb_series_episode_and_bounded_retry(self):
        calls=[]
        def transport(url,h):
            calls.append(url)
            if len(calls)==1: return Response(429,b'',{})
            return Response(200,b'{"results":[],"season_number":1,"episode_number":2}',{})
        api=TMDB('test-placeholder',transport,sleep=lambda n:None)
        self.assertEqual(api.search_series('Demo',2020),[])
        self.assertEqual(api.episode(9,1,2)['episode_number'],2)
        self.assertIn('/tv/9/season/1/episode/2',calls[-1])
        self.assertEqual(len(calls),3)

    def test_tvmaze_ambiguous_results_preserved(self):
        def transport(url,h):
            self.assertNotIn('Authorization',h)
            return Response(200,b'[{"show":{"id":1,"name":"Demo","premiered":"2020-01-01"}},{"show":{"id":2,"name":"Demo","premiered":"2020-02-01"}}]',{})
        hits=TVMaze(transport).search_series('Demo',2020)
        self.assertEqual(len(hits),2)
        self.assertEqual(hits[0]['first_air_date'],'2020-01-01')
