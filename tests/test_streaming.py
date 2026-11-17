"""Synthetic streaming regressions; no personal inputs."""
from dataclasses import replace
import gzip
from pathlib import Path
import tempfile
import unittest

from xcmate_epg.streaming import Limits, StreamStore, SourceError


def document(events=b'', extra=b'', declaration=b''):
    return declaration + b'<tv><channel id="one"><display-name>Fixture</display-name></channel>' + events + extra + b'</tv>'


EVENT = b'<programme channel="one" start="20261117100000 +0100" stop="20261117110000 +0100"><title>Fixture</title><desc>Details</desc><category>Series</category><episode-num system="xmltv_ns">0.1.</episode-num></programme>'


class StreamingTests(unittest.TestCase):
    def test_complete_large_input_without_tree_retention(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); feed=p/'input.gz'
            # Over the old 32 MiB decompressed limit, deliberately repeated records.
            with gzip.open(feed,'wb') as out:
                out.write(b'<tv><channel id="one"><display-name>Fixture</display-name></channel>')
                for _ in range(500000): out.write(EVENT)
                out.write(b'</tv>')
            # This deliberately repetitive load fixture needs a declared ratio override;
            # the production default must still reject such a compression ratio.
            with StreamStore(p/'state.sqlite',limits=replace(Limits(),max_expansion_ratio=10000)) as store:
                report=store.import_source('a',feed)
                self.assertGreater(report['decompressed_bytes'],100_000_000)
                self.assertEqual(report['input_programmes'],500000)
                self.assertEqual(report['exact_duplicates'],499999)
                self.assertEqual(store.counts(),{'channels':1,'programmes':1})
                programme=next(store.iter_programmes())['element']
                self.assertEqual(programme.findtext('desc'),'Details')
                self.assertEqual(programme.findtext('episode-num'),'0.1.')

    def test_bad_event_and_missing_reference_are_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); feed=p/'input.xml'
            bad=EVENT.replace(b'20261117110000',b'20261117090000')
            unknown=EVENT.replace(b'channel="one"',b'channel="missing"')
            feed.write_bytes(document(EVENT+bad+unknown))
            with StreamStore(p/'s') as store:
                r=store.import_source('a',feed)
                self.assertEqual(r['input_programmes'],3)
                self.assertEqual(r['invalid_programmes'],2)
                self.assertEqual(r['invalid_reasons'],{'INVALID_TIME':1,'UNKNOWN_CHANNEL':1})
                self.assertEqual(store.counts()['programmes'],1)

    def test_safe_doctype_and_entity_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); feed=p/'input.xml'
            with StreamStore(p/'s') as store:
                feed.write_bytes(document(EVENT,declaration=b'<!DOCTYPE tv SYSTEM "xmltv.dtd">'))
                self.assertTrue(store.import_source('a',feed)['doctype_present'])
                feed.write_bytes(document(EVENT,declaration=b'<!DOCTYPE tv [<!ENTITY x "bad">]>'))
                r=store.import_source('a',feed)
                self.assertEqual(r['cache_status'],'lastgood')
                self.assertFalse(r['successful'])
                self.assertEqual(store.counts()['programmes'],1)
                with self.assertRaises(SourceError): store.import_source('b',feed)

    def test_limits_bomb_depth_record_and_compressed_size(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); feed=p/'input.gz'
            cases=[(gzip.compress(document(EVENT)),replace(Limits(),max_compressed_bytes=10)),
                   (gzip.compress(document(EVENT+b' '*10000)),replace(Limits(),max_expanded_bytes=1000)),
                   (gzip.compress(document(EVENT+b' '*10000)),replace(Limits(),max_expansion_ratio=2)),
                   (gzip.compress(document(EVENT.replace(b'Details',b'x'*1000))),replace(Limits(),max_record_bytes=500)),
                   (gzip.compress(document(EVENT.replace(b'Details',b'<x>'*20+b'y'+b'</x>'*20))),replace(Limits(),max_depth=10))]
            for i,(payload,limits) in enumerate(cases):
                feed.write_bytes(payload)
                with StreamStore(p/f's{i}',limits=limits) as store, self.assertRaises(SourceError):
                    store.import_source('a',feed)

    def test_late_channel_and_five_sources_unchanged_and_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); feed=p/'input.xml'
            other=EVENT.replace(b'Fixture</title>',b'Alternative</title>')
            feed.write_bytes(b'<tv>'+EVENT+other+b'<channel id="one"><display-name>Fixture</display-name></channel></tv>')
            with StreamStore(p/'s') as store:
                for i in range(5):
                    r=store.import_source(f'a{i}',feed,priority=i)
                    self.assertEqual(r['conflicting_starts'],1)
                    self.assertEqual(r['overlaps'],1)
                self.assertEqual(store.counts(),{'channels':5,'programmes':10})
                self.assertEqual(store.import_source('a0',feed)['cache_status'],'unchanged')
                feed.write_bytes(b'<tv>broken')
                self.assertEqual(store.import_source('a0',feed)['cache_status'],'lastgood')
                self.assertEqual(store.counts()['programmes'],10)

    def test_legacy_schema_orphan_generations_cannot_collide_on_restore(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); feed=root/'feed.xml'; feed.write_bytes(document(EVENT))
            database=root/'store.sqlite'
            with StreamStore(database) as store:
                store.import_source('a',feed)
                # Reproduce an old V2 source prune with surviving generation rows.
                with store.db:
                    store.db.execute('DELETE FROM full_sources WHERE id="a"')
                    store.db.execute('UPDATE full_channels SET generation=9')
                    store.db.execute('UPDATE full_programmes SET generation=9')
            with sqlite3.connect(database) as db:
                columns=[r[1] for r in db.execute('PRAGMA table_info(full_sources)')]
                if 'active' in columns: db.execute('ALTER TABLE full_sources DROP COLUMN active')
            feed.write_bytes(document(EVENT.replace(b'Fixture</title>',b'Restored</title>')))
            with StreamStore(database) as store:
                report=store.import_source('a',feed)
                self.assertTrue(report['successful'])
                self.assertEqual(store.counts(),{'channels':1,'programmes':1})
                self.assertGreater(store.db.execute('SELECT generation FROM full_sources').fetchone()[0],9)
                self.assertEqual(next(store.iter_programmes())['element'].findtext('title'),'Restored')

    def test_accounting_mismatch_is_a_sanitized_error_even_with_assertions_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); feed=root/'feed.xml'; feed.write_bytes(document(EVENT))
            with StreamStore(root/'store.sqlite') as store:
                store.import_source('a',feed)
                feed.write_bytes(document(EVENT.replace(b'Fixture</title>',b'Changed</title>')))
                # Force the old generation collision directly to exercise the guard.
                with self.assertRaisesRegex(SourceError,'^PROGRAMME_ACCOUNTING_MISMATCH$'),store.db:
                    store._parse('a',1,feed,feed.stat().st_size)
                self.assertEqual(store.counts()['programmes'],1)

    def test_dst_and_zero_duration(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); feed=p/'input.xml'
            dst=EVENT.replace(b'20261117100000 +0100',b'20261025023000 +0200').replace(b'20261117110000 +0100',b'20261025023000 +0100')
            feed.write_bytes(document(dst+EVENT.replace(b'20261117110000',b'20261117100000')))
            with StreamStore(p/'s') as store:
                r=store.import_source('a',feed)
                self.assertEqual(r['valid_programmes'],1)
                self.assertEqual(r['invalid_programmes'],1)


if __name__=='__main__': unittest.main()
