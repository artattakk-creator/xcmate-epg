"""Offline TMDB integration fixtures; no credentials or external requests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from image_fixtures import PNG
from test_live_artwork import event
from xcmate_epg.artwork import TMDB
from xcmate_epg.fetch import Response
from xcmate_epg.full_preview import run_full
from xcmate_epg.live_artwork import ArtworkEnricher
from xcmate_epg.state import State


class TMDBFixture:
    def __init__(self):
        self.hits = [dict(id=42, name='Example', original_name='Original Example', first_air_date='2010-02-03')]
        self.show = dict(id=42, name='Example', original_name='Original Example', original_language='en',
                         first_air_date='2010-02-03', overview='Verified show summary',
                         genres=[{'id':18,'name':'Drama'}], vote_average=8.1,
                         poster_path='/show.png', backdrop_path='/back.png')
        self.ep = dict(id=123, name='Episode Two', season_number=1, episode_number=2,
                       air_date='2020-01-04', overview='Verified episode summary', vote_average=7.5,
                       still_path='/still.png')
        self.season = dict(id=777, name='Season 1', season_number=1, poster_path='/season.png')
        self.calls = []
        self.failures = {}

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        parts=urlsplit(url)
        if parts.hostname=='image.tmdb.org':
            if headers: raise AssertionError('CDN credentials')
            return Response(200, PNG, {'Content-Type':'image/png'})
        if headers: raise AssertionError('managed HTTP must be header-free')
        path=parts.path
        if path in self.failures:
            return Response(self.failures[path],b'',{})
        if path=='/3/search/tv': body={'results':self.hits}
        elif path=='/3/tv/42': body=self.show
        elif path=='/3/tv/42/season/1': body=self.season
        elif path=='/3/tv/42/season/1/episode/2': body=self.ep
        else: raise AssertionError('unexpected synthetic API path '+path)
        return Response(200,json.dumps(body).encode(),{})

    def api_paths(self):
        return [urlsplit(u).path for u,h in self.calls if urlsplit(u).hostname=='api.themoviedb.org']


class TMDBIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.state=State(Path(self.tmp.name)/'art.sqlite')
        self.fixture=TMDBFixture()
    def tearDown(self):
        self.state.db.close(); self.tmp.cleanup()
    def adapter(self, **config):
        return ArtworkEnricher(self.state,dict(request_interval=0,**config),
                               tmdb=TMDB(managed_http=True),transport=self.fixture)

    def test_managed_clone_uses_bounded_header_free_transport(self):
        result, records=self.adapter().enrich(*event(category='series',year='2010'))
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'tv/42')
        self.assertEqual({r['type'] for r in records},{'poster','backdrop'})
        self.assertTrue(all(r['verified'] and r['sha256'] and r['size_bytes']==len(PNG) for r in records))
        self.assertEqual(self.fixture.api_paths(),['/3/search/tv','/3/tv/42'])
        self.assertEqual(result.findtext('desc'),'Verified show summary')
        self.assertIn('Original Example',[n.text for n in result.findall('title')])
        self.assertEqual(result.findtext('star-rating/value'),'8.1/10')
        self.assertIn('Drama',[n.text for n in result.findall('category')])

    def test_series_unknown_year_can_match_unique_exact_and_fill_premiere(self):
        result, records=self.adapter().enrich(*event(category='series',year=''))
        self.assertEqual(result.findtext('date'),'2010')
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'tv/42')
        self.assertTrue(records)

    def test_series_rejects_wrong_year_id_title_original_name_or_ambiguity(self):
        changes=[('year',None),('id',99),('name','Other'),('original_name','Other'),('first_air_date','2011-01-01'),('ambiguous',None)]
        for key,value in changes:
            with self.subTest(key=key):
                self.state.db.execute('DELETE FROM artwork'); self.fixture=TMDBFixture()
                year='2011' if key=='year' else '2010'
                if key=='ambiguous': self.fixture.hits.append(dict(self.fixture.hits[0],id=43))
                elif key!='year': self.fixture.show[key]=value
                result,records=self.adapter().enrich(*event(category='series',year=year))
                self.assertEqual(records,[])
                self.assertIsNone(result.find('episode-num[@system="tmdb"]'))
                self.assertIsNone(result.find('desc'))

    def test_positive_and_negative_series_cache_reuse(self):
        adapter=self.adapter()
        self.assertTrue(adapter.enrich(*event(category='series',year='2010'))[1])
        count=len(self.fixture.calls)
        adapter.enrich(*event(category='series',year='2010'))
        self.assertEqual(len(self.fixture.calls),count)
        self.fixture.hits=[]
        self.assertEqual(adapter.enrich(*event(category='series',year='2001'))[1],[])
        count=len(self.fixture.calls)
        adapter.enrich(*event(category='series',year='2001'))
        self.assertEqual(len(self.fixture.calls),count)

    def test_malformed_premiere_cannot_prove_series(self):
        for bad_date in ('2010-nonsense', '2010-02-30', ''):
            with self.subTest(date=bad_date):
                self.state.db.execute('DELETE FROM artwork'); self.fixture=TMDBFixture()
                self.fixture.hits[0]['first_air_date']=bad_date
                self.fixture.show['first_air_date']=bad_date
                result,records=self.adapter().enrich(*event(category='series',year='2010'))
                self.assertEqual(records,[])
                self.assertIsNone(result.find('episode-num[@system="tmdb"]'))

    def test_transient_optional_season_is_retried_while_positive_details_are_reused(self):
        self.fixture.failures['/3/tv/42/season/1']=503
        adapter=self.adapter()
        first,records=adapter.enrich(*event(episode=True,category='series'))
        self.assertTrue(next(r for r in records if r['type']=='episode_still')['verified'])
        self.assertFalse(any(r.get('scope')=='season' for r in records))
        self.fixture.failures.clear()
        self.fixture.calls.clear()
        result,records=adapter.enrich(*event(episode=True,category='series'))
        self.assertTrue(next(r for r in records if r.get('scope')=='season')['verified'])
        self.assertEqual(self.fixture.api_paths(),['/3/tv/42/season/1'])

    def test_series_transient_detail_failure_retries_without_negative_cache(self):
        self.fixture.failures['/3/tv/42']=503
        adapter=self.adapter()
        self.assertEqual(adapter.enrich(*event(category='series',year='2010'))[1],[])
        self.fixture.failures.clear()
        result,records=adapter.enrich(*event(category='series',year='2010'))
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'tv/42')
        self.assertTrue(records)

    def test_episode_verified_series_and_season_keep_all_identity_evidence(self):
        channel,p=event(episode=True,category='series',year='20200104')
        p.find('episode-num').text=' 0 / 3 . 1 / 10 . 0/1 '
        ET.SubElement(p,'episode-num',system='source').text='opaque-source-id'
        ET.SubElement(p,'episode-num',system='tmdb').text='original-source-value'
        ET.SubElement(p,'desc').text='Source description'
        ET.SubElement(p,'star-rating',system='source').append(ET.fromstring('<value>5/5</value>'))
        old=copy.deepcopy(p)
        result,records=self.adapter().enrich(channel,p)
        self.assertEqual({(r['type'],r.get('scope')) for r in records},
                         {('episode_still',None),('poster','series'),('backdrop','series'),('poster','season')})
        for r in records:
            self.assertEqual(r['evidence']['episode_id'],123)
            self.assertEqual(r['evidence']['episode_airdate'],'2020-01-04')
            self.assertEqual(r['evidence']['episode_year_validation'],'exact')
            self.assertTrue(r['verified'])
        for node in old:
            self.assertTrue(any(ET.tostring(node)==ET.tostring(n) for n in result if n.tag==node.tag))
        self.assertEqual(result.attrib,p.attrib)
        self.assertEqual(result.findtext('star-rating/value'),'5/5')
        self.assertIn('Drama',[n.text for n in result.findall('category')])

    def test_optional_details_or_season_failure_cannot_erase_verified_episode(self):
        for broken in ('details','season','wrong_season'):
            with self.subTest(broken=broken):
                self.state.db.execute('DELETE FROM artwork'); self.fixture=TMDBFixture()
                if broken=='details': self.fixture.show['id']=90
                elif broken=='season': self.fixture.failures['/3/tv/42/season/1']=503
                else: self.fixture.season['season_number']=2
                result,records=self.adapter().enrich(*event(episode=True,category='series'))
                self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'tv/42/1/2')
                self.assertTrue(next(r for r in records if r['type']=='episode_still')['verified'])
                if broken!='details':
                    self.assertFalse(any(r.get('scope')=='season' for r in records))
                else:
                    self.assertFalse(any(r.get('scope')=='series' for r in records))

    def test_series_fills_only_missing_metadata(self):
        channel,p=event(category='series',year='20100203')
        ET.SubElement(p,'title',lang='en').text='Original supplied title'
        ET.SubElement(p,'desc').text='Original description'
        ET.SubElement(p,'star-rating',system='source').append(ET.fromstring('<value>5/5</value>'))
        old=copy.deepcopy(p)
        result,records=self.adapter().enrich(channel,p)
        self.assertTrue(records)
        self.assertEqual(result.findtext('desc'),'Original description')
        self.assertEqual(result.findtext('date'),'20100203')
        self.assertEqual([n.text for n in result.findall('star-rating/value')],['5/5'])
        self.assertEqual([n.text for n in result.findall('title')],['Example','Original supplied title'])
        for node in old:
            self.assertTrue(any(ET.tostring(node)==ET.tostring(n) for n in result if n.tag==node.tag))

    def test_episode_optional_api_budget_preserves_still(self):
        adapter=self.adapter(max_requests=2)
        result,records=adapter.enrich(*event(episode=True,category='series'))
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'tv/42/1/2')
        self.assertEqual(adapter.summary()['api_attempts'],2)
        self.assertTrue(next(r for r in records if r['type']=='episode_still')['verified'])
        self.assertGreater(adapter.summary()['api_budget_exhausted'],0)

    def test_episode_identity_requires_real_positive_id_and_integer_numbers(self):
        for key,value in (('id',None),('id',False),('id',0),('season_number',True),('episode_number','2')):
            with self.subTest(key=key,value=value):
                self.state.db.execute('DELETE FROM artwork'); self.fixture=TMDBFixture()
                self.fixture.ep[key]=value
                result,records=self.adapter().enrich(*event(episode=True,category='series'))
                self.assertEqual(records,[])
                self.assertIsNone(result.find('episode-num[@system="tmdb"]'))


class FullRunnerTMDBTests(unittest.TestCase):
    def run_fixture(self, artwork, factory_result, *, authorize_network=True, series=False, transport=None):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            xml=b'<tv><channel id="c"><display-name>Fixture</display-name></channel><programme channel="c" start="20261117100000 +0000" stop="20261117110000 +0000"><title>Fixture</title><category>News</category></programme></tv>'
            if series:
                xml=xml.replace(b'Fixture</title>',b'Example</title>').replace(b'<category>News</category>',b'<category>series</category><date>2010</date>')
            sources=[]
            for name in ('one','two'):
                path=root/(name+'.xml'); path.write_bytes(xml); sources.append({'id':name,'path':str(path)})
            config={'sources':sources,'artwork':artwork,'horizon':{'reference_time':'2026-11-17T00:00:00+00:00'}}
            with patch('xcmate_epg.tmdb_runtime.runtime_tmdb_client',return_value=factory_result) as factory:
                with patch('xcmate_epg.live_artwork.http_get',side_effect=transport or AssertionError('unexpected network')):
                    result=run_full(config,root/'private',authorize_network=authorize_network)
            return result, factory

    def test_pending_factory_continues_all_sources_and_reports_safe_status(self):
        result,factory=self.run_fixture({'tmdb_secret_env':'TMDB_READ_ACCESS_TOKEN'},(None,'TMDB_LIVE_PENDING'))
        self.assertEqual(result['real_sources_successful'],2)
        self.assertEqual(result['full']['programmes'],2)
        self.assertEqual(result['artwork']['tmdb_live_status'],'TMDB_LIVE_PENDING')
        factory.assert_called_once_with(binding=None)

    def test_unconfigured_runner_reports_pending_and_still_imports_all(self):
        result,factory=self.run_fixture({},(None,'TMDB_LIVE_PENDING'))
        self.assertEqual(result['full']['programmes'],2)
        self.assertEqual(result['artwork']['tmdb_live_status'],'TMDB_LIVE_PENDING')
        factory.assert_not_called()

    def test_binding_cannot_enable_requests_without_network_authorization(self):
        binding={'name':'TMDB_READ_ACCESS_TOKEN','host':'api.themoviedb.org','state':'ready'}
        result,factory=self.run_fixture({'tmdb_http_binding':binding},(None,'TMDB_LIVE_PENDING'),authorize_network=False,series=True)
        self.assertEqual(result['full']['programmes'],2)
        self.assertEqual(result['artwork']['tmdb_live_status'],'TMDB_LIVE_PENDING')
        self.assertEqual(result['artwork']['api_attempts'],0)
        factory.assert_not_called()

    def test_pending_tmdb_allows_authorized_tvmaze_fallback_for_all_sources(self):
        def transport(url,headers):
            if urlsplit(url).hostname=='static.tvmaze.com':
                return Response(200,PNG,{'Content-Type':'image/png'})
            if '/search/shows' in url:
                body=[{'show':dict(id=7,name='Example',premiered='2010-02-03')}]
            elif urlsplit(url).path=='/shows/7':
                body=dict(id=7,name='Example',premiered='2010-02-03',summary='<p>Verified fallback</p>',
                          image={'original':'https://static.tvmaze.com/uploads/images/fallback.png'})
            else: raise AssertionError('unexpected synthetic fallback path')
            return Response(200,json.dumps(body).encode(),{})
        result,factory=self.run_fixture({'tmdb_secret_env':'TMDB_READ_ACCESS_TOKEN','authorize_tvmaze':True,
            'request_interval':0},(None,'TMDB_LIVE_PENDING'),series=True,transport=transport)
        self.assertEqual(result['full']['programmes'],2)
        self.assertEqual(result['artwork']['tmdb_live_status'],'TMDB_LIVE_PENDING')
        self.assertEqual(result['artwork']['programmes_with_verified_artwork'],2)
        self.assertEqual(result['artwork']['tvmaze_status'],'authorized')

    def test_observed_binding_metadata_reaches_factory_without_live_verified_claim(self):
        binding={'name':'TMDB_READ_ACCESS_TOKEN','host':'api.themoviedb.org','state':'ready'}
        result,factory=self.run_fixture({'tmdb_http_binding':binding},(TMDB(managed_http=True),'TMDB_RUNTIME_BOUND'))
        self.assertEqual(result['artwork']['tmdb_live_status'],'TMDB_RUNTIME_BOUND')
        self.assertEqual(result['artwork']['artwork_status'],'zero_verified_images')
        factory.assert_called_once_with(binding=binding)

    def test_foreign_environment_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'ONLY_TMDB_RUNTIME_SECRET_ALLOWED'):
            self.run_fixture({'tmdb_secret_env':'OTHER'},(None,'TMDB_LIVE_PENDING'))
