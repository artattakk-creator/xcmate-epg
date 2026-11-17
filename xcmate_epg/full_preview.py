"""One-shot complete private import. No demo, publisher, schedule or Kodi mutation."""
import argparse
from collections import Counter
import copy
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import shutil
import tempfile
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from .channel_mapping import build_mapping,persist_mapping
from .export import atomic
from .full_export import publish_stream,publish_run
from .full_fetch import SnapshotCache
from .private_preview import private_root
from .state import State
from .streaming import Limits,SourceError,StreamStore,memory_guard
from .xmltv import IMAGE_HOSTS,public_image


class _NoArtwork:
    def enrich(self,channel,programme): return programme,[]
    def summary(self): return {'programmes_matched':0,'verified_images':0,'status':'ARTWORK_BLOCKED',
                              'tmdb_status':'NOT_CONFIGURED','tvmaze_status':'NOT_AUTHORIZED'}


def _url_safe(value,forbidden=()):
    try:
        p=urlsplit(value)
        return (p.scheme in ('http','https') and p.hostname and not p.username and not p.password
                and not p.query and not p.fragment and p.port in (None,80,443)
                and not any(v and v in value for v in forbidden))
    except ValueError: return False


def clean_element(element,verified_urls,hosts,forbidden=()):
    """Retain valid programme metadata; quarantine unverified media/unsafe fields.

    Original records remain in the source spool, not in deliverable configuration.
    An omission reference contains a content hash, never a secret-bearing URL.
    """
    node=copy.deepcopy(element); omitted=[]
    def omission(child,reason):
        payload=ET.tostring(child,encoding='utf-8')
        item={'field':child.tag,'reason':reason,'content_sha256':hashlib.sha256(payload).hexdigest()}
        value=child.get('src') or (child.text or '').strip()
        if _url_safe(value,forbidden): item['public_reference']=value
        omitted.append(item)
    for parent in node.iter():
        for child in list(parent):
            if child.tag in ('icon','image'):
                url=child.get('src') or (child.text or '').strip()
                if child.tag=='icon' and url in verified_urls:
                    public_image(url,hosts)
                    child.attrib={k:v for k,v in child.attrib.items() if k in ('src','width','height')}
                    child.text=None; child[:]=[]; child.tail=None
                else:
                    omission(child,'UNVERIFIED_SOURCE_MEDIA'); parent.remove(child)
            elif child.tag=='url' and not _url_safe((child.text or '').strip(),forbidden):
                omission(child,'UNSAFE_OR_PRIVATE_URL'); parent.remove(child)
        # Credential patterns or configured source references in arbitrary metadata
        # are quarantined field by field without discarding the valid event.
        for child in list(parent):
            serialized=ET.tostring(child,encoding='unicode')
            if (any(value and value in serialized for value in forbidden)
                    or re.search(r'Bearer\s|(?:password|token|api_key|username)\s*[:=]',serialized,re.I)):
                omission(child,'SENSITIVE_METADATA_FIELD'); parent.remove(child)
    return node,omitted


def run_full(config,root,*,authorize_network=False,metadata=None):
    sources=config.get('sources')
    if not isinstance(sources,list) or not sources: raise ValueError('FULL_INPUT_REQUIRED')
    ids=[s.get('id') for s in sources]
    if any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',v) for v in ids) or len(set(ids))!=len(ids):
        raise ValueError('INVALID_SOURCE_IDS')
    for source in sources:
        if ('path' in source)==('url' in source): raise ValueError('ONE_SOURCE_INPUT_REQUIRED')
        if 'channel_ids' in source: raise ValueError('FULL_IMPORT_FORBIDS_SUBSET')
        if 'url' in source and not authorize_network: raise ValueError('NETWORK_NOT_AUTHORIZED')
    artwork_config=config.get('artwork',{})
    if artwork_config.get('authorize_tvmaze') and not authorize_network:
        raise ValueError('ARTWORK_NETWORK_NOT_AUTHORIZED')
    limits=Limits(**config.get('limits',{}))
    mask=os.umask(0o077)
    try:
        root=private_root(root)
        return _run(config,root,limits,metadata,artwork_config,authorize_network)
    finally: os.umask(mask)


