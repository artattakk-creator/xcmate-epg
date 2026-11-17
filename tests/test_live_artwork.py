"""Synthetic adapter tests: no credentials, external requests or real listings."""
import json
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from xcmate_epg.fetch import Response
from xcmate_epg.state import State
from xcmate_epg.live_artwork import ArtworkEnricher

PNG = b'\x89PNG\r\n\x1a\n' + b'synthetic'


def event(episode=False, year='2020', category='movie'):
    channel = ET.fromstring('<channel id="synthetic"><display-name>Synthetic</display-name></channel>')
    p = ET.fromstring('<programme channel="synthetic" start="20200101000000 +0000" stop="20200101010000 +0000"><title>Example</title></programme>')
    if year: ET.SubElement(p, 'date').text = year
    if category: ET.SubElement(p, 'category').text = category
    if episode: ET.SubElement(p, 'episode-num', system='xmltv_ns').text = '0.1.'
    return channel, p


class MovieClient:
    def __init__(self, hits=None):
        self.hits = hits if hits is not None else [dict(id=1, title='Example', release_date='2020-01-01', poster_path='/poster.jpg', backdrop_path='/back.jpg')]
        self.calls = 0
    def search(self, title, year):
        self.calls += 1
        return self.hits
    def movie(self, identifier):
        self.calls += 1
        return dict(id=identifier, overview='Synthetic overview', original_title='Original', original_language='en', genres=[{'name':'Drama'}], vote_average=7)
    def search_series(self, title, year):
        self.calls += 1
        return [dict(id=2, name='Example', first_air_date='2020-01-01')]
    def episode(self, identifier, season, number):
        self.calls += 1
        return dict(id=3, season_number=season, episode_number=number, still_path='/still.jpg')


class LiveArtworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(self.tmp.name + '/state.sqlite')
    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()
    def adapter(self, **kwargs):
        config = {'request_interval': 0}
        config.update(kwargs.pop('config', {}))
        return ArtworkEnricher(self.state, config, **kwargs)
    def image(self, url, headers):
        return Response(200, PNG, {'Content-Type':'image/png'})

    def test_verified_movie_preserves_metadata_and_calls_existing_aggregate(self):
        import xcmate_epg.pipeline as pipeline
        channel, p = event()
        ET.SubElement(p, 'desc').text = 'Existing description'
        client = MovieClient()
        adapter = self.adapter(tmdb=client, transport=self.image)
        with patch.object(pipeline, 'aggregate', wraps=pipeline.aggregate) as existing:
            result, records = adapter.enrich(channel, p)
        self.assertEqual(existing.call_count, 1)
        self.assertEqual(result.findtext('desc'), 'Existing description')
        self.assertEqual(p.findtext('episode-num'), None)
        self.assertEqual({r['type'] for r in records}, {'poster','backdrop'})
        self.assertTrue(all(r['verified'] and r['provider_id'] == 1 for r in records))
        self.assertEqual(len(result.findall('icon')), 2)
        self.assertEqual(adapter.summary()['image_successes'], 2)

    def test_wrong_movie_title_year_and_ambiguous_are_not_matched(self):
        for hits in ([dict(id=1,title='Other',release_date='2020')], [dict(id=1,title='Example',release_date='2021')], [dict(id=1,title='Example',release_date='2020'),dict(id=2,title='Example',release_date='2020')]):
            with self.subTest(hits=hits):
                self.state.db.execute('DELETE FROM artwork')
                adapter = self.adapter(tmdb=MovieClient(hits), transport=self.image)
                result, records = adapter.enrich(*event())
                self.assertEqual(records, [])
                self.assertIsNone(result.find('episode-num'))
        self.assertEqual(adapter.summary()['ambiguous'], 1)

    def test_episode_still_requires_matching_explicit_identity(self):
        adapter = self.adapter(tmdb=MovieClient(), transport=self.image)
        result, records = adapter.enrich(*event(episode=True,category='series'))
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'), 'tv/2/1/2')
        self.assertEqual([r['type'] for r in records], ['episode_still'])
        bad = MovieClient()
        bad.episode = lambda *args: dict(season_number=1, episode_number=8, still_path='/wrong.jpg')
        self.state.db.execute('DELETE FROM artwork')
        result, records = self.adapter(tmdb=bad,transport=self.image).enrich(*event(episode=True,category='series'))
        self.assertEqual(records, [])

    def test_positive_negative_and_image_caches_survive_new_adapter(self):
        client = MovieClient()
        self.adapter(tmdb=client,transport=self.image).enrich(*event())
        calls = client.calls
        other = self.adapter(tmdb=client, transport=lambda *args: self.fail('cached image fetched'))
        other.enrich(*event())
        self.assertEqual(client.calls, calls)
        self.assertEqual(other.summary()['api_attempts'], 0)
        self.assertGreater(other.summary()['metadata_cache_hits'], 0)
        self.state.db.execute('DELETE FROM artwork')
        client = MovieClient([])
        self.adapter(tmdb=client).enrich(*event())
        self.adapter(tmdb=client).enrich(*event())
        self.assertEqual(client.calls, 1)

    def test_transient_errors_do_not_poison_negative_cache(self):
        client = MovieClient()
        client.search = lambda *args: (_ for _ in ()).throw(TimeoutError('private details'))
        adapter = self.adapter(tmdb=client)
        adapter.enrich(*event())
        client.search = lambda *args: [dict(id=1,title='Example',release_date='2020',poster_path='/poster.jpg')]
        result, records = self.adapter(tmdb=client,transport=self.image).enrich(*event())
        self.assertEqual(len(records), 1)
        self.assertEqual(adapter.summary()['api_errors'], 1)

    def test_source_tmdb_id_is_preserved_without_becoming_verified_match(self):
        channel,p=event()
        ET.SubElement(p,'episode-num',system='tmdb').text='movie/123'
        adapter=self.adapter(transport=lambda *args:self.fail('implicit request'))
        result,records=adapter.enrich(channel,p)
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'movie/123')
        self.assertEqual(adapter.summary()['programmes_matched'],0)
        self.assertEqual(adapter.summary()['unique_identities_matched'],0)
        self.assertEqual(adapter.summary()['api_attempts'],0)
        self.assertEqual(records,[])

    def test_source_tmdb_id_does_not_block_real_tvmaze_lookup(self):
        channel,p=event(episode=True,category='series')
        ET.SubElement(p,'episode-num',system='tmdb').text='tv/123/1/2'
        def transport(url,headers):
            if 'search/shows' in url:
                body=[{'show':dict(id=7,name='Example',premiered='2020-01-01')}]
            elif '/episodebynumber' in url:
                body=dict(id=9,season=1,number=2)
            else:
                body=dict(id=7,name='Example',premiered='2020-01-01')
            return Response(200,json.dumps(body).encode(),{})
        adapter=self.adapter(transport=transport,config={'authorize_tvmaze':True})
        result,records=adapter.enrich(channel,p)
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'tv/123/1/2')
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'),'tv/7/1/2')
        self.assertEqual(adapter.summary()['api_attempts'],3)
        self.assertEqual(adapter.summary()['programmes_matched'],1)

    def test_legacy_positive_cache_cannot_bypass_current_identity_validation(self):
        legacy_key=json.dumps(['movie','example','2020','de-DE'])
        for hit in (dict(id=1,title='Other',release_date='2020',poster_path='/wrong.jpg'),dict(id=1,title='Example',release_date='2021',poster_path='/wrong.jpg')):
            self.state.cache(legacy_key,hit,86400)
            adapter=self.adapter(tmdb=MovieClient([]),transport=lambda *args:self.fail('legacy image fetched'))
            result,records=adapter.enrich(*event())
            self.assertEqual(records,[])
            self.assertIsNone(result.find('episode-num[@system="tmdb"]'))
            self.assertEqual(adapter.summary()['programmes_matched'],0)
            self.assertEqual(self.state.cached(legacy_key),(True,hit))

    def test_legacy_episode_cache_without_identity_evidence_is_not_a_match(self):
        key=json.dumps(['tv','example','2020',1,2,'de-DE'])
        legacy={'id':999,'still':'https://image.tmdb.org/t/p/w500/wrong.jpg'}
        self.state.cache(key,legacy,86400)
        client=MovieClient()
        client.search_series=lambda *args:[]
        adapter=self.adapter(tmdb=client,transport=lambda *args:self.fail('unproven episode image fetched'))
        result,records=adapter.enrich(*event(episode=True,category='series'))
        self.assertEqual(records,[])
        self.assertIsNone(result.find('episode-num[@system="tmdb"]'))
        self.assertEqual(adapter.summary()['programmes_matched'],0)
        self.assertEqual(self.state.cached(key),(True,legacy))

    def test_original_title_match_accepts_same_candidate_localized_details(self):
        channel,p=event()
        p.find('title').text='Original'
        client=MovieClient([dict(id=1,title='Localized',original_title='Original',release_date='2020-01-01',poster_path='/poster.jpg')])
        client.movie=lambda *args:dict(id=1,title='Localized',original_title='Original',release_date='2020-01-01')
        adapter=self.adapter(tmdb=client,transport=self.image)
        result,records=adapter.enrich(channel,p)
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'movie/1')
        self.assertTrue(records[0]['verified'])
        self.assertEqual(adapter.summary()['programmes_matched'],1)

    def test_movie_details_conflicting_title_or_year_are_rejected(self):
        for detail in (dict(id=1,title='Other'),dict(id=1,release_date='2021-01-01')):
            client=MovieClient()
            client.movie=lambda *args:detail
            result,records=self.adapter(tmdb=client,transport=self.image).enrich(*event())
            self.assertEqual(records,[])
            self.assertIsNone(result.find('episode-num[@system="tmdb"]'))

    def test_episode_metadata_and_show_images_are_retained_without_overwrite(self):
        client=MovieClient()
        client.search_series=lambda *args:[dict(id=2,name='Example',original_name='Original',original_language='en',first_air_date='2020-01-01',poster_path='/show.jpg',backdrop_path='/wide.jpg')]
        client.episode=lambda *args:dict(id=3,season_number=1,episode_number=2,still_path='/still.jpg',overview='Episode description',name='Episode name',vote_average=8)
        result,records=self.adapter(tmdb=client,transport=self.image).enrich(*event(episode=True,category='series'))
        self.assertEqual(result.findtext('desc'),'Episode description')
        self.assertEqual(result.findtext('sub-title'),'Episode name')
        self.assertEqual(result.findtext('title[@lang="en"]'),'Original')
        self.assertEqual(result.findtext('star-rating/value'),'8/10')
        self.assertEqual({r['type'] for r in records},{'poster','backdrop','episode_still'})
        self.assertEqual(next(r for r in records if r['type']=='episode_still')['evidence']['episode_id'],3)
        channel,p=event(episode=True,category='series')
        ET.SubElement(p,'desc').text='Original description'
        ET.SubElement(p,'sub-title').text='Original episode'
        cached=self.adapter(tmdb=client,transport=lambda *args:self.fail('cached image fetched'))
        result,records=cached.enrich(channel,p)
        self.assertEqual(result.findtext('desc'),'Original description')
        self.assertEqual(result.findtext('sub-title'),'Original episode')
        self.assertEqual(cached.summary()['api_attempts'],0)

    def test_unverified_feed_icons_removed_and_no_implicit_network(self):
        channel,p = event()
        ET.SubElement(p,'icon',src='https://foreign.invalid/private.jpg?token=synthetic')
        adapter = self.adapter(transport=lambda *args: self.fail('implicit network'))
        result,records = adapter.enrich(channel,p)
        self.assertIsNone(result.find('icon'))
        self.assertFalse(records[0]['verified'])
        self.assertEqual(adapter.summary()['tmdb_status'],'not_configured')
        self.assertEqual(adapter.summary()['image_successes'],0)

    def test_image_policy_redirect_html_and_magic(self):
        for response in (Response(302,b'',{'Location':'https://foreign.invalid/a'}),Response(200,b'<html>',{'Content-Type':'image/png'}),Response(200,PNG,{'Content-Type':'text/html'}),Response(404,b'',{})):
            self.state.db.execute('DELETE FROM artwork')
            result,records = self.adapter(tmdb=MovieClient(),transport=lambda *args: response).enrich(*event())
            self.assertFalse(any(r['verified'] for r in records))
            self.assertIsNone(result.find('icon'))
        channel,p=event()
        ET.SubElement(p,'icon',src='https://image.tmdb.org/a.jpg?token=synthetic')
        adapter=self.adapter(config={'allow_feed_images':True},transport=lambda *args:self.fail('unsafe URL fetched'))
        self.assertIsNone(adapter.enrich(channel,p)[0].find('icon'))

    def test_request_and_image_budgets_are_enforced(self):
        client=MovieClient()
        adapter=self.adapter(tmdb=client,transport=self.image,config={'max_requests':1,'max_image_requests':0})
        result,records=adapter.enrich(*event())
        self.assertEqual(client.calls,1)
        self.assertEqual(adapter.summary()['api_attempts'],1)
        self.assertGreater(adapter.summary()['api_budget_exhausted'],0)
        adapter=self.adapter(tmdb=MovieClient(),transport=self.image,config={'max_image_requests':1})
        result,records=adapter.enrich(*event())
        self.assertEqual(len(result.findall('icon')),1)
        self.assertEqual(adapter.summary()['image_attempts'],1)
        self.assertEqual(adapter.summary()['image_budget_exhausted'],1)

    def test_tvmaze_exact_show_and_episode_not_movie(self):
        def transport(url,headers):
            if 'search/shows' in url:
                body=[{'show':dict(id=7,name='Example',premiered='2020-01-01',type='Scripted')}]
            elif '/episodebynumber' in url:
                body=dict(id=9,season=1,number=2,image={'original':'https://static.tvmaze.com/uploads/images/original_untouched/1/2.jpg'})
            else:
                body=dict(id=7,name='Example',premiered='2020-01-01',type='Scripted',genres=['Drama'],summary='<p>Show description</p>',image={'original':'https://static.tvmaze.com/uploads/images/original_untouched/1/1.jpg'})
            if 'static.tvmaze.com' in url: return self.image(url,headers)
            return Response(200,json.dumps(body).encode(),{})
        adapter=self.adapter(transport=transport,config={'authorize_tvmaze':True})
        result,records=adapter.enrich(*event(episode=True,category='series'))
        self.assertTrue(all(r['verified'] and r['provider']=='tvmaze' for r in records))
        self.assertEqual({r['type'] for r in records},{'poster','episode_still'})
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'),'tv/7/1/2')
        self.assertEqual(result.findtext('desc'),'Show description')
        self.assertEqual(self.adapter(transport=transport,config={'authorize_tvmaze':True}).enrich(*event())[1],[])

    def test_tvmaze_ambiguous_and_wrong_year(self):
        for shows in ([dict(id=7,name='Example',premiered='2021')],[dict(id=7,name='Example',premiered='2020'),dict(id=8,name='Example',premiered='2020')]):
            self.state.db.execute('DELETE FROM artwork')
            adapter=self.adapter(config={'authorize_tvmaze':True},transport=lambda *args:Response(200,json.dumps([{'show':s} for s in shows]).encode(),{}))
            result,records=adapter.enrich(*event(episode=True,category='series'))
            self.assertEqual(records,[])

    def test_tvmaze_transient_status_and_retry_after_are_bounded(self):
        for status in (401,403,429,503):
            adapter=self.adapter(config={'authorize_tvmaze':True,'max_requests':2},transport=lambda *args:Response(status,b'',{'Retry-After':'9999'}))
            with patch('xcmate_epg.live_artwork.time.sleep') as sleep:
                adapter.enrich(*event(episode=True,category='series'))
            self.assertEqual(adapter.summary()['api_errors'],1)
            self.assertEqual(self.state.db.execute('SELECT count(*) FROM artwork').fetchone()[0],0)
            self.assertTrue(all(call.args[0]<=5 for call in sleep.call_args_list))

