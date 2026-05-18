#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SLIDE_CONFIG = {
    29: {
        "review_id": "slide-29-dense-panel-review",
        "title": "Slide 29 Dense Panel Review",
        "focus": [
            "right_panel_bounds",
            "description_body_overlay",
            "version_stack_alignment",
            "issue_card_overlay",
            "panel_small_assets_scope",
        ],
        "entry_bundle": "docs/block-bundles/block-slide-29-right-panel-axis-compare.bundle.json",
        "bundles": {
            "baseline_bundle": "docs/block-bundles/block-slide-29.bundle.json",
            "ir_panel_bundle": "docs/block-bundles/ir-dense-ui-panel-29.bundle.json",
            "hybrid_bundle": "docs/block-bundles/block-slide-29-full-style-hybrid.bundle.json",
            "hybrid_compare_bundle": "docs/block-bundles/block-slide-29-full-style-hybrid-compare.bundle.json",
            "axis_compare_bundle": "docs/block-bundles/block-slide-29-full-style-axis-compare.bundle.json",
            "panel_axis_compare_bundle": "docs/block-bundles/block-slide-29-right-panel-axis-compare.bundle.json",
            "group_spread_bundle": "docs/block-bundles/block-slide-29-group-spread.bundle.json",
        },
        "reports": {
            "chunk_report": "docs/dense-ui-panel-chunk-report.json",
            "composition_report": "docs/dense-ui-panel-composition-report.json",
            "resolved_ir": "docs/resolved-ppt-ir-12-19-29.json",
            "reference_manifest": "docs/figma-page-3.reference-manifest.json",
        },
        "exports_dir": "docs/review-exports/slide-29",
    }
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def rel(repo_root: Path, target: Path) -> str:
    return str(target.resolve().relative_to(repo_root.resolve())).replace("\\", "/")


def existing_file_info(repo_root: Path, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": rel(repo_root, path), "exists": False}
    return {
        "path": rel(repo_root, path),
        "exists": True,
        "size_bytes": path.stat().st_size,
    }


def build_manifest(repo_root: Path, slide_no: int) -> dict[str, Any]:
    config = SLIDE_CONFIG[slide_no]
    exports_dir = repo_root / config["exports_dir"]
    exports_dir.mkdir(parents=True, exist_ok=True)

    bundle_paths = {name: repo_root / rel_path for name, rel_path in config["bundles"].items()}
    report_paths = {name: repo_root / rel_path for name, rel_path in config["reports"].items()}
    entry_bundle_path = repo_root / config["entry_bundle"]
    entry_bundle = load_json(entry_bundle_path)

    export_files = {
        "figma_page_json": exports_dir / "figma-page.json",
        "figma_selection_json": exports_dir / "figma-selection.json",
        "actual_manifest": exports_dir / "actual-manifest.json",
        "figma_page_manifest": exports_dir / "figma-page.manifest.json",
        "actual_vs_reference_diff": exports_dir / "actual-vs-reference.diff.json",
    }

    manifest = {
        "kind": "slide-review-manifest",
        "review_manifest_version": "v1",
        "review_id": config["review_id"],
        "slide_no": slide_no,
        "title": config["title"],
        "focus": config["focus"],
        "entry_bundle_label": "panel_axis_compare_bundle",
        "entry_bundle_source": rel(repo_root, entry_bundle_path),
        "entry_bundle": entry_bundle,
        "bundles": {
            name: existing_file_info(repo_root, path)
            for name, path in bundle_paths.items()
        },
        "reports": {
            name: existing_file_info(repo_root, path)
            for name, path in report_paths.items()
        },
        "figma_exports": {
            name: existing_file_info(repo_root, path)
            for name, path in export_files.items()
        },
        "workflow": {
            "open_first": [
                "Load this review manifest in the Figma plugin to render the review canvas.",
                "Use '현재 페이지 JSON' or '선택 영역 JSON' to export qualitative evidence.",
                "Use 'Actual Manifest 내보내기' to export quantitative diff input.",
            ],
            "export_refresh_command": (
                "python3 scripts/update_slide_review_exports.py "
                f"--slide {slide_no} "
                "--page-json <figma-page.json> "
                "--selection-json <figma-selection.json> "
                "--actual-manifest <actual-manifest.json>"
            ),
            "fix_basis": {
                "qualitative": "figma_page_json / figma_selection_json",
                "quantitative": "actual_manifest + actual_vs_reference_diff",
            },
        },
    }
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a single-entry slide review manifest.")
    parser.add_argument("--slide", type=int, required=True, help="Slide number to build")
    parser.add_argument(
        "--output",
        help="Output manifest path (defaults to docs/review-manifests/slide-<n>.review.json)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    if args.slide not in SLIDE_CONFIG:
        raise SystemExit(f"slide {args.slide} is not configured")

    output_path = (
        Path(args.output).resolve()
        if args.output
        else repo_root / "docs" / "review-manifests" / f"slide-{args.slide}.review.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(repo_root, args.slide)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
