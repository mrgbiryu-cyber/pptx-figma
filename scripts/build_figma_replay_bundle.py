#!/usr/bin/env python3
import argparse
import base64
import json
from pathlib import Path


def collect_image_refs(node, refs):
    if not isinstance(node, dict):
        return
    for fill in node.get("fills") or []:
        if isinstance(fill, dict) and fill.get("type") == "IMAGE" and fill.get("imageRef"):
            refs.add(fill["imageRef"])
    for child in node.get("children") or []:
        collect_image_refs(child, refs)


def build_bundle(page_json_path: Path, assets_dir: Path):
    with page_json_path.open("r", encoding="utf-8") as handle:
        page_data = json.load(handle)

    nodes = page_data.get("nodes") or {}
    if not nodes:
      raise ValueError(f"{page_json_path.name} does not contain a nodes payload")

    node_id, entry = next(iter(nodes.items()))
    document = entry.get("document")
    if not document:
        raise ValueError(f"{page_json_path.name} does not contain a document node")

    image_refs = set()
    collect_image_refs(document, image_refs)

    assets = {}
    missing_assets = []
    for image_ref in sorted(image_refs):
        asset_path = assets_dir / f"{image_ref}.png"
        if not asset_path.exists():
            missing_assets.append(image_ref)
            continue
        assets[image_ref] = {
            "filename": asset_path.name,
            "mime_type": "image/png",
            "base64": base64.b64encode(asset_path.read_bytes()).decode("ascii"),
        }

    bundle = {
        "kind": "figma-replay-bundle",
        "source_file": page_json_path.name,
        "file_name": page_data.get("name"),
        "page_name": document.get("name"),
        "node_id": node_id,
        "last_modified": page_data.get("lastModified"),
        "version": page_data.get("version"),
        "document": document,
        "assets": assets,
        "missing_assets": missing_assets,
    }
    return bundle


def main():
    parser = argparse.ArgumentParser(description="Build a self-contained replay bundle from Figma page JSON + assets.")
    parser.add_argument("--base-dir", default=".", help="Project root containing figma-page-*.json and assets/")
    parser.add_argument("--pages", nargs="*", default=["figma-page-1.json", "figma-page-2.json", "figma-page-3.json"])
    parser.add_argument("--output-dir", default="docs", help="Output directory for bundle files")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    assets_dir = base_dir / "assets"
    output_dir = (base_dir / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    for page_name in args.pages:
        page_json_path = (base_dir / page_name).resolve()
        bundle = build_bundle(page_json_path, assets_dir)
        output_name = page_json_path.stem + ".bundle.json"
        output_path = output_dir / output_name
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, indent=2)
        print(f"saved {output_path}")


if __name__ == "__main__":
    main()
