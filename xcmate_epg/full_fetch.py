"""Bounded disk snapshots; validators become durable only after XML validation."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import Request,build_opener

from .export import atomic
from .fetch import NoRedirect
from .streaming import file_sha


class SnapshotCache:
    def __init__(self,root,*,max_bytes=64*1024*1024,max_seconds=300,opener=None):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.max_bytes=max_bytes; self.max_seconds=max_seconds
        self.opener=opener or build_opener(NoRedirect).open

    def fetch(self,identifier,url):
        parts=urlsplit(url)
        if (not re.fullmatch(r'[A-Za-z0-9_-]+',identifier) or parts.scheme!='https'
                or not parts.hostname or parts.port not in (None,443) or parts.username or parts.password or parts.query or parts.fragment):
            raise ValueError('CREDENTIAL_FREE_HTTPS_SOURCE_REQUIRED')
        identity=hashlib.sha256(url.encode()).hexdigest()
        index=self.root/(identifier+'.json'); old=None
        try:
            candidate=json.loads(index.read_text())
            if candidate['identity']==identity:
                old_path=self.root/candidate['file']
                # Do not trust an on-disk index to escape the private cache.
                if (old_path.parent!=self.root or old_path.is_symlink()
                        or old_path.stat().st_nlink!=1): raise ValueError()
                sha,size=file_sha(old_path,self.max_bytes)
                if sha==candidate['sha256']: old=candidate
        except (OSError,ValueError,KeyError): pass
        headers={'User-Agent':'XCMateEPG/0.1 private-one-shot-preview'}
        if old:
            if old.get('etag'): headers['If-None-Match']=old['etag']
            if old.get('modified'): headers['If-Modified-Since']=old['modified']
        http_status=None; temporary=None
        try:
            try:
                response=self.opener(Request(url,headers=headers),timeout=20)
            except HTTPError as error:
                http_status=error.code
                if error.code==304 and old:
                    return dict(old,id=identifier,path=str(self.root/old['file']),http_status=304,
                                cache_status='unchanged',successful=True)
                raise ValueError('HTTP_SOURCE_FAILURE') from None
            with response:
                http_status=response.status
                if response.status!=200: raise ValueError('HTTP_SOURCE_FAILURE')
                fd,temporary=tempfile.mkstemp(prefix=identifier+'-',suffix='.pending',dir=self.root)
                digest=hashlib.sha256(); size=0; started=time.monotonic()
                with os.fdopen(fd,'wb') as stream:
                    while True:
                        chunk=response.read(65536)
                        if not chunk: break
                        size+=len(chunk)
                        if size>self.max_bytes: raise ValueError('DOWNLOAD_SIZE_LIMIT')
                        if time.monotonic()-started>self.max_seconds: raise ValueError('DOWNLOAD_TIME_LIMIT')
                        digest.update(chunk); stream.write(chunk)
                    stream.flush(); os.fsync(stream.fileno())
                sha=digest.hexdigest(); filename=identifier+'-'+identity+'-'+sha+'.bin'
                path=self.root/filename
                if path.is_symlink() or (path.exists() and path.stat().st_nlink!=1):
                    raise ValueError('UNSAFE_CACHE_FILE')
                os.replace(temporary,path); temporary=None; path.chmod(0o600)
                h={k.lower():v for k,v in response.headers.items()}
                return {'id':identifier,'identity':identity,'file':filename,'path':str(path),
                        'sha256':sha,'compressed_bytes':size,'etag':h.get('etag'),
                        'modified':h.get('last-modified'),'http_status':200,'cache_status':'changed',
                        'successful':True}
        except (OSError,URLError,ValueError):
            if old:
                return dict(old,id=identifier,path=str(self.root/old['file']),http_status=http_status,
                            cache_status='lastgood',successful=False,error='NETWORK_OR_DOWNLOAD_FAILURE')
            raise ValueError('SOURCE_UNAVAILABLE_NO_VALID_SNAPSHOT') from None
        finally:
            if temporary and os.path.exists(temporary): os.unlink(temporary)

    def accept(self,result):
        """Caller invokes ONLY after that candidate was successfully XML validated."""
        if not result.get('successful'): return
        identifier=result['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+',identifier): raise ValueError('INVALID_SOURCE_ID')
        value={k:result.get(k) for k in ('identity','file','sha256','etag','modified','compressed_bytes')}
        path=self.root/value['file']
        if path.parent!=self.root or path.is_symlink() or path.stat().st_nlink!=1:
            raise ValueError('UNSAFE_CACHE_FILE')
        sha,size=file_sha(path,self.max_bytes)
        if sha!=value['sha256']: raise ValueError('CACHE_CHECKSUM_MISMATCH')
        value['compressed_bytes']=size
        atomic(self.root/(identifier+'.json'),(json.dumps(value,sort_keys=True)+'\n').encode())
