"""Immutable generations; manifest replacement is the transaction commit point."""
import gzip
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
from xml.etree import ElementTree as ET
from .xmltv import validate, LIMIT


def atomic(path,data):
    fd,name=tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def publish(root,directory,*,status="DEVELOPMENT_NOT_FOR_PUBLICATION",image_hosts=None):
    validate(root,image_hosts)
    root=copy.deepcopy(root)
    root.attrib.clear()
    root.set('generator-info-name','XCMate EPG Agent')
    order='title sub-title desc credits date category keyword language orig-language length icon url country episode-num video audio previously-shown premiere last-chance new subtitles rating star-rating review'.split()
    ranks={name:i for i,name in enumerate(order)}
    for programme in root.findall('programme'):
        programme[:]=sorted(programme,key=lambda node:ranks.get(node.tag,len(ranks)))
    xml=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    if len(xml)>LIMIT: raise ValueError('OUTPUT_LIMIT_REQUIRES_SUBSET')
    # Fail closed rather than silently remove legally required attribution.
    scrubbed_root=copy.deepcopy(root)
    for parent in scrubbed_root.iter():
        for icon in list(parent.findall('icon')):
            icon.attrib.pop('src',None)  # Scan all remaining data, including text/tails.
    scrubbed=ET.tostring(scrubbed_root,encoding='unicode')
    if re.search(r'https?://|Bearer\s|(?:password|token|api_key|username)\s*[:=]',scrubbed,re.I):
        raise ValueError('sensitive or source URL in public output')
    archive=gzip.compress(xml,mtime=0)
    if len(archive)>LIMIT: raise ValueError('OUTPUT_LIMIT_REQUIRES_SUBSET')
    digest=hashlib.sha256(archive).hexdigest()
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    previous=directory/'manifest.json'
    try:
        existing=json.loads(previous.read_text())
        expected=f'versions/{digest}/xcmate-epg.xml.gz'
        if (existing.get('version')==digest and existing.get('sha256')==digest and existing.get('status')==status
                and existing.get('file')==expected
                and (directory/expected).read_bytes()==archive
                and (directory/'xcmate-epg.xml.gz').read_bytes()==archive
                and (directory/'xcmate-epg.xml.gz.sha256').read_text()==digest+'  xcmate-epg.xml.gz\n'):
            return existing
    except (OSError,ValueError,AttributeError):
        pass
    version=directory/'versions'/digest
    version.mkdir(parents=True,exist_ok=True)
    atomic(version/'xcmate-epg.xml.gz',archive)
    checksum=(digest+'  xcmate-epg.xml.gz\n').encode()
    atomic(version/'xcmate-epg.xml.gz.sha256',checksum)
    manifest={'schema_version':1,'version':digest,
              'file':f'versions/{digest}/xcmate-epg.xml.gz','sha256':digest,
              'bytes':len(archive),'channels':len(root.findall('channel')),
              'programmes':len(root.findall('programme')),
              'status':status}
    atomic(directory/'xcmate-epg.xml.gz',archive)
    atomic(directory/'xcmate-epg.xml.gz.sha256',checksum)
    atomic(directory/'manifest.json',(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode())
    return manifest
