#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from build_block_replay_bundle import build_bundle_from_page
from ppt_source_extractor import TARGET_SLIDE_HEIGHT, TARGET_SLIDE_WIDTH, identity_affine, iter_selected_pages, load_intermediate_payload


def shift_node(node: dict, prefix: str, dx: float, dy: float) -> dict:
    cloned = copy.deepcopy(node)
    cloned["id"] = f"{prefix}:{cloned['id']}"
    bounds = cloned.get("absoluteBoundingBox")
    if bounds:
        cloned["absoluteBoundingBox"] = {
            "x": round(float(bounds["x"]) + dx, 2),
            "y": round(float(bounds["y"]) + dy, 2),
            "width": float(bounds["width"]),
            "height": float(bounds["height"]),
        }
    children = cloned.get("children") or []
    if children:
        cloned["children"] = [shift_node(child, prefix, dx, dy) for child in children]
    return cloned


def make_label_node(node_id: str, label: str, x: float, y: float) -> dict:
    return {
        "id": node_id,
        "type": "TEXT",
        "name": label,
        "characters": label,
        "absoluteBoundingBox": {"x": x, "y": y, "width": 180.0, "height": 18.0},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 0.12, "g": 0.12, "b": 0.12}, "opacity": 1.0}],
        "style": {
            "fontSize": 14,
            "fontFamily": "Inter",
            "textAlignHorizontal": "LEFT",
            "textAlignVertical": "TOP",
            "textAutoResize": "HEIGHT",
            "lineHeightPx": None,
        },
        "children": [],
        "debug": {"role": "compare_label"},
    }


def build_compare_bundle(slide_no: int, bundles: list[tuple[str, dict]], source_file: str) -> dict:
    gap = 40.0
    top_pad = 28.0
    total_width = len(bundles) * TARGET_SLIDE_WIDTH + (len(bundles) - 1) * gap
    total_height = TARGET_SLIDE_HEIGHT + top_pad
    compare_children: list[dict] = []
    merged_assets: dict = {}

    for index, (label, bundle) in enumerate(bundles):
        dx = index * (TARGET_SLIDE_WIDTH + gap)
        dy = top_pad
        inner = bundle["document"]["children"][0]
        shifted = shift_node(inner, f"compare:{label}", dx, dy)
        shifted["name"] = label
        compare_children.append(make_label_node(f"compare:{label}:label", label, dx + 8.0, 6.0))
        compare_children.append(shifted)
        merged_assets.update(bundle.get("assets") or {})

    inner_frame = {
        "id": f"page:{slide_no}:compare:frame",
        "type": "FRAME",
        "name": "Frame",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": compare_children,
    }
    root = {
        "id": f"page:{slide_no}:compare",
        "type": "FRAME",
        "name": f"Slide {slide_no} Compare",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {"generator": "page29-variant-compare"},
    }
    return {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-block-prototype-compare",
        "visual_model_version": "block-v1-compare",
        "source_file": source_file,
        "file_name": Path(source_file).name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": merged_assets,
        "missing_assets": [],
        "debug": {"status": "page29_compare_bundle", "variants": [label for label, _ in bundles]},
    }


def find_right_panel_node(bundle: dict) -> dict | None:
    root = bundle.get("document") or {}
    inner = (root.get("children") or [None])[0]
    if not inner:
        return None
    for child in inner.get("children") or []:
        if child.get("name") == "right_panel_block":
            return child
    return None


def build_panel_compare_bundle(slide_no: int, bundles: list[tuple[str, dict]], source_file: str) -> dict:
    gap = 28.0
    outer_pad_x = 12.0
    outer_pad_y = 28.0
    panels: list[tuple[str, dict]] = []
    merged_assets: dict = {}

    for label, bundle in bundles:
        panel = find_right_panel_node(bundle)
        if not panel:
            continue
        panels.append((label, panel))
        merged_assets.update(bundle.get("assets") or {})

    if not panels:
        raise SystemExit("no right_panel_block found in variants")

    panel_width = max(float(panel["absoluteBoundingBox"]["width"]) for _, panel in panels)
    panel_height = max(float(panel["absoluteBoundingBox"]["height"]) for _, panel in panels)
    total_width = len(panels) * panel_width + (len(panels) - 1) * gap + outer_pad_x * 2
    total_height = panel_height + outer_pad_y + 12.0

    compare_children: list[dict] = []
    for index, (label, panel) in enumerate(panels):
        bounds = panel["absoluteBoundingBox"]
        dx = outer_pad_x + index * (panel_width + gap) - float(bounds["x"])
        dy = outer_pad_y - float(bounds["y"])
        shifted = shift_node(panel, f"panel-compare:{label}", dx, dy)
        shifted["name"] = label
        compare_children.append(make_label_node(f"panel-compare:{label}:label", label, outer_pad_x + index * (panel_width + gap) + 4.0, 6.0))
        compare_children.append(shifted)

    inner_frame = {
        "id": f"page:{slide_no}:panel-compare:frame",
        "type": "FRAME",
        "name": "Frame",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": compare_children,
    }
    root = {
        "id": f"page:{slide_no}:panel-compare",
        "type": "FRAME",
        "name": f"Slide {slide_no} Right Panel Compare",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {"generator": "page29-panel-compare"},
    }
    return {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-block-prototype-panel-compare",
        "visual_model_version": "block-v1-panel-compare",
        "source_file": source_file,
        "file_name": Path(source_file).name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": merged_assets,
        "missing_assets": [],
        "debug": {"status": "page29_panel_compare_bundle", "variants": [label for label, _ in panels]},
    }