def _run(config,root,limits,metadata,artwork_config,authorize_network):
    reports=[]; forbidden=[s['url'] for s in config['sources'] if 'url' in s]
    hosts=IMAGE_HOSTS|set(artwork_config.get('approved_image_hosts',[]))
    cache=SnapshotCache(root/'snapshots',max_bytes=limits.max_compressed_bytes,max_seconds=limits.max_seconds)
    with StreamStore(root/'stream.sqlite',limits=limits) as spool:
        for source in config['sources']:
            downloaded=None
            try:
                if 'url' in source:
                    downloaded=cache.fetch(source['id'],source['url']); path=downloaded['path']
                else: path=source['path']
                report=spool.import_source(source['id'],path,source.get('priority',100),
                    identity=downloaded['identity'] if downloaded else None)
                if downloaded:
                    report['http_status']=downloaded['http_status']
                    if report['successful']: cache.accept(downloaded)
                    if not downloaded['successful']:
                        report.update(successful=False,cache_status='lastgood',error=downloaded['error'])
                reports.append(report)
            except (SourceError,ValueError,OSError):
                reports.append({'id':source['id'],'successful':False,'cache_status':'missing','error':'SOURCE_UNAVAILABLE_OR_QUARANTINED'})
        # A run is complete only for its configured sources, never leftover database rows.
        spool.set_active_sources(s['id'] for s in config['sources'])
        if any(not r['successful'] for r in reports):
            result={'status':'SOURCE_BLOCKED','status_flags':['SOURCE_BLOCKED'],'sources':reports,
                    'real_sources_successful':sum(r['successful'] for r in reports),'previous_export_retained':(root/'full/manifest.json').exists()}
            atomic(root/'blocked-report.json',(json.dumps(result,indent=2)+'\n').encode())
            return result
        mapping_input=config.get('mapping',{})
        if isinstance(mapping_input,dict) and mapping_input.get('path'):
            mapping_input=json.loads(Path(mapping_input['path']).read_text())
        catalog=mapping_input.get('catalog',[]); confirmations=mapping_input.get('confirmations',[])
        channels=[]
        for row in spool.iter_channels():
            channels.append(row)
            memory_guard(limits)
        mapping=build_mapping(catalog,confirmations,[{k:v for k,v in c.items() if k!='element'} for c in channels])
        memory_guard(limits)
        catalog_complete=mapping_input.get('catalog_complete',False) is True
        mapping['catalog_complete']=catalog_complete
        mapping['full_provider_denominator']=len(catalog) if catalog_complete else None
        with State(root/'artwork.sqlite') as state:
            if metadata is None and (artwork_config.get('authorize_tvmaze') or artwork_config.get('tmdb_secret_env')):
                from .live_artwork import ArtworkEnricher
                tmdb=None; env_name=artwork_config.get('tmdb_secret_env')
                if env_name and env_name!='TMDB_READ_ACCESS_TOKEN':
                    raise ValueError('ONLY_TMDB_RUNTIME_SECRET_ALLOWED')
                # A runtime-secret variable must be explicitly named and authorized.
                # No GitHub token retrieval, token file, CLI token or token serialization.
                if env_name and authorize_network and re.fullmatch(r'[A-Z][A-Z0-9_]+',env_name):
                    value=os.environ.get(env_name)
                    if value:
                        from .artwork import TMDB
                        tmdb=TMDB(value)
                metadata=ArtworkEnricher(state,artwork_config,tmdb=tmdb)
            metadata=metadata or _NoArtwork()
            stage=Path(tempfile.mkdtemp(prefix='.run-pending-',dir=root))
            try:
                persist_mapping(mapping,stage/'mapping-review.json')
                result=_render(config,stage,spool,channels,mapping,metadata,reports,hosts,forbidden,artwork_config)
                publish_run(stage,root,memory_check=lambda:memory_guard(limits))
                return result
            finally:
                shutil.rmtree(stage,ignore_errors=True)


