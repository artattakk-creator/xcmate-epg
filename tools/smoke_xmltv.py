"""Offline-only proof of a tiny, synthetic XMLTV gzip publication.

This is NOT the production EPG aggregator: no source downloads, secrets or TMDB.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def demo_xmltv() -> bytes:
    root = ET.Element("tv", {"generator-info-name": "XCMate EPG Agent OFFLINE DEMO"})
    channel = ET.SubElement(root, "channel", {"id": "synthetic.demo.invalid"})
    ET.SubElement(channel, "display-name", {"lang": "de"}).text = "Demo Sender – keine echten Daten"
    programme = ET.SubElement(
        root,
        "programme",
        {
            "channel": "synthetic.demo.invalid",
            "start": "20300101000000 +0000",
            "stop": "20300101010000 +0000",
        },
    )
    ET.SubElement(programme, "title", {"lang": "de"}).text = "Synthetische Testsendung"
    ET.SubElement(programme, "desc", {"lang": "de"}).text = (
        "Nur für den kostenlosen GitHub-Actions-Funktionstest."
    )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def verify_xmltv(xml_bytes: bytes) -> None:
    root = ET.fromstring(xml_bytes)
    if root.tag != "tv":
        raise ValueError("Root element is not tv")
    channels = {node.attrib["id"] for node in root.findall("channel")}
    programmes = root.findall("programme")
    if not channels or len(programmes) != 1:
        raise ValueError("Invalid demo channel/programme count")
    for node in programmes:
        if node.attrib["channel"] not in channels:
            raise ValueError("Programme has no corresponding channel")
        if node.attrib["start"] >= node.attrib["stop"]:
            raise ValueError("Programme has invalid start/stop")


def generate(output_directory: Path) -> dict:
    data = demo_xmltv()
    verify_xmltv(data)
    compressed = gzip.compress(data, mtime=0)
    if gzip.decompress(compressed) != data:
        raise ValueError("gzip round-trip failed")
    output_directory.mkdir(parents=True, exist_ok=True)
    name = "xcmate-epg.DEMO-DO-NOT-PUBLISH.xml.gz"
    (output_directory / name).write_bytes(compressed)
    digest = hashlib.sha256(compressed).hexdigest()
    (output_directory / (name + ".sha256")).write_text(
        digest + "  " + name + "\n", encoding="utf-8"
    )
    manifest = {
        "status": "DEMO_ONLY_NOT_FOR_PUBLICATION",
        "xmltv_file": name,
        "sha256": digest,
        "sources_fetched": 0,
        "tmdb_calls": 0,
        "is_production_epg": False,
    }
    (output_directory / "manifest.demo.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = generate(args.output)
    print(
        "OFFLINE_DEMO_OK sources_fetched=0 tmdb_calls=0 "
        "sha256=" + result["sha256"]
    )


if __name__ == "__main__":
    main()
