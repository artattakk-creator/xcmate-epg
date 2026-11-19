"""Durable event artwork decisions for the private V2 export."""
import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit
import zlib

from .artwork import title_key
from .xmltv import public_image

PROVIDERS = ('tmdb', 'tvmaze', 'xmltv')
ROLES = {
    'backdrop': ('backdrop', 'episode_still', 'poster'),
    'poster': ('poster', 'episode_still', 'backdrop'),
    'episode_still': ('episode_still', 'backdrop', 'poster'),
}
PLACEHOLDER = 'assets/artwork/standard.png'
LOG = logging.getLogger(__name__)


def standard_image():
    return (Path(__file__).with_name('assets') / 'standard.png').read_bytes()


def event_key(row):
    programme = row['element']
    episodes = sorted((item.get('system') or '', (item.text or '').strip())
                      for item in programme.findall('episode-num')
                      if item.get('system') not in ('tmdb', 'tvmaze'))
    identity = [row['qualified_id'], row['start'], row['stop'],
                title_key(programme.findtext('title')), title_key(programme.findtext('sub-title')),
                programme.findtext('date', '')[:4], episodes]
    return digest(identity)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def fits(role, width, height):
    if type(width) is not int or type(height) is not int or min(width, height) < 64:
        return False
    if role == 'poster':
        return height > width
    return width > height


def approved_url(url, hosts, forbidden):
    if (not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url)
            or any(value and value in url for value in forbidden)):
        return False
    try:
        public_image(url, hosts)
        return urlsplit(url).path not in ('', '/')
    except ValueError:
        return False


def validated_candidate(record, hosts, forbidden):
    url = record.get('url')
    provider = record.get('provider')
    if (provider not in (*PROVIDERS, 'feed', 'manual') or record.get('verified') is not True
            or not approved_url(url, hosts, forbidden)):
        return None
    if provider == 'feed':
        provider = 'xmltv'
    role = record.get('type')
    width, height = record.get('width'), record.get('height')
    if provider == 'xmltv' and role == 'unclassified_source_image':
        role = 'poster' if type(width) is int and type(height) is int and height > width else 'backdrop'
    if role not in ROLES or not fits(role, width, height):
        return None
    choice = {'selected_provider': provider, 'selected_role': role, 'selected_url': url,
            'provider_id': record.get('provider_id'), 'width': width, 'height': height,
            'attribution': record.get('attribution'), 'sha256': record.get('sha256'),
            'mime_type': record.get('mime_type'), 'size_bytes': record.get('size_bytes'),
            'validation': 'verified_image_structure',
            'confidence': 'confirmed' if provider == 'manual' else
                          'source_declared' if provider == 'xmltv' else 'verified_match'}
    if record.get('scope'):
        choice['scope'] = record['scope']
    elif role == 'episode_still':
        choice['scope'] = 'episode'
    if record.get('evidence'):
        choice['evidence_sha256'] = digest(record['evidence'])
    return choice


def local_choice(logo=None):
    return {'selected_provider': 'logo' if logo else 'placeholder',
            'selected_role': 'channel_logo' if logo else 'standard',
            'local_ref': logo or PLACEHOLDER,
            'confidence': 'confirmed' if logo else 'bundled'}


