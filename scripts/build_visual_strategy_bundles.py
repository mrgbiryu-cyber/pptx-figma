#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import copy
import json
from pathlib import Path
from typing import Any

from build_visual_first_replay_bundle import build_bundle_from_page
from ppt_source_extractor import TARGET_SLIDE_HEIGHT, TARGET_SLIDE_WIDTH, load_intermediate_payload


SLIDE_TO_PAGE = {
    12: 1,
    19: 2,
    29: 3,
}


STRATEGY_C_BLOCKS = {
    12: [
        {"id": "header", "name": "Header Block", "x": 0.0, "y": 0.0, "width": 960.0, "height": 92.0},
        {"id": "body", "name": "Body Block", "x": 0.0, "y": 92.0, "width": 960.0, "height": 448.0},
    ],
    19: [
        {"id": "header", "name": "Header Block", "x": 0.0, "y": 0.0, "width": 960.0, "height": 96.0},
        {"id": "left-table", "name": "Left Table Block", "x": 32.0, "y": 78.0, "width": 410.0, "height": 462.0},
        {"id": "right-pane", "name": "Right Pane Block", "x": 442.0, "y": 78.0, "width": 518.0, "height": 462.0},
    ],
    29: [
        {"id": "header", "name": "Header Block", "x": 0.0, "y": 0.0, "width": 960.0, "height": 62.0},
        {"id": "body", "name": "Body Block", "x": 0.0, "y": 62.0, "width": 960.0, "height": 478.0},
    ],
}


def load_page_payload(input_path: str, slides: set[int] | None) -> list[dict[str, Any]]:
    payload = load_intermediate_payload(input_path)
    pages = payload.get("pages") or []
    if not slides:
        return pages
    return [page for page in pages if int(page.get("slide_no") or 0) in slides]


def convert_rect_to_vector(node: dict[str, Any]) -> dict[str, Any]:
    bounds = node.get("absoluteBoundingBox") or {"width": 1, "height": 1}
    width = float(bounds.get("width") or 1)
    height = float(bounds.get("height") or 1)
    path = f"M 0 0 H {round(width,2)} V {round(height,2)} H 0 Z"
    return {
        "id": f"{node['id']}:vectorized",
        "type": "VECTOR",
        "name": node.get("name") or "Vectorized Rectangle",
        "absoluteBoundingBox": bounds,
        "relativeTransform": node.get("relativeTransform"),
        "fillGeometry": [{"path": path, "windingRule": "NONZERO"}] if node.get("fills") else [],
        "strokeGeometry": [{"path": path}] if node.get("strokes") else [],
        "fills": node.get("fills") or [],
        "strokes": node.get("strokes") or [],
        "strokeWeight": node.get("strokeWeight") or 1,
        "children": [],
        "debug": dict(node.get("debug") or {}, strategy="vector-heavy"),
    }


def strategy_b_postprocess(node: dict[str, Any], *, is_root: bool = False) -> dict[str, Any]:
    updated = copy.deepcopy(node)
    children = [strategy_b_postprocess(child) for child in updated.get("children") or []]
    updated["children"] = children

    if updated.get("type") == "RECTANGLE" and not any(fill.get("type") == "IMAGE" for fill in updated.get("fills") or []):
        return convert_rect_to_vector(updated)

    if updated.get("type") == "FRAME" and not is_root:
        shell_children: list[dict[str, Any]] = []
        if updated.get("fills") or updated.get("strokes"):
            shell_children.append(convert_rect_to_vector(updated))
        shell_children.extend(children)
        return {
            "id": updated["id"],
            "type": "GROUP",
            "name": updated.get("name") or "Group",
            "absoluteBoundingBox": updated.get("absoluteBoundingBox"),
            "relativeTransform": updated.get("relativeTransform"),
            "children": shell_children,
            "debug": dict(updated.get("debug") or {}, strategy="vector-heavy"),
        }
    return updated


def read_png_asset(path: Path, image_ref: str) -> dict[str, Any]:
    return {
        "filename": path.name,
        "mime_type": "image/png",
        "base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "image_ref": image_ref,
    }


