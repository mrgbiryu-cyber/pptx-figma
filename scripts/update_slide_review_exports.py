#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from build_figma_page_manifest import build_manifest as build_figma_page_manifest
from diff_visual_replay_manifests import build_diff, load_json as load_diff_json


SLIDE_REFERENCE_MANIFEST = {
    29: "docs/figma-page-3.reference-manifest.json",
}


def copy_json(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh slide review exports from Figma-exported JSON files.")
    parser.add_argument("--slide", type=int, required=True, help="Slide number")
    parser.add_argument("--page-json", help="Figma page JSON export path")
    parser.add_argument("--selection-json", help="Figma selection JSON export path")
    parser.add_argument("--actual-manifest", help="Actual manifest export path")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    exports_dir = repo_root / "docs" / "review-exports" / f"slide-{args.slide}"
    exports_dir.mkdir(parents=True, exist_ok=True)

    if args.page_json:
        src = Path(args.page_json).resolve()
        dst = exports_dir / "figma-page.json"
        copy_json(src, dst)
        page_manifest = build_figma_page_manifest(dst)
        (exports_dir / "figma-page.manifest.json").write_text(
            json.dumps(page_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"saved {dst}")
        print(f"saved {exports_dir / 'figma-page.manifest.json'}")

    if args.selection_json:
        src = Path(args.selection_json).resolve()
        dst = exports_dir / "figma-selection.json"
        copy_json(src, dst)
        print(f"saved {dst}")

    if args.actual_manifest:
        src = Path(args.actual_manifest).resolve()
        dst = exports_dir / "actual-manifest.json"
        copy_json(src, dst)
        print(f"saved {dst}")

    actual_manifest_path = exports_dir / "actual-manifest.json"
    reference_rel = SLIDE_REFERENCE_MANIFEST.get(args.slide)
    if reference_rel and actual_manifest_path.exists():
        reference_manifest_path = repo_root / reference_rel
        reference_manifest = load_diff_json(reference_manifest_path)
        actual_manifest = load_diff_json(actual_manifest_path)
        diff = build_diff(reference_manifest, actual_manifest)
        diff_path = exports_dir / "actual-vs-reference.diff.json"
        diff_path.write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved {diff_path}")

    build_manifest_script = repo_root / "scripts" / "build_slide_review_manifest.py"
    output_path = repo_root / "docs" / "review-manifests" / f"slide-{args.slide}.review.json"
    import subprocess

    subprocess.run(
        ["python3", str(build_manifest_script), "--slide", str(args.slide), "--output", str(output_path)],
        check=True,
    )


if __name__ == "__main__":
    main()