def clone_panel_step(panel: dict, step: int) -> dict:
    cloned = copy.deepcopy(panel)
    children = cloned.get("children") or []

    if step == 1:
        filtered_children = []
        for child in children:
            name = str(child.get("name") or "")
            if child.get("type") == "GROUP" and name.startswith("description_lane_"):
                lane = copy.deepcopy(child)
                lane["children"] = [
                    c
                    for c in (lane.get("children") or [])
                    if c.get("type") in {"SVG_BLOCK", "TEXT", "GROUP"}
                ]
                filtered_children.append(lane)
            elif name == "right_panel_block:card_labels":
                filtered_children.append(child)
        cloned["children"] = filtered_children
        cloned["name"] = "29-step-1"
        return cloned

    if step == 2:
        filtered_children = []
        for child in children:
            name = str(child.get("name") or "")
            if (child.get("type") == "GROUP" and name.startswith("description_lane_")) or name == "right_panel_block:card_labels":
                filtered_children.append(child)
        cloned["children"] = filtered_children
        cloned["name"] = "29-step-2"
        return cloned

    cloned["name"] = "29-step-3"
    return cloned


def build_panel_steps_compare_bundle(slide_no: int, base_bundle: dict, source_file: str) -> dict:
    base_panel = find_right_panel_node(base_bundle)
    if not base_panel:
        raise SystemExit("no right_panel_block found in base bundle")

    panels = [
        ("step-1", clone_panel_step(base_panel, 1)),
        ("step-2", clone_panel_step(base_panel, 2)),
        ("step-3", clone_panel_step(base_panel, 3)),
    ]
    gap = 28.0
    outer_pad_x = 12.0
    outer_pad_y = 28.0
    panel_width = max(float(panel["absoluteBoundingBox"]["width"]) for _, panel in panels)
    panel_height = max(float(panel["absoluteBoundingBox"]["height"]) for _, panel in panels)
    total_width = len(panels) * panel_width + (len(panels) - 1) * gap + outer_pad_x * 2
    total_height = panel_height + outer_pad_y + 12.0

    compare_children: list[dict] = []
    for index, (label, panel) in enumerate(panels):
        bounds = panel["absoluteBoundingBox"]
        dx = outer_pad_x + index * (panel_width + gap) - float(bounds["x"])
        dy = outer_pad_y - float(bounds["y"])
        shifted = shift_node(panel, f"panel-steps:{label}", dx, dy)
        shifted["name"] = label
        compare_children.append(make_label_node(f"panel-steps:{label}:label", label, outer_pad_x + index * (panel_width + gap) + 4.0, 6.0))
        compare_children.append(shifted)

    inner_frame = {
        "id": f"page:{slide_no}:panel-steps:frame",
        "type": "FRAME",
        "name": "Frame",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": compare_children,
    }
    root = {
        "id": f"page:{slide_no}:panel-steps",
        "type": "FRAME",
        "name": f"Slide {slide_no} Right Panel Steps",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {"generator": "page29-panel-steps"},
    }
    return {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-block-prototype-panel-steps",
        "visual_model_version": "block-v1-panel-steps",
        "source_file": source_file,
        "file_name": Path(source_file).name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": base_bundle.get("assets") or {},
        "missing_assets": [],
        "debug": {"status": "page29_panel_steps_bundle", "variants": [label for label, _ in panels]},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build page 29 comparison variants for block replay bundle.")
    parser.add_argument(
        "--input",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "ppt-intermediate-candidates-12-19-29.json"),
        help="Intermediate candidates JSON path",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "block-bundles"),
        help="Output directory",
    )
    parser.add_argument("--slide", type=int, default=29, help="Slide number to export variants for")
    args = parser.parse_args()

    payload = load_intermediate_payload(args.input)
    selected = list(iter_selected_pages(payload, {args.slide}))
    if not selected:
        raise SystemExit(f"slide {args.slide} not found in {args.input}")
    page = selected[0]

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    variants = [
        ("29-1", "v1"),
        ("29-2", "v2"),
        ("29-3", "v3"),
    ]
    source_file = str(Path(args.input).resolve())
    built_variants: list[tuple[str, dict]] = []
    for label, variant in variants:
        bundle = build_bundle_from_page(page, source_file, variant)
        output_path = output_dir / f"block-slide-{args.slide}-{label.split('-')[-1]}.bundle.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, indent=2)
        print(f"saved {output_path}")
        built_variants.append((label, bundle))

    compare_bundle = build_compare_bundle(args.slide, built_variants, source_file)
    compare_output_path = output_dir / f"block-slide-{args.slide}-compare.bundle.json"
    with compare_output_path.open("w", encoding="utf-8") as handle:
        json.dump(compare_bundle, handle, ensure_ascii=False, indent=2)
    print(f"saved {compare_output_path}")

    panel_compare_bundle = build_panel_compare_bundle(args.slide, built_variants, source_file)
    panel_compare_output_path = output_dir / f"block-slide-{args.slide}-panel-compare.bundle.json"
    with panel_compare_output_path.open("w", encoding="utf-8") as handle:
        json.dump(panel_compare_bundle, handle, ensure_ascii=False, indent=2)
    print(f"saved {panel_compare_output_path}")

    base_bundle = next(bundle for label, bundle in built_variants if label == "29-1")
    panel_steps_bundle = build_panel_steps_compare_bundle(args.slide, base_bundle, source_file)
    panel_steps_output_path = output_dir / f"block-slide-{args.slide}-panel-steps.bundle.json"
    with panel_steps_output_path.open("w", encoding="utf-8") as handle:
        json.dump(panel_steps_bundle, handle, ensure_ascii=False, indent=2)
    print(f"saved {panel_steps_output_path}")


if __name__ == "__main__":
    main()
