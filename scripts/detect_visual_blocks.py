#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ppt_source_extractor import (
    build_page_context,
    iter_selected_pages,
    load_intermediate_payload,
    make_bounds,
    placeholder_key,
    scale_bounds,
)


def resolve_candidate_bounds(candidate: dict[str, Any], context: dict[str, Any]) -> dict[str, float]:
    bounds = candidate.get("bounds_px")
    if bounds:
        return scale_bounds(bounds, context["scale_x"], context["scale_y"])
    extra = candidate.get("extra") or {}
    placeholder = extra.get("placeholder") or {}
    anchor = context.get("placeholder_anchor_map", {}).get(placeholder_key(placeholder))
    if anchor and anchor.get("bounds_px"):
        return scale_bounds(anchor["bounds_px"], context["scale_x"], context["scale_y"])
    return make_bounds(0, 0, 120, 24)


def union_bounds(bounds_list: list[dict[str, float]]) -> dict[str, float]:
    if not bounds_list:
        return make_bounds(0, 0, 1, 1)
    min_x = min(bounds["x"] for bounds in bounds_list)
    min_y = min(bounds["y"] for bounds in bounds_list)
    max_x = max(bounds["x"] + bounds["width"] for bounds in bounds_list)
    max_y = max(bounds["y"] + bounds["height"] for bounds in bounds_list)
    return make_bounds(min_x, min_y, max_x - min_x, max_y - min_y)


def render_mode_for_block(block_type: str, page_type: str) -> str:
    if block_type == "header_block":
        return "vector"
    if block_type == "top_meta_block":
        return "vector"
    if block_type == "aux_preview_block":
        return "image"
    if block_type == "flow_block":
        return "image"
    if block_type == "table_block":
        return "vector" if page_type == "table-heavy" else "image"
    if block_type == "right_panel_block":
        return "image"
    return "native"


def detect_block_type(candidate: dict[str, Any], context: dict[str, Any], bounds: dict[str, float]) -> str | None:
    extra = candidate.get("extra") or {}
    source_scope = str(extra.get("source_scope") or "slide").lower()
    subtype = str(candidate.get("subtype") or "")
    page_type = ((context.get("visual_strategy") or {}).get("page_type") or "generic")
    placeholder = extra.get("placeholder") or {}
    placeholder_type = str(placeholder.get("type") or "").lower()
    top_cutoff = 58 if page_type == "ui-mockup" else 72
    is_top_band = bounds["y"] <= top_cutoff
    is_compact_band = bounds["height"] <= (90 if page_type == "ui-mockup" else 110)
    center_x = bounds["x"] + bounds["width"] / 2
    right_edge = bounds["x"] + bounds["width"]

    if source_scope in {"layout", "master"} and is_top_band:
        return "header_block"

    if page_type == "ui-mockup":
        right_panel_x_cutoff = context["width"] * 0.72
        right_panel_center_cutoff = context["width"] * 0.78
        aux_preview_x_min = context["width"] * 0.38
        aux_preview_x_max = context["width"] * 0.82
        aux_preview_y_min = context["height"] * 0.72
        if (
            bounds["y"] <= 170
            and (right_edge >= context["width"] * 0.74 or center_x >= context["width"] * 0.7)
            and bounds["width"] <= context["width"] * 0.22
            and subtype in {"labeled_shape", "text_block", "shape"}
        ):
            return "top_meta_block"
        if (
            bounds["y"] >= aux_preview_y_min
            and bounds["x"] >= aux_preview_x_min
            and bounds["x"] < aux_preview_x_max
            and subtype in {"labeled_shape", "shape", "image", "text_block", "connector", "group", "section_block"}
        ):
            return "aux_preview_block"
        if (
            bounds["y"] >= 36
            and (
                bounds["x"] >= right_panel_x_cutoff
                or center_x >= right_panel_center_cutoff
            )
        ):
            return "right_panel_block"
        if subtype == "table" and (
            bounds["x"] >= right_panel_x_cutoff
            or center_x >= right_panel_center_cutoff
        ):
            return "right_panel_block"

    if placeholder_type == "title" or (is_top_band and is_compact_band):
        return "header_block"

    if subtype == "table":
        return "table_block"

    if page_type == "flow-process":
        if subtype in {"connector", "labeled_shape", "shape", "text_block", "group", "section_block"}:
            return "flow_block"

    if page_type == "ui-mockup":
        if (
            bounds["x"] >= context["width"] * 0.72
            or center_x >= context["width"] * 0.78
        ):
            return "right_panel_block"
        return "content_block"

    if page_type == "table-heavy":
        return "content_block"

    return "content_block"


def build_blocks_for_page(page: dict[str, Any]) -> dict[str, Any]:
    context = build_page_context(page)
    blocks: dict[str, dict[str, Any]] = {}

    for candidate in context["roots"]:
        bounds = resolve_candidate_bounds(candidate, context)
        block_type = detect_block_type(candidate, context, bounds)
        if not block_type:
            continue
        block = blocks.setdefault(
            block_type,
            {
                "block_id": f"{context['page_id']}:{block_type}",
                "block_type": block_type,
                "page_id": context["page_id"],
                "render_mode": render_mode_for_block(block_type, (context.get("visual_strategy") or {}).get("page_type") or "generic"),
                "bounds": None,
                "root_candidate_ids": [],
                "root_candidates": [],
                "page_type": (context.get("visual_strategy") or {}).get("page_type") or "generic",
            },
        )
        block["root_candidate_ids"].append(candidate["candidate_id"])
        block["root_candidates"].append(
            {
                "candidate_id": candidate["candidate_id"],
                "subtype": candidate.get("subtype"),
                "title": candidate.get("title"),
                "source_scope": (candidate.get("extra") or {}).get("source_scope") or "slide",
                "bounds": bounds,
            }
        )

    for block in blocks.values():
        block["bounds"] = union_bounds([row["bounds"] for row in block["root_candidates"]])

    preferred_order = {
        "header_block": 0,
        "top_meta_block": 1,
        "flow_block": 2,
        "table_block": 3,
        "right_panel_block": 4,
        "aux_preview_block": 5,
        "content_block": 6,
    }
    ordered = sorted(blocks.values(), key=lambda row: preferred_order.get(row["block_type"], 99))
    return {
        "page_id": context["page_id"],
        "slide_no": context["slide_no"],
        "title": context["title"],
        "page_type": (context.get("visual_strategy") or {}).get("page_type") or "generic",
        "blocks": ordered,
    }


def build_report(payload: dict[str, Any], slide_numbers: set[int] | None = None) -> dict[str, Any]:
    pages = []
    for page in iter_selected_pages(payload, slide_numbers):
        pages.append(build_blocks_for_page(page))
    return {
        "kind": "visual-block-detection-report",
        "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect visual blocks from PPT intermediate payload.")
    parser.add_argument("--input", required=True, help="Intermediate candidates JSON path")
    parser.add_argument("--output", required=True, help="Output JSON report path")
    parser.add_argument("--slides", nargs="*", type=int, help="Optional slide numbers")
    args = parser.parse_args()

    payload = load_intermediate_payload(args.input)
    report = build_report(payload, set(args.slides) if args.slides else None)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
