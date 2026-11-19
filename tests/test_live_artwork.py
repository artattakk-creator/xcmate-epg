"""Synthetic adapter tests: no credentials, external requests or real listings."""
import json
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from xcmate_epg.fetch import Response
from xcmate_epg.state import State
from xcmate_epg.live_artwork import ArtworkEnricher

from image_fixtures import PNG, JPEG, PROGRESSIVE_JPEG, WEBP, LOSSLESS_WEBP, png


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
        return dict(id=3, season_number=season, episode_number=number, air_date='2020-01-01', still_path='/still.jpg')


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
                body=dict(id=9,season=1,number=2,airdate='2020-01-01')
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
        client.episode=lambda *args:dict(id=3,season_number=1,episode_number=2,air_date='2020-01-01',still_path='/still.jpg',overview='Episode description',name='Episode name',vote_average=8)
        result,records=self.adapter(tmdb=client,transport=self.image).enrich(*event(episode=True,category='series'))
        self.assertEqual(result.findtext('desc'),'Episode description')
        self.assertEqual(result.findtext('sub-title'),'Episode name')
        self.assertEqual(result.findtext('title[@lang="en"]'),'Original')
        self.assertEqual(result.findtext('star-rating/value'),'8/10')
        self.assertEqual({r['type'] for r in records},{'poster','backdrop','episode_still'})
        for record in records:
            self.assertEqual(record['evidence']['episode_id'],3)
            self.assertEqual(record['evidence']['episode_airdate'],'2020-01-01')
            self.assertEqual(record['evidence']['episode_year_validation'],'exact')
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
                body=dict(id=9,season=1,number=2,airdate='2020-01-01',image={'original':'https://static.tvmaze.com/uploads/images/original_untouched/1/2.jpg'})
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

    def test_complete_encoded_formats_are_verified(self):
        for mime,body in (('image/png',PNG),('image/jpeg',JPEG),('image/jpeg',PROGRESSIVE_JPEG),('image/webp',WEBP)):
            with self.subTest(mime=mime, size=len(body)):
                self.state.db.execute('DELETE FROM artwork')
                adapter=self.adapter(transport=lambda *args:Response(200,body,{'Content-Type':mime}))
                self.assertTrue(adapter.verify_media('https://image.tmdb.org/complete')['verified'])

    def test_header_only_truncated_bad_pixel_stream_and_dimensions_rejected(self):
        import struct
        huge_jpeg=JPEG.replace(b'\xff\xc0\x00\x11\x08\x00\x02\x00\x02',b'\xff\xc0\x00\x11\x08\xff\xff\xff\xff')
        bad_webp=bytearray(WEBP); bad_webp[26:30]=b'\xff\x3f\xff\x3f'
        cases=[('image/png',b'\x89PNG\r\n\x1a\n'),('image/jpeg',b'\xff\xd8\xff'),
               ('image/webp',b'RIFF\x04\x00\x00\x00WEBP'),('image/png',PNG[:-3]),
               ('image/jpeg',JPEG[:-2]),('image/jpeg',PROGRESSIVE_JPEG[:-2]),
               ('image/webp',WEBP[:-2]),('image/png',png(pixels=b'not a zlib stream')),
               ('image/png',png(width=0)),('image/png',png(width=16384,height=16384,pixels=b'x')),
               ('image/jpeg',huge_jpeg),('image/webp',bytes(bad_webp))]
        for mime,body in cases:
            with self.subTest(mime=mime,size=len(body)):
                self.state.db.execute('DELETE FROM artwork')
                adapter=self.adapter(transport=lambda *args:Response(200,body,{'Content-Type':mime}))
                self.assertFalse(adapter.verify_media('https://image.tmdb.org/incomplete')['verified'])
                self.assertEqual(adapter.summary()['verified_images'],0)

    def test_incomplete_tables_scans_and_riff_frames_are_rejected(self):
        # Turn a complete baseline frame into an incomplete DC-only progressive one.
        dc_only=bytearray(JPEG.replace(b'\xff\xc0',b'\xff\xc2',1))
        scan=dc_only.index(b'\xff\xda'); size=int.from_bytes(dc_only[scan+2:scan+4],'big')
        dc_only[scan+size-1:scan+size+2]=b'\x00\x00\x00'
        bad_table=JPEG.replace(b'\xff\xc4\x00\x1f',b'\xff\xc4\x00\x02',1)
        no_frame=JPEG.replace(b'\xff\xc0',b'\xff\xe0',1)
        empty_frame=b'RIFF'+(12).to_bytes(4,'little')+b'WEBPVP8 '+(0).to_bytes(4,'little')
        partition=bytearray(WEBP); partition[20:23]=b'\xf0\xff\xff'
        # The RIFF length is deliberately repaired, so a missing chunk body is still rejected.
        shortened=bytearray(WEBP[:-2]); shortened[4:8]=(len(shortened)-8).to_bytes(4,'little')
        for mime,body in (('image/jpeg',bytes(dc_only)),('image/jpeg',bad_table),('image/jpeg',no_frame),
                          ('image/webp',empty_frame),('image/webp',bytes(partition)),('image/webp',bytes(shortened))):
            with self.subTest(mime=mime,size=len(body)):
                self.state.db.execute('DELETE FROM artwork')
                adapter=self.adapter(transport=lambda *args:Response(200,body,{'Content-Type':mime}))
                self.assertFalse(adapter.verify_media('https://image.tmdb.org/malformed')['verified'])

    def test_lossless_and_animated_webp_are_conservatively_unsupported(self):
        from xcmate_epg.image_validation import validate_image
        animation=b'ANIM'+(6).to_bytes(4,'little')+b'\0'*6
        animated=b'RIFF'+(4+len(animation)).to_bytes(4,'little')+b'WEBP'+animation
        for body in (LOSSLESS_WEBP,animated):
            with self.subTest(size=len(body)):
                with self.assertRaisesRegex(ValueError,'WEBP_UNSUPPORTED_CODING'):
                    validate_image(body,'image/webp')
                self.state.db.execute('DELETE FROM artwork')
                adapter=self.adapter(transport=lambda *args:Response(200,body,{'Content-Type':'image/webp'}))
                self.assertFalse(adapter.verify_media('https://image.tmdb.org/unsupported')['verified'])

    def test_legacy_header_only_positive_cache_is_retained_but_revalidated(self):
        url='https://image.tmdb.org/old.png'; key=json.dumps(['verified-image-v1',url])
        legacy={'verified':True,'sha256':'old','size_bytes':8,'mime_type':'image/png'}
        self.state.cache(key,legacy,86400)
        adapter=self.adapter(transport=lambda *args:Response(200,b'\x89PNG\r\n\x1a\n',{'Content-Type':'image/png'}))
        self.assertFalse(adapter.verify_media(url)['verified'])
        self.assertEqual(adapter.summary()['image_attempts'],1)
        self.assertEqual(adapter.summary()['verified_images'],0)
        self.assertEqual(self.state.cached(key),(True,legacy))

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

