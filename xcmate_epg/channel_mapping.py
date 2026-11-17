"""Conservative private provider review; heuristic similarity never confirms a match.

This module intentionally does not log catalog values or private storage locations.
Lower numeric priorities win; ties use source-qualified identity for reproducibility.
"""
from collections import defaultdict
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import unicodedata


def _name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _without_resolution(value):
    # Only a separated trailing resolution marker is dispensable. Geographic,
    # language, punctuation and brand differences remain significant.
    return re.sub(r"\s+(?:\((?:sd|hd|fhd|uhd|4k|8k)\)|\[(?:sd|hd|fhd|uhd|4k|8k)\]|(?:sd|hd|fhd|uhd|4k|8k))$", "", value)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def build_mapping(catalog, confirmations, channels):
    """Build a deterministic private review report from explicit evidence.

    ``confirmed_sources`` contains evidence records, including locked records in
    conflict. Such records are retained for review, never selected automatically.
    Missing locked targets stay replayable in ``unresolved_confirmations`` and
    block selection until restored or explicitly resolved by the reviewer.
    Unknown confirmation references are counted without manufacturing providers.
    """
    providers = {}
    for row in catalog:
        if not _text(row.get("provider_id")) or not _text(row.get("name")):
            raise ValueError("invalid provider catalog record")
        if row["provider_id"] in providers:
            raise ValueError("duplicate provider identity")
        providers[row["provider_id"]] = row

    feeds = {}
    for row in channels:
        qid, source = row.get("qualified_id"), row.get("source")
        if not _text(qid) or not _text(source) or not qid.startswith(source + ":") or not qid[len(source) + 1:] or not _text(row.get("name")):
            raise ValueError("invalid qualified channel record")
        priority = row.get("priority", 100)
        if isinstance(priority, bool) or not isinstance(priority, (int, float)) or not math.isfinite(priority):
            raise ValueError("invalid channel priority")
        if qid in feeds:
            previous = feeds[qid]
            if previous["source"] != source or previous["priority"] != priority:
                raise ValueError("conflicting qualified channel identity")
            previous["names"].add(_name(row["name"]))
        else:
            feeds[qid] = {"source": source, "priority": priority, "names": {_name(row["name"])}}

    by_raw, by_name, by_base = defaultdict(set), defaultdict(set), defaultdict(set)
    for qid, feed in feeds.items():
        by_raw[qid.split(":", 1)[1]].add(qid)
        for name in feed["names"]:
            by_name[name].add(qid)
            by_base[_without_resolution(name)].add(qid)

    explicit, problems = defaultdict(dict), defaultdict(set)
    unresolved = defaultdict(list)
    invalid = 0
    for row in confirmations:
        pid, qid = row.get("provider_id"), row.get("qualified_id")
        if not isinstance(pid, str) or pid not in providers:
            invalid += 1
            continue
        if not isinstance(qid, str) or qid not in feeds:
            problems[pid].add("confirmation_target_missing")
            if row.get("locked") is True:
                problems[pid].add("locked_confirmation_target_missing")
                unresolved[pid].append({
                    "provider_id": pid,
                    "qualified_id": qid if isinstance(qid, str) else None,
                    "evidence": row.get("evidence") if isinstance(row.get("evidence"), str) else "",
                    "locked": True,
                })
            invalid += 1
            continue
        if not _text(row.get("evidence")) or not isinstance(row.get("locked", False), bool):
            problems[pid].add("confirmation_evidence_invalid")
            invalid += 1
            continue
        record = explicit[pid].setdefault(qid, {
            "qualified_id": qid, "source": feeds[qid]["source"],
            "priority": feeds[qid]["priority"], "evidence": set(), "locked": False,
        })
        record["evidence"].add(row["evidence"].strip())
        record["locked"] |= row.get("locked", False)

    entries = []
    for pid, provider in providers.items():
        matches = {}
        def candidate(qids, reason, confidence):
            for qid in qids:
                if qid not in matches or confidence > matches[qid]["confidence"]:
                    matches[qid] = {"qualified_id": qid, "reason": reason, "confidence": confidence}
        name = _name(provider["name"])
        candidate(by_base.get(_without_resolution(name), ()), "resolution_variant", 0.7)
        candidate(by_name.get(name, ()), "normalized_name", 0.85)
        if _text(provider.get("epg_id")):
            candidate(by_raw.get(provider["epg_id"], ()), "provider_epg_id", 0.95)
            if provider["epg_id"] in feeds:
                candidate([provider["epg_id"]], "provider_epg_id", 0.95)
        confirmed = [{**r, "evidence": sorted(r["evidence"])} for r in explicit[pid].values()]
        confirmed.sort(key=lambda r: (r["priority"], r["source"], r["qualified_id"]))
        unavailable = sorted(unresolved[pid], key=lambda r: (r["qualified_id"] or "", r["evidence"]))
        locked = [r for r in confirmed if r["locked"]]
        evidence = set(problems[pid])
        if len({r["qualified_id"] for r in locked + unavailable}) > 1:
            evidence.add("conflicting_locked_confirmations")
        # Distinct IDs inside one source are not source fallbacks. A single lock
        # can resolve that ambiguity; otherwise it remains reviewable.
        if not locked and len({r["source"] for r in confirmed}) < len(confirmed):
            evidence.add("conflicting_source_confirmations")
        conflicted = any(x.startswith("conflicting_") for x in evidence)
        selected = None if unavailable or conflicted or not confirmed else (locked or confirmed)[0]["qualified_id"]
        for record in confirmed:
            evidence.update(record["evidence"])
        for record in unavailable:
            if _text(record["evidence"]):
                evidence.add(record["evidence"])
        status = "confirmed" if selected else "candidate_manual_review" if matches or evidence or confirmed else "unmapped"
        entries.append({
            "provider_id": pid, "name": provider["name"], "status": status,
            "qualified_id": selected, "confirmed_sources": confirmed,
            "unresolved_confirmations": unavailable,
            "candidates": sorted(matches.values(), key=lambda r: (-r["confidence"], feeds[r["qualified_id"]]["priority"], r["qualified_id"])),
            "evidence": sorted(evidence),
        })
    total = len(entries)
    mapped = sum(e["status"] == "confirmed" for e in entries)
    return {"entries": entries, "summary": {
        "total_provider_channels": total, "mapped_provider_channels": mapped,
        "candidate_manual_review": sum(e["status"] == "candidate_manual_review" for e in entries),
        "unmapped": sum(e["status"] == "unmapped" for e in entries),
        "coverage": mapped / total if total else None,
        "invalid_confirmations": invalid,
    }}


def _safe_store(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe mapping store")
    return info


def _stage_private(directory, payload):
    fd, name = tempfile.mkstemp(prefix=".mapping-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        os.unlink(name)
        raise
    return Path(name)


def persist_mapping(report, path):
    """Atomically replace a 0600 JSON review, retaining the previous ``.bak``.

    Both target and backup must be regular, singly linked files. Serialization
    completes before touching either generation; temporary files are cleaned on
    failure. The existing target remains intact if staging or backup fails.
    """
    payload = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    target = Path(path)
    backup = target.with_name(target.name + ".bak")
    original = _safe_store(target)
    _safe_store(backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = previous = None
    try:
        staged = _stage_private(target.parent, payload)
        if original is not None:
            # O_NOFOLLOW plus fstat closes the ordinary lstat/open symlink race.
            fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                current = os.fstat(handle.fileno())
                if current.st_nlink != 1 or (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    raise ValueError("unsafe mapping store")
                previous = _stage_private(target.parent, handle.read())
            _safe_store(backup)
            _safe_store(target)
            os.replace(previous, backup)
            previous = None
        _safe_store(target)
        os.replace(staged, target)
        staged = None
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        for temporary in (staged, previous):
            if temporary is not None:
                temporary.unlink(missing_ok=True)