def build_strategy_c_bundle(slide_no: int, page_name: str, png_path: Path) -> dict[str, Any]:
    image_ref = f"reference-slide-{slide_no}"
    assets = {image_ref: read_png_asset(png_path, image_ref)}
    blocks = STRATEGY_C_BLOCKS[slide_no]
    children = []
    full_image_bounds = {
        "x": 0.0,
        "y": 0.0,
        "width": TARGET_SLIDE_WIDTH,
        "height": TARGET_SLIDE_HEIGHT,
    }
    for block in blocks:
        frame_bounds = {
            "x": block["x"],
            "y": block["y"],
            "width": block["width"],
            "height": block["height"],
        }
        children.append(
            {
                "id": f"s{slide_no}:strategy-c:{block['id']}",
                "type": "FRAME",
                "name": block["name"],
                "clipsContent": True,
                "absoluteBoundingBox": frame_bounds,
                "relativeTransform": [[1, 0, 0], [0, 1, 0]],
                "fills": [],
                "strokes": [],
                "strokeWeight": 0,
                "children": [
                    {
                        "id": f"s{slide_no}:strategy-c:{block['id']}:image",
                        "type": "RECTANGLE",
                        "name": f"{block['name']} Image",
                        "absoluteBoundingBox": full_image_bounds,
                        "relativeTransform": [[1, 0, 0], [0, 1, 0]],
                        "fills": [{
                            "type": "IMAGE",
                            "imageRef": image_ref,
                            "scaleMode": "FIT",
                        }],
                        "strokes": [],
                        "strokeWeight": 0,
                        "children": [],
                        "debug": {"strategy": "block-image-fallback"},
                    }
                ],
                "debug": {"strategy": "block-image-fallback"},
            }
        )

    document = {
        "id": f"strategy-c:{slide_no}",
        "type": "FRAME",
        "name": f"Strategy C - Slide {slide_no}",
        "absoluteBoundingBox": {
            "x": 0.0,
            "y": 0.0,
            "width": TARGET_SLIDE_WIDTH,
            "height": TARGET_SLIDE_HEIGHT,
        },
        "relativeTransform": [[1, 0, 0], [0, 1, 0]],
        "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [
            {
                "id": f"strategy-c:{slide_no}:frame",
                "type": "FRAME",
                "name": "Frame",
                "absoluteBoundingBox": {
                    "x": 0.0,
                    "y": 0.0,
                    "width": TARGET_SLIDE_WIDTH,
                    "height": TARGET_SLIDE_HEIGHT,
                },
                "relativeTransform": [[1, 0, 0], [0, 1, 0]],
                "fills": [],
                "strokes": [],
                "strokeWeight": 0,
                "children": children,
                "debug": {"strategy": "block-image-fallback"},
            }
        ],
        "debug": {"strategy": "block-image-fallback", "page_name": page_name},
    }
    return {
        "kind": "figma-replay-bundle",
        "source_kind": "strategy-c-block-image",
        "visual_model_version": "v1",
        "page_name": page_name,
        "node_id": document["id"],
        "document": document,
        "assets": assets,
        "missing_assets": [],
        "debug": {"strategy": "C", "slide_no": slide_no},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build A/B/C visual strategy bundles for slides 12/19/29.")
    parser.add_argument("--input", required=True, help="Intermediate candidates JSON path")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--slides", nargs="*", type=int, default=[12, 19, 29], help="Slide numbers")
    parser.add_argument("--reference-png-dir", default="scripts", help="Directory containing figma-page-1/2/3.png")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pages = load_page_payload(args.input, set(args.slides))
    ref_png_dir = Path(args.reference_png_dir).resolve()
    for page in pages:
        slide_no = int(page["slide_no"])
        page_name = f"Slide {slide_no} - {page.get('title_or_label') or page.get('title') or ''}".strip()

        # Strategy A: current hybrid visual-first
        bundle_a = build_bundle_from_page(page, str(Path(args.input).resolve()))
        bundle_a["page_name"] = f"Strategy A - {bundle_a['page_name']}"
        (output_dir / f"strategy-a-slide-{slide_no}.bundle.json").write_text(json.dumps(bundle_a, ensure_ascii=False, indent=2), encoding="utf-8")

        # Strategy B: vector-heavy postprocess
        bundle_b = copy.deepcopy(bundle_a)
        bundle_b["source_kind"] = "strategy-b-vector-heavy"
        bundle_b["page_name"] = bundle_b["page_name"].replace("Strategy A", "Strategy B")
        bundle_b["document"] = strategy_b_postprocess(bundle_b["document"], is_root=True)
        (output_dir / f"strategy-b-slide-{slide_no}.bundle.json").write_text(json.dumps(bundle_b, ensure_ascii=False, indent=2), encoding="utf-8")

        # Strategy C: block image fallback using reference PNGs
        page_idx = SLIDE_TO_PAGE.get(slide_no)
        png_path = ref_png_dir / f"figma-page-{page_idx}.png"
        if page_idx and png_path.exists():
            bundle_c = build_strategy_c_bundle(slide_no, page_name, png_path)
            (output_dir / f"strategy-c-slide-{slide_no}.bundle.json").write_text(json.dumps(bundle_c, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"saved strategy bundles for slide {slide_no}")


if __name__ == "__main__":
    main()
