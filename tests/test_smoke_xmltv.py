"""Offline-only structural checks for the initial no-cost GitHub runner."""
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

from tools.smoke_xmltv import demo_xmltv, generate, verify_xmltv


class XmltvSmokeTests(unittest.TestCase):
    def test_single_synthetic_programme_matches_channel(self):
        data = demo_xmltv()
        verify_xmltv(data)
        root = ET.fromstring(data)
        self.assertEqual(root.find("channel").attrib["id"], "synthetic.demo.invalid")
        self.assertEqual(
            root.find("programme").attrib["channel"], "synthetic.demo.invalid"
        )
        self.assertNotIn(b"tmdb", data.lower())
        self.assertNotIn(b"epgshare", data.lower())

    def test_demo_gzip_and_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            result = generate(base)
            self.assertFalse(result["is_production_epg"])
            self.assertEqual(result["sources_fetched"], 0)
            self.assertEqual(result["tmdb_calls"], 0)
            archive = base / result["xmltv_file"]
            self.assertEqual(
                hashlib.sha256(archive.read_bytes()).hexdigest(), result["sha256"]
            )
            self.assertEqual(gzip.decompress(archive.read_bytes()), demo_xmltv())
            manifest = json.loads((base / "manifest.demo.json").read_text("utf-8"))
            self.assertEqual(manifest["status"], "DEMO_ONLY_NOT_FOR_PUBLICATION")

    def test_inconsistent_channel_rejected(self):
        bad = demo_xmltv().replace(b"synthetic.demo.invalid", b"other.invalid", 1)
        with self.assertRaises(ValueError):
            verify_xmltv(bad)


if __name__ == "__main__":
    unittest.main()
