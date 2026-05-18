#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from build_figma_page_manifest import node_bounds, walk


def build_manifest(bundle_path: Path) -> dict:
    with bundle_path.open("r", encoding="utf-8") as handle:
        bundle = json.load(handle)
    document = bundle.get("document")
    if not document:
        raise ValueError(f"{bundle_path.name} does not contain document")
    page_id = document.get("id", bundle.get("node_id", bundle_path.stem))
    page_bounds = node_bounds(document)
    rows = []
    walk(document, page_id, page_bounds, rows)
    return {
        "kind": "page-manifest",
        "source_file": bundle_path.name,
        "page_id": page_id,
        "page_name": document.get("name", bundle.get("page_name", "")),
        "page_bounds": page_bounds,
        "bundle_debug": bundle.get("debug") or {},
        "document_debug": document.get("debug") or {},
        "nodes": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build manifests from replay bundle JSON files.")
    parser.add_argument("--input", nargs="+", required=True, help="Input replay bundle JSON paths")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    for input_name in args.input:
        bundle_path = Path(input_name).resolve()
        manifest = build_manifest(bundle_path)
        output_path = output_dir / f"{bundle_path.stem}.manifest.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        print(f"saved {output_path}")


if __name__ == "__main__":
    main()
