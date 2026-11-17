"""Bounded image proofs using only the standard library.

PNG verifies the decompressed scanline stream. JPEG verifies frames, tables,
scans and terminal EOI; WebP verifies a complete static lossy RIFF/VP8 frame.
JPEG/VP8 entropy is not decoded and this is not a rendering/pixel proof.
Lossless/animated WebP and uncommon JPEG coding modes are conservatively rejected.
"""
import struct

from .private_logos import _png_dimensions

MAX_PIXELS = 16 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024


def _dimensions(width, height):
    if (not 0 < width <= 16384 or not 0 < height <= 16384
            or width * height > MAX_PIXELS or width * height * 4 > MAX_DECODED_BYTES):
        raise ValueError('IMAGE_DIMENSION_LIMIT')
    return width, height


def _jpeg_dimensions(data):
    if not data.startswith(b'\xff\xd8'): raise ValueError('JPEG_SOI')
    pos=2; frame=None; components={}; quant=set(); huffman=set(); scans={}; restart=0
    while pos<len(data):
        if data[pos]!=255: raise ValueError('JPEG_MARKER')
        while pos<len(data) and data[pos]==255: pos+=1
        if pos==len(data): raise ValueError('JPEG_TRUNCATED')
        marker=data[pos]; pos+=1
        if marker==0xd9:
            if pos!=len(data) or not frame or any(None in values for values in scans.values()):
                raise ValueError('JPEG_INCOMPLETE')
            return frame
        if marker in (0,0xd8,0x01) or 0xd0<=marker<=0xd7: raise ValueError('JPEG_MARKER')
        if pos+2>len(data): raise ValueError('JPEG_TRUNCATED')
        size=int.from_bytes(data[pos:pos+2],'big')
        if size<2 or pos+size>len(data): raise ValueError('JPEG_SEGMENT')
        block=data[pos+2:pos+size]; pos+=size
        if marker in (0xc0,0xc1,0xc2):
            if frame or len(block)<6: raise ValueError('JPEG_FRAME')
            precision,height,width,count=struct.unpack('>BHHB',block[:6])
            frame=_dimensions(width,height); progressive=marker==0xc2
            if precision!=8 or count not in (1,3,4) or len(block)!=6+3*count: raise ValueError('JPEG_FRAME')
            sampling=0
            for i in range(count):
                cid,factors,q=block[6+3*i:9+3*i]
                if cid in components or not 1<=factors>>4<=4 or not 1<=factors&15<=4 or q>3:
                    raise ValueError('JPEG_COMPONENT')
                components[cid]=q; scans[cid]=[None]*64; sampling+=(factors>>4)*(factors&15)
            if sampling>10: raise ValueError('JPEG_SAMPLING')
        elif marker==0xdb:
            cursor=0
            while cursor<len(block):
                info=block[cursor]; cursor+=1
                precision=info>>4; table=info&15
                if precision>1 or table>3: raise ValueError('JPEG_QUANTIZATION')
                length=64*(precision+1)
                if cursor+length>len(block): raise ValueError('JPEG_QUANTIZATION')
                values=block[cursor:cursor+length]
                if precision==0 and 0 in values: raise ValueError('JPEG_QUANTIZATION')
                if precision==1 and any(values[i:i+2]==b'\0\0' for i in range(0,length,2)):
                    raise ValueError('JPEG_QUANTIZATION')
                quant.add(table); cursor+=length
            if not block: raise ValueError('JPEG_QUANTIZATION')
        elif marker==0xc4:
            cursor=0
            while cursor<len(block):
                if cursor+17>len(block): raise ValueError('JPEG_HUFFMAN')
                info=block[cursor]; counts=block[cursor+1:cursor+17]; cursor+=17
                count=sum(counts); space=1
                for n in counts:
                    space=space*2-n
                    if space<0: raise ValueError('JPEG_HUFFMAN')
                if info>>4>1 or info&15>3 or not 0<count<=256 or cursor+count>len(block):
                    raise ValueError('JPEG_HUFFMAN')
                huffman.add((info>>4,info&15)); cursor+=count
            if not block: raise ValueError('JPEG_HUFFMAN')
        elif marker==0xdd:
            if len(block)!=2: raise ValueError('JPEG_RESTART')
            restart=int.from_bytes(block,'big')
        elif marker==0xda:
            if not frame or not block: raise ValueError('JPEG_SCAN')
            count=block[0]
            if not 1<=count<=len(components) or len(block)!=4+2*count: raise ValueError('JPEG_SCAN')
            first,last,approx=block[-3:]; high,low=approx>>4,approx&15
            if (not 0<=first<=last<=63 or high>13 or low>13
                    or (high and high!=low+1)
                    or (not progressive and (first,last,approx)!=(0,63,0))
                    or (progressive and ((first==0 and last!=0) or (first>0 and count!=1)))):
                raise ValueError('JPEG_SCAN')
            seen=set()
            for i in range(count):
                cid,tables=block[1+2*i:3+2*i]; dc,ac=tables>>4,tables&15
                if cid not in components or cid in seen or dc>3 or ac>3 or components[cid] not in quant:
                    raise ValueError('JPEG_SCAN_COMPONENT')
                if ((first==0 and not high and (0,dc) not in huffman)
                        or (last>0 and (1,ac) not in huffman)):
                    raise ValueError('JPEG_SCAN_TABLE')
                seen.add(cid)
            for cid in seen:
                for coefficient in range(first,last+1):
                    previous=scans[cid][coefficient]
                    if (not high and previous is not None) or (high and previous!=high):
                        raise ValueError('JPEG_PROGRESSIVE_ORDER')
                    scans[cid][coefficient]=low
            entropy=0
            while pos<len(data):
                if data[pos]!=255: entropy+=1; pos+=1; continue
                start=pos
                while pos<len(data) and data[pos]==255: pos+=1
                if pos==len(data): raise ValueError('JPEG_TRUNCATED_SCAN')
                code=data[pos]
                if code==0:
                    if pos!=start+1: raise ValueError('JPEG_STUFFING')
                    entropy+=1; pos+=1
                elif 0xd0<=code<=0xd7:
                    if not restart: raise ValueError('JPEG_RESTART')
                    pos+=1
                else:
                    pos=start; break
            if not entropy: raise ValueError('JPEG_EMPTY_SCAN')
        elif not (0xe0<=marker<=0xef or marker==0xfe):
            raise ValueError('JPEG_UNSUPPORTED_CODING')
    raise ValueError('JPEG_MISSING_EOI')