class EpisodeIntegrationTests(unittest.TestCase):
    setUp = LiveArtworkTests.setUp
    tearDown = LiveArtworkTests.tearDown
    adapter = LiveArtworkTests.adapter
    image = LiveArtworkTests.image

    def test_xmltv_ns_optional_parts_totals_and_whitespace(self):
        from xcmate_epg.live_artwork import parse_xmltv_ns
        for text,want in (('0 . 1 . 0/1',(1,2)),('1/4.2/8.',(2,3)),(' 0 / 1 . 1 / 3 . ',(1,2)),('0.1.',(1,2)),('0.1',(1,2)),('0.1./2',(1,2))):
            with self.subTest(text=text):
                self.assertEqual(parse_xmltv_ns(text),want)
        for text in (' .1.', '0. .', '/2.1.', '0/0.1.', '1/1.1.', '0.2/2.', '0.1.1/1', '0.1.-1', '0.1.0/0', '-1.1.', '0.1.extra', '0.1.0.1', '0/unknown.1.'):
            with self.subTest(text=text):
                self.assertIsNone(parse_xmltv_ns(text))

    def test_canonical_episode_passes_existing_tmdb_and_preserves_source_text(self):
        channel,p=event(episode=True,category='series')
        original=' 0 / 3 . 1 / 10 . 0/1 '
        p.find('episode-num').text=original
        client=MovieClient()
        client.episode=lambda *args:dict(id=3,season_number=1,episode_number=2,air_date='2020-01-01',still_path='/still.jpg')
        result,records=self.adapter(tmdb=client,transport=self.image).enrich(channel,p)
        self.assertEqual(result.findtext('episode-num[@system="xmltv_ns"]'),original)
        self.assertEqual(result.findtext('episode-num[@system="tmdb"]'),'tv/2/1/2')
        self.assertEqual([r['type'] for r in records],['episode_still'])
        self.assertEqual(records[0]['evidence']['episode'],[1,2])

    def tvmaze_transport(self,url,headers):
        from urllib.parse import parse_qs, urlsplit
        if 'static.tvmaze.com' in url:
            return self.image(url,headers)
        if 'search/shows' in url:
            body=[{'show':dict(id=7,name='Example',premiered='2010-01-01')}]
        elif '/episodebynumber' in url:
            params=parse_qs(urlsplit(url).query)
            body=dict(id=100+int(params['number'][0]),season=int(params['season'][0]),number=int(params['number'][0]),airdate='2020-01-01',image={'original':'https://static.tvmaze.com/uploads/images/episode.jpg'})
        else:
            body=dict(id=7,name='Example',premiered='2010-01-01',image={'original':'https://static.tvmaze.com/uploads/images/show.jpg'})
        return Response(200,json.dumps(body).encode(),{})

    def test_tvmaze_episode_air_year_not_show_premiere_and_shared_show_cache(self):
        adapter=self.adapter(config={'authorize_tvmaze':True},transport=self.tvmaze_transport)
        for text,identity in (('0 . 1 . 0/1','tv/7/1/2'),('0/2.2/8.','tv/7/1/3')):
            channel,p=event(episode=True,category='series')
            p.find('episode-num').text=text
            result,records=adapter.enrich(channel,p)
            self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'),identity)
            self.assertTrue(next(r for r in records if r['type']=='episode_still')['verified'])
            self.assertEqual(result.findtext('date'),'2020')
            self.assertEqual(result.findtext('episode-num[@system="xmltv_ns"]'),text)
        self.assertEqual(adapter.summary()['api_attempts'],4)
        cached=self.adapter(config={'authorize_tvmaze':True},transport=lambda *args:self.fail('persistent show/episode cache missed'))
        cached.enrich(*event(episode=True,category='series'))
        self.assertEqual(cached.summary()['api_attempts'],0)

    def test_episode_year_mismatch_and_unproven_year_are_not_episode_matches(self):
        for date in ('2021-01-01',None):
            self.state.db.execute('DELETE FROM artwork')
            def transport(url,headers):
                response=self.tvmaze_transport(url,headers)
                if '/episodebynumber' in url:
                    data=json.loads(response.body)
                    data['airdate']=date
                    return Response(200,json.dumps(data).encode(),{})
                return response
            adapter=self.adapter(config={'authorize_tvmaze':True},transport=transport)
            result,records=adapter.enrich(*event(episode=True,category='series'))
            self.assertFalse(any(r['type']=='episode_still' for r in records))
            self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
            self.assertEqual(adapter.summary()['programmes_matched'],0)
            metric='episode_year_mismatched' if date else 'episode_year_unproven'
            self.assertEqual(adapter.summary()[metric],1)

    def test_tmdb_shared_exact_show_match_checks_actual_episode_air_year(self):
        client=MovieClient()
        def series(*args):
            client.calls+=1
            return [dict(id=2,name='Example',first_air_date='2010-01-01')]
        def episode(identifier,season,number):
            client.calls+=1
            return dict(id=number,season_number=season,episode_number=number,air_date='2020-01-01',still_path='/still.jpg')
        client.search_series=series
        client.episode=episode
        adapter=self.adapter(tmdb=client,transport=self.image)
        for ns in ('0 . 1 . 0/1','0.2.'):
            channel,p=event(episode=True,category='series')
            p.find('episode-num').text=ns
            result,records=adapter.enrich(channel,p)
            self.assertTrue(records[0]['verified'])
            self.assertEqual(result.findtext('date'),'2020')
        self.assertEqual(client.calls,3)
        self.assertEqual(adapter.summary()['programmes_matched'],2)

    def test_invalid_ns_does_not_slip_through_legacy_episode_pipeline(self):
        channel,p=event(episode=True,category='series')
        ET.SubElement(p,'episode-num',system='xmltv_ns').text='0.2.'
        client=MovieClient()
        result,records=self.adapter(tmdb=client,transport=self.image).enrich(channel,p)
        self.assertEqual(records,[])
        self.assertEqual(client.calls,0)
        self.assertEqual([n.text for n in result.findall('episode-num[@system="xmltv_ns"]')],['0.1.','0.2.'])

    def test_transient_episode_failure_keeps_show_cache_and_retries_episode(self):
        fail=[True]
        def transport(url,headers):
            if '/episodebynumber' in url and fail[0]:
                fail[0]=False
                return Response(503,b'',{})
            return self.tvmaze_transport(url,headers)
        adapter=self.adapter(config={'authorize_tvmaze':True},transport=transport)
        self.assertEqual(adapter.enrich(*event(episode=True,category='series'))[1],[])
        result,records=adapter.enrich(*event(episode=True,category='series'))
        self.assertTrue(next(r for r in records if r['type']=='episode_still')['verified'])
        self.assertEqual(adapter.summary()['api_attempts'],4)

    def test_episode_cache_rechecks_programme_year_for_both_providers(self):
        for provider in ('tmdb','tvmaze'):
            self.state.db.execute('DELETE FROM artwork')
            adapter=(self.adapter(tmdb=MovieClient(),transport=self.image) if provider=='tmdb' else
                     self.adapter(config={'authorize_tvmaze':True},transport=self.tvmaze_transport))
            result,records=adapter.enrich(*event(episode=True,category='series'))
            self.assertTrue(any(r['type']=='episode_still' for r in records))
            calls=adapter.summary()['api_attempts']
            result,records=adapter.enrich(*event(episode=True,category='series',year='2021'))
            self.assertEqual(records,[])
            self.assertEqual(adapter.summary()['api_attempts'],calls)
            self.assertEqual(adapter.summary()['episode_year_mismatched'],1)
            self.assertEqual(adapter.summary()['programmes_matched'],1)

    def test_non_episode_show_keeps_premiere_year_constraint(self):
        adapter=self.adapter(config={'authorize_tvmaze':True},transport=self.tvmaze_transport)
        result,records=adapter.enrich(*event(category='series',year='2020'))
        self.assertEqual(records,[])
        self.assertEqual(adapter.summary()['programmes_matched'],0)
        self.assertEqual(adapter.summary()['api_attempts'],1)

    def test_episode_without_programme_year_preserves_absence(self):
        adapter=self.adapter(tmdb=MovieClient(),transport=self.image)
        result,records=adapter.enrich(*event(episode=True,category='series',year=''))
        self.assertTrue(any(r['type']=='episode_still' for r in records))
        self.assertIsNone(result.find('date'))


if __name__=='__main__': unittest.main()
