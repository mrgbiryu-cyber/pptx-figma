#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


TARGET_SLIDE_WIDTH = 960.0
TARGET_SLIDE_HEIGHT = 540.0


def load_intermediate_payload(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path).resolve()
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def identity_affine() -> list[list[float]]:
    return [[1, 0, 0], [0, 1, 0]]


def make_bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {
        "x": round(float(x), 2),
        "y": round(float(y), 2),
        "width": round(max(float(width), 1.0), 2),
        "height": round(max(float(height), 1.0), 2),
    }


def normalize_degrees(value: float | int | None) -> float:
    degrees = float(value or 0)
    while degrees > 180:
        degrees -= 360
    while degrees <= -180:
        degrees += 360
    return round(degrees, 2)


def relative_transform_from_bounds(bounds: dict[str, Any] | None) -> list[list[float]]:
    if not bounds:
        return identity_affine()
    rotation = normalize_degrees(bounds.get("rotation", 0))
    radians = math.radians(rotation)
    scale_x = -1.0 if bounds.get("flipH") else 1.0
    scale_y = -1.0 if bounds.get("flipV") else 1.0
    cos_v = math.cos(radians)
    sin_v = math.sin(radians)
    return [
        [round(cos_v * scale_x, 6), round(-sin_v * scale_y, 6), 0],
        [round(sin_v * scale_x, 6), round(cos_v * scale_y, 6), 0],
    ]


def build_page_scale(
    page: dict[str, Any],
    page_type: str | None = None,
    *,
    preserve_native_size: bool = False,
) -> tuple[float, float]:
    slide_size = page.get("slide_size") or {}
    width = float(slide_size.get("width_px") or TARGET_SLIDE_WIDTH)
    height = float(slide_size.get("height_px") or TARGET_SLIDE_HEIGHT)
    if preserve_native_size or page_type == "table-heavy":
        return 1.0, 1.0
    scale_x = TARGET_SLIDE_WIDTH / width if width else 1.0
    scale_y = TARGET_SLIDE_HEIGHT / height if height else 1.0
    return scale_x, scale_y


def scale_value(value: float | int | None, scale: float) -> float:
    return float(value or 0) * scale


def scale_bounds(bounds: dict[str, Any] | None, scale_x: float, scale_y: float) -> dict[str, float]:
    bounds = bounds or {}
    return make_bounds(
        scale_value(bounds.get("x"), scale_x),
        scale_value(bounds.get("y"), scale_y),
        scale_value(bounds.get("width", 120), scale_x),
        scale_value(bounds.get("height", 24), scale_y),
    )


def scale_point(point: dict[str, Any] | None, scale_x: float, scale_y: float) -> dict[str, float] | None:
    if not point:
        return None
    return {
        "x": round(scale_value(point.get("x"), scale_x), 2),
        "y": round(scale_value(point.get("y"), scale_y), 2),
    }


def sort_by_position_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    bounds = candidate.get("bounds_px") or {}
    source_scope = ((candidate.get("extra") or {}).get("source_scope") or "slide").lower()
    scope_rank = {"master": 0, "layout": 1, "slide": 2}.get(source_scope, 3)
    source_z_order = candidate_source_z_order(candidate)
    source_order_path = tuple(int(v) for v in (candidate.get("source_order_path") or []) if isinstance(v, int))
    return (
        scope_rank,
        source_z_order if source_z_order is not None else 999_999,
        source_order_path,
        float(bounds.get("y", 0)),
        float(bounds.get("x", 0)),
    )


