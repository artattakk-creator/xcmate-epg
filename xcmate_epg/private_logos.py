"""Verify explicit private provider/logo evidence without inferring EPG aliases."""
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

from .export import atomic


def _png_dimensions(data):
    if not data.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('NOT_PNG')
    pos=8; dimensions=None; ended=False; image_data=False
    while pos<len(data):
        if pos+12>len(data): raise ValueError('TRUNCATED_PNG')
        size=struct.unpack('>I',data[pos:pos+4])[0]; kind=data[pos+4:pos+8]
        if pos+size+12>len(data): raise ValueError('TRUNCATED_PNG')
        block=data[pos+8:pos+8+size]; crc=struct.unpack('>I',data[pos+8+size:pos+12+size])[0]
        if zlib.crc32(kind+block)&0xffffffff!=crc: raise ValueError('PNG_CRC')
        if pos==8 and kind!=b'IHDR': raise ValueError('PNG_HEADER')
        if kind==b'IHDR':
            if dimensions is not None or size!=13: raise ValueError('PNG_HEADER')
            dimensions=struct.unpack('>II',block[:8])
            if not all(0<n<=16384 for n in dimensions): raise ValueError('PNG_DIMENSIONS')
        elif kind==b'IDAT': image_data=True
        elif kind==b'IEND':
            if size or pos+12!=len(data): raise ValueError('PNG_TRAILING_DATA')
            ended=True
        pos+=size+12
    if not ended or not dimensions or not image_data: raise ValueError('INCOMPLETE_PNG')
    return dimensions


def verify_provider_logos(inventory_path,files_root,output_root):
    """Input bindings must come from explicit backup-key evidence, not names.

    Files are verified against the trusted private manifest; provider/feed
    mappings are intentionally outside this contract. No URLs are exported.
    """
    inventory=json.loads(Path(inventory_path).read_text())
    if len(inventory.get('provider_bindings',[]))>10000: raise ValueError('LOGO_RECORD_LIMIT')
    manifest={r['cache_key']:r for r in inventory.get('records',[])}
    files_root=Path(files_root); output_root=Path(output_root)
    assets=output_root/'assets/logos'; assets.mkdir(parents=True,exist_ok=True,mode=0o700)
    records=[]; invalid=0; seen=set(); total_bytes={}
    for binding in inventory.get('provider_bindings',[]):
        try:
            provider=binding['provider_id']; digest=binding['sha256']; entry=manifest[binding['cache_key']]
            if (not isinstance(provider,str) or not provider or provider in seen or not binding.get('evidence')
                    or not re.fullmatch('[a-f0-9]{64}',digest) or entry['sha256']!=digest):
                raise ValueError('LOGO_IDENTITY')
            source=files_root/(digest+'.png')
            if source.is_symlink() or source.stat().st_nlink!=1 or source.stat().st_size>8*1024*1024:
                raise ValueError('UNSAFE_LOGO_FILE')
            data=source.read_bytes()
            if hashlib.sha256(data).hexdigest()!=digest: raise ValueError('LOGO_SHA')
            dimensions=_png_dimensions(data)
            if dimensions!=(int(entry['width']),int(entry['height'])): raise ValueError('LOGO_DIMENSIONS')
            target=assets/(digest+'.png')
            if target.is_symlink() or (target.exists() and target.stat().st_nlink!=1): raise ValueError('UNSAFE_LOGO_FILE')
            atomic(target,data); total_bytes[digest]=len(data); seen.add(provider)
            records.append({'type':'channel_logo','provider':'private_kodi_catalog','provider_channel_id':provider,
                'sha256':digest,'size_bytes':len(data),'mime_type':'image/png','width':dimensions[0],'height':dimensions[1],
                'asset':'assets/logos/'+digest+'.png','verified':True,'feed_mapping_confirmed':False,
                'xmltv_icon_exported':False,'evidence':'Explicit backup key binding; PNG CRC/dimensions/SHA verified'})
        except (ValueError,KeyError,OSError,TypeError,struct.error): invalid+=1
    atomic(output_root/'provider-logo-metadata.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records).encode())
    return {'bound_provider_channels':len(inventory.get('provider_bindings',[])),
            'verified_provider_logos':len(records),'verified_logo_files':len(total_bytes),
            'invalid_logo_records':invalid,'size_bytes':sum(total_bytes.values()),
            'xmltv_logos_exported':0,'full_provider_denominator_known':False}
