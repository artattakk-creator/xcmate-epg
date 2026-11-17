"""Synthetic fixtures for conservative provider mapping and private storage."""
import importlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


class ChannelMappingTests(unittest.TestCase):
    def api(self):
        try:
            return importlib.import_module("xcmate_epg.channel_mapping")
        except ModuleNotFoundError:
            self.fail("conservative mapping API is not implemented")

    def channels(self):
        return [
            {"qualified_id": "s2:news", "name": "Example News", "source": "s2", "priority": 20},
            {"qualified_id": "s1:news", "name": "Example News HD", "source": "s1", "priority": 10},
        ]

    def test_only_explicit_confirmations_map_and_priority_selects_fallback(self):
        catalog = [{"provider_id": "p1", "name": "Example News", "stream_url": "private-ignored"}]
        confirmations = [
            {"provider_id": "p1", "qualified_id": "s2:news", "evidence": "synthetic review 2"},
            {"provider_id": "p1", "qualified_id": "s1:news", "evidence": "synthetic review 1"},
        ]
        result = self.api().build_mapping(catalog, confirmations, iter(self.channels()))
        entry = result["entries"][0]
        self.assertEqual((entry["status"], entry["qualified_id"]), ("confirmed", "s1:news"))
        self.assertEqual([x["qualified_id"] for x in entry["confirmed_sources"]], ["s1:news", "s2:news"])
        self.assertEqual(result["summary"]["coverage"], 1.0)
        self.assertNotIn("stream_url", json.dumps(result))
        self.assertEqual(result, self.api().build_mapping(catalog, list(reversed(confirmations)), reversed(self.channels())))

    def test_nonexistent_source_and_empty_evidence_cannot_confirm(self):
        catalog = [{"provider_id": "p1", "name": "Different"}]
        for confirmation in [
            {"provider_id": "p1", "qualified_id": "s9:gone", "evidence": "synthetic review"},
            {"provider_id": "p1", "qualified_id": "s1:news", "evidence": " "},
        ]:
            with self.subTest(confirmation=confirmation):
                result = self.api().build_mapping(catalog, [confirmation], self.channels())
                self.assertIsNone(result["entries"][0]["qualified_id"])
                self.assertEqual(result["summary"]["mapped_provider_channels"], 0)
                self.assertEqual(result["entries"][0]["status"], "candidate_manual_review")
        result = self.api().build_mapping(catalog, [{"provider_id": "missing", "qualified_id": "s1:news", "evidence": "review"}], self.channels())
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["summary"]["invalid_confirmations"], 1)

    def test_identifier_aliases_and_name_collisions_are_candidates(self):
        catalog = [{"provider_id": "p1", "name": "Ｅxample\u00a0 News", "epg_id": "news"}]
        result = self.api().build_mapping(catalog, [], self.channels())
        entry = result["entries"][0]
        self.assertEqual(entry["status"], "candidate_manual_review")
        self.assertIsNone(entry["qualified_id"])
        self.assertEqual({c["qualified_id"] for c in entry["candidates"]}, {"s1:news", "s2:news"})
        self.assertTrue(all(0 < c["confidence"] < 1 for c in entry["candidates"]))
        self.assertEqual(entry["confirmed_sources"], [])

    def test_locked_conflict_remains_reviewable(self):
        confirmations = [{"provider_id": "p1", "qualified_id": c["qualified_id"], "evidence": "review", "locked": True} for c in self.channels()]
        entry = self.api().build_mapping([{"provider_id": "p1", "name": "Example News"}], confirmations, self.channels())["entries"][0]
        self.assertEqual(entry["status"], "candidate_manual_review")
        self.assertIsNone(entry["qualified_id"])
        self.assertEqual(len(entry["confirmed_sources"]), 2)
        self.assertIn("conflicting_locked_confirmations", entry["evidence"])

    def test_one_lock_selects_confirmed_primary(self):
        confirmations = [{"provider_id": "p1", "qualified_id": c["qualified_id"], "evidence": "review", "locked": c["source"] == "s2"} for c in self.channels()]
        entry = self.api().build_mapping([{"provider_id": "p1", "name": "Example News"}], confirmations, self.channels())["entries"][0]
        self.assertEqual(entry["qualified_id"], "s2:news")

    def test_missing_locked_target_survives_persistence_and_restoration(self):
        catalog = [{"provider_id": "p1", "name": "Example News"}]
        missing_lock = {"provider_id": "p1", "qualified_id": "s9:primary", "evidence": "synthetic manual override", "locked": True}
        fallback = {"provider_id": "p1", "qualified_id": "s1:news", "evidence": "synthetic fallback review"}
        result = self.api().build_mapping(catalog, [missing_lock, fallback], self.channels())
        entry = result["entries"][0]
        self.assertEqual(entry["status"], "candidate_manual_review")
        self.assertIsNone(entry["qualified_id"])
        self.assertEqual(result["summary"]["mapped_provider_channels"], 0)
        self.assertEqual(result["summary"]["coverage"], 0)
        self.assertIn("locked_confirmation_target_missing", entry["evidence"])
        self.assertIn(missing_lock["evidence"], entry["evidence"])
        self.assertEqual(entry["unresolved_confirmations"], [missing_lock])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            self.api().persist_mapping(result, path)
            stored = json.loads(path.read_text())
        retained = stored["entries"][0]["unresolved_confirmations"]
        restored_channels = self.channels() + [{"qualified_id": "s9:primary", "name": "Example News", "source": "s9", "priority": 99}]
        restored = self.api().build_mapping(catalog, retained + [fallback], restored_channels)
        self.assertEqual(restored["entries"][0]["status"], "confirmed")
        self.assertEqual(restored["entries"][0]["qualified_id"], "s9:primary")
        self.assertEqual(restored["entries"][0]["unresolved_confirmations"], [])
        self.assertIn(missing_lock["evidence"], restored["entries"][0]["evidence"])

    def test_missing_lock_conflicts_with_a_different_present_lock(self):
        catalog = [{"provider_id": "p1", "name": "Example News"}]
        missing = {"provider_id": "p1", "qualified_id": "s9:primary", "evidence": "synthetic unavailable lock", "locked": True}
        present = {"provider_id": "p1", "qualified_id": "s1:news", "evidence": "synthetic present lock", "locked": True}
        result = self.api().build_mapping(catalog, [missing, present], self.channels())
        entry = result["entries"][0]
        self.assertEqual(entry["status"], "candidate_manual_review")
        self.assertIsNone(entry["qualified_id"])
        self.assertEqual(entry["unresolved_confirmations"], [missing])
        self.assertIn("conflicting_locked_confirmations", entry["evidence"])
        self.assertEqual(result["summary"]["mapped_provider_channels"], 0)

    def test_resolution_variants_require_review(self):
        result = self.api().build_mapping([{"provider_id": "p1", "name": "Example News (SD)"}], [], self.channels())
        entry = result["entries"][0]
        self.assertEqual(entry["status"], "candidate_manual_review")
        self.assertIsNone(entry["qualified_id"])
        self.assertEqual({x["reason"] for x in entry["candidates"]}, {"resolution_variant"})

    def test_regional_and_language_differences_are_not_erased(self):
        catalog = [{"provider_id": "p1", "name": "Example North DE"}]
        channels = [{"qualified_id": "s1:a", "name": "Example South DE", "source": "s1"}, {"qualified_id": "s1:b", "name": "Example North FR", "source": "s1"}]
        result = self.api().build_mapping(catalog, [], channels)
        self.assertEqual(result["entries"][0]["status"], "unmapped")
        self.assertEqual(result["entries"][0]["candidates"], [])
        self.assertEqual(result["summary"]["coverage"], 0)

    def test_empty_catalog_has_no_coverage_denominator(self):
        result = self.api().build_mapping([], [], self.channels())
        self.assertEqual(result["entries"], [])
        self.assertIsNone(result["summary"]["coverage"])
        self.assertEqual(result["summary"]["total_provider_channels"], 0)

    def test_duplicate_provider_identity_is_rejected_without_private_value(self):
        with self.assertRaisesRegex(ValueError, "duplicate provider identity"):
            self.api().build_mapping([{"provider_id": "synthetic", "name": "One"}, {"provider_id": "synthetic", "name": "Two"}], [], [])

    def test_mapping_store_is_private_and_keeps_previous_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            first = {"entries": [], "summary": {"coverage": None}}
            second = {"entries": [], "summary": {"coverage": 0}}
            self.api().persist_mapping(first, path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.api().persist_mapping(second, path)
            self.assertEqual(json.loads(path.read_text()), second)
            self.assertEqual(json.loads(path.with_suffix(".json.bak").read_text()), first)
            self.assertEqual(stat.S_IMODE(path.with_suffix(".json.bak").stat().st_mode), 0o600)

    def test_mapping_store_refuses_symbolic_and_hard_links(self):
        with tempfile.TemporaryDirectory() as directory:
            actual = Path(directory) / "actual"
            actual.write_text("unchanged")
            for hard in [False, True]:
                target = Path(directory) / ("hard" if hard else "symbolic")
                os.link(actual, target) if hard else target.symlink_to(actual)
                with self.assertRaisesRegex(ValueError, "unsafe mapping store"):
                    self.api().persist_mapping({}, target)
                self.assertEqual(actual.read_text(), "unchanged")
                target.unlink()

    def test_mapping_store_refuses_unsafe_backup_and_preserves_old_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            path.write_text("old")
            backup = path.with_suffix(".json.bak")
            backup.symlink_to(path)
            with self.assertRaisesRegex(ValueError, "unsafe mapping store"):
                self.api().persist_mapping({}, path)
            self.assertEqual(path.read_text(), "old")
            backup.unlink()
            with patch("xcmate_epg.channel_mapping.os.replace", side_effect=OSError("synthetic failure")):
                with self.assertRaises(OSError):
                    self.api().persist_mapping({}, path)
            self.assertEqual(path.read_text(), "old")
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["mapping.json"])


if __name__ == "__main__":
    unittest.main()
