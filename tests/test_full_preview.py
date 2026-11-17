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
