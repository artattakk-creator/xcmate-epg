from concurrent.futures import ThreadPoolExecutor
import gzip
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from xcmate_epg.full_fetch import SnapshotCache


class Reply(BytesIO):
    status=200
    headers={'ETag':'generation-1'}
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


class FullFetchTests(unittest.TestCase):
    def test_conditional_304_and_failed_download_keep_accepted_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            calls=[]
            def open_(request,timeout):
                calls.append(dict(request.header_items()))
                if len(calls)==1: return Reply(gzip.compress(b'fixture'))
                raise HTTPError(request.full_url,304,'',{},None)
            cache=SnapshotCache(Path(d),opener=open_)
            first=cache.fetch('s','https://feed.example.invalid/input.gz')
            self.assertFalse((Path(d)/'s.json').exists())
            cache.accept(first)
            second=cache.fetch('s','https://feed.example.invalid/input.gz')
            self.assertEqual(second['path'],first['path'])
            self.assertEqual(second['http_status'],304)
            self.assertEqual(calls[-1]['If-none-match'],'generation-1')
            cache.opener=lambda *a,**k: (_ for _ in ()).throw(OSError('private URL must not leak'))
            failed=cache.fetch('s','https://feed.example.invalid/input.gz')
            self.assertEqual(failed['cache_status'],'lastgood')
            self.assertNotIn('private URL',str(failed))

    def test_five_concurrent_sources_and_url_change_do_not_share_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cache=SnapshotCache(Path(d),opener=lambda *a,**k:Reply(b'fixture'))
            def fetch(i):
                r=cache.fetch(f's{i}',f'https://feed.example.invalid/{i}.gz'); cache.accept(r); return r
            with ThreadPoolExecutor(max_workers=5) as pool: rows=list(pool.map(fetch,range(5)))
            self.assertEqual(len({str(r['path']) for r in rows}),5)
            cache.opener=lambda *a,**k: (_ for _ in ()).throw(OSError())
            with self.assertRaises(ValueError): cache.fetch('s0','https://feed.example.invalid/other.gz')

    def test_denies_credentials_redirects_and_oversized_payload(self):
        with tempfile.TemporaryDirectory() as d:
            cache=SnapshotCache(Path(d),max_bytes=5,opener=lambda *a,**k:Reply(b'oversized'))
            with self.assertRaises(ValueError): cache.fetch('s','https://feed.example.invalid/input')
            self.assertFalse(list(Path(d).glob('*.pending')))
            for url in ('http://feed.example.invalid/a','https://u:p@feed.example.invalid/a','https://feed.example.invalid/a?token=x'):
                with self.assertRaises(ValueError): cache.fetch('s',url)
            def redirect(request,timeout): raise HTTPError(request.full_url,302,'',{'Location':'https://other.example.invalid'},None)
            cache.opener=redirect
            with self.assertRaises(ValueError): cache.fetch('s','https://feed.example.invalid/a')


if __name__=='__main__': unittest.main()
