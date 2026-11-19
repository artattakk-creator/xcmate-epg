"""Private streaming XMLTV generations; the manifest is the commit point."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from xml.etree import ElementTree as ET
from xml.parsers import expat

from .export import atomic
from .streaming import file_sha
from .xmltv import timestamp
from .xmltv_images import MAX_IMAGE_URL, MAX_MEDIA_PER_EVENT, image_url, validate_image_attrs


def _copy_atomic(source,target):
    fd,name=tempfile.mkstemp(prefix='.pending-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as out, Path(source).open('rb') as stream:
            shutil.copyfileobj(stream,out,65536); out.flush(); os.fsync(out.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name): os.unlink(name)


def verify_archive(path, *, image_hosts=None, max_xml_bytes=256*1024*1024, allow_empty=False,
                   memory_check=lambda:None, on_channel=None, on_programme=None, on_icon=None,
                   on_image=None, on_times=None):
    """Independent gzip/Expat verifier: no XML tree, no entity expansion."""
    known = set()
    counts = {'channels': 0, 'programmes': 0}
    depth = expanded = 0
    item = None
    title = has_title = in_programme = False
    image_attrs = image_text = None
    image_size = image_count = icon_count = 0

    def reject(*args):
        raise ValueError('UNSAFE_EXPORT_XML')

    def start(tag, attrs):
        nonlocal depth, title, has_title, in_programme, item
        nonlocal image_attrs, image_text, image_size, image_count, icon_count
        depth += 1
        if depth > 32 or image_text is not None:
            reject()
        if depth == 1 and tag != 'tv':
            reject()
        if depth == 2:
            image_count = icon_count = 0
        if depth == 2 and tag == 'channel':
            identifier = attrs.get('id')
            if not identifier or identifier in known:
                raise ValueError('INVALID_EXPORTED_CHANNEL')
            known.add(identifier)
            counts['channels'] += 1
            item = (identifier, '', '')
            if on_channel:
                on_channel(identifier)
        if depth == 2 and tag == 'programme':
            if attrs.get('channel') not in known:
                raise ValueError('DANGLING_EXPORTED_PROGRAMME')
            begin, end_time = timestamp(attrs.get('start', '')), timestamp(attrs.get('stop', ''))
            if begin >= end_time:
                raise ValueError('INVALID_EXPORTED_TIME')
            counts['programmes'] += 1
            in_programme, has_title = True, False
            item = (attrs['channel'], attrs['start'], attrs['stop'])
            if on_programme:
                on_programme(attrs['channel'])
            if on_times:
                on_times(begin, end_time)
        if depth == 3 and tag == 'title' and in_programme:
            title = True
        if tag == 'icon':
            url = image_url(attrs.get('src', ''), image_hosts)
            if depth == 3 and item:
                icon_count += 1
                if icon_count > MAX_MEDIA_PER_EVENT:
                    raise ValueError('XMLTV_ICON_LIMIT')
                if on_icon:
                    on_icon(*item, url)
            elif on_icon:
                reject()
        if tag == 'image':
            if depth != 3 or not in_programme:
                reject()
            image_count += 1
            if image_count > MAX_MEDIA_PER_EVENT:
                raise ValueError('XMLTV_IMAGE_LIMIT')
            validate_image_attrs(attrs)
            image_attrs, image_text, image_size = attrs, [], 0

    def data(value):
        nonlocal has_title, image_size
        if title and value.strip():
            has_title = True
        if image_text is not None:
            image_size += len(value)
            if image_size > MAX_IMAGE_URL:
                raise ValueError('INVALID_XMLTV_IMAGE_URL')
            image_text.append(value)

    def end(tag):
        nonlocal depth, title, in_programme, item, image_text, image_attrs
        if image_text is not None:
            url = image_url(''.join(image_text).strip(), image_hosts)
            if on_image:
                on_image(*item, url, image_attrs)
            image_text = image_attrs = None
        if depth == 3 and tag == 'title':
            title = False
        if depth == 2 and tag == 'programme':
            if not has_title:
                raise ValueError('MISSING_EXPORTED_TITLE')
            in_programme = False
        if depth == 2:
            item = None
        depth -= 1

    parser = expat.ParserCreate(encoding='UTF-8')
    parser.StartElementHandler, parser.EndElementHandler, parser.CharacterDataHandler = start, end, data
    parser.StartDoctypeDeclHandler = parser.EntityDeclHandler = parser.ExternalEntityRefHandler = reject
    with gzip.open(path, 'rb') as stream:
        while chunk := stream.read(65536):
            memory_check()
            expanded += len(chunk)
            if expanded > max_xml_bytes:
                raise ValueError('OUTPUT_XML_LIMIT')
            parser.Parse(chunk, False)
        parser.Parse(b'', True)
    if not allow_empty and (not counts['channels'] or not counts['programmes']):
        raise ValueError('EMPTY_FULL_EXPORT')
    return dict(counts, xml_bytes=expanded)


class _CompressedSink:
    def __init__(self,stream,limit): self.stream=stream; self.limit=limit; self.size=0
    def write(self,data):
        self.size+=len(data)
        if self.size>self.limit: raise ValueError('OUTPUT_COMPRESSED_LIMIT')
        return self.stream.write(data)
    def flush(self): self.stream.flush()


def publish_stream(channels,programmes,directory,*,status='PARTIAL_COVERAGE',view='full',
                   image_hosts=None,max_xml_bytes=256*1024*1024,max_gzip_bytes=64*1024*1024,
                   forbidden_values=(),memory_check=lambda:None):
    if status not in {'PRIVATE_FULL_EPG_CREATED','PARTIAL_COVERAGE','ARTWORK_BLOCKED','SOURCE_BLOCKED'}:
        raise ValueError('INVALID_FULL_EXPORT_STATUS')
    if view not in {'full','device'}: raise ValueError('INVALID_EXPORT_VIEW')
    memory_check()
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    directory.chmod(0o700)
    if directory.is_symlink(): raise ValueError('UNSAFE_EXPORT_DIRECTORY')
    stage=Path(tempfile.mkdtemp(prefix='.pending-',dir=directory))
    archive=stage/'xcmate-epg.xml.gz'; known=set(); expected={'channels':0,'programmes':0}; xml_size=0
    forbidden=[value.encode() if isinstance(value,str) else value for value in forbidden_values if value]
    def validate_node(node):
        memory_check()
        for icon in node.iter('icon'):
            if set(icon.attrib)-{'src','width','height'} or len(icon) or (icon.text or '').strip():
                raise ValueError('INVALID_EXPORTED_ICON')
            image_url(icon.get('src',''), image_hosts)
        for image in node.iter('image'):
            if node.tag != 'programme' or image not in list(node) or len(image):
                raise ValueError('INVALID_EXPORTED_IMAGE')
            validate_image_attrs(image.attrib)
            image.text = image_url((image.text or '').strip(), image_hosts)
        payload=ET.tostring(node,encoding='utf-8')
        if len(payload)>2*1024*1024: raise ValueError('EXPORT_RECORD_LIMIT')
        if any(value in payload for value in forbidden): raise ValueError('PRIVATE_SOURCE_OR_CREDENTIAL_IN_EXPORT')
        if re.search(rb'Bearer\s|(?:password|token|api_key|username)\s*[:=]',payload,re.I):
            raise ValueError('CREDENTIAL_PATTERN_IN_EXPORT')
        return payload
    programme_order = ('title sub-title desc credits date category keyword language orig-language '
                       'length icon url country episode-num video audio previously-shown premiere '
                       'last-chance new subtitles rating star-rating review image').split()
    programme_ranks = {name: index for index, name in enumerate(programme_order)}
    channel_ranks = {'display-name': 0, 'icon': 1, 'url': 2}
    try:
        with archive.open('wb') as raw:
            sink=_CompressedSink(raw,max_gzip_bytes)
            with gzip.GzipFile(filename='',mode='wb',fileobj=sink,mtime=0) as out:
                def write(payload):
                    nonlocal xml_size
                    xml_size+=len(payload)
                    if xml_size>max_xml_bytes: raise ValueError('OUTPUT_XML_LIMIT')
                    out.write(payload)
                write(b'<?xml version="1.0" encoding="utf-8"?>\n<tv generator-info-name="XCMate EPG Agent V2">\n')
                for node in channels:
                    identifier=node.get('id')
                    if not identifier or identifier in known: raise ValueError('DUPLICATE_EXPORTED_CHANNEL')
                    known.add(identifier); expected['channels']+=1
                    node[:]=sorted(node,key=lambda child:channel_ranks.get(child.tag,len(channel_ranks)))
                    write(validate_node(node)+b'\n')
                for node in programmes:
                    if node.get('channel') not in known or not (node.findtext('title') or '').strip():
                        raise ValueError('INVALID_EXPORTED_PROGRAMME')
                    if timestamp(node.get('start',''))>=timestamp(node.get('stop','')):
                        raise ValueError('INVALID_EXPORTED_TIME')
                    # XMLTV field ordering, preserving all repeated metadata fields.
                    node[:]=sorted(node,key=lambda child:programme_ranks.get(child.tag,len(programme_ranks)))
                    expected['programmes']+=1; write(validate_node(node)+b'\n')
                write(b'</tv>\n')
            raw.flush(); os.fsync(raw.fileno())
        archive.chmod(0o600)
        checked=verify_archive(archive,image_hosts=image_hosts,max_xml_bytes=max_xml_bytes,allow_empty=view=='device',memory_check=memory_check)
        if any(checked[key]!=value for key,value in expected.items()) or checked['xml_bytes']!=xml_size:
            raise ValueError('EXPORT_COUNT_MISMATCH')
        memory_check()
        digest=hashlib.sha256()
        with archive.open('rb') as stream:
            for chunk in iter(lambda:stream.read(65536),b''): digest.update(chunk)
        sha=digest.hexdigest(); name='xcmate-epg.xml.gz'; checksum=sha+'  '+name+'\n'
        manifest={'schema_version':2,'status':status,'view':view,'version':sha,'sha256':sha,
                  'file':f'versions/{sha}/{name}','bytes':archive.stat().st_size,'xml_bytes':xml_size,**expected}
        previous=directory/'manifest.json'
        previous_generation=None; previous_manifest=None
        try:
            old=json.loads(previous.read_text())
            old_sha=old.get('sha256','')
            if (re.fullmatch('[a-f0-9]{64}',old_sha) and old.get('file')==f'versions/{old_sha}/{name}'
                    and _sha_file(directory/old['file'])==old_sha):
                previous_generation=directory/old['file']; previous_manifest=previous.read_bytes()
            if (old==manifest and (directory/manifest['file']).is_file()
                    and (directory/name).is_file()
                    and (directory/(name+'.sha256')).read_text()==checksum):
                # Check bytes, not just existence, before accepting unchanged publication.
                if all(_sha_file(p)==sha for p in (directory/name,directory/manifest['file'])):
                    return old
        except (OSError,ValueError): pass
        generation=directory/'versions'/sha; generation.mkdir(parents=True,exist_ok=True,mode=0o700)
        generation.chmod(0o700); (directory/'versions').chmod(0o700)
        _copy_atomic(archive,generation/name)
        atomic(generation/(name+'.sha256'),checksum.encode())
        try:
            _copy_atomic(generation/name,directory/name)
            atomic(directory/(name+'.sha256'),checksum.encode())
            atomic(previous,(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode())
        except BaseException:
            if previous_generation is not None:
                # Restore convenience aliases too, using immutable LastGood. The
                # old generation remains valid even if storage itself fails here.
                try:
                    _copy_atomic(previous_generation,directory/name)
                    (stage/'previous.sha256').write_text(old_sha+'  '+name+'\n')
                    (stage/'previous.json').write_bytes(previous_manifest)
                    _copy_atomic(stage/'previous.sha256',directory/(name+'.sha256'))
                    _copy_atomic(stage/'previous.json',previous)
                except OSError: pass
            raise
        return manifest
    finally:
        shutil.rmtree(stage)


def _sha_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(65536),b''): digest.update(chunk)
    return digest.hexdigest()


def read_private_json(path, maximum=1024 * 1024):
    with path.open('rb') as file:
        data = file.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('DELIVERY_METADATA_LIMIT')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('INVALID_DELIVERY_METADATA')
    return value


def read_run_manifest(root):
    run = read_private_json(root / 'run-manifest.json')
    generation, files = run.get('generation'), run.get('files')
    if (not isinstance(generation, str) or not re.fullmatch(r'[a-f0-9]{64}', generation)
            or run.get('directory') != f'runs/{generation}' or not isinstance(files, dict)
            or hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() != generation):
        raise ValueError('INVALID_RUN_MANIFEST')
    return run


def verify_run_member(bundle, checksums, name, limit):
    path = bundle / name
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('INVALID_RUN_MEMBER')
    digest, size = file_sha(path, limit)
    if checksums.get(name) != digest:
        raise ValueError('RUN_FILE_CHECKSUM_MISMATCH')
    return path, digest, size


def read_full_archive(bundle, checksums, limit):
    full = read_private_json(verify_run_member(bundle, checksums, 'full/manifest.json', 65536)[0])
    digest = full.get('sha256')
    filename = 'xcmate-epg.xml.gz'
    expected = f'versions/{digest}/{filename}'
    if (not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
            or full.get('view') != 'full' or full.get('file') != expected):
        raise ValueError('INVALID_UNIVERSAL_MANIFEST')
    archive, sha, size = verify_run_member(bundle, checksums, f'full/{expected}', limit)
    if sha != digest or size != full.get('bytes'):
        raise ValueError('UNIVERSAL_CHECKSUM_MISMATCH')
    return archive, full


def _verify_run_bundle(bundle,hashes,memory_check):
    """Reused immutable names are untrusted until every member is verified."""
    try:
        if not stat.S_ISDIR(bundle.lstat().st_mode): raise ValueError('INVALID_RUN_BUNDLE')
        pending=[bundle]; seen=set()
        while pending:
            directory=pending.pop()
            for member in directory.iterdir():
                memory_check()
                info=member.lstat()
                if stat.S_ISDIR(info.st_mode):
                    pending.append(member)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
                    relative=str(member.relative_to(bundle))
                    if relative not in hashes or _sha_file(member)!=hashes[relative]:
                        raise ValueError('INVALID_RUN_BUNDLE')
                    seen.add(relative)
                else: raise ValueError('INVALID_RUN_BUNDLE')
        if seen!=hashes.keys(): raise ValueError('INVALID_RUN_BUNDLE')
    except OSError:
        raise ValueError('INVALID_RUN_BUNDLE') from None


def publish_run(stage,root,*,memory_check=lambda:None):
    """Commit a complete immutable run, then update recoverable convenience paths.

    Readers needing crash-consistent multi-file snapshots use run-manifest.json.
    Handled failures restore all previous convenience files from disk backups.
    No feed payload is retained in memory or linked through symlinks/hardlinks.
    """
    stage=Path(stage); root=Path(root); memory_check()
    files=sorted(p.relative_to(stage) for p in stage.rglob('*') if p.is_file())
    hashes={str(p):_sha_file(stage/p) for p in files}
    memory_check()
    identity=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    bundle=root/'runs'/identity
    bundle.parent.mkdir(mode=0o700,exist_ok=True)
    if not stat.S_ISDIR(bundle.parent.lstat().st_mode): raise ValueError('INVALID_RUN_BUNDLE')
    created=not bundle.exists() and not bundle.is_symlink()
    if created: os.replace(stage,bundle)
    else: _verify_run_bundle(bundle,hashes,memory_check)
    manifest={'schema_version':1,'generation':identity,'directory':'runs/'+identity,
              'files':hashes,'quality_report':'runs/'+identity+'/quality-report.json'}
    backup=Path(tempfile.mkdtemp(prefix='.run-pending-rollback-',dir=root))
    changed=[]
    try:
        # Immutable per-view generations can be safely retained even on failure.
        for relative in files:
            memory_check()
            source=bundle/relative; target=root/relative
            if target.is_file() and _sha_file(target)==hashes[str(relative)]: continue
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            saved=backup/relative
            existed=target.exists()
            if existed:
                saved.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                _copy_atomic(target,saved)
            changed.append((target,saved,existed))
            _copy_atomic(source,target)
        memory_check()
        committed=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
        current=root/'run-manifest.json'
        previous=current.read_bytes() if current.exists() else None
        memory_check()
        if previous!=committed:
            atomic(current,committed)
    except BaseException:
        for target,saved,existed in reversed(changed):
            if existed: _copy_atomic(saved,target)
            else: target.unlink(missing_ok=True)
        if created: shutil.rmtree(bundle)
        raise
    finally:
        shutil.rmtree(backup)
