"""Bounded full XMLTV imports into a private transactional SQLite spool.

No full feed tree or decompressed feed buffer is constructed. Alternative
programmes are retained; only byte-equivalent records within a source deduplicate.
"""
from collections import Counter
from dataclasses import asdict, dataclass
import codecs
import gzip
import hashlib
import json
from pathlib import Path
import re
import resource
import sqlite3
import time
import zlib
from xml.etree import ElementTree as ET
from xml.parsers import expat

from .xmltv import timestamp


@dataclass(frozen=True)
class Limits:
    max_compressed_bytes: int = 64 * 1024 * 1024
    max_expanded_bytes: int = 256 * 1024 * 1024
    max_expansion_ratio: int = 200
    max_records: int = 1_000_000
    max_record_bytes: int = 2 * 1024 * 1024
    max_depth: int = 32
    max_seconds: int = 300
    max_memory_bytes: int = 512 * 1024 * 1024
    max_store_bytes: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError('INVALID_STREAM_LIMITS')


class SourceError(ValueError):
    """A sanitized structural/source failure, never an input address."""


def memory_guard(limits):
    """Enforce the configured process peak budget throughout import and export."""
    if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024>limits.max_memory_bytes:
        raise SourceError('PROCESS_MEMORY_LIMIT')


def file_sha(path, limit):
    digest=hashlib.sha256(); size=0
    with Path(path).open('rb') as stream:
        while True:
            chunk=stream.read(65536)
            if not chunk: break
            size += len(chunk)
            if size > limit: raise SourceError('COMPRESSED_SIZE_LIMIT')
            digest.update(chunk)
    return digest.hexdigest(), size


