"""Bounded XMLTV parsing and deterministic source-qualified merge."""
import copy
from bisect import bisect_left
import gzip
import io
import re
import zlib
import unicodedata
from datetime import datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

LIMIT = 32 * 1024 * 1024
IMAGE_HOSTS = {'image.tmdb.org', 'static.tvmaze.com'}


def public_image(url, image_hosts=None):
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname not in (IMAGE_HOSTS if image_hosts is None else image_hosts) or p.username
            or p.password or p.port not in (None,443) or p.query or p.fragment):
        raise ValueError('image policy violation')
    return url


def normalize_name(name):
    value = unicodedata.normalize('NFKC', name).casefold()
    return re.sub(r'\s+(hd|sd|uhd|4k)$', '', value).strip()


def timestamp(value):
    if not re.fullmatch(r'\d{14} [+-](?:0\d|1[0-4])[0-5]\d', value):
        raise ValueError('explicit valid XMLTV timezone required')
    try:
        return datetime.strptime(value, '%Y%m%d%H%M%S %z')
    except ValueError:
        raise ValueError('invalid XMLTV time') from None


def validate(root, image_hosts=None):
    if root.tag != 'tv':
        raise ValueError('XMLTV tv root required')
    channels = root.findall('channel')
    ids = [c.get('id') for c in channels]
    if not ids or any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('missing or duplicate channel IDs')
    known_ids = set(ids)
    programmes = root.findall('programme')
    if not programmes:
        raise ValueError('missing programme data')
    for p in programmes:
        if p.get('channel') not in known_ids or not (p.findtext('title') or '').strip():
            raise ValueError('invalid programme reference or title')
        if timestamp(p.get('start','')) >= timestamp(p.get('stop','')):
            raise ValueError('non-positive programme duration')
    for icon in root.iter('icon'):
        if (set(icon.attrib)-{'src','width','height'} or len(icon)
                or (icon.text or '').strip() or (icon.tail or '').strip()):
            raise ValueError('invalid XMLTV icon structure')
        for attribute in ('width','height'):
            if attribute in icon.attrib and not re.fullmatch(r'[1-9][0-9]{0,4}',icon.get(attribute)):
                raise ValueError('invalid icon dimension')
        public_image(icon.get('src',''), image_hosts)


def parse(data, source, aliases=None, limit=LIMIT, image_hosts=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',source):
        raise ValueError('invalid source identifier')
    if len(data) > limit:
        raise ValueError('input too large')
    try:
        if data.startswith(b'\x1f\x8b'):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
                data = f.read(limit+1)
        # UTF-8 only prevents UTF-16 DTD/ENTITY filter bypasses.
        text = data.decode('utf-8-sig')
        if len(data)>limit or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
            raise ValueError('unsafe XML')
        root = ET.fromstring(text)
        validate(root, image_hosts)
    except (ET.ParseError, UnicodeError, OSError, EOFError, zlib.error):
        raise ValueError('invalid XMLTV or gzip') from None
    aliases = aliases or {}
    mapping = {}
    for c in root.findall('channel'):
        raw = c.get('id')
        qualified = source + ':' + raw
        mapping[raw] = aliases.get(qualified, qualified)
        c.set('id',mapping[raw])
    for p in root.findall('programme'):
        p.set('channel',mapping[p.get('channel')])
    # Two raw IDs cannot silently map into one channel inside a source.
    validate(root, image_hosts)
    return root


def merge(sources, image_hosts=None):
    root = ET.Element('tv', {'generator-info-name':'XCMate EPG Agent'})
    channels, programmes = {}, {}
    conflicts = 0
    intervals = {}
    for _, tree in sorted(sources,key=lambda pair: pair[0]):
        for c in tree.findall('channel'):
            channels.setdefault(c.get('id'),copy.deepcopy(c))
        for p in tree.findall('programme'):
            key=(p.get('channel'),timestamp(p.get('start','')))
            if key in programmes:
                conflicts += ET.tostring(programmes[key]) != ET.tostring(p)
            else:
                start=key[1]
                stop=timestamp(p.get('stop',''))
                accepted=intervals.setdefault(key[0],[])
                position=bisect_left(accepted,(start,))
                if ((position and accepted[position-1][1]>start)
                        or (position<len(accepted) and accepted[position][0]<stop)):
                    conflicts+=1
                    continue
                accepted.insert(position,(start,stop))
                programmes[key]=copy.deepcopy(p)
    root.extend(channels[k] for k in sorted(channels))
    root.extend(programmes[k] for k in sorted(programmes))
    validate(root, image_hosts)
    return root, conflicts
