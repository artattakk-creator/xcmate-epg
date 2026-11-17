"""Composable aggregation; network-capable metadata must be explicitly injected."""
from xml.etree import ElementTree as ET
from .artwork import enrich,enrich_episode
from .xmltv import merge,public_image,validate


def aggregate(sources,state,metadata=None,logos=None):
    root,conflicts=merge(sources)
    if metadata is not None:
        enrich(root,state,metadata.search,getattr(metadata,'movie',None))
        enrich_episode(root,state,metadata.search_series,metadata.episode)
    for channel in root.findall('channel'):
        logo=(logos or {}).get(channel.get('id'))
        if logo:
            public_image(logo)
            for icon in channel.findall('icon'): channel.remove(icon)
            ET.SubElement(channel,'icon',{'src':logo})
    validate(root)
    return root,{'conflicts':conflicts}
