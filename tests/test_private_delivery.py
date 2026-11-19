import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from xcmate_epg.full_preview import run_full
from xcmate_epg import private_delivery

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

    def git(self, *args):
        return subprocess.check_output(
            ['git', '--git-dir', self.root / 'delivery.git', *args], stderr=subprocess.DEVNULL)

    def test_manifest_pins_validated_universal_and_artwork_payloads(self):
        result = private_delivery.publish_delivery(self.root, approval=self.approval)
        self.assertEqual(result['status'], 'PRIVATE_READY')
        revision = self.git('rev-parse', 'refs/heads/main').decode().strip()
        manifest = json.loads(self.git('show', f'{revision}:manifest.json'))
        self.assertEqual((manifest['schema_version'], manifest['channel_count'],
                          manifest['programme_count'], manifest['source_count']), (2, 3, 3, 3))
        self.assertRegex(manifest['payload_ref'], r'^[a-f0-9]{40}$')
        self.assertEqual(self.git('rev-parse', f'{revision}^').decode().strip(), manifest['payload_ref'])
        for descriptor in (manifest, manifest['artwork']):
            blob = self.git('show', f"{manifest['payload_ref']}:{descriptor['file']}")
            self.assertEqual((len(blob), hashlib.sha256(blob).hexdigest()),
                             (descriptor['bytes'], descriptor['sha256']))
            self.assertNotIn(b'personal-42', gzip.decompress(blob))
        records = [json.loads(line) for line in gzip.decompress(
            self.git('show', f"{manifest['payload_ref']}:xcmate-artwork.jsonl.gz")).splitlines()]
        self.assertEqual(len(records), manifest['artwork']['records'])
        self.assertEqual({row['type'] for row in records}, {'poster', 'channel_logo'})
        self.assertIn({'poster', 'backdrop', 'episode_still'},
                      [{row.get('display_role') for row in records if row['channel'] == 's1:one'} -
                       {None}])
        self.assertEqual(set(self.git('ls-tree', '--name-only', revision).decode().splitlines()),
                         {'manifest.json', 'xcmate-epg.xml.gz', 'xcmate-artwork.jsonl.gz'})
        self.assertEqual(private_delivery.publish_delivery(self.root, approval=self.approval)['changed'], False)
        self.assertEqual(self.git('rev-parse', 'refs/heads/main').decode().strip(), revision)

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
        decision = private_delivery.publish_delivery(
            self.root, approval=dict(self.approval, generation=large['generation']))
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
        ready = private_delivery.publish_delivery(fresh, approval=approval)
        self.assertEqual(ready['status'], 'PRIVATE_READY')
        content = subprocess.check_output(
            ['git', '--git-dir', fresh / 'delivery.git', 'show',
             f"{ready['payload_ref']}:xcmate-artwork.jsonl.gz"])
        records = [json.loads(line) for line in gzip.decompress(content).splitlines()]
        logo = next(row for row in records if row['type'] == 'channel_logo')
        self.assertEqual((logo['width'], logo['height'], logo['scope']), (64, 100, 'channel'))

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
        pending = private_delivery.publish_delivery(self.root)
        self.assertEqual((pending['status'], pending['reason_code']),
                         ('PRIVATE_PENDING', 'PRODUCTION_DATA_NOT_APPROVED'))
        self.assertFalse((self.root / 'delivery.git').exists())
        private_delivery.publish_delivery(self.root, approval=self.approval)
        revision = self.git('rev-parse', 'refs/heads/main')
        self.approval['gates'].pop('rights')
        pending = private_delivery.publish_delivery(self.root, approval=self.approval)
        self.assertEqual(pending['status'], 'PRIVATE_PENDING')
        self.assertEqual(self.git('rev-parse', 'refs/heads/main'), revision)

    def test_rejects_mismatched_archive_and_keeps_last_good(self):
        private_delivery.publish_delivery(self.root, approval=self.approval)
        revision = self.git('rev-parse', 'refs/heads/main')
        bundle = self.root / self.run['directory']
        (bundle / 'artwork-metadata.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'RUN_FILE_CHECKSUM_MISMATCH'):
            private_delivery.publish_delivery(self.root, approval=self.approval)
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
            private_delivery.publish_delivery(self.root, approval=self.approval)
        self.assertFalse((self.root / 'delivery.git').exists())

    def test_delivery_checks_actual_programme_count_for_each_source(self):
        quality = self.root / self.run['directory'] / 'quality-report.json'
        report = json.loads(quality.read_text())
        report['sources'][1]['exported_programmes'] = 50
        self.replace_bundle_member('quality-report.json', json.dumps(report).encode())
        with self.assertRaisesRegex(ValueError, 'DELIVERY_SOURCE_COUNT_MISMATCH'):
            private_delivery.publish_delivery(self.root, approval=self.approval)
        self.assertFalse((self.root / 'delivery.git').exists())
