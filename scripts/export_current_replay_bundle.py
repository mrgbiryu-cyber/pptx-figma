#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

from build_intermediate_candidates import build_intermediate_model
from build_visual_first_replay_bundle import build_bundle_from_page
from pptx_inspector import extract_slide_details


def load_page_from_intermediate(intermediate_path: Path, slide_no: int) -> dict[str, Any]:
    payload = json.loads(intermediate_path.read_text(encoding="utf-8"))
    for page in payload.get("pages") or []:
        if int(page.get("slide_no") or 0) == slide_no:
            return page
    raise SystemExit(f"slide {slide_no} not found in {intermediate_path}")


def build_page_from_pptx(pptx_path: Path, slide_no: int) -> dict[str, Any]:
    detail_payload = extract_slide_details(pptx_path, [slide_no])
    intermediate = build_intermediate_model(detail_payload)
    pages = intermediate.get("pages") or []
    if not pages:
        raise SystemExit(f"slide {slide_no} not found in {pptx_path}")
    return pages[0]


def add_pdf_reference_background(bundle: dict[str, Any], reference_image_path: Path, slide_no: int) -> None:
    if not reference_image_path.exists():
        raise SystemExit(f"reference image not found: {reference_image_path}")
    doc = bundle.get("document") or {}
    bounds = doc.get("absoluteBoundingBox") or {}
    if not bounds:
        raise SystemExit("bundle document has no absoluteBoundingBox")

    asset_ref = f"pdf-reference-slide-{slide_no}"
    bundle.setdefault("assets", {})[asset_ref] = {
        "filename": reference_image_path.name,
        "mime_type": "image/png",
        "base64": base64.b64encode(reference_image_path.read_bytes()).decode("ascii"),
    }

    background_node = {
        "id": f"{doc.get('id', f'page:{slide_no}')}:pdf-reference-background",
        "type": "RECTANGLE",
        "name": "PDF reference background",
        "absoluteBoundingBox": dict(bounds),
        "relativeTransform": [[1, 0, 0], [0, 1, 0]],
        "fills": [{"type": "IMAGE", "imageRef": asset_ref, "scaleMode": "FILL"}],
        "strokes": [],
        "strokeWeight": 0,
        "debug": {
            "generator": "export_current_replay_bundle",
            "render_layer": "visual/reference",
            "render_intent": "pdf_reference_background",
            "stack_policy": "background",
            "stack_reason": "pdf_raster_is_product_visual_fidelity_baseline",
            "source_file": str(reference_image_path),
        },
    }

    visual_layer = next((child for child in doc.get("children") or [] if child.get("name") == "visual/reference"), None)
    if visual_layer is None:
        doc.setdefault("children", []).insert(
            0,
            {
                "id": f"{doc.get('id', f'page:{slide_no}')}:visual-reference",
                "type": "FRAME",
                "name": "visual/reference",
                "absoluteBoundingBox": dict(bounds),
                "relativeTransform": [[1, 0, 0], [0, 1, 0]],
                "fills": [],
                "strokes": [],
                "strokeWeight": 0,
                "children": [background_node],
                "debug": {"render_layer": "visual/reference", "render_intent": "visual_reference"},
            },
        )
    else:
        visual_layer.setdefault("children", []).insert(0, background_node)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export one current visual-first replay bundle for PDF/reference comparison."
    )
    parser.add_argument("--pptx", required=True, help="Source PPTX path used for semantic extraction.")
    parser.add_argument("--slide", type=int, required=True, help="One-based slide number to export.")
    parser.add_argument("--out", required=True, help="Output replay bundle JSON path.")
    parser.add_argument(
        "--intermediate",
        help="Optional prebuilt intermediate candidates JSON. When supplied, avoids re-extracting the PPTX.",
    )
    parser.add_argument(
        "--reference-pdf",
        help="Optional PDF reference path. The PDF is not parsed here; it is recorded for QA lineage.",
    )
    parser.add_argument(
        "--reference-image",
        help="Optional PNG rendering of the reference PDF page to embed as the visual/reference background.",
    )
    parser.add_argument(
        "--normalize-size",
        action="store_true",
        help="Normalize output to the canonical 960x540 replay size instead of preserving native slide size.",
    )
    args = parser.parse_args()

    pptx_path = Path(args.pptx).resolve()
    if not pptx_path.exists():
        raise SystemExit(f"PPTX not found: {pptx_path}")

    if args.intermediate:
        page = load_page_from_intermediate(Path(args.intermediate).resolve(), args.slide)
    else:
        page = build_page_from_pptx(pptx_path, args.slide)

    bundle = build_bundle_from_page(
        page,
        str(pptx_path),
        preserve_native_size=not args.normalize_size,
    )
    if args.reference_image:
        add_pdf_reference_background(bundle, Path(args.reference_image).resolve(), args.slide)
    bundle["source_kind"] = "pptx-semantic-with-pdf-reference"
    bundle["debug"] = dict(
        bundle.get("debug") or {},
        export_script="scripts/export_current_replay_bundle.py",
        source_pptx=str(pptx_path),
        reference_pdf=str(Path(args.reference_pdf).resolve()) if args.reference_pdf else None,
        source_of_truth="pdf_reference_for_visual_qa",
    )

    output_path = Path(args.out).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
