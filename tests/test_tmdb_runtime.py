"""Offline authentication handoff tests; synthetic bindings only."""
import unittest
from urllib.parse import urlsplit

from xcmate_epg.artwork import TMDB, image_url
from xcmate_epg.fetch import Response, http_get
from xcmate_epg.tmdb_runtime import runtime_tmdb_client


class TMDBRuntimeTests(unittest.TestCase):
    def response(self,url,headers):
        return Response(200,b'{"id":7,"season_number":1,"results":[]}',{})

    def test_absent_runtime_binding_stays_pending_without_requests(self):
        def forbidden(*args): self.fail('pending runtime attempted network')
        client,status=runtime_tmdb_client(environ={},transport=forbidden)
        self.assertIsNone(client)
        self.assertEqual(status,'TMDB_LIVE_PENDING')
        client,status=runtime_tmdb_client(environ={'UNRELATED_TOKEN':'synthetic'},transport=forbidden)
        self.assertIsNone(client)
        self.assertEqual(status,'TMDB_LIVE_PENDING')

    def test_only_exact_environment_key_is_read_and_is_preferred(self):
        class BindingEnvironment:
            def get(self,key,default=None):
                self_key='TMDB_READ_ACCESS_TOKEN'
                if key!=self_key: raise AssertionError('read unrelated environment key')
                return 'synthetic-runtime-value'
        seen=[]
        client,status=runtime_tmdb_client(environ=BindingEnvironment(),binding={'name':'TMDB_READ_ACCESS_TOKEN','host':'api.themoviedb.org','state':'ready'},transport=lambda url,headers:(seen.append((url,headers)) or self.response(url,headers)))
        self.assertEqual(status,'TMDB_RUNTIME_BOUND')
        client.series(7)
        self.assertEqual(seen[0][1],{'Authorization':'Bearer synthetic-runtime-value'})
        self.assertEqual(urlsplit(seen[0][0]).hostname,'api.themoviedb.org')
        self.assertNotIn('synthetic-runtime-value',seen[0][0])

    def test_ready_exact_managed_binding_sends_no_authorization_header(self):
        seen=[]
        client,status=runtime_tmdb_client(environ={},binding={'name':'TMDB_READ_ACCESS_TOKEN','host':'api.themoviedb.org','state':'ready'},transport=lambda url,headers:(seen.append((url,headers)) or self.response(url,headers)))
        self.assertEqual(status,'TMDB_RUNTIME_BOUND')
        clone=client.clone(transport=client.transport)
        clone.series(7)
        self.assertEqual(seen[0][1],{})
        self.assertEqual(clone.auth_mode,'managed_http')
        self.assertEqual(urlsplit(seen[0][0]).hostname,'api.themoviedb.org')

    def test_unknown_notready_foreign_and_missing_bindings_are_pending(self):
        for binding in ({}, {'name':'OTHER','host':'api.themoviedb.org','state':'ready'}, {'name':'TMDB_READ_ACCESS_TOKEN','host':'foreign.invalid','state':'ready'}, {'name':'TMDB_READ_ACCESS_TOKEN','host':'api.themoviedb.org','state':'pending'}, {'name':'TMDB_READ_ACCESS_TOKEN','state':'ready'}):
            client,status=runtime_tmdb_client(environ={},binding=binding)
            self.assertIsNone(client)
            self.assertEqual(status,'TMDB_LIVE_PENDING')

    def test_factory_uses_normal_transport_and_validates_binding_value(self):
        client,status=runtime_tmdb_client(environ={'TMDB_READ_ACCESS_TOKEN':'synthetic'})
        self.assertIs(client.transport,http_get)
        for value in ('','   ','\nsynthetic','synthetic\r',None,7):
            client,status=runtime_tmdb_client(environ={'TMDB_READ_ACCESS_TOKEN':value})
            self.assertIsNone(client)
            self.assertEqual(status,'TMDB_LIVE_PENDING')

    def test_base_series_season_configuration_and_clone_preserve_bearer_mode(self):
        seen=[]
        client=TMDB('synthetic',transport=lambda url,headers:(seen.append((url,headers)) or self.response(url,headers)),sleep=lambda seconds:None)
        self.assertEqual(client.series(7)['id'],7)
        self.assertEqual(client.season(7,1)['season_number'],1)
        client.configuration()
        clone=client.clone(transport=client.transport)
        clone.movie(7)
        self.assertEqual(clone.auth_mode,'bearer')
        self.assertEqual([urlsplit(url).path for url,headers in seen],['/3/tv/7','/3/tv/7/season/1','/3/configuration','/3/movie/7'])
        self.assertTrue(all(headers=={'Authorization':'Bearer synthetic'} for url,headers in seen))
        self.assertTrue(all(urlsplit(url).hostname=='api.themoviedb.org' for url,headers in seen))
        self.assertNotIn('Authorization',image_url('/poster.jpg'))
        self.assertNotIn(client._token,image_url('/poster.jpg'))
        self.assertNotIn('?',image_url('/poster.jpg'))
        self.assertEqual(urlsplit(image_url('/poster.jpg')).hostname,'image.tmdb.org')

    def test_identifiers_are_validated_without_network(self):
        client=TMDB('synthetic',transport=lambda *args:self.fail('bad identifier requested'))
        for identifier in (0,-1,True,'7'):
            with self.assertRaisesRegex(ValueError,'invalid series ID'):
                client.series(identifier)
        for series,season in ((0,1),(True,1),(7,-1),(7,True),(7,'1')):
            with self.assertRaisesRegex(ValueError,'invalid season identifiers'):
                client.season(series,season)

    def test_auth_failures_and_rate_limit_recover_without_secret_in_errors(self):
        for status in (401,403,429):
            remaining=[status,status,status] if status==429 else [status]
            def transport(url,headers):
                code=remaining.pop(0) if remaining else 200
                return Response(code,b'{}',{})
            client=TMDB('synthetic-private-value',transport=transport,sleep=lambda seconds:None)
            with self.assertRaisesRegex(ValueError,'^metadata lookup failed$') as caught:
                client.series(7)
            self.assertNotIn('synthetic-private-value',str(caught.exception))
            self.assertNotIn('http',str(caught.exception))
            self.assertEqual(client.series(7),{})

    def test_managed_mode_is_explicit_and_cannot_mix_bearer_token(self):
        with self.assertRaises(ValueError): TMDB()
        with self.assertRaises(ValueError): TMDB('synthetic',managed_http=True)
        client=TMDB(managed_http=True,transport=self.response)
        self.assertEqual(client.auth_mode,'managed_http')


if __name__=='__main__': unittest.main()