if __name__=='__main__': unittest.main()

class MediaVerifierTests(unittest.TestCase):
    setUp = LiveArtworkTests.setUp
    tearDown = LiveArtworkTests.tearDown
    adapter = LiveArtworkTests.adapter
    image = LiveArtworkTests.image
    def test_public_verifier_checks_digest_size_mime_and_cache(self):
        import hashlib
        adapter=self.adapter(transport=self.image)
        result=adapter.verify_media('https://static.tvmaze.com/uploads/images/1.jpg')
        self.assertEqual(result,dict(verified=True,sha256=hashlib.sha256(PNG).hexdigest(),size_bytes=len(PNG),mime_type='image/png'))
        other=self.adapter(transport=lambda *args:self.fail('cached verifier made request'))
        self.assertTrue(other.verify_media('https://static.tvmaze.com/uploads/images/1.jpg')['verified'])
        self.assertEqual(other.summary()['artwork_status'],'verified')

    def test_root_credentials_foreign_host_fragment_and_oversize_rejected(self):
        adapter=self.adapter(transport=lambda *args:self.fail('invalid image requested'))
        for url in ('https://image.tmdb.org/', 'https://user:pass@image.tmdb.org/a.jpg', 'https://foreign.invalid/a.jpg', 'https://image.tmdb.org/a.jpg#fragment', 'http://image.tmdb.org/a.jpg'):
            self.assertFalse(adapter.verify_media(url)['verified'])
        adapter=self.adapter(transport=lambda *args:Response(200,PNG+b'x'*(8*1024*1024),{'Content-Type':'image/png'}))
        self.assertFalse(adapter.verify_media('https://image.tmdb.org/large.jpg')['verified'])

    def test_transport_redirect_metadata_rejected_even_on_success(self):
        adapter=self.adapter(transport=lambda *args:Response(200,PNG,{'Content-Type':'image/png','Location':'https://foreign.invalid/a.jpg'}))
        self.assertFalse(adapter.verify_media('https://image.tmdb.org/a.jpg')['verified'])

    def test_tmdb_transport_auth_status_budget_and_rate_interval(self):
        from xcmate_epg.artwork import TMDB
        client=TMDB('synthetic-nonsecret')
        adapter=self.adapter(tmdb=client,config={'request_interval':.1},transport=lambda *args:Response(429,b'',{'Retry-After':'1'}))
        with patch('xcmate_epg.live_artwork.time.sleep') as sleep:
            adapter.enrich(*event())
            adapter.enrich(*event())
        self.assertEqual(adapter.summary()['api_attempts'],1)
        self.assertEqual(adapter.summary()['api_errors'],1)
        self.assertEqual(self.state.db.execute('SELECT count(*) FROM artwork').fetchone()[0],0)
        self.assertIn(1,[call.args[0] for call in sleep.call_args_list])
