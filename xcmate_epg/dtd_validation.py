"""Validate the prepared gzip with the pinned upstream XMLTV grammar offline."""
import gzip
import hashlib
import os
from pathlib import Path
from .streaming import Limits, memory_guard

DTD_SHA256 = 'a1c88c4b840a6a80f8c5b6e6b850a864a0340148a01e63d6f59326c846f1d5c2'
XML_DECLARATION = b'<?xml version="1.0" encoding="utf-8"?>\n'


def validate(archive, dtd_path=None, *, max_bytes=768 * 1024 * 1024):
    dtd_path = dtd_path or os.environ.get('XCMATE_XMLTV_DTD')
    if not dtd_path:
        raise ValueError('OFFLINE_XMLTV_DTD_REQUIRED')
    try:
        with Path(dtd_path).open('rb') as stream:
            grammar = stream.read(128 * 1024 + 1)
    except OSError:
        raise ValueError('OFFLINE_XMLTV_DTD_REQUIRED') from None
    if hashlib.sha256(grammar).hexdigest() != DTD_SHA256:
        raise ValueError('OFFLINE_XMLTV_DTD_CHECKSUM_MISMATCH')
    try:
        from lxml import etree
    except ImportError:
        raise ValueError('OFFLINE_XMLTV_DTD_VALIDATOR_REQUIRED') from None

    class PinnedGrammar(etree.Resolver):
        def resolve(self, url, public_id, context):
            if url != 'xmltv.dtd' or public_id:
                raise OSError('EXTERNAL_XMLTV_RESOURCE_DISABLED')
            return self.resolve_string(grammar, context)

    parser = etree.XMLPullParser(events=('end',), dtd_validation=True, no_network=True, resolve_entities=False)
    parser.resolvers.add(PinnedGrammar())
    limits = Limits()
    try:
        with gzip.open(archive, 'rb') as stream:
            declaration = stream.readline(len(XML_DECLARATION) + 1)
            if declaration != XML_DECLARATION:
                raise ValueError('XMLTV_DTD_VALIDATION_FAILED')
            parser.feed(declaration + b'<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
            expanded = len(declaration)
            while chunk := stream.read(64 * 1024):
                expanded += len(chunk)
                if expanded > max_bytes:
                    raise ValueError('OUTPUT_XML_LIMIT')
                memory_guard(limits)
                parser.feed(chunk)
                for _, element in parser.read_events():
                    parent = element.getparent()
                    if parent is not None and parent.tag == 'tv':
                        parent.remove(element)
            parser.close()
    except (OSError, EOFError, etree.XMLSyntaxError):
        raise ValueError('XMLTV_DTD_VALIDATION_FAILED') from None