def build_children_map(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_parent.setdefault(candidate.get("parent_candidate_id", ""), []).append(candidate)
    return by_parent


def classify_page_visual_strategy(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    table_text_cell_count = 0
    for candidate in candidates:
        subtype = str(candidate.get("subtype") or "unknown")
        counts[subtype] = counts.get(subtype, 0) + 1
        if subtype == "table_cell" and str(candidate.get("text") or "").strip():
            table_text_cell_count += 1

    table_cell_count = counts.get("table_cell", 0)
    table_count = counts.get("table", 0)
    connector_count = counts.get("connector", 0)
    labeled_shape_count = counts.get("labeled_shape", 0)
    shape_count = counts.get("shape", 0)
    image_count = counts.get("image", 0)
    group_count = counts.get("group", 0) + counts.get("section_block", 0)

    if image_count >= 5 or (labeled_shape_count >= 20 and shape_count >= 15 and table_cell_count < 40):
        page_type = "ui-mockup"
    elif table_cell_count >= 40 or table_count >= 2:
        page_type = "table-heavy"
    elif connector_count >= 10 and labeled_shape_count >= 10:
        page_type = "flow-process"
    elif image_count >= 3 or (labeled_shape_count >= 20 and shape_count >= 15):
        page_type = "ui-mockup"
    elif shape_count + labeled_shape_count + image_count >= 30 and connector_count <= 6:
        page_type = "ui-mockup"
    else:
        page_type = "generic"

    return {
        "page_type": page_type,
        "counts": counts,
        "signals": {
            "table_cell_count": table_cell_count,
            "table_text_cell_count": table_text_cell_count,
            "table_count": table_count,
            "connector_count": connector_count,
            "labeled_shape_count": labeled_shape_count,
            "shape_count": shape_count,
            "image_count": image_count,
            "group_count": group_count,
        },
    }


def placeholder_key(placeholder: dict[str, Any] | None) -> str:
    placeholder = placeholder or {}
    ph_type = str(placeholder.get("type") or "").strip().lower()
    ph_idx = str(placeholder.get("idx") or "").strip().lower()
    ph_sz = str(placeholder.get("sz") or "").strip().lower()
    return "|".join([ph_type, ph_idx, ph_sz])


def build_placeholder_anchor_map(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    anchors: dict[str, dict[str, Any]] = {}
    scope_rank = {"master": 0, "layout": 1, "slide": 2}
    for candidate in candidates:
        extra = candidate.get("extra") or {}
        placeholder = extra.get("placeholder") or {}
        key = placeholder_key(placeholder)
        bounds = candidate.get("bounds_px")
        scope = str(extra.get("source_scope") or "slide").lower()
        if not key or not bounds or scope not in {"master", "layout"}:
            continue
        existing = anchors.get(key)
        if existing is None or scope_rank[scope] < scope_rank[existing["scope"]]:
            anchors[key] = {
                "scope": scope,
                "candidate_id": candidate.get("candidate_id"),
                "bounds_px": bounds,
                "placeholder": placeholder,
            }
    return anchors


def build_page_context(page: dict[str, Any], *, preserve_native_size: bool = False) -> dict[str, Any]:
    candidates = page.get("candidates") or []
    visual_strategy = classify_page_visual_strategy(candidates)
    page_type = str(visual_strategy.get("page_type") or "generic")
    scale_x, scale_y = build_page_scale(page, page_type, preserve_native_size=preserve_native_size)
    slide_size = page.get("slide_size") or {}
    source_width = float(slide_size.get("width_px") or TARGET_SLIDE_WIDTH)
    source_height = float(slide_size.get("height_px") or TARGET_SLIDE_HEIGHT)
    width = source_width if (preserve_native_size or page_type == "table-heavy") else TARGET_SLIDE_WIDTH
    height = source_height if (preserve_native_size or page_type == "table-heavy") else TARGET_SLIDE_HEIGHT
    children_map = build_children_map(candidates)
    return {
        "page": page,
        "page_id": page.get("page_id") or f"page:{page.get('slide_no')}",
        "slide_no": page.get("slide_no"),
        "title": page.get("title_or_label") or f"Slide {page.get('slide_no')}",
        "scale_x": scale_x,
        "scale_y": scale_y,
        "width": width,
        "height": height,
        "candidates": candidates,
        "children_map": children_map,
        "roots": sorted(children_map.get(page.get("page_id"), []), key=sort_by_position_key),
        "placeholder_anchor_map": build_placeholder_anchor_map(candidates),
        "visual_strategy": visual_strategy,
        "preserve_native_size": preserve_native_size,
    }


def candidate_source_z_order(candidate: dict[str, Any]) -> int | None:
    raw_z_order = candidate.get("source_z_order")
    if raw_z_order is None:
        raw_z_order = (candidate.get("extra") or {}).get("source_z_order")
    if isinstance(raw_z_order, (int, float)):
        return int(raw_z_order)
    source_order_path = candidate.get("source_order_path") or []
    if source_order_path:
        return max(int(source_order_path[0]) - 1, 0)
    return None


def build_source_debug(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_path": candidate.get("source_path", ""),
        "source_order_path": candidate.get("source_order_path", []),
        "source_z_order": candidate_source_z_order(candidate),
        "source_node_id": candidate.get("source_node_id", ""),
        "source_subtype": candidate.get("subtype", ""),
        "source_scope": ((candidate.get("extra") or {}).get("source_scope") or "slide"),
    }


def iter_selected_pages(payload: dict[str, Any], slide_numbers: set[int] | None = None) -> list[dict[str, Any]]:
    pages = payload.get("pages") or []
    if not slide_numbers:
        return pages
    return [page for page in pages if int(page.get("slide_no") or 0) in slide_numbers]
