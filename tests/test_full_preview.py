import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from xcmate_epg.full_preview import run_full


XML=b'<tv><channel id="one"><display-name>Fixture</display-name></channel><programme channel="one" start="20261117100000 +0100" stop="20261117110000 +0100"><title>Fixture</title><desc>Keep all metadata</desc><category>News</category></programme></tv>'


class FullPreviewTests(unittest.TestCase):
    def make(self,d):
        p=Path(d); source=p/'source.xml'; source.write_bytes(XML)
        config={'sources':[{'id':'s1','path':str(source)}],'minimum_sources':1}
        return p,source,config

    def test_full_input_and_empty_device_are_not_falsely_full_success(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.make(d)
            report=run_full(config,p/'private')
            self.assertEqual(report['full']['channels'],1)
            self.assertEqual(report['full']['programmes'],1)
            self.assertEqual(report['device']['programmes'],0)
            self.assertNotEqual(report['status'],'PRIVATE_FULL_EPG_CREATED')
            self.assertIn('PARTIAL_COVERAGE',report['status_flags'])
            self.assertIn('ARTWORK_BLOCKED',report['status_flags'])

    def test_only_explicit_confirmations_populate_device_profile(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.make(d)
            config['mapping']={'catalog':[{'provider_id':'fixture-id','name':'Fixture'}],
                               'confirmations':[{'provider_id':'fixture-id','qualified_id':'s1:one','evidence':'manual fixture'}],
                               'catalog_complete':True}
            report=run_full(config,p/'private')
            self.assertEqual(report['device']['programmes'],1)
            self.assertEqual(report['mapping']['mapped_provider_channels'],1)
            self.assertEqual(report['mapping']['coverage'],1)
            self.assertEqual(report['status'],'ARTWORK_BLOCKED')

    def test_unchanged_replay_and_missing_source_keep_lastgood(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.make(d)
            first=run_full(config,p/'private')
            archive=p/'private/full/xcmate-epg.xml.gz'; before=archive.read_bytes()
            second=run_full(config,p/'private')
            self.assertEqual(second['full']['sha256'],first['full']['sha256'])
            source.unlink()
            fallback=run_full(config,p/'private')
            self.assertEqual(fallback['status'],'SOURCE_BLOCKED')
            self.assertEqual(archive.read_bytes(),before)
            self.assertEqual(fallback['sources'][0]['cache_status'],'lastgood')

    def test_removed_source_retains_cache_and_rejoins_without_stale_events(self):
        import gzip
        import sqlite3
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as d:
                root,source,config=self.make(d)
                b=root/'b.xml'; b.write_bytes(XML)
                config['sources'].append({'id':'b','path':str(b)})
                all_sources=list(config['sources']); private=root/'private'
                self.assertEqual(run_full(config,private)['full']['programmes'],2)
                config['sources']=[all_sources[1]]
                self.assertEqual(run_full(config,private)['full']['programmes'],1)
                with sqlite3.connect(private/'stream.sqlite') as db:
                    self.assertIsNotNone(db.execute('SELECT sha FROM full_sources WHERE id="s1"').fetchone())
                    self.assertEqual(db.execute('SELECT count(*) FROM full_programmes WHERE source="s1"').fetchone()[0],1)
                if changed: source.write_bytes(XML.replace(b'Fixture</title>',b'Restored</title>'))
                config['sources']=all_sources
                report=run_full(config,private)
                self.assertEqual(report['full']['programmes'],2)
                self.assertEqual(report['sources'][0]['cache_status'],'changed' if changed else 'unchanged')
                xml=gzip.decompress((private/'full/xcmate-epg.xml.gz').read_bytes())
                self.assertEqual(xml.count(b'Restored</title>'),int(changed))
                config['sources']=[all_sources[1]]; run_full(config,private)
                before=(private/'full/xcmate-epg.xml.gz').read_bytes()
                source.write_bytes(b'<tv>broken')
                config['sources']=all_sources
                blocked=run_full(config,private)
                self.assertEqual(blocked['status'],'SOURCE_BLOCKED')
                self.assertEqual(blocked['sources'][0]['cache_status'],'lastgood')
                self.assertEqual((private/'full/xcmate-epg.xml.gz').read_bytes(),before)

    def test_header_only_images_cannot_satisfy_full_success_gates(self):
        from xcmate_epg.live_artwork import ArtworkEnricher
        from xcmate_epg.fetch import Response
        from xcmate_epg.state import State
        from test_live_artwork import MovieClient
        with tempfile.TemporaryDirectory() as d:
            root,source,config=self.make(d)
            source.write_bytes(XML.replace(b'Fixture</title>',b'Example</title>').replace(
                b'<category>News</category>',b'<category>movie</category><date>2020</date>'))
            config['mapping']={'catalog':[{'provider_id':'fixture','name':'Fixture'}],'catalog_complete':True,
                'confirmations':[{'provider_id':'fixture','qualified_id':'s1:one','evidence':'synthetic explicit'}]}
            config['artwork']={'confirmed_source_logos':[{'qualified_id':'s1:one',
                'url':'https://image.tmdb.org/logo.png','evidence':'synthetic explicit','rights_reviewed':True}]}
            with State(root/'art.sqlite') as state:
                adapter=ArtworkEnricher(state,{'request_interval':0},tmdb=MovieClient(),transport=lambda *args:
                    Response(200,b'\x89PNG\r\n\x1a\n',{'Content-Type':'image/png'}))
                report=run_full(config,root/'private',metadata=adapter)
            self.assertNotEqual(report['status'],'PRIVATE_FULL_EPG_CREATED')
            self.assertIn('ARTWORK_BLOCKED',report['status_flags'])
            self.assertEqual(adapter.summary()['verified_images'],0)

    def test_horizon_counts_and_source_addresses_do_not_leak(self):
        with tempfile.TemporaryDirectory() as d:
            p,source,config=self.make(d)
            config['horizon']={'reference_time':'2026-11-17T00:00:00+00:00','past_days':7,'future_days':20}
            outside=XML.replace(b'20261117',b'20300101')
            source.write_bytes(outside.replace(b'</tv>',XML[XML.index(b'<programme'):XML.index(b'</tv>')]+b'</tv>'))
            report=run_full(config,p/'private')
            self.assertEqual(report['horizon']['excluded_programmes'],1)
            self.assertEqual(report['full']['programmes'],1)
            self.assertNotIn(str(source),json.dumps(report))


if __name__=='__main__': unittest.main()

class ReviewRegressions(unittest.TestCase):
    def test_confirmed_empty_channel_never_counts_as_full_device_coverage(self):
        from xml.etree import ElementTree as ET
        class Verified:
            def verify_media(self,url): return {'verified':True}
            def enrich(self,channel,event):
                url='https://image.tmdb.org/t/p/w500/fixture.jpg'
                ET.SubElement(event,'icon',{'src':url})
                return event,[{'verified':True,'url':url,'type':'poster','provider':'tmdb'}]
            def summary(self): return {}
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'source.xml'
            source.write_bytes(XML.replace(b'</channel>',b'<icon src="https://image.tmdb.org/t/p/w500/logo.png"/></channel>',1).replace(b'</tv>',b'<channel id="empty"><display-name>Empty</display-name></channel></tv>'))
            config={'sources':[{'id':'s1','path':str(source)}],
                'mapping':{'catalog':[{'provider_id':'locked','name':'Empty'}],'catalog_complete':True,
                'confirmations':[{'provider_id':'locked','qualified_id':'s1:empty','evidence':'manual'}]},
                'artwork':{'confirmed_source_logos':[{'qualified_id':'s1:one','url':'https://image.tmdb.org/t/p/w500/logo.png','evidence':'manual','rights_reviewed':True}]}}
            report=run_full(config,root/'private',metadata=Verified())
            self.assertNotEqual(report['status'],'PRIVATE_FULL_EPG_CREATED')
            self.assertIn('PARTIAL_COVERAGE',report['status_flags'])
            self.assertEqual(report['mapping']['mapped_channels_with_programmes'],0)
            self.assertEqual(report['mapping']['mapped_channels_without_programmes'],1)
            self.assertEqual(report['device']['programmes'],0)

    def test_later_view_and_report_failures_preserve_whole_generation(self):
        from unittest.mock import patch
        from xcmate_epg.full_export import publish_stream,_copy_atomic
        from xcmate_epg.export import atomic
        for failure in ('device','quality-report.json','run-manifest.json','device-alias','provider-logo-preparation'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                root,source,config=FullPreviewTests().make(d); private=root/'private'
                run_full(config,private)
                tracked=['full/manifest.json','full/xcmate-epg.xml.gz','device/manifest.json',
                         'device/xcmate-epg.xml.gz','quality-report.json','source-media.jsonl',
                         'artwork-metadata.jsonl','mapping-review.json','run-manifest.json']
                before={name:(private/name).read_bytes() if (private/name).exists() else None for name in tracked}
                config['mapping']={'catalog':[{'provider_id':'f','name':'Fixture'}],'confirmations':[{'provider_id':'f','qualified_id':'s1:one','evidence':'manual'}]}
                source.write_bytes(XML.replace(b'Keep all metadata',b'Changed'))
                def publish(*args,**kwargs):
                    if failure=='device' and kwargs.get('view')=='device': raise OSError('synthetic device failure')
                    return publish_stream(*args,**kwargs)
                if failure=='provider-logo-preparation':
                    config['provider_logos']={'inventory_path':str(root/'missing.json'),'files_root':str(root)}
                failed_alias=False
                def copy_file(source_path,target):
                    nonlocal failed_alias
                    if failure=='device-alias' and target==private/'device/xcmate-epg.xml.gz' and not failed_alias:
                        failed_alias=True
                        raise OSError('synthetic convenience failure')
                    return _copy_atomic(source_path,target)
                def commit(path,data):
                    if failure=='run-manifest.json' and path==private/'run-manifest.json': raise OSError('synthetic run commit failure')
                    return atomic(path,data)
                def write(path,data):
                    if failure=='quality-report.json' and path.name==failure: raise OSError('synthetic report failure')
                    return atomic(path,data)
                with patch('xcmate_epg.full_preview.publish_stream',side_effect=publish), patch('xcmate_epg.full_preview.atomic',side_effect=write), patch('xcmate_epg.full_export.atomic',side_effect=commit), patch('xcmate_epg.full_export._copy_atomic',side_effect=copy_file),self.assertRaises(OSError):
                    run_full(config,private)
                for name,payload in before.items():
                    self.assertEqual((private/name).read_bytes() if (private/name).exists() else None,payload,name)
                self.assertFalse(list(private.glob('.*pending*')))

    def test_memory_limit_applies_after_parse_and_on_cache_replay(self):
        import subprocess,sys,textwrap
        script=textwrap.dedent('''
            import json,resource,sys,sqlite3
            from pathlib import Path
            from xcmate_epg.full_preview import run_full
            from xcmate_epg.streaming import SourceError
            root=Path(sys.argv[1]); source=root/'source.xml'
            config={'sources':[{'id':'s1','path':str(source)}], 'limits':{'max_memory_bytes':40*1024*1024}}
            try:
                r=run_full(config,root/'private')
                blocked=r['status']=='SOURCE_BLOCKED'
            except SourceError as error:
                blocked=str(error)=='PROCESS_MEMORY_LIMIT'
            db=sqlite3.connect(root/'private/stream.sqlite')
            cached=db.execute('SELECT generation FROM full_sources WHERE id="s1"').fetchone()
            print(json.dumps({'blocked':blocked,'manifest':(root/'private/full/manifest.json').exists(),'parsed_generation':cached[0] if cached else None}))
        ''')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); source=root/'source.xml'
            with source.open('wb') as out:
                out.write(b'<tv>')
                for i in range(20000): out.write(('<channel id="c%d"><display-name>Fixture</display-name></channel>'%i).encode())
                out.write(XML[XML.index(b'<programme'):].replace(b'channel="one"',b'channel="c0"'))
            for attempt in range(2):
                result=subprocess.run([sys.executable,'-c',script,d],capture_output=True,text=True,check=True)
                report=json.loads(result.stdout)
                self.assertTrue(report['blocked'],(attempt,report))
                self.assertFalse(report['manifest'],(attempt,report))
                self.assertEqual(report['parsed_generation'],1,(attempt,report))

    def test_run_manifest_pins_complete_bundle_and_replay_keeps_view_manifests(self):
        with tempfile.TemporaryDirectory() as d:
            root,source,config=FullPreviewTests().make(d); private=root/'private'
            run_full(config,private)
            current=json.loads((private/'run-manifest.json').read_text())
            bundle=private/current['directory']
            for relative,digest in current['files'].items():
                self.assertEqual(hashlib.sha256((bundle/relative).read_bytes()).hexdigest(),digest)
            mtimes={view:(private/view/'manifest.json').stat().st_mtime_ns for view in ('full','device')}
            run_full(config,private)
            for view,mtime in mtimes.items(): self.assertEqual((private/view/'manifest.json').stat().st_mtime_ns,mtime)
            self.assertTrue(bundle.is_dir())

    def test_empty_declared_channels_and_time_envelope_gaps_are_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            root,source,config=FullPreviewTests().make(d)
            source.write_bytes(XML.replace(b'</tv>',b'<channel id="empty"><display-name>Empty</display-name></channel></tv>'))
            config['horizon']={'reference_time':'2026-11-17T08:00:00+00:00','past_days':0,'future_days':1}
            report=run_full(config,root/'private')
            for counts in (report['full'],report['sources'][0]):
                self.assertEqual(counts['channels_with_programmes'],1)
                self.assertEqual(counts['channels_without_programmes'],1)
            for envelope in (report['horizon']['time_envelope'],report['sources'][0]['time_envelope']):
                self.assertEqual(envelope['leading_missing_seconds'],3600)
                self.assertEqual(envelope['trailing_missing_seconds'],22*3600)
                self.assertEqual(envelope['coverage_kind'],'time_envelope_only')
                self.assertIsNone(envelope['missing_programme_count'])
                self.assertFalse(envelope['internal_gaps_evaluated'])

    def test_reused_bundle_corruption_or_missing_member_fails_before_current_updates(self):
        for damage in ('corrupt','missing'):
            for remove_alias in (False,True):
                with self.subTest(damage=damage,remove_alias=remove_alias),tempfile.TemporaryDirectory() as d:
                    root,source,config=FullPreviewTests().make(d); private=root/'private'
                    config['horizon']={'reference_time':'2026-11-17T08:00:00+00:00'}
                    run_full(config,private); run_full(config,private)
                    committed=(private/'run-manifest.json').read_bytes()
                    bundle=private/json.loads(committed)['directory']
                    archive=bundle/'full/xcmate-epg.xml.gz'
                    if damage=='corrupt': archive.write_bytes(b'corrupt')
                    else: archive.unlink()
                    if remove_alias: (private/'full/xcmate-epg.xml.gz').unlink()
                    tracked=['full/manifest.json','full/xcmate-epg.xml.gz','device/manifest.json',
                             'device/xcmate-epg.xml.gz','quality-report.json','source-media.jsonl',
                             'artwork-metadata.jsonl','mapping-review.json','run-manifest.json']
                    before={name:(private/name).read_bytes() if (private/name).exists() else None for name in tracked}
                    with self.assertRaisesRegex(ValueError,'INVALID_RUN_BUNDLE'):
                        run_full(config,private)
                    for name,payload in before.items():
                        self.assertEqual((private/name).read_bytes() if (private/name).exists() else None,payload,name)
                    self.assertEqual((private/'run-manifest.json').read_bytes(),committed)
                    self.assertFalse(list(private.glob('.*pending*')))
