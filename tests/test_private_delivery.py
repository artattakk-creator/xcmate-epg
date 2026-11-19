import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from xcmate_epg.full_preview import run_full
from xcmate_epg import private_delivery

DTD = os.environ.get("XCMATE_XMLTV_DTD")
PNG_SHA = 'a' * 64
LOGO = 'https://image.tmdb.org/t/p/w185/fixture-logo.png'
POSTER = 'https://image.tmdb.org/t/p/w500/fixture-poster.png'
SOURCE = f'''<tv><channel id="one"><display-name>Fixture</display-name><icon src="{LOGO}"/></channel>
<programme channel="one" start="20261119120000 +0000" stop="20261119140000 +0000">
<title>Test Event</title></programme></tv>'''


class VerifiedFixture:
    def verify_media(self, url):
        return {'verified': True, 'sha256': PNG_SHA, 'size_bytes': 100,
                'mime_type': 'image/png', 'width': 360, 'height': 640}

    def enrich(self, channel, event):
        return event, [dict(self.verify_media(POSTER), provider='tmdb', type='poster',
                            url=POSTER, attribution='TMDB', provider_id=123)]

    def summary(self):
        return {}


class PrivateDeliveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root = self.base / 'private'
        self.config = {
            'sources': [],
            'horizon': {'reference_time': '2026-11-19T12:00:00+00:00'},
            'artwork': {'stable_selection': True, 'confirmed_source_logos': [{
                'qualified_id': 's1:one', 'url': LOGO, 'rights_reviewed': True,
                'evidence': 'fixture', 'attribution': 'TMDB'}]},
            'mapping': {
                'catalog_complete': True, 'catalog': [{'provider_id': 'personal-42', 'name': 'Fixture'}],
                'confirmations': [{'provider_id': 'personal-42', 'qualified_id': 's1:one',
                                   'evidence': 'test confirmation'}],
            },
        }
        for number in (1, 2, 3):
            path = self.base / f'feed{number}.xml'
            path.write_text(SOURCE)
            self.config['sources'].append({'id': f's{number}', 'path': str(path)})
        self.report = run_full(self.config, self.root, metadata=VerifiedFixture())
        self.run = json.loads((self.root / 'run-manifest.json').read_text())
        self.approval = {'generation': self.run['generation'], 'gates': {
            name: f'synthetic evidence for {name}' for name in (
                'restore', 'sources', 'mapping', 'artwork', 'delivery', 'client', 'rights', 'release')
        }}

    def publish(self, *, approval=None, workspace=None):
        if approval and not DTD:
            self.skipTest('Set XCMATE_XMLTV_DTD to the pinned official grammar')
        return private_delivery.publish_delivery(workspace or self.root, approval=approval, dtd_path=DTD)

    def git(self, *args):
        return subprocess.check_output(
            ['git', '--git-dir', self.root / 'delivery.git', *args], stderr=subprocess.DEVNULL)

    def test_manifest_pins_only_one_standard_xmltv_generation(self):
        result = self.publish(approval=self.approval)
        revision = self.git('rev-parse', 'refs/heads/main').decode().strip()
        manifest = json.loads(self.git('show', f'{revision}:manifest.json'))
        self.assertEqual(result['status'], 'PRIVATE_READY')
        self.assertEqual((manifest['schema_version'], manifest['channel_count'],
                          manifest['programme_count'], manifest['source_count']), (3, 3, 3, 3))
        self.assertRegex(manifest['payload_ref'], r'^[a-f0-9]{40}$')
        self.assertEqual(self.git('rev-parse', f'{revision}^').decode().strip(), manifest['payload_ref'])
        self.assertEqual(manifest['window'], {'start_utc': '2026-11-19T12:00:00+00:00',
                                               'stop_utc': '2026-11-19T14:00:00+00:00'})
        self.assertEqual(manifest['image_counts'], {'poster': 3, 'backdrop': 0, 'still': 0,
                         'person': 0, 'character': 0, 'programme_icon': 3, 'channel_logo': 1})
        blob = self.git('show', f"{manifest['payload_ref']}:xcmate-epg.xml.gz")
        self.assertEqual((len(blob), hashlib.sha256(blob).hexdigest()),
                         (manifest['bytes'], manifest['sha256']))
        self.assertEqual(manifest['revision'], manifest['sha256'])
        self.assertNotIn(b'personal-42', gzip.decompress(blob))
        self.assertNotIn('artwork', manifest)
        self.assertEqual(set(self.git('ls-tree', '--name-only', revision).decode().splitlines()),
                         {'manifest.json', 'xcmate-epg.xml.gz'})
        self.assertFalse(self.publish(approval=self.approval)['changed'])
        self.assertEqual(self.git('rev-parse', 'refs/heads/main').decode().strip(), revision)

    def test_multiple_actual_roles_private_override_and_nonstandard_evidence(self):
        from xml.etree import ElementTree as ET

        class RolesFixture(VerifiedFixture):
            def enrich(self, channel, event):
                event, records = super().enrich(channel, event)
                for role, name, width, height in (
                    ('fanart', 'backdrop', 800, 400), ('episode_still', 'still', 400, 220),
                    ('person', 'person', 180, 190), ('character', 'character', 200, 200),
                    ('clearlogo', 'private-clearlogo', 300, 160),
                    ('season_poster', 'private-season', 300, 420),
                ):
                    record = dict(self.verify_media(POSTER), provider='tvmaze', type=role,
                                  url=f'https://static.tvmaze.com/uploads/images/{name}.png',
                                  attribution='TVMaze', provider_id=42, width=width, height=height)
                    records.append(record)
                ET.SubElement(event, 'review', {'type': 'text'}).text = 'Synthetic review'
                return event, records

        fresh = self.base / 'standard-roles'
        run_full(self.config, fresh, metadata=RolesFixture())
        decisions = json.loads((fresh / 'artwork-selection.jsonl').read_text().splitlines()[0])
        manual = 'https://image.tmdb.org/t/p/w500/manual-fixture.png'
        self.config['artwork']['manual_images'] = {decisions['event_key']: {
            'poster': {'url': manual, 'evidence': 'synthetic', 'rights_reviewed': True,
                       'attribution': 'fixture owner'},
        }}
        run_full(self.config, fresh, metadata=RolesFixture())
        universal = ET.fromstring(gzip.decompress((fresh / 'full/xcmate-epg.xml.gz').read_bytes()))
        profile = ET.fromstring(gzip.decompress((fresh / 'device/xcmate-epg.xml.gz').read_bytes()))
        self.assertNotIn(manual, ET.tostring(universal, encoding='unicode'))
        self.assertIn(manual, ET.tostring(profile, encoding='unicode'))
        self.assertEqual({node.get('type') for node in profile.findall('programme/image')},
                         {'poster', 'backdrop', 'still', 'person', 'character'})
        programme = universal.find('programme')
        self.assertEqual({node.get('type') for node in programme.findall('image')},
                         {'poster', 'backdrop', 'still', 'person', 'character'})
        self.assertLess(list(programme).index(programme.find('review')),
                        list(programme).index(programme.find('image')))
        by_type = {node.get('type'): node for node in programme.findall('image')}
        self.assertEqual(by_type['backdrop'].attrib,
                         {'type': 'backdrop', 'system': 'tvmaze', 'orient': 'L', 'size': '3'})
        self.assertEqual(by_type['person'].get('size'), '1')
        self.assertEqual(by_type['still'].get('size'), '2')
        self.assertIsNone(by_type['character'].get('orient'))
        self.assertEqual(programme.find('icon').get('src'), by_type['backdrop'].text)
        self.assertIn('private-clearlogo', (fresh / 'artwork-extra-private.jsonl').read_text())
        self.assertIn('private-season', (fresh / 'artwork-extra-private.jsonl').read_text())
        self.assertNotIn('private-clearlogo', ET.tostring(universal, encoding='unicode'))
        run = json.loads((fresh / 'run-manifest.json').read_text())
        report = self.publish(workspace=fresh, approval=dict(self.approval, generation=run['generation']))
        self.assertEqual(report['image_counts'], dict.fromkeys(
            ('poster', 'backdrop', 'still', 'person', 'character', 'programme_icon'), 3) |
            {'channel_logo': 1})

    def test_missing_or_substituted_local_dtd_blocks_a_ready_manifest(self):
        from unittest.mock import patch

        weaker = self.base / 'substitute.dtd'
        weaker.write_text('<!ELEMENT tv ANY>')
        with patch.dict(os.environ, {'XCMATE_XMLTV_DTD': ''}):
            with self.assertRaisesRegex(ValueError, 'OFFLINE_XMLTV_DTD_REQUIRED'):
                private_delivery.publish_delivery(self.root, approval=self.approval)
        with self.assertRaisesRegex(ValueError, 'OFFLINE_XMLTV_DTD_CHECKSUM_MISMATCH'):
            private_delivery.publish_delivery(self.root, approval=self.approval, dtd_path=weaker)
        self.assertFalse((self.root / 'delivery.git').exists())

    def test_v2_to_v3_local_promotion_retains_old_commit_without_mixing_payloads(self):
        from xcmate_epg import dtd_validation
        from unittest.mock import patch

        if not DTD:
            self.skipTest('Set XCMATE_XMLTV_DTD to the pinned official grammar')
        stage = self.base / 'legacy'
        stage.mkdir()
        legacy_files = {
            'manifest.json': b'{"schema_version":2,"status":"PRIVATE_READY"}',
            'xcmate-epg.xml.gz': gzip.compress(b'<tv/>'),
            'xcmate-artwork.jsonl.gz': gzip.compress(b''),
        }
        delivery = private_delivery._LocalDelivery(self.root, stage)
        blobs = {}
        for name, data in legacy_files.items():
            path = stage / name
            path.write_bytes(data)
            blobs[name] = delivery.blob(path)
        legacy = delivery.commit(blobs, None, 'Legacy synthetic V2')
        delivery.install(legacy, None)
        with patch.object(dtd_validation, 'validate', side_effect=ValueError('XMLTV_DTD_VALIDATION_FAILED')):
            with self.assertRaisesRegex(ValueError, 'XMLTV_DTD_VALIDATION_FAILED'):
                self.publish(approval=self.approval)
        self.assertEqual(self.git('rev-parse', 'refs/heads/main').decode().strip(), legacy)
        result = self.publish(approval=self.approval)
        self.assertEqual(result['schema_version'], 3)
        self.assertEqual(self.git('show', f'{legacy}:xcmate-artwork.jsonl.gz'),
                         legacy_files['xcmate-artwork.jsonl.gz'])
        self.assertEqual(set(self.git('ls-tree', '--name-only', 'refs/heads/main').decode().splitlines()),
                         {'manifest.json', 'xcmate-epg.xml.gz'})

    def test_official_dtd_rejects_nonstandard_element_before_commit(self):
        from xcmate_epg.full_export import publish_run, publish_stream
        from xml.etree import ElementTree as ET

        channels = [ET.fromstring(f'<channel id="s{i}:one"><display-name>Fixture</display-name></channel>')
                    for i in (1, 2, 3)]
        events = [ET.fromstring(f'<programme channel="s{i}:one" start="20261119120000 +0000" '
                               'stop="20261119140000 +0000"><title>Fixture</title><invented/></programme>')
                  for i in (1, 2, 3)]
        stage = self.root / '.invalid-dtd-stage'
        stage.mkdir(mode=0o700)
        previous = self.root / self.run['directory']
        for name in ('quality-report.json', 'artwork-metadata.jsonl'):
            (stage / name).write_bytes((previous / name).read_bytes())
        publish_stream(channels, events, stage / 'full')
        publish_run(stage, self.root)
        invalid = json.loads((self.root / 'run-manifest.json').read_text())
        with self.assertRaisesRegex(ValueError, 'XMLTV_DTD_VALIDATION_FAILED'):
            self.publish(approval=dict(self.approval, generation=invalid['generation']))
        self.assertFalse((self.root / 'delivery.git').exists())

    def test_complete_delivery_streams_more_than_185000_unique_programmes(self):
        from datetime import datetime, timedelta
        from xml.etree import ElementTree as ET
        from xcmate_epg.full_export import publish_run, publish_stream

        count = 185001
        stage = self.root / '.large-synthetic-stage'
        stage.mkdir(mode=0o700)
        last_bundle = self.root / self.run['directory']
        quality = json.loads((last_bundle / 'quality-report.json').read_text())
        first = datetime.fromisoformat('2026-11-19T12:00:00+00:00')
        last_stop = '20261119120100 +0000'

        def channels():
            for source in range(1, 4):
                channel = ET.Element('channel', {'id': f's{source}:one'})
                ET.SubElement(channel, 'display-name').text = f'Synthetic {source}'
                if source == 1:
                    ET.SubElement(channel, 'icon', {'src': LOGO})
                yield channel

        def programmes():
            for index in range(count):
                source = index % 3 + 1
                start = first + timedelta(minutes=index // 3)
                stop = start + timedelta(minutes=1)
                programme = ET.Element('programme', {'channel': f's{source}:one',
                    'start': start.strftime('%Y%m%d%H%M%S %z'),
                    'stop': stop.strftime('%Y%m%d%H%M%S %z')})
                ET.SubElement(programme, 'title').text = 'Synthetic Event'
                if index < 3:
                    ET.SubElement(programme, 'icon', {'src': POSTER})
                    ET.SubElement(programme, 'image', {'type': 'poster', 'system': 'tmdb',
                                                       'orient': 'P', 'size': '3'}).text = POSTER
                yield programme

        published = publish_stream(channels(), programmes(), stage / 'full')
        self.assertEqual(published['programmes'], count)
        for source in quality['sources']:
            source['exported_programmes'] = count // 3
        (stage / 'quality-report.json').write_text(json.dumps(quality))
        sidecar = [json.loads(line) for line in (last_bundle / 'artwork-metadata.jsonl').read_text().splitlines()]
        for row in sidecar:
            if row['type'] != 'channel_logo':
                row['stop'] = last_stop
        (stage / 'artwork-metadata.jsonl').write_text(
            ''.join(json.dumps(row) + '\n' for row in sidecar))
        publish_run(stage, self.root)
        large = json.loads((self.root / 'run-manifest.json').read_text())
        decision = self.publish(approval=dict(self.approval, generation=large['generation']))
        self.assertEqual((decision['channel_count'], decision['programme_count']), (3, count))

    def test_actual_image_verifier_supplies_logo_dimensions_to_delivery(self):
        from image_fixtures import png
        from xcmate_epg.fetch import Response
        from xcmate_epg.live_artwork import ArtworkEnricher
        from xcmate_epg.state import State

        self.config['artwork']['allow_feed_images'] = True
        for source in self.config['sources']:
            Path(source['path']).write_text(SOURCE.replace('</programme>', f'<icon src="{POSTER}"/></programme>'))

        def transport(url, headers):
            return Response(200, png(64, 100), {'Content-Type': 'image/png'})

        fresh = self.base / 'real-verifier'
        with State(self.base / 'test-artwork.sqlite') as state:
            metadata = ArtworkEnricher(state, {'allow_feed_images': True, 'request_interval': 0},
                                       transport=transport)
            run_full(self.config, fresh, metadata=metadata)
        run = json.loads((fresh / 'run-manifest.json').read_text())
        approval = dict(self.approval, generation=run['generation'])
        ready = self.publish(workspace=fresh, approval=approval)
        self.assertEqual(ready['status'], 'PRIVATE_READY')
        self.assertEqual(ready['image_counts']['channel_logo'], 1)
        content = subprocess.check_output(
            ['git', '--git-dir', fresh / 'delivery.git', 'show',
             f"{ready['payload_ref']}:xcmate-epg.xml.gz"])
        from xml.etree import ElementTree as ET
        logo = ET.fromstring(gzip.decompress(content)).find('channel/icon')
        self.assertEqual((logo.get('width'), logo.get('height')), ('64', '100'))

    def test_derive_additional_private_profile_from_committed_master_without_sources(self):
        from xcmate_epg.profiles import derive_profile

        master = self.root / 'run-manifest.json'
        before = master.read_bytes()
        for source in self.config['sources']:
            Path(source['path']).unlink()
        mapping = {'catalog': [{'provider_id': 'other-device-17', 'name': 'Fixture'}],
                   'confirmations': [{'provider_id': 'other-device-17', 'qualified_id': 's3:one',
                                      'evidence': 'synthetic confirmation'}]}
        result = derive_profile(self.root, 'extra-profile', mapping)
        self.assertEqual(result['programme_count'], 1)
        self.assertEqual(result['universal_generation'], self.run['generation'])
        archive = self.root / 'profiles/extra-profile/device/xcmate-epg.xml.gz'
        self.assertIn(b'provider:other-device-17', gzip.decompress(archive.read_bytes()))
        self.assertEqual(master.read_bytes(), before)
        self.assertNotIn(b'other-device-17', (self.root / 'mapping-review.json').read_bytes())

    def test_missing_approval_does_not_create_or_replace_current_ref(self):
        pending = self.publish()
        self.assertEqual((pending['status'], pending['reason_code']),
                         ('PRIVATE_PENDING', 'PRODUCTION_DATA_NOT_APPROVED'))
        self.assertFalse((self.root / 'delivery.git').exists())
        self.publish(approval=self.approval)
        revision = self.git('rev-parse', 'refs/heads/main')
        self.approval['gates'].pop('rights')
        pending = self.publish(approval=self.approval)
        self.assertEqual(pending['status'], 'PRIVATE_PENDING')
        self.assertEqual(self.git('rev-parse', 'refs/heads/main'), revision)

    def test_rejects_mismatched_archive_and_keeps_last_good(self):
        self.publish(approval=self.approval)
        revision = self.git('rev-parse', 'refs/heads/main')
        bundle = self.root / self.run['directory']
        (bundle / 'artwork-metadata.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'RUN_FILE_CHECKSUM_MISMATCH'):
            self.publish(approval=self.approval)
        self.assertEqual(self.git('rev-parse', 'refs/heads/main'), revision)

    def replace_bundle_member(self, name, data):
        bundle = self.root / self.run['directory']
        (bundle / name).write_bytes(data)
        files = self.run['files']
        files[name] = hashlib.sha256(data).hexdigest()
        generation = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        bundle.rename(bundle.with_name(generation))
        self.run.update(generation=generation, directory=f'runs/{generation}')
        (self.root / 'run-manifest.json').write_text(json.dumps(self.run))
        self.approval['generation'] = generation

    def test_unverified_or_orphan_sidecar_cannot_be_published_even_with_updated_bundle_hashes(self):
        bundle = self.root / self.run['directory']
        sidecar = bundle / 'artwork-metadata.jsonl'
        data = sidecar.read_bytes().replace(POSTER.encode(), b'https://image.tmdb.org/t/p/w500/unrelated.png')
        self.replace_bundle_member('artwork-metadata.jsonl', data)
        with self.assertRaisesRegex(ValueError, 'ARTWORK_XMLTV_REFERENCE_MISMATCH'):
            self.publish(approval=self.approval)
        self.assertFalse((self.root / 'delivery.git').exists())

    def test_delivery_checks_actual_programme_count_for_each_source(self):
        quality = self.root / self.run['directory'] / 'quality-report.json'
        report = json.loads(quality.read_text())
        report['sources'][1]['exported_programmes'] = 50
        self.replace_bundle_member('quality-report.json', json.dumps(report).encode())
        with self.assertRaisesRegex(ValueError, 'DELIVERY_SOURCE_COUNT_MISMATCH'):
            self.publish(approval=self.approval)
        self.assertFalse((self.root / 'delivery.git').exists())