class ArtworkSelections:
    def __init__(self, db, hosts, forbidden, verifier, overrides, invalidations, titles,
                 *, upgrade_fallbacks=False):
        self.db = db
        self.hosts = hosts
        self.forbidden = forbidden
        self.verifier = verifier
        self.overrides = overrides
        self.invalidations = invalidations
        self.titles = titles
        self.upgrade_fallbacks = upgrade_fallbacks
        db.execute('''CREATE TABLE IF NOT EXISTS artwork_selections (
            event_key TEXT PRIMARY KEY, stop REAL NOT NULL, decisions BLOB NOT NULL)''')

    def prune(self, earliest_retained_end):
        self.db.execute('DELETE FROM artwork_selections WHERE stop<=?', (earliest_retained_end,))

    def title_override(self, key):
        override = self.titles.get(key, {})
        title = override.get('title')
        if isinstance(title, str) and 0 < len(title.strip()) <= 512 and override.get('evidence'):
            return title.strip()
        return None

    def previous(self, key):
        previous_row = self.db.execute(
            'SELECT decisions FROM artwork_selections WHERE event_key=?', (key,)).fetchone()
        return json.loads(zlib.decompress(previous_row[0])) if previous_row else {}

    def _confirmed_defect(self, key, role, old):
        failed = self.invalidations.get(key, {}).get(role, {})
        return (old.get('selected_url') and not old.get('replacement')
                and failed.get('evidence') and failed.get('selected_url_sha256') ==
                hashlib.sha256(old['selected_url'].encode()).hexdigest())

    def _blocked(self, old):
        return old.get('selected_url') and not approved_url(old['selected_url'], self.hosts, self.forbidden)

    def _new_title(self, title, old):
        return (title and old.get('title_override') != digest(title)
                and old['selected_provider'] != 'manual')

    def needs_candidates(self, key, previous, title):
        return not previous or any(self._blocked(old) or self._new_title(title, old)
                                   or self._confirmed_defect(key, role, old)
                                   or (self.upgrade_fallbacks and not old.get('selected_url'))
                                   for role, old in previous.items())

    def select(self, key, stop, previous, records, logo, title):
        candidates = [value for record in records
                      if (value := validated_candidate(record, self.hosts, self.forbidden))]
        decisions = {}
        for role, preferences in ROLES.items():
            old = previous.get(role)
            selection = self._manual(key, role, old)
            policy_blocked = old and self._blocked(old)
            logo_missing = old and old['selected_provider'] == 'logo' and old.get('local_ref') != logo
            replace = old and self._confirmed_defect(key, role, old)
            title_changed = old and self._new_title(title, old)
            upgrade = self.upgrade_fallbacks and old and not old.get('selected_url')
            if selection is None and old and not (replace or policy_blocked or title_changed or logo_missing or upgrade):
                decisions[role] = old
                continue
            reason = 'manual_override' if selection else 'verified_candidate'
            if selection is None:
                available = [item for item in candidates if item['selected_role'] in preferences
                             and item['selected_provider'] in PROVIDERS
                             and (not (replace or policy_blocked) or item['selected_url'] != old['selected_url'])]
                selection = None if logo_missing and not (title_changed or upgrade) else min(available, key=lambda item: (
                    PROVIDERS.index(item['selected_provider']),
                    preferences.index(item['selected_role']), item['selected_url'],
                    digest(item)), default=None)
                if (selection is None and (title_changed or (upgrade and not logo_missing))
                        and not (replace or policy_blocked)):
                    decisions[role] = old
                    continue
                if selection is None:
                    selection = local_choice(None if logo_missing else logo)
                    reason = 'logo_no_longer_confirmed' if logo_missing else 'no_verified_programme_image'
                if replace or policy_blocked:
                    selection['replacement'] = True
                    reason = 'blocked_by_policy' if policy_blocked else 'confirmed_invalid_reference'
                elif title_changed:
                    reason = 'manual_title_match'
            if title:
                selection['title_override'] = digest(title)
            selection['reason'] = reason
            selection['generation'] = digest([key, role, selection])
            decisions[role] = selection
            LOG.info('ARTWORK_SELECTED event=%s role=%s source=%s reason=%s cache=miss generation=%s',
                     key, role, selection['selected_provider'], reason, selection['generation'])
        if decisions != previous:
            self.db.execute('INSERT OR REPLACE INTO artwork_selections VALUES (?,?,?)',
                            (key, stop, zlib.compress(json.dumps(decisions, sort_keys=True).encode())))
        return decisions

    def _manual(self, key, role, old):
        override = self.overrides.get(key, {}).get(role, {})
        url = override.get('url')
        if (self.verifier is None or not isinstance(url, str) or not override.get('evidence')
                or override.get('rights_reviewed') is not True):
            return None
        override_role = override.get('role', role)
        if override_role not in ROLES:
            return None
        if old and (old['selected_provider'], old.get('selected_url'), old['selected_role']) == (
                'manual', url, override_role):
            return None
        if not approved_url(url, self.hosts, self.forbidden):
            return None
        failed = self.invalidations.get(key, {}).get(role, {})
        if failed.get('evidence') and failed.get('selected_url_sha256') == hashlib.sha256(url.encode()).hexdigest():
            return None
        record = dict(self.verifier(url), provider='manual', type=override_role, url=url,
                      attribution=override.get('attribution'))
        return validated_candidate(record, self.hosts, self.forbidden)