def _render(config,root,spool,channels,mapping,metadata,reports,hosts,forbidden,artwork_config):
    guard=lambda:memory_guard(spool.limits)
    guard()
    horizon=config.get('horizon',{})
    reference=datetime.fromisoformat(horizon.get('reference_time') or datetime.now(timezone.utc).isoformat())
    if reference.tzinfo is None: raise ValueError('HORIZON_TIMEZONE_REQUIRED')
    past=horizon.get('past_days',7); future=horizon.get('future_days',20)
    if type(past) is not int or type(future) is not int or not 0<=past<=7 or not 0<=future<=20:
        raise ValueError('HORIZON_RANGE_INVALID')
    lower=(reference-timedelta(days=past)).timestamp(); upper=(reference+timedelta(days=future)).timestamp()
    channel_map={c['qualified_id']:c['element'] for c in channels}
    clean_channels={}; logo_records=[]; omissions=Counter(); matched=0; verified_count=0; eligible=0
    horizon_excluded=Counter(); exported_by_source=Counter(); missing_profile=0
    explicit_logos=artwork_config.get('confirmed_source_logos',[])
    logo_by_id={}
    for logo in explicit_logos:
        qid=logo.get('qualified_id'); url=logo.get('url')
        if qid not in channel_map or not logo.get('evidence') or logo.get('rights_reviewed') is not True:
            continue
        if not any(i.get('src')==url for i in channel_map[qid].findall('icon')): continue
        verifier=getattr(metadata,'verify_media',None)
        if not verifier: continue
        checked=verifier(url)
        if not checked.get('verified'): continue
        public_image(url,hosts)
        logo_by_id[qid]=url
        logo_records.append({'type':'channel_logo','provider':'source_declared','channel':qid,
                             'url':url,'verified':True,'evidence':logo['evidence'],
                             'attribution':logo.get('attribution'),**checked})
    provenance_temp=root/'.source-media.pending'; artwork_temp=root/'.artwork.pending'
    with provenance_temp.open('w',encoding='utf-8') as provenance, artwork_temp.open('w',encoding='utf-8') as sidecar:
        for row in channels:
            qid=row['qualified_id']; urls={logo_by_id[qid]} if qid in logo_by_id else set()
            clean,excluded=clean_element(row['element'],urls,hosts,forbidden)
            clean_channels[qid]=clean
            guard()
            for item in excluded:
                omissions[item['reason']]+=1
                provenance.write(json.dumps({'source':row['source'],'channel':qid,**item},ensure_ascii=False)+'\n')
        for record in logo_records: sidecar.write(json.dumps(record,ensure_ascii=False)+'\n')
        with spool.db:
            spool.db.execute('DROP TABLE IF EXISTS rendered_events')
            spool.db.execute('CREATE TABLE rendered_events(source TEXT,channel TEXT,start REAL,stop REAL,fingerprint TEXT PRIMARY KEY,payload BLOB)')
            for row in spool.iter_programmes():
                if row['stop']<=lower or row['start']>=upper:
                    horizon_excluded[row['source']]+=1; continue
                event,records=metadata.enrich(channel_map[row['qualified_id']],row['element'])
                verified=[r for r in records if r.get('verified') and r.get('url')
                          and r.get('type') in {'poster','backdrop','episode_still'}]
                urls={r['url'] for r in verified}
                event,excluded=clean_element(event,urls,hosts,forbidden)
                # The enrichment adapter may never alter schedule identity.
                for key in ('channel','start','stop'):
                    if event.get(key)!=row['element'].get(key): raise ValueError('ARTWORK_CHANGED_PROGRAMME_IDENTITY')
                if not (event.findtext('title') or '').strip(): raise ValueError('ARTWORK_REMOVED_PROGRAMME_TITLE')
                if verified: matched+=1; verified_count+=len(verified)
                for record in records:
                    if record.get('provider')=='feed' and not record.get('verified'):
                        omissions['UNVERIFIED_SOURCE_MEDIA']+=1
                        value=record.get('url','')
                        item={'field':'icon','reason':'UNVERIFIED_SOURCE_MEDIA',
                              'content_sha256':hashlib.sha256(value.encode()).hexdigest()}
                        if _url_safe(value,forbidden): item['public_reference']=value
                        provenance.write(json.dumps({'source':row['source'],'channel':row['qualified_id'],
                                                     'start':event.get('start'),**item},ensure_ascii=False)+'\n')
                    elif record.get('provider') in {'tmdb','tvmaze'}:
                        if not _url_safe(record.get('url',''),forbidden):
                            record={k:v for k,v in record.items() if k!='url'}
                            record.update(verified=False,error='UNSAFE_ARTWORK_REFERENCE')
                        sidecar.write(json.dumps({'source':row['source'],'channel':row['qualified_id'],
                                                 'start':event.get('start'),'stop':event.get('stop'),**record},ensure_ascii=False)+'\n')
                for item in excluded:
                    omissions[item['reason']]+=1
                    provenance.write(json.dumps({'source':row['source'],'channel':row['qualified_id'],
                                                 'start':event.get('start'),**item},ensure_ascii=False)+'\n')
                spool.db.execute('INSERT INTO rendered_events VALUES(?,?,?,?,?,?)',
                    (row['source'],row['qualified_id'],row['start'],row['stop'],row['fingerprint'],ET.tostring(event,encoding='utf-8')))
                exported_by_source[row['source']]+=1
                guard()
        provenance.flush(); os.fsync(provenance.fileno()); sidecar.flush(); os.fsync(sidecar.fileno())
    summary=mapping['summary']; mapped=[e for e in mapping['entries'] if e['status']=='confirmed']
    device_by_source={}
    for entry in mapped:
        device_by_source.setdefault(entry['qualified_id'],[]).append('provider:'+entry['provider_id'])
    populated={row[0] for row in spool.db.execute('SELECT DISTINCT channel FROM rendered_events')}
    mapped_with=sum(e['qualified_id'] in populated for e in mapped)
    summary.update(mapped_channels_with_programmes=mapped_with,
                   mapped_channels_without_programmes=len(mapped)-mapped_with)
    guard()
    flags=[]
    if (not mapping['catalog_complete'] or not summary['total_provider_channels']
            or summary['mapped_provider_channels']<summary['total_provider_channels']
            or not mapped_with or mapped_with<len(mapped)): flags.append('PARTIAL_COVERAGE')
    if not matched or not logo_records: flags.append('ARTWORK_BLOCKED')
    status=('ARTWORK_BLOCKED' if 'ARTWORK_BLOCKED' in flags else 'PARTIAL_COVERAGE') if flags else 'PRIVATE_FULL_EPG_CREATED'
    def full_programmes():
        for row in spool.db.execute('SELECT payload FROM rendered_events ORDER BY channel,start,fingerprint'):
            yield ET.fromstring(row[0])
    full=publish_stream((clean_channels[k] for k in sorted(clean_channels)),full_programmes(),root/'full',
                        status=status,view='full',image_hosts=hosts,forbidden_values=forbidden,memory_check=guard)
    device_channels=[]
    for entry in mapped:
        source_id=entry['qualified_id']; identifier='provider:'+entry['provider_id']
        c=copy.deepcopy(clean_channels[source_id]); c.set('id',identifier)
        device_channels.append(c); guard()
    def device_programmes():
        for row in spool.db.execute('SELECT channel,payload FROM rendered_events ORDER BY channel,start,fingerprint'):
            for identifier in device_by_source.get(row['channel'],[]):
                p=ET.fromstring(row['payload']); p.set('channel',identifier); yield p
    device=publish_stream(device_channels,device_programmes(),root/'device',status=status,view='device',
                          image_hosts=hosts,forbidden_values=forbidden,memory_check=guard)
    os.replace(provenance_temp,root/'source-media.jsonl'); os.replace(artwork_temp,root/'artwork-metadata.jsonl')
    def time_envelope(first,last):
        available=first is not None
        leading=max(0,first-lower) if available else None
        trailing=max(0,upper-last) if available else None
        return {'coverage_kind':'time_envelope_only','internal_gaps_evaluated':False,
                'missing_programme_count':None,
                'requested_start':datetime.fromtimestamp(lower,timezone.utc).isoformat(),
                'requested_stop':datetime.fromtimestamp(upper,timezone.utc).isoformat(),
                'available_start':datetime.fromtimestamp(first,timezone.utc).isoformat() if available else None,
                'available_stop':datetime.fromtimestamp(last,timezone.utc).isoformat() if available else None,
                'leading_missing_seconds':leading,'trailing_missing_seconds':trailing,
                'missing_envelope_seconds':leading+trailing if available else upper-lower}
    source_channels=dict(spool.db.execute('SELECT source,COUNT(*) FROM full_channels GROUP BY source'))
    source_coverage={row[0]:row[1:] for row in spool.db.execute(
        'SELECT source,COUNT(DISTINCT channel),MIN(start),MAX(stop) FROM rendered_events GROUP BY source')}
    guard()
    for source in reports:
        populated_count,first,last=source_coverage.get(source['id'],(0,None,None))
        source.update(channels_with_programmes=populated_count,
                      channels_without_programmes=source_channels[source['id']]-populated_count,
                      programme_channel_coverage_scope='eligible_export_horizon',
                      time_envelope=time_envelope(first,last))
        source['horizon_excluded_programmes']=horizon_excluded[source['id']]
        source['exported_programmes']=exported_by_source[source['id']]
        if source['valid_programmes']!=source['exported_programmes']+source['horizon_excluded_programmes']:
            raise ValueError('FULL_COVERAGE_ACCOUNTING_FAILED')
    counts=spool.counts()
    provider_logos={'verified_provider_logos':0,'xmltv_logos_exported':0,'full_provider_denominator_known':False}
    if config.get('provider_logos'):
        from .private_logos import verify_provider_logos
        local=config['provider_logos']
        provider_logos=verify_provider_logos(local['inventory_path'],local['files_root'],root,memory_check=guard)
    bounds=spool.db.execute('SELECT MIN(start),MAX(stop) FROM rendered_events').fetchone()
    report={'status':status,'status_flags':flags or [status],'configured_sources':len(config['sources']),
            'real_sources_successful':sum(s['successful'] for s in reports),'source_coverage_complete':True,
            'sources':reports,'input_channels':sum(s['input_channels'] for s in reports),
            'input_programmes':sum(s['input_programmes'] for s in reports),
            'valid_programmes':counts['programmes'],'invalid_programmes':sum(s['invalid_programmes'] for s in reports),
            'exact_duplicates':sum(s['exact_duplicates'] for s in reports),
            'conflicting_starts':sum(s['conflicting_starts'] for s in reports),
            'overlaps_retained':sum(s['overlaps'] for s in reports),
            'full':dict(full,channels_with_programmes=len(populated),
              channels_without_programmes=full['channels']-len(populated)),
            'device':device,'mapping':dict(summary,catalog_complete=mapping['catalog_complete'],
              full_provider_denominator=mapping['full_provider_denominator']),
            'artwork':dict(metadata.summary(),programmes_with_verified_artwork=matched,
              verified_image_references=verified_count,programme_coverage=matched/full['programmes'] if full['programmes'] else 0,
              logos_present=len(logo_records),logo_coverage=len(logo_records)/full['channels'] if full['channels'] else 0),
            'metadata_omissions':dict(omissions),'horizon':{'past_days':past,'future_days':future,
              'reference_time':reference.isoformat(),'time_envelope':time_envelope(*bounds),
              'excluded_programmes':sum(horizon_excluded.values()),
              'requested_start':datetime.fromtimestamp(lower,timezone.utc).isoformat(),
              'requested_stop':datetime.fromtimestamp(upper,timezone.utc).isoformat()},
            'provider_logos':provider_logos,
            'time_range':{'start':datetime.fromtimestamp(bounds[0],timezone.utc).isoformat(),
                          'stop':datetime.fromtimestamp(bounds[1],timezone.utc).isoformat()} if bounds[0] is not None else None,
            'limitations':['Full and device views have distinct coverage; no heuristic provider confirmations.',
              'Conflicting/overlapping source alternatives retained, not silently discarded.',
              'Time envelopes do not establish complete schedules; missing programme counts are unknown.',
              'Original unverified media remains in private snapshots; Android rendering not verified.']}
    guard()
    atomic(root/'quality-report.json',(json.dumps(report,sort_keys=True,indent=2)+'\n').encode())
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--private-root',type=Path,required=True)
    parser.add_argument('--authorize-network',action='store_true')
    args=parser.parse_args()
    try:
        report=run_full(json.loads(args.config.read_text()),args.private_root,authorize_network=args.authorize_network)
        safe={k:report.get(k) for k in ('status','configured_sources','real_sources_successful','input_channels','input_programmes','valid_programmes','invalid_programmes')}
        print(json.dumps(safe)); return 1 if report['status']=='SOURCE_BLOCKED' else 0
    except (ValueError,OSError,TypeError,KeyError):
        print('SOURCE_BLOCKED: private full import/export failed; prior immutable exports retained',file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
