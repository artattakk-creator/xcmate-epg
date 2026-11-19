"""Role-safe standard XMLTV references and bounded delivery URL checks."""
from urllib.parse import urlsplit

from .xmltv import public_image

IMAGE_TYPES = ('poster', 'backdrop', 'still', 'person', 'character')
IMAGE_COUNTS = (*IMAGE_TYPES, 'programme_icon', 'channel_logo')
MAX_MEDIA_PER_EVENT = 16
MAX_IMAGE_URL = 2048
SYSTEMS = {'tmdb', 'tvmaze', 'xmltv'}
ATTRIBUTES = {'type', 'size', 'orient', 'system'}
STANDARD_ROLES = {'poster': 'poster', 'backdrop': 'backdrop', 'episode_still': 'still',
                  'person': 'person', 'character': 'character'}


def image_url(url, hosts):
    if (not isinstance(url, str) or not url.isascii() or len(url) > MAX_IMAGE_URL
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or any(char in url for char in ('|', '\\'))
            or urlsplit(url).path in ('', '/')):
        raise ValueError('INVALID_XMLTV_IMAGE_URL')
    return public_image(url, hosts)


def validate_image_attrs(attrs):
    if (set(attrs) - ATTRIBUTES or attrs.get('type') not in IMAGE_TYPES
            or ('size' in attrs and attrs['size'] not in ('1', '2', '3'))
            or ('orient' in attrs and attrs['orient'] not in ('P', 'L'))
            or ('system' in attrs and attrs['system'] not in SYSTEMS)):
        raise ValueError('INVALID_XMLTV_IMAGE_ATTRIBUTES')


def image_attributes(choice, *, include_manual=False):
    role = STANDARD_ROLES[choice['selected_role']]
    provider = choice['selected_provider']
    attrs = {'type': role}
    if provider in SYSTEMS:
        attrs['system'] = provider
    elif not include_manual or provider != 'manual':
        raise ValueError('INVALID_XMLTV_IMAGE_PROVIDER')
    width, height = choice.get('width'), choice.get('height')
    if type(width) is int and type(height) is int and min(width, height) > 0:
        maximum = max(width, height)
        attrs['size'] = '1' if maximum < 200 else '2' if maximum <= 400 else '3'
        if width != height:
            attrs['orient'] = 'P' if height > width else 'L'
    return attrs