def _webp_dimensions(data):
    if (len(data)<12 or data[:4]!=b'RIFF' or data[8:12]!=b'WEBP'
            or int.from_bytes(data[4:8],'little')+8!=len(data)):
        raise ValueError('WEBP_RIFF')
    pos=12; canvas=None; image=None; seen=set(); alpha=False
    while pos<len(data):
        if pos+8>len(data): raise ValueError('WEBP_CHUNK')
        kind=data[pos:pos+4]; length=int.from_bytes(data[pos+4:pos+8],'little'); pos+=8
        end=pos+length
        if end+(length&1)>len(data) or (length&1 and data[end]!=0): raise ValueError('WEBP_CHUNK')
        block=data[pos:end]; pos=end+(length&1)
        if kind in seen: raise ValueError('WEBP_DUPLICATE_CHUNK')
        seen.add(kind)
        if kind in (b'ANIM',b'ANMF',b'VP8L'): raise ValueError('WEBP_UNSUPPORTED_CODING')
        if kind==b'VP8X':
            if len(seen)!=1 or length!=10 or block[0]&0xc3 or block[1:4]!=b'\0\0\0':
                raise ValueError('WEBP_EXTENDED_HEADER')
            canvas=_dimensions(1+int.from_bytes(block[4:7],'little'),1+int.from_bytes(block[7:10],'little'))
        elif kind==b'ALPH':
            # Only the uncompressed alpha form can be checked without a VP8L decoder.
            if not canvas or image or not block or block[0]!=0 or length!=1+canvas[0]*canvas[1]:
                raise ValueError('WEBP_UNSUPPORTED_ALPHA')
            alpha=True
        elif kind==b'VP8 ':
            if image or length<11: raise ValueError('WEBP_FRAME')
            tag=int.from_bytes(block[:3],'little'); partition=tag>>5
            if tag&1 or (tag>>1)&7>3 or not tag&16 or block[3:6]!=b'\x9d\x01\x2a':
                raise ValueError('WEBP_FRAME')
            width,height=struct.unpack('<HH',block[6:10])
            if width>>14 or height>>14 or not partition or 10+partition>=length:
                raise ValueError('WEBP_PARTITION')
            image=_dimensions(width,height)
        elif kind not in (b'ICCP',b'EXIF',b'XMP '): raise ValueError('WEBP_UNKNOWN_CHUNK')
    if not image or (canvas and canvas!=image) or (alpha and not canvas): raise ValueError('WEBP_INCOMPLETE')
    return image


def validate_image(data, mime):
    """Return bounded dimensions after the documented format-specific proof."""
    if not isinstance(data,bytes) or len(data)>8*1024*1024: raise ValueError('IMAGE_ENCODED_LIMIT')
    validators={'image/png':_png_dimensions,'image/jpeg':_jpeg_dimensions,'image/webp':_webp_dimensions}
    if mime not in validators: raise ValueError('IMAGE_UNSUPPORTED_MIME')
    if mime=='image/png' and len(data)>=24 and data[12:16]==b'IHDR':
        _dimensions(*struct.unpack('>II',data[16:24]))
    return _dimensions(*validators[mime](data))
