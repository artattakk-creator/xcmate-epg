"""Run exactly one genuine owner-only full EPG cycle; never publish demo/partial results.

This CLI is intended for the restored, authorized private runner where all source
rights, TMDB credentials and the complete current provider mapping are present.
It deliberately does not create GitHub credentials, source rights or approvals.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from xcmate_epg.daily import run
from xcmate_epg.private_delivery import publish_delivery

REQUIRED_SOURCES = 3


def audit(report):
    """Return a redacted quality snapshot; no URLs, titles or credentials."""
    sources = report.get("sources", [])
    full = report.get("full", {})
    device = report.get("device", {})
    artwork = report.get("artwork", {})
    mapping = report.get("mapping", {})
    source_ok = (
        len(sources) == REQUIRED_SOURCES
        and report.get("configured_sources") == REQUIRED_SOURCES
        and report.get("real_sources_successful") == REQUIRED_SOURCES
        and report.get("source_coverage_complete") is True
        and all(s.get("successful") is True and s.get("exported_programmes", 0) > 0
                for s in sources)
    )
    complete = (
        source_ok and report.get("status") == "PRIVATE_FULL_EPG_CREATED"
        and full.get("channels", 0) > 0 and full.get("programmes", 0) > 0
        and device.get("programmes", 0) > 0
        and mapping.get("catalog_complete") is True
        and mapping.get("mapped_channels_with_programmes", 0) > 0
        and artwork.get("programmes_with_verified_artwork", 0) > 0
        and artwork.get("logos_present", 0) > 0
    )
    safe = {
        "stage": "REAL_FULL_QUALITY_AUDIT",
        "status": "PASS" if complete else "BLOCKED",
        "source_count": len(sources),
        "sources_ok": source_ok,
        "input_channels": report.get("input_channels", 0),
        "input_programmes": report.get("input_programmes", 0),
        "valid_programmes": report.get("valid_programmes", 0),
        "full_channels": full.get("channels", 0),
        "full_programmes": full.get("programmes", 0),
        "device_programmes": device.get("programmes", 0),
        "mapped_channels_with_programmes": mapping.get("mapped_channels_with_programmes", 0),
        "catalog_complete": mapping.get("catalog_complete") is True,
        "programmes_with_verified_artwork": artwork.get("programmes_with_verified_artwork", 0),
        "verified_image_references": artwork.get("verified_image_references", 0),
        "logos_present": artwork.get("logos_present", 0),
        "time_range": report.get("time_range"),
    }
    return safe


def perform(config, rights, state, *, approval=None, dtd_path=None):
    """Execute one authorized real cycle and gate even local delivery on its quality."""
    cycle = run(config, state, rights, authorize_network=True)
    if cycle.get("cycle_status") != "VALIDATED":
        return {"stage": "REAL_FULL_IMPORT", "status": "BLOCKED",
                "reason": str(cycle.get("cycle_status", "NOT_VALIDATED"))}
    quality = audit(cycle.get("report", {}))
    if quality["status"] != "PASS":
        return quality
    if approval is None:
        return dict(quality, stage="REAL_FULL_EXPORT_CREATED",
                    remote_status="NOT_PUBLISHED", local_delivery="NOT_REQUESTED")
    local = publish_delivery(state, approval=approval, dtd_path=dtd_path)
    if local.get("status") != "PRIVATE_READY":
        return dict(quality, stage="LOCAL_DELIVERY_GATE",
                    status="BLOCKED", local_delivery="PRIVATE_PENDING",
                    remote_status="NOT_PUBLISHED")
    return dict(quality, stage="LOCAL_DELIVERY_VERIFIED",
                status="PASS", local_delivery="PRIVATE_READY",
                remote_status="NOT_PUBLISHED",
                payload_sha256=local["sha256"],
                xmltv_bytes=local["bytes"],
                xmltv_channels=local["channel_count"],
                xmltv_programmes=local["programme_count"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--source-rights", type=Path, required=True)
    p.add_argument("--private-root", type=Path, required=True)
    p.add_argument("--delivery-approval", type=Path)
    p.add_argument("--dtd", type=Path)
    args = p.parse_args(argv)
    try:
        os.umask(0o077)
        config = json.loads(args.config.read_text(encoding="utf-8"))
        rights = json.loads(args.source_rights.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not isinstance(rights, dict):
            raise ValueError("CONFIG_INVALID")
        if args.delivery_approval and not args.dtd:
            raise ValueError("DTD_REQUIRED_FOR_DELIVERY")
        approval = (json.loads(args.delivery_approval.read_text(encoding="utf-8"))
                    if args.delivery_approval else None)
        status = perform(config, rights, args.private_root,
                         approval=approval, dtd_path=args.dtd)
        print(json.dumps(status, sort_keys=True, ensure_ascii=True))
        return 0 if status["status"] == "PASS" else 1
    except (ValueError, OSError, TypeError, KeyError):
        # Never echo third-party feed URLs, PATs or detailed exception messages.
        print(json.dumps({"stage": "PRIVATE_PREFLIGHT", "status": "BLOCKED",
                          "reason": "CONFIG_RIGHTS_OR_STORAGE_UNAVAILABLE"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
