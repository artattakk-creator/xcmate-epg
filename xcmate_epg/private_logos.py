"""Verify explicit private provider/logo evidence without inferring EPG aliases."""
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

from .export import atomic


def _png_dimensions(data):
    """Validate bounded PNG structure and scanlines, including Adam7 passes."""
    if len(data)>8*1024*1024: raise ValueError('PNG_ENCODED_LIMIT')
    if not data.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('NOT_PNG')
    pos=8; dimensions=None; ended=False; idat=[]; image_ended=False; palette=False
    while pos<len(data):
        if pos+12>len(data): raise ValueError('TRUNCATED_PNG')
        size=struct.unpack('>I',data[pos:pos+4])[0]; kind=data[pos+4:pos+8]
        if pos+size+12>len(data): raise ValueError('TRUNCATED_PNG')
        if not re.fullmatch(b'[A-Za-z]{4}',kind) or not 65<=kind[2]<=90:
            raise ValueError('PNG_CHUNK_TYPE')
        block=data[pos+8:pos+8+size]; crc=struct.unpack('>I',data[pos+8+size:pos+12+size])[0]
        if zlib.crc32(kind+block)&0xffffffff!=crc: raise ValueError('PNG_CRC')
        if pos==8 and kind!=b'IHDR': raise ValueError('PNG_HEADER')
        if idat and kind!=b'IDAT': image_ended=True
        if kind==b'IHDR':
            if dimensions is not None or size!=13: raise ValueError('PNG_HEADER')
            width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',block)
            dimensions=(width,height)
            if not all(0<n<=16384 for n in dimensions): raise ValueError('PNG_DIMENSIONS')
            modes={0:(1,2,4,8,16),2:(8,16),3:(1,2,4,8),4:(8,16),6:(8,16)}
            if (color not in modes or depth not in modes[color] or compression or filtering
                    or interlace not in (0,1)): raise ValueError('PNG_FORMAT')
        elif kind==b'PLTE':
            if palette or idat or not size or size%3 or size>768 or color in (0,4):
                raise ValueError('PNG_PALETTE')
            if color==3 and size//3>2**depth: raise ValueError('PNG_PALETTE')
            palette=True
        elif kind==b'IDAT':
            if image_ended or (color==3 and not palette): raise ValueError('PNG_IDAT_ORDER')
            idat.append(block)
        elif kind==b'IEND':
            if size or pos+12!=len(data): raise ValueError('PNG_TRAILING_DATA')
            ended=True
        elif kind[0]<97: raise ValueError('PNG_UNKNOWN_CRITICAL_CHUNK')
        pos+=size+12
    if not ended or not dimensions or not idat: raise ValueError('INCOMPLETE_PNG')
    samples={0:1,2:3,3:1,4:2,6:4}[color]
    passes=[(0,0,1,1)] if not interlace else [
        (0,0,8,8),(4,0,8,8),(0,4,4,8),(2,0,4,4),(0,2,2,4),(1,0,2,2),(0,1,1,2)]
    rows=[]
    for x,y,dx,dy in passes:
        w=max(0,(width-x+dx-1)//dx); h=max(0,(height-y+dy-1)//dy)
        if w and h: rows.extend([1+(w*samples*depth+7)//8]*h)
    expected=sum(rows)
    if expected>64*1024*1024: raise ValueError('PNG_DECODED_LIMIT')
    decoder=zlib.decompressobj(); decoded=0; row=0; offset=0
    try:
        for block in idat:
            pending=block
            while pending:
                pixels=decoder.decompress(pending,min(65536,expected-decoded+1))
                pending=decoder.unconsumed_tail; decoded+=len(pixels)
                if decoded>expected or decoder.unused_data: raise ValueError('PNG_PIXEL_LENGTH')
                i=0
                while i<len(pixels):
                    if row>=len(rows): raise ValueError('PNG_PIXEL_LENGTH')
                    if offset==0 and pixels[i]>4: raise ValueError('PNG_FILTER')
                    take=min(rows[row]-offset,len(pixels)-i); i+=take; offset+=take
                    if offset==rows[row]: row+=1; offset=0
        if not decoder.eof or decoded!=expected or offset or row!=len(rows):
            raise ValueError('PNG_PIXEL_LENGTH')
    except zlib.error: raise ValueError('PNG_ZLIB') from None
    return dimensions


def verify_provider_logos(inventory_path,files_root,output_root,*,memory_check=lambda:None):
    """Input bindings must come from explicit backup-key evidence, not names.

    Files are verified against the trusted private manifest; provider/feed
    mappings are intentionally outside this contract. No URLs are exported.
    """
    memory_check()
    inventory=json.loads(Path(inventory_path).read_text())
    if len(inventory.get('provider_bindings',[]))>10000: raise ValueError('LOGO_RECORD_LIMIT')
    manifest={r['cache_key']:r for r in inventory.get('records',[])}
    files_root=Path(files_root); output_root=Path(output_root)
    assets=output_root/'assets/logos'; assets.mkdir(parents=True,exist_ok=True,mode=0o700)
    records=[]; invalid=0; seen=set(); total_bytes={}
    for binding in inventory.get('provider_bindings',[]):
        memory_check()
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
                'xmltv_icon_exported':False,'evidence':'Explicit backup key binding; PNG structure/pixel stream/dimensions/SHA verified'})
        except (ValueError,KeyError,OSError,TypeError,struct.error): invalid+=1
        memory_check()
    atomic(output_root/'provider-logo-metadata.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records).encode())
    return {'bound_provider_channels':len(inventory.get('provider_bindings',[])),
            'verified_provider_logos':len(records),'verified_logo_files':len(total_bytes),
            'invalid_logo_records':invalid,'size_bytes':sum(total_bytes.values()),
            'xmltv_logos_exported':0,'full_provider_denominator_known':False}
