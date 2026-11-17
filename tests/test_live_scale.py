"""Synthetic coverage of bulk metadata and cast lookups."""
import json
import tempfile
import unittest
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from image_fixtures import PNG
from test_live_artwork import event
from xcmate_epg.artwork import TMDB
from xcmate_epg.fetch import Response
from xcmate_epg.live_artwork import ArtworkEnricher
from xcmate_epg.state import State


SHOW = {'id': 7, 'name': 'Example', 'premiered': '2020-01-01',
        'rating': {'average': 7.8}}
STILL = 'https://static.tvmaze.com/synthetic/episode.png'


def episode(number=2, /, **fields):
    return {'id': number + 10, 'season': 1, 'number': number, 'airdate': '2020-02-03',
            'type': 'regular', **fields}


class BulkMetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = State(temporary.name + '/state.sqlite')
        self.addCleanup(self.state.db.close)
        self.routes = {
            '/search/shows': [{'show': SHOW}],
            '/shows/7': SHOW,
            '/shows/7/cast': [],
            '/shows/7/episodebynumber': episode(),
        }
        self.requests = []

    def transport(self, url, headers):
        parts = urlsplit(url)
        self.requests.append(parts.path)
        if parts.hostname == 'static.tvmaze.com':
            return Response(200, PNG, {'Content-Type': 'image/png'})
        self.assertIn(parts.path, self.routes)
        body = self.routes[parts.path]
        return body if isinstance(body, Response) else Response(200, json.dumps(body).encode(), {})

    def adapter(self, *, bulk=True, **config):
        return ArtworkEnricher(self.state, {'request_interval': 0, 'authorize_tvmaze': True,
                            'tvmaze_bulk_metadata': bulk, **config}, transport=self.transport)

    def programme(self, number=None, *, title='Example', year='2020'):
        channel, programme = event(episode=number is not None, year=year, category='series')
        programme.find('title').text = title
        if number is not None:
            programme.find('episode-num').text = f'0.{number - 1}.'
        return channel, programme

    def test_raw_search_shared_across_title_variants_and_years(self):
        self.routes['/search/shows'].append({'show': {'id': 8, 'name': 'example', 'premiered': '2021-01-04'}})
        self.routes['/shows/8'] = self.routes['/search/shows'][-1]['show']
        self.routes['/shows/8/cast'] = []
        adapter = self.adapter()
        for title, year, identity in [('Example', '2020', 'tv/7'), (' example ', '2021', 'tv/8'),
                                      ('EXAMPLE', '2022', None)]:
            result, _ = adapter.enrich(*self.programme(title=title, year=year))
            self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), identity)
        self.assertEqual(self.requests.count('/search/shows'), 1)
        self.assertEqual(self.requests.count('/shows/7'), 1)
        self.assertEqual(self.requests.count('/shows/8'), 1)

    def test_detail_cache_is_shared_and_revalidated_for_each_binding(self):
        self.routes['/shows/7/episodes'] = [episode()]
        adapter = self.adapter()
        self.assertEqual(adapter.enrich(*self.programme())[0].findtext('episode-num'), 'tv/7')
        self.assertIsNotNone(adapter.enrich(*self.programme(2))[0].find('episode-num[@system="tvmaze"]'))
        self.assertEqual(self.requests.count('/shows/7'), 1)
        self.routes['/search/shows'] = [{'show': dict(SHOW, name='Different')}]
        result, _ = adapter.enrich(*self.programme(title='Different'))
        self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
        self.assertEqual(self.requests.count('/shows/7'), 1)

    def test_invalid_legacy_show_positive_cannot_bind(self):
        key = json.dumps(['live-tvmaze-show-v2', 'example', '2020'])
        stale = dict(SHOW, premiered='2019-01-01')
        self.state.cache(key, stale, 300)
        self.routes['/search/shows'] = []
        result, _ = self.adapter().enrich(*self.programme())
        self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
        self.assertEqual(self.requests, ['/search/shows'])

    def test_bulk_episode_list_reuses_exact_episodes_and_proves_absence(self):
        self.routes['/shows/7/episodes'] = [episode(image={'original': STILL}), episode(3)]
        adapter = self.adapter()
        result, records = adapter.enrich(*self.programme(2))
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7/1/2')
        self.assertEqual([r['url'] for r in records if r['verified']], [STILL])
        for number, year, wanted in [(3, '2020', 'tv/7/1/3'), (8, '2020', None), (2, '2019', None)]:
            result, _ = adapter.enrich(*self.programme(number, year=year))
            self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), wanted)
        self.assertEqual(self.requests.count('/shows/7/episodes'), 1)
        self.assertNotIn('/shows/7/episodebynumber', self.requests)
        self.assertEqual(adapter.summary()['episode_year_mismatched'], 1)
        calls = len(self.requests)
        self.adapter().enrich(*self.programme(2))
        self.adapter(bulk=False).enrich(*self.programme(2))
        self.assertEqual(len(self.requests), calls)

    def test_bulk_abstains_on_duplicate_coordinates_ids_and_specials(self):
        self.routes['/shows/7/episodes'] = [
            episode(2), episode(2, id=100), episode(3, id=200),
            episode(4, id=200), episode(5, type='significant_special'), episode(6, number=None),
        ]
        self.state.cache(json.dumps(['live-tvmaze-episode-v2', 7, 1, 2]), episode(), 300)
        adapter = self.adapter()
        for number in (2, 3, 4, 5, 6):
            result, _ = adapter.enrich(*self.programme(number))
            self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
        self.assertNotIn('/shows/7/episodebynumber', self.requests)
        self.assertEqual(adapter.summary()['ambiguous'], 3)

    def test_unavailable_bulk_list_can_fall_back_and_retry_after_restart(self):
        for invalid in (Response(503, b'', {}), {'not': 'episodes'}, [episode(season=True)]):
            with self.subTest(invalid=invalid):
                self.state.db.execute('DELETE FROM artwork')
                self.requests.clear()
                self.routes['/shows/7/episodes'] = invalid
                result, _ = self.adapter().enrich(*self.programme(2))
                self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7/1/2')
                self.assertIn('/shows/7/episodebynumber', self.requests)
                self.routes['/shows/7/episodes'] = [episode(3)]
                result, _ = self.adapter().enrich(*self.programme(3))
                self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7/1/3')
                self.assertEqual(self.requests.count('/shows/7/episodes'), 2)

    def test_api_budget_failure_does_not_cache_missing_episodes(self):
        self.routes['/shows/7/episodes'] = [episode()]
        adapter = self.adapter(max_requests=2)
        result, _ = adapter.enrich(*self.programme(2))
        self.assertIsNone(result.find('episode-num[@system="tvmaze"]'))
        self.assertEqual(adapter.summary()['tvmaze_api_attempts'], 2)
        self.assertGreater(adapter.summary()['api_budget_exhausted'], 0)
        result, _ = self.adapter().enrich(*self.programme(2))
        self.assertEqual(result.findtext('episode-num[@system="tvmaze"]'), 'tv/7/1/2')

    def test_tvmaze_episode_keeps_own_rating_scope(self):
        adapter = self.adapter(bulk=False)
        result, _ = adapter.enrich(*self.programme(2))
        self.assertIsNone(result.find('star-rating'))
        self.state.db.execute('DELETE FROM artwork')
        self.routes['/shows/7/episodebynumber'] = episode(rating={'average': 8.3})
        result, _ = adapter.enrich(*self.programme(2))
        self.assertEqual(result.findtext('star-rating/value'), '8.3/10')
        source = self.programme(2)
        ET.SubElement(ET.SubElement(source[1], 'star-rating', system='Source'), 'value').text = '4/5'
        result, _ = adapter.enrich(*source)
        self.assertEqual([(n.get('system'), n.findtext('value')) for n in result.findall('star-rating')],
                         [('Source', '4/5')])
        result, _ = adapter.enrich(*self.programme())
        self.assertEqual(result.findtext('star-rating/value'), '7.8/10')

    def test_tvmaze_series_cast_appends_valid_missing_actors(self):
        self.routes['/shows/7'] = dict(SHOW, image={'original': STILL})
        self.routes['/shows/7/cast'] = [
            {'person': {'name': ' First Actor '}, 'character': {'name': 'A Person'}},
            {'person': {'name': 'Already credited'}, 'character': {'name': 'New role'}},
            {'person': {'name': 'first actor'}, 'character': {'name': 'Duplicate'}},
            {'person': {'name': '<Bad Name>'}, 'character': {'name': 'A Role'}},
            {'person': {'name': 'Second Actor'}, 'character': {'name': 'Bad\u0000role'}},
        ]
        channel, programme = self.programme()
        original = ET.SubElement(programme, 'credits')
        ET.SubElement(original, 'director').text = 'Source Director'
        ET.SubElement(original, 'actor', role='Source Role').text = 'ALREADY CREDITED'
        ET.SubElement(original, 'writer').text = 'Source Writer'
        adapter = self.adapter()
        result, records = adapter.enrich(channel, programme)
        self.assertEqual([(n.tag, n.text, n.get('role')) for n in result.find('credits')], [
            ('director', 'Source Director', None), ('actor', 'ALREADY CREDITED', 'Source Role'),
            ('actor', 'First Actor', 'A Person'), ('actor', 'Second Actor', None),
            ('writer', 'Source Writer', None),
        ])
        evidence = {'provider': 'tvmaze', 'provider_id': 7, 'scope': 'series', 'applied_to_xmltv': True,
                    'actors': [{'name': 'First Actor', 'character': 'A Person'}, {'name': 'Second Actor'}]}
        self.assertEqual(records[0]['evidence']['credits'], evidence)
        self.assertEqual(adapter.last_metadata['credits'], evidence)
        adapter.enrich(*self.programme())
        self.assertEqual(self.requests.count('/shows/7/cast'), 1)

    def test_tvmaze_episode_cast_remains_private_series_evidence(self):
        self.routes['/shows/7/episodes'] = [episode(image={'original': STILL}), episode(3)]
        self.routes['/shows/7/cast'] = [{'person': {'name': 'Series Actor'}, 'character': {'name': 'Role'}}]
        channel, programme = self.programme(2)
        ET.SubElement(ET.SubElement(programme, 'credits'), 'actor').text = 'Source Actor'
        adapter = self.adapter()
        result, records = adapter.enrich(channel, programme)
        self.assertEqual([n.text for n in result.findall('credits/actor')], ['Source Actor'])
        evidence = {'provider': 'tvmaze', 'provider_id': 7, 'scope': 'series', 'applied_to_xmltv': False,
                    'actors': [{'name': 'Series Actor', 'character': 'Role'}]}
        self.assertEqual(records[0]['evidence']['credits'], evidence)
        result, records = adapter.enrich(*self.programme(3))
        self.assertIsNone(result.find('credits'))
        self.assertEqual(records, [])
        self.assertEqual(adapter.last_metadata['credits'], evidence)
        self.assertEqual(self.requests.count('/shows/7/cast'), 1)

    def test_tvmaze_bulk_uses_only_verified_episode_plot_and_subtitle(self):
        self.routes['/shows/7'] = dict(SHOW, summary='<p>Series plot</p>')
        self.routes['/shows/7/episodes'] = [
            episode(name='Episode Title', summary='<p>Episode plot &amp; detail</p>'), episode(3),
        ]
        adapter = self.adapter()
        result, _ = adapter.enrich(*self.programme(2))
        self.assertEqual(result.findtext('sub-title'), 'Episode Title')
        self.assertEqual(result.findtext('desc'), 'Episode plot & detail')
        result, _ = adapter.enrich(*self.programme(3))
        self.assertIsNone(result.find('desc'))
        self.assertIsNone(result.find('sub-title'))
        channel, programme = self.programme(2)
        ET.SubElement(programme, 'sub-title').text = 'Source Title'
        ET.SubElement(programme, 'desc').text = 'Source Plot'
        result, _ = adapter.enrich(channel, programme)
        self.assertEqual([n.text for n in result.findall('sub-title')], ['Source Title'])
        self.assertEqual([n.text for n in result.findall('desc')], ['Source Plot'])
    def test_tmdb_credits_are_opt_in_and_bound_to_verified_provider_id(self):
        self.routes.update({
            '/3/search/movie': {'results': [{'id': 1, 'title': 'Example', 'release_date': '2020-02-03'}]},
            '/3/movie/1': {'id': 1, 'title': 'Example', 'release_date': '2020-02-03'},
            '/3/movie/1/credits': {'id': 2, 'cast': [{'name': 'Wrong Actor'}]},
        })
        def adapter(**config):
            return ArtworkEnricher(self.state, {'request_interval': 0, **config},
                                    tmdb=TMDB('synthetic-test'), transport=self.transport)
        result, _ = adapter().enrich(*event())
        self.assertIsNone(result.find('credits'))
        self.assertNotIn('/3/movie/1/credits', self.requests)
        result, _ = adapter(tmdb_credits=True).enrich(*event())
        self.assertIsNone(result.find('credits'))
        self.routes['/3/movie/1/credits'] = {'id': 1, 'cast': [{'name': 'Movie Actor', 'character': 'Movie role'}]}
        authorized = adapter(tmdb_credits=True)
        result, _ = authorized.enrich(*event())
        self.assertEqual(result.findtext('credits/actor'), 'Movie Actor')
        self.assertEqual(authorized.last_metadata['credits']['scope'], 'movie')
        self.assertTrue(authorized.last_metadata['credits']['applied_to_xmltv'])
        self.assertEqual(authorized.summary()['tmdb_api_attempts'], 1)
        self.assertEqual(authorized.summary()['tvmaze_api_attempts'], 0)
        self.assertEqual(adapter(tmdb_credits=True).enrich(*event())[0].findtext('credits/actor'), 'Movie Actor')
        self.assertEqual(self.requests.count('/3/movie/1/credits'), 2)

    def test_tmdb_series_cast_scope_and_provider_error_counters(self):
        self.routes.update({
            '/3/search/tv': {'results': [{'id': 2, 'name': 'Example', 'first_air_date': '2020-02-03'}]},
            '/3/tv/2': {'id': 2, 'name': 'Example', 'first_air_date': '2020-02-03'},
            '/3/tv/2/aggregate_credits': {'id': 2, 'cast': [{'name': 'Series Actor', 'roles': [{'character': 'Role'}]}]},
            '/3/tv/2/season/1/episode/2': {'id': 3, 'season_number': 1, 'episode_number': 2, 'air_date': '2020-02-04'},
            '/3/tv/2/season/1': {'id': 4, 'season_number': 1},
        })
        adapter = ArtworkEnricher(self.state, {'request_interval': 0, 'tmdb_credits': True},
                                  tmdb=TMDB('synthetic-test'), transport=self.transport)
        result, _ = adapter.enrich(*self.programme())
        self.assertEqual(result.find('credits/actor').attrib, {'role': 'Role'})
        self.assertEqual(adapter.last_metadata['credits']['scope'], 'series')
        self.assertEqual(adapter.summary()['tmdb_api_successes'], 3)
        result, _ = adapter.enrich(*self.programme(2))
        self.assertIsNone(result.find('credits'))
        self.assertEqual(adapter.last_metadata['credits']['scope'], 'series')
        self.assertFalse(adapter.last_metadata['credits']['applied_to_xmltv'])
        self.routes['/shows/7/episodes'] = Response(503, b'', {})
        tvmaze = self.adapter()
        tvmaze.enrich(*self.programme(2))
        self.assertEqual(tvmaze.summary()['tvmaze_api_attempts'], 5)
        self.assertEqual(tvmaze.summary()['tvmaze_api_successes'], 4)
        self.assertEqual(tvmaze.summary()['tvmaze_api_errors'], 1)
        self.assertEqual(tvmaze.summary()['tmdb_api_errors'], 0)