class StreamStore:
    def __init__(self, path, *, limits=None):
        self.path=Path(path)
        self.limits=limits or Limits()
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        PRAGMA cache_size=-4096;
        PRAGMA temp_store=FILE;
        CREATE TABLE IF NOT EXISTS full_sources(
          id TEXT PRIMARY KEY,generation INTEGER NOT NULL,sha TEXT NOT NULL,
          fingerprint TEXT NOT NULL,priority INTEGER NOT NULL,report TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS full_channels(
          source TEXT,generation INTEGER,raw_id TEXT,qualified_id TEXT,name TEXT,payload BLOB,
          PRIMARY KEY(source,generation,raw_id));
        CREATE TABLE IF NOT EXISTS full_programmes(
          source TEXT,generation INTEGER,sequence INTEGER,raw_channel TEXT,qualified_id TEXT,
          start REAL,stop REAL,fingerprint TEXT,payload BLOB,
          PRIMARY KEY(source,generation,fingerprint));
        CREATE INDEX IF NOT EXISTS full_event_order ON full_programmes(source,generation,qualified_id,start,sequence);
        ''')

    def __enter__(self): return self
    def __exit__(self,*args): self.db.close()

    def import_source(self, identifier, path, priority=100, *, identity=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',identifier): raise SourceError('INVALID_SOURCE_ID')
        if type(priority) is not int: raise SourceError('INVALID_SOURCE_PRIORITY')
        memory_guard(self.limits)
        path=Path(path)
        fingerprint=hashlib.sha256(json.dumps([identity or str(path.absolute()),asdict(self.limits)],sort_keys=True).encode()).hexdigest()
        old=self.db.execute('SELECT * FROM full_sources WHERE id=?',(identifier,)).fetchone()
        try:
            sha,size=file_sha(path,self.limits.max_compressed_bytes)
            if old and old['fingerprint']==fingerprint and old['sha']==sha:
                # Priority is an output policy, not a reason to reparse an identical source.
                with self.db: self.db.execute('UPDATE full_sources SET priority=? WHERE id=?',(priority,identifier))
                return dict(json.loads(old['report']),cache_status='unchanged',successful=True)
            generation=old['generation']+1 if old else 1
            with self.db:
                report=self._parse(identifier,generation,path,size)
                report.update(id=identifier,sha256=sha,cache_status='changed',successful=True)
                self.db.execute('INSERT OR REPLACE INTO full_sources VALUES(?,?,?,?,?,?)',
                                (identifier,generation,sha,fingerprint,priority,json.dumps(report)))
                self.db.execute('DELETE FROM full_channels WHERE source=? AND generation!=?',(identifier,generation))
                self.db.execute('DELETE FROM full_programmes WHERE source=? AND generation!=?',(identifier,generation))
            return report
        except (OSError,ValueError,expat.ExpatError,EOFError,zlib.error) as error:
            code=str(error) if isinstance(error,SourceError) else 'INVALID_XML_GZIP_OR_INPUT'
            if old and old['fingerprint']==fingerprint:
                return dict(json.loads(old['report']),cache_status='lastgood',successful=False,error=code)
            raise SourceError(code) from None

    def _parse(self, source, generation, path, compressed_size):
        limits=self.limits; started=time.monotonic()
        report={'compressed_bytes':compressed_size,'decompressed_bytes':0,'encoding':'UTF-8',
                'doctype_present':False,'input_channels':0,'input_programmes':0,
                'valid_programmes':0,'invalid_programmes':0,'exact_duplicates':0}
        invalid=Counter(); depth=0; builder=None; record_bytes=0; sequence=0
        earliest=None; latest=None; serialized=0
        def guard():
            if time.monotonic()-started>limits.max_seconds: raise SourceError('SOURCE_TIME_LIMIT')
            memory_guard(limits)
        def reject(*args): raise SourceError('UNSAFE_DTD_OR_ENTITY')
        def declaration(version,encoding,standalone):
            if encoding and encoding.lower().replace('-','')!='utf8': raise SourceError('ENCODING_NOT_UTF8')
        def doctype(name,system,public,internal):
            if name!='tv' or system!='xmltv.dtd' or public or internal: reject()
            report['doctype_present']=True
        def start(tag,attrs):
            nonlocal depth,builder,record_bytes
            depth+=1
            if depth>limits.max_depth: raise SourceError('XML_DEPTH_LIMIT')
            if depth==1:
                if tag!='tv': raise SourceError('XMLTV_ROOT_REQUIRED')
                if sum(len(k.encode())+len(v.encode()) for k,v in attrs.items())>limits.max_record_bytes:
                    raise SourceError('RECORD_SIZE_LIMIT')
                return
            if depth==2: builder=ET.TreeBuilder(); record_bytes=0
            record_bytes+=len(tag.encode())+sum(len(k.encode())+len(v.encode()) for k,v in attrs.items())
            if record_bytes>limits.max_record_bytes: raise SourceError('RECORD_SIZE_LIMIT')
            builder.start(tag,attrs)
        def data(value):
            nonlocal record_bytes
            if depth>1:
                record_bytes+=len(value.encode('utf-8'))
                if record_bytes>limits.max_record_bytes: raise SourceError('RECORD_SIZE_LIMIT')
                builder.data(value)
        def end(tag):
            nonlocal depth,builder,sequence,earliest,latest,serialized
            if depth>1: builder.end(tag)
            if depth==2:
                node=builder.close(); builder=None; node.tail=None
                if tag in ('channel','programme'):
                    report['input_'+('channels' if tag=='channel' else 'programmes')]+=1
                    if report['input_channels']+report['input_programmes']>limits.max_records:
                        raise SourceError('RECORD_COUNT_LIMIT')
                if tag=='channel':
                    raw=node.get('id')
                    if not raw: raise SourceError('MISSING_CHANNEL_ID')
                    qualified=source+':'+raw; node.set('id',qualified)
                    payload=ET.tostring(node,encoding='utf-8'); serialized+=len(payload)
                    previous=self.db.execute('SELECT payload FROM full_channels WHERE source=? AND generation=? AND raw_id=?',(source,generation,raw)).fetchone()
                    if previous:
                        if bytes(previous['payload'])!=payload: raise SourceError('CONFLICTING_CHANNEL_ID')
                    else:
                        self.db.execute('INSERT INTO full_channels VALUES(?,?,?,?,?,?)',
                                        (source,generation,raw,qualified,node.findtext('display-name',''),payload))
                elif tag=='programme':
                    sequence+=1; raw=node.get('channel')
                    if not raw or not (node.findtext('title') or '').strip(): invalid['MISSING_CHANNEL_OR_TITLE']+=1
                    else:
                        try:
                            begin=timestamp(node.get('start','')); finish=timestamp(node.get('stop',''))
                            if begin>=finish: raise ValueError()
                        except ValueError: invalid['INVALID_TIME']+=1
                        else:
                            node.set('channel',source+':'+raw)
                            payload=ET.tostring(node,encoding='utf-8'); serialized+=len(payload)
                            fingerprint=hashlib.sha256(payload).hexdigest()
                            cursor=self.db.execute('INSERT OR IGNORE INTO full_programmes VALUES(?,?,?,?,?,?,?,?,?)',
                                (source,generation,sequence,raw,source+':'+raw,begin.timestamp(),finish.timestamp(),fingerprint,payload))
                            if cursor.rowcount==0: report['exact_duplicates']+=1
                            else:
                                earliest=begin if earliest is None or begin<earliest else earliest
                                latest=finish if latest is None or finish>latest else latest
                if serialized>limits.max_store_bytes: raise SourceError('SOURCE_STORE_LIMIT')
                if sequence%1000==0:
                    guard()
                    pages=self.db.execute('PRAGMA page_count').fetchone()[0]
                    page_size=self.db.execute('PRAGMA page_size').fetchone()[0]
                    if pages*page_size>limits.max_store_bytes: raise SourceError('SQLITE_STORE_LIMIT')
            depth-=1
        parser=expat.ParserCreate(encoding='UTF-8')
        parser.StartElementHandler=start; parser.EndElementHandler=end; parser.CharacterDataHandler=data
        parser.XmlDeclHandler=declaration; parser.StartDoctypeDeclHandler=doctype
        parser.EntityDeclHandler=reject; parser.ExternalEntityRefHandler=reject
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        decoder=codecs.getincrementaldecoder('utf-8-sig')('strict')
        with path.open('rb') as raw_stream:
            magic=raw_stream.read(2); raw_stream.seek(0)
            compressed=magic==b'\x1f\x8b'; report['compression']='gzip' if compressed else 'none'
            stream=gzip.GzipFile(fileobj=raw_stream) if compressed else raw_stream
            try:
                while True:
                    guard(); chunk=stream.read(65536)
                    if not chunk: break
                    report['decompressed_bytes']+=len(chunk)
                    if report['decompressed_bytes']>limits.max_expanded_bytes: raise SourceError('EXPANDED_SIZE_LIMIT')
                    if report['decompressed_bytes']>max(compressed_size,1)*limits.max_expansion_ratio:
                        raise SourceError('EXPANSION_RATIO_LIMIT')
                    decoder.decode(chunk); parser.Parse(chunk,False)
                    if report['decompressed_bytes']-parser.CurrentByteIndex>limits.max_record_bytes:
                        raise SourceError('PENDING_XML_TOKEN_LIMIT')
                decoder.decode(b'',final=True); parser.Parse(b'',True)
            finally:
                if compressed: stream.close()
        channels=self.db.execute('SELECT COUNT(*) FROM full_channels WHERE source=? AND generation=?',(source,generation)).fetchone()[0]
        if not channels or not report['input_programmes']: raise SourceError('EMPTY_SOURCE')
        missing=self.db.execute('''SELECT COUNT(*) FROM full_programmes p WHERE source=? AND generation=?
            AND NOT EXISTS(SELECT 1 FROM full_channels c WHERE c.source=p.source AND c.generation=p.generation AND c.raw_id=p.raw_channel)''',(source,generation)).fetchone()[0]
        if missing:
            invalid['UNKNOWN_CHANNEL']+=missing
            self.db.execute('''DELETE FROM full_programmes AS p WHERE source=? AND generation=? AND NOT EXISTS(
                SELECT 1 FROM full_channels c WHERE c.source=p.source AND c.generation=p.generation AND c.raw_id=p.raw_channel)''',(source,generation))
        report['invalid_programmes']=sum(invalid.values()); report['invalid_reasons']=dict(invalid)
        report['channels']=channels
        report['valid_programmes']=self.db.execute('SELECT COUNT(*) FROM full_programmes WHERE source=? AND generation=?',(source,generation)).fetchone()[0]
        report['conflicting_starts']=self.db.execute('''SELECT COALESCE(SUM(n-1),0) FROM
            (SELECT COUNT(*) n FROM full_programmes WHERE source=? AND generation=? GROUP BY qualified_id,start HAVING n>1)''',(source,generation)).fetchone()[0]
        report['overlaps']=self.db.execute('''SELECT COUNT(*) FROM
            (SELECT start,MAX(stop) OVER(PARTITION BY qualified_id ORDER BY start,sequence ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) previous_stop
             FROM full_programmes WHERE source=? AND generation=?) WHERE start<previous_stop''',(source,generation)).fetchone()[0]
        bounds=self.db.execute('SELECT MIN(start),MAX(stop) FROM full_programmes WHERE source=? AND generation=?',(source,generation)).fetchone()
        from datetime import datetime,timezone
        report['time_range']={'start':datetime.fromtimestamp(bounds[0],timezone.utc).isoformat(),'stop':datetime.fromtimestamp(bounds[1],timezone.utc).isoformat()} if bounds[0] is not None else None
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        report['peak_process_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
        assert report['input_programmes']==report['valid_programmes']+report['invalid_programmes']+report['exact_duplicates']
        return report

    def counts(self):
        return {name:self.db.execute(f'SELECT COUNT(*) FROM full_{name} x JOIN full_sources s ON x.source=s.id AND x.generation=s.generation').fetchone()[0] for name in ('channels','programmes')}

    def iter_channels(self):
        cursor=self.db.execute('''SELECT c.*,s.priority FROM full_channels c JOIN full_sources s
            ON c.source=s.id AND c.generation=s.generation ORDER BY c.qualified_id''')
        for row in cursor:
            memory_guard(self.limits)
            yield {'qualified_id':row['qualified_id'],'name':row['name'],'source':row['source'],
                   'priority':row['priority'],'element':ET.fromstring(row['payload'])}

    def iter_programmes(self):
        cursor=self.db.execute('''SELECT p.*,s.priority FROM full_programmes p JOIN full_sources s
            ON p.source=s.id AND p.generation=s.generation ORDER BY p.qualified_id,p.start,p.sequence''')
        for row in cursor:
            memory_guard(self.limits)
            yield {'source':row['source'],'qualified_id':row['qualified_id'],'start':row['start'],
                   'stop':row['stop'],'fingerprint':row['fingerprint'],'element':ET.fromstring(row['payload'])}
