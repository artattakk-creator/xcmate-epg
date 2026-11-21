"""Never promote a real EPG unless all three sources, artwork and mapping pass."""
import unittest
from unittest.mock import patch

from tools.owner_full_live_once import audit, perform


def valid():
    return {
        "status": "PRIVATE_FULL_EPG_CREATED",
        "sources": [
            {"id": name, "successful": True, "exported_programmes": 100}
            for name in ("s1", "s2", "s3")
        ],
        "configured_sources": 3, "real_sources_successful": 3,
        "source_coverage_complete": True,
        "input_channels": 1200, "input_programmes": 190000,
        "valid_programmes": 189999,
        "full": {"channels": 1200, "programmes": 189999},
        "device": {"programmes": 5000},
        "mapping": {"catalog_complete": True, "mapped_channels_with_programmes": 200},
        "artwork": {"programmes_with_verified_artwork": 1000,
                    "verified_image_references": 3000, "logos_present": 50},
        "time_range": {"start": "2026-11-21T00:00:00+00:00",
                       "stop": "2026-11-28T00:00:00+00:00"},
    }


class FullLiveGateTests(unittest.TestCase):
    def test_real_complete_feed_is_auditable_without_source_urls(self):
        result = audit(valid())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["full_programmes"], 189999)
        self.assertEqual(result["verified_image_references"], 3000)
        self.assertNotIn("sources", result)

    def test_partial_artwork_and_missing_catalog_are_never_accepted(self):
        for field, section in (
            ("logos_present", "artwork"),
            ("programmes_with_verified_artwork", "artwork"),
            ("mapped_channels_with_programmes", "mapping"),
        ):
            with self.subTest(field=field):
                r = valid()
                r[section][field] = 0
                self.assertEqual(audit(r)["status"], "BLOCKED")
        r = valid()
        r["mapping"]["catalog_complete"] = False
        self.assertEqual(audit(r)["status"], "BLOCKED")
        r = valid()
        r["sources"][2]["successful"] = False
        self.assertEqual(audit(r)["status"], "BLOCKED")

    @patch("tools.owner_full_live_once.publish_delivery")
    @patch("tools.owner_full_live_once.run")
    def test_partial_cycle_never_publishes(self, mock_run, mock_publish):
        report = valid()
        report["status"] = "ARTWORK_BLOCKED"
        mock_run.return_value = {"cycle_status": "VALIDATED", "report": report}
        result = perform({}, {}, "/private", approval={"gates": {}})
        self.assertEqual(result["status"], "BLOCKED")
        mock_publish.assert_not_called()

    @patch("tools.owner_full_live_once.publish_delivery")
    @patch("tools.owner_full_live_once.run")
    def test_full_run_does_not_claim_remote_publish(self, mock_run, mock_publish):
        mock_run.return_value = {"cycle_status": "VALIDATED", "report": valid()}
        result = perform({}, {}, "/private")
        self.assertEqual(result["stage"], "REAL_FULL_EXPORT_CREATED")
        self.assertEqual(result["remote_status"], "NOT_PUBLISHED")
        mock_publish.assert_not_called()

    @patch("tools.owner_full_live_once.publish_delivery")
    @patch("tools.owner_full_live_once.run")
    def test_local_delivery_is_not_remote_live(self, mock_run, mock_publish):
        mock_run.return_value = {"cycle_status": "VALIDATED", "report": valid()}
        mock_publish.return_value = {
            "status": "PRIVATE_READY", "sha256": "0" * 64,
            "bytes": 1234, "channel_count": 1200, "programme_count": 189999
        }
        result = perform({}, {}, "/private", approval={"generation": "tested"})
        self.assertEqual(result["stage"], "LOCAL_DELIVERY_VERIFIED")
        self.assertEqual(result["remote_status"], "NOT_PUBLISHED")
        self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
