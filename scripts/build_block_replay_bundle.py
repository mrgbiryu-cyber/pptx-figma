#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
import textwrap
from pathlib import Path
from typing import Any

from build_visual_first_replay_bundle import (
    build_connector_node,
    build_rectangle_node,
    build_shape_node,
    build_table_node,
    build_text_node,
    build_text_style,
    build_visual_node_from_candidate,
    should_skip_layout_placeholder_text,
    solid_paint,
)
from detect_visual_blocks import build_blocks_for_page
from ppt_source_extractor import (
    TARGET_SLIDE_HEIGHT,
    TARGET_SLIDE_WIDTH,
    build_page_context,
    identity_affine,
    iter_selected_pages,
    load_intermediate_payload,
    make_bounds,
    scale_point,
    scale_value,
    sort_by_position_key,
)
from visual_ownership import (
    build_text_owner_map,
    candidate_abs_bounds,
    filter_block_candidates,
    should_skip_candidate_inside_owner,
)

REFERENCE_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "docs" / "reference-visual-templates.json"
_REFERENCE_TEMPLATES_CACHE: dict[str, Any] | None = None
RIGHT_PANEL_VARIANTS = {"v1", "v2", "v3"}


def build_block_frame(block: dict[str, Any]) -> dict[str, Any]:
    bounds = block["bounds"]
    return {
        "id": block["block_id"],
        "type": "FRAME",
        "name": block["block_type"],
        "absoluteBoundingBox": bounds,
        "relativeTransform": identity_affine(),
        "fills": [],
        "strokes": [],
        "strokeWeight": 0,
        "children": [],
        "debug": {
            "generator": "block-prototype-v1",
            "block_type": block["block_type"],
            "render_mode": block["render_mode"],
            "page_type": block["page_type"],
            "root_candidate_ids": block["root_candidate_ids"],
        },
    }


def collect_block_candidates(block: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {candidate["candidate_id"]: candidate for candidate in context["candidates"]}
    children_map = context["children_map"]
    queue = list(block["root_candidate_ids"])
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    while queue:
        candidate_id = queue.pop(0)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        candidate = by_id.get(candidate_id)
        if not candidate:
            continue
        ordered.append(candidate)
        for child in sorted(children_map.get(candidate_id, []), key=sort_by_position_key):
            queue.append(child["candidate_id"])
    return ordered


def collect_candidates_in_block_bounds(
    block: dict[str, Any],
    context: dict[str, Any],
    *,
    min_overlap: float = 0.35,
) -> list[dict[str, Any]]:
    block_bounds = block["bounds"]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in sorted(context["candidates"], key=sort_by_position_key):
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        abs_bounds = candidate_abs_bounds(candidate, context)
        if not abs_bounds:
            continue
        overlap = bounds_overlap_ratio(abs_bounds, block_bounds)
        center_x = float(abs_bounds["x"]) + float(abs_bounds["width"]) / 2
        center_y = float(abs_bounds["y"]) + float(abs_bounds["height"]) / 2
        center_inside = (
            float(block_bounds["x"]) <= center_x <= float(block_bounds["x"]) + float(block_bounds["width"])
            and float(block_bounds["y"]) <= center_y <= float(block_bounds["y"]) + float(block_bounds["height"])
        )
        if overlap >= min_overlap or center_inside:
            selected.append(candidate)
            seen.add(candidate_id)
    return selected


def build_block_group_node(block: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "id": block["block_id"],
        "type": "GROUP",
        "name": block["block_type"],
        "absoluteBoundingBox": block["bounds"],
        "relativeTransform": identity_affine(),
        "children": [],
        "debug": {
            "generator": "block-prototype-v1",
            "block_type": block["block_type"],
            "render_mode": block["render_mode"],
            "page_type": block["page_type"],
            "role": role,
            "root_candidate_ids": block["root_candidate_ids"],
        },
    }


def style_color_to_svg(style_color: dict[str, Any] | None, fallback: str) -> tuple[str, float]:
    style_color = style_color or {}
    resolved_hex = style_color.get("resolved_value") or style_color.get("value")
    if isinstance(resolved_hex, str) and len(resolved_hex) == 6:
        return f"#{resolved_hex}", float(style_color.get("alpha") if style_color.get("alpha") is not None else 1.0)
    return fallback, float(style_color.get("alpha") if style_color.get("alpha") is not None else 1.0)


def local_bounds(bounds: dict[str, Any], origin: dict[str, Any]) -> dict[str, float]:
    return {
        "x": round(float(bounds["x"]) - float(origin["x"]), 2),
        "y": round(float(bounds["y"]) - float(origin["y"]), 2),
        "width": round(float(bounds["width"]), 2),
        "height": round(float(bounds["height"]), 2),
    }


def intersect_bounds(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    x1 = max(float(a["x"]), float(b["x"]))
    y1 = max(float(a["y"]), float(b["y"]))
    x2 = min(float(a["x"]) + float(a["width"]), float(b["x"]) + float(b["width"]))
    y2 = min(float(a["y"]) + float(a["height"]), float(b["y"]) + float(b["height"]))
    if x2 <= x1 or y2 <= y1:
        return make_bounds(x1, y1, 1.0, 1.0)
    return make_bounds(x1, y1, x2 - x1, y2 - y1)


def union_bounds(bounds_list: list[dict[str, Any]]) -> dict[str, float]:
    if not bounds_list:
        return make_bounds(0.0, 0.0, 1.0, 1.0)
    min_x = min(float(bounds["x"]) for bounds in bounds_list)
    min_y = min(float(bounds["y"]) for bounds in bounds_list)
    max_x = max(float(bounds["x"]) + float(bounds["width"]) for bounds in bounds_list)
    max_y = max(float(bounds["y"]) + float(bounds["height"]) for bounds in bounds_list)
    return make_bounds(min_x, min_y, max_x - min_x, max_y - min_y)


def source_bounds_for_block(block: dict[str, Any]) -> dict[str, float]:
    return dict(block.get("source_bounds") or block["bounds"])


def block_scale(block: dict[str, Any]) -> tuple[float, float]:
    source = source_bounds_for_block(block)
    target = block["bounds"]
    sx = float(target["width"]) / max(float(source["width"]), 1.0)
    sy = float(target["height"]) / max(float(source["height"]), 1.0)
    return sx, sy


def block_coordinate_mode(block: dict[str, Any]) -> str:
    return str(block.get("coordinate_mode") or "normalized")


def local_bounds_in_block(bounds: dict[str, Any], block: dict[str, Any]) -> dict[str, float]:
    if block_coordinate_mode(block) == "viewport_clip":
        target = block["bounds"]
        return {
            "x": round(float(bounds["x"]) - float(target["x"]), 2),
            "y": round(float(bounds["y"]) - float(target["y"]), 2),
            "width": round(float(bounds["width"]), 2),
            "height": round(float(bounds["height"]), 2),
        }
    source = source_bounds_for_block(block)
    sx, sy = block_scale(block)
    return {
        "x": round((float(bounds["x"]) - float(source["x"])) * sx, 2),
        "y": round((float(bounds["y"]) - float(source["y"])) * sy, 2),
        "width": round(float(bounds["width"]) * sx, 2),
        "height": round(float(bounds["height"]) * sy, 2),
    }


def transform_point_into_block(point: dict[str, Any], block: dict[str, Any]) -> dict[str, float]:
    if block_coordinate_mode(block) == "viewport_clip":
        target = block["bounds"]
        return {
            "x": round(float(point["x"]) - float(target["x"]), 2),
            "y": round(float(point["y"]) - float(target["y"]), 2),
        }
    source = source_bounds_for_block(block)
    sx, sy = block_scale(block)
    return {
        "x": round((float(point["x"]) - float(source["x"])) * sx, 2),
        "y": round((float(point["y"]) - float(source["y"])) * sy, 2),
    }


def svg_escape(text: str) -> str:
    return html.escape(str(text or ""), quote=False)


def wrap_text_lines(text: str, max_chars: int) -> list[str]:
    raw_lines = str(text or "").splitlines() or [str(text or "")]
    output: list[str] = []
    for line in raw_lines:
        if len(line) <= max_chars:
            output.append(line)
            continue
        output.extend(textwrap.wrap(line, width=max_chars, break_long_words=False, break_on_hyphens=False) or [line])
    return output


def load_reference_templates() -> dict[str, Any]:
    global _REFERENCE_TEMPLATES_CACHE
    if _REFERENCE_TEMPLATES_CACHE is not None:
        return _REFERENCE_TEMPLATES_CACHE
    if not REFERENCE_TEMPLATE_PATH.exists():
        _REFERENCE_TEMPLATES_CACHE = {"connector_route_templates": {}, "pages": []}
        return _REFERENCE_TEMPLATES_CACHE
    with REFERENCE_TEMPLATE_PATH.open("r", encoding="utf-8") as handle:
        _REFERENCE_TEMPLATES_CACHE = json.load(handle)
    return _REFERENCE_TEMPLATES_CACHE


def connector_route_preferences(page_type: str) -> dict[str, int]:
    templates = load_reference_templates()
    bucket = (templates.get("connector_route_templates") or {}).get(page_type) or {}
    return dict(bucket.get("route_signatures") or {})


def connector_case_key(kind: str, start_dir: str, end_dir: str) -> str:
    return f"{(kind or '').lower()}|{start_dir or '-'}|{end_dir or '-'}"


def connector_route_case_preferences(page_type: str, kind: str, start_dir: str, end_dir: str) -> dict[str, int]:
    templates = load_reference_templates()
    page_bucket = (templates.get("connector_route_cases") or {}).get(page_type) or {}
    case = page_bucket.get(connector_case_key(kind, start_dir, end_dir)) or {}
    return dict(case.get("route_signatures") or {})


def page_template(page_type: str) -> dict[str, Any]:
    templates = load_reference_templates()
    for page in templates.get("pages") or []:
        if page.get("page_type") == page_type:
            return page
    return {}


def block_template(page_type: str, block_type: str) -> dict[str, Any]:
    return dict((page_template(page_type) or {}).get(block_type) or {})


def block_text_policy(page_type: str, block_type: str) -> dict[str, Any]:
    template = block_template(page_type, block_type)
    avg_font = template.get("font_size_avg")
    min_font = template.get("font_size_min")
    max_font = template.get("font_size_max")
    if block_type == "header_block":
        return {
            "font_avg": avg_font or 8.0,
            "font_min": min_font or 7.0,
            "font_max": max_font or 11.0,
            "padding": {"l": 2.0, "r": 2.0, "t": 1.5, "b": 1.5},
        }
    if block_type == "top_meta_block":
        return {
            "font_avg": avg_font or 8.0,
            "font_min": min_font or 7.0,
            "font_max": max_font or 11.0,
            "padding": {"l": 2.0, "r": 2.0, "t": 1.5, "b": 1.5},
        }
    if block_type == "table_block":
        return {
            "font_avg": avg_font or 7.7,
            "font_min": min_font or 7.2,
            "font_max": max_font or 8.3,
            "padding": {"l": 5.0, "r": 5.0, "t": 2.5, "b": 2.5},
        }
    if block_type == "right_panel_block":
        return {
            "font_avg": avg_font or 7.2,
            "font_min": min_font or 6.5,
            "font_max": max_font or 9.0,
            "padding": {"l": 6.0, "r": 6.0, "t": 3.0, "b": 3.0},
        }
    return {
        "font_avg": avg_font or 8.0,
        "font_min": min_font or 7.0,
        "font_max": max_font or 10.0,
        "padding": {"l": 4.0, "r": 4.0, "t": 2.0, "b": 2.0},
    }


def clamp_font_size(value: float, policy: dict[str, Any]) -> float:
    return round(min(max(value, float(policy["font_min"])), float(policy["font_max"])), 2)


def resolve_block_font_size(
    candidate: dict[str, Any],
    style: dict[str, Any],
    policy: dict[str, Any],
    *,
    block_type: str,
) -> float:
    text_style = (candidate.get("extra") or {}).get("text_style") or {}
    placeholder = (candidate.get("extra") or {}).get("placeholder") or {}
    placeholder_type = str(placeholder.get("type") or "").lower()
    explicit = text_style.get("font_size_max") or text_style.get("font_size_avg")
    style_font = float(style.get("fontSize") or policy["font_avg"])
    if placeholder_type == "title":
        return round(style_font, 2)
    if explicit:
        explicit_value = float(explicit)
        if block_type == "header_block":
            return round(max(explicit_value, float(policy["font_min"])), 2)
        if block_type in {"table_block", "right_panel_block"}:
            return round(max(explicit_value, float(policy["font_min"])), 2)
        return round(explicit_value, 2)
    return clamp_font_size(style_font, policy)


def template_bounds_for_page(page_type: str, block_type: str) -> dict[str, float] | None:
    template = block_template(page_type, block_type)
    ratios = template.get("bounds_ratio") or {}
    if not ratios:
        return None
    return {
        "x": round(TARGET_SLIDE_WIDTH * float(ratios.get("x_ratio", 0.0)), 2),
        "y": round(TARGET_SLIDE_HEIGHT * float(ratios.get("y_ratio", 0.0)), 2),
        "width": round(TARGET_SLIDE_WIDTH * float(ratios.get("width_ratio", 0.0)), 2),
        "height": round(TARGET_SLIDE_HEIGHT * float(ratios.get("height_ratio", 0.0)), 2),
    }


def blend_bounds(actual: dict[str, float], expected: dict[str, float], weight: float) -> dict[str, float]:
    keep = 1.0 - weight
    return {
        "x": round(actual["x"] * keep + expected["x"] * weight, 2),
        "y": round(actual["y"] * keep + expected["y"] * weight, 2),
        "width": round(actual["width"] * keep + expected["width"] * weight, 2),
        "height": round(actual["height"] * keep + expected["height"] * weight, 2),
    }


def normalize_block_bounds(block: dict[str, Any]) -> dict[str, Any]:
    expected = template_bounds_for_page(block["page_type"], block["block_type"])
    if not expected:
        return block
    weight = {
        "header_block": 0.45,
        "table_block": 0.55,
        "right_panel_block": 0.7,
    }.get(block["block_type"], 0.0)
    if weight <= 0:
        return block
    normalized = dict(block)
    normalized["source_bounds"] = dict(block["bounds"])
    normalized["bounds"] = blend_bounds(block["bounds"], expected, weight)
    return normalized


def direction_from_idx(value: Any) -> str:
    mapping = {0: "up", 1: "left", 2: "down", 3: "right", 4: "left", 5: "right", 6: "left", 7: "right"}
    try:
        return mapping.get(int(value), "")
    except Exception:
        return ""


def axis_from_direction(direction: str) -> str:
    if direction in {"left", "right"}:
        return "H"
    if direction in {"up", "down"}:
        return "V"
    return ""


def opposite_direction(direction: str) -> str:
    return {
        "up": "down",
        "down": "up",
        "left": "right",
        "right": "left",
    }.get(direction, "")


def offset_point(point: dict[str, float], direction: str, amount: float) -> dict[str, float]:
    if direction == "up":
        return {"x": point["x"], "y": point["y"] - amount}
    if direction == "down":
        return {"x": point["x"], "y": point["y"] + amount}
    if direction == "left":
        return {"x": point["x"] - amount, "y": point["y"]}
    if direction == "right":
        return {"x": point["x"] + amount, "y": point["y"]}
    return {"x": point["x"], "y": point["y"]}


def clamp_ratio_from_adjust(value: Any, fallback: float = 0.5) -> float:
    try:
        ratio = float(value) / 100000.0
    except Exception:
        return fallback
    if math.isnan(ratio) or math.isinf(ratio):
        return fallback
    return min(max(ratio, -0.35), 1.35)


def direction_from_connection(point: dict[str, float] | None, bounds: dict[str, Any] | None, idx: Any) -> str:
    fallback = direction_from_idx(idx)
    if not point or not bounds:
        return fallback
    x = float(point["x"])
    y = float(point["y"])
    left = float(bounds["x"])
    top = float(bounds["y"])
    right = left + float(bounds["width"])
    bottom = top + float(bounds["height"])
    distances = {
        "left": abs(x - left),
        "right": abs(x - right),
        "up": abs(y - top),
        "down": abs(y - bottom),
    }
    ranked = sorted(distances.items(), key=lambda item: item[1])
    direction, distance = ranked[0]
    if distance <= 1.5:
        return direction
    return fallback


def compatible_start_direction(direction: str, dx: float, dy: float) -> str:
    if direction == "right" and dx <= 0:
        return ""
    if direction == "left" and dx >= 0:
        return ""
    if direction == "down" and dy <= 0:
        return ""
    if direction == "up" and dy >= 0:
        return ""
    return direction


def compatible_end_direction(direction: str, dx: float, dy: float) -> str:
    if direction == "right" and dx >= 0:
        return direction
    if direction == "left" and dx <= 0:
        return direction
    if direction == "down" and dy >= 0:
        return direction
    if direction == "up" and dy <= 0:
        return direction
    return ""


def effective_connector_directions(kind: str, start_dir: str, end_dir: str, dx: float, dy: float) -> tuple[str, str]:
    effective_start = compatible_start_direction(start_dir, dx, dy)
    effective_end = compatible_end_direction(end_dir, dx, dy)
    kind_name = str(kind or "").lower()
    if kind_name.startswith("bentconnector"):
        if not effective_start and start_dir:
            effective_start = start_dir
        if not effective_end and end_dir:
            effective_end = end_dir
    return effective_start, effective_end


def route_candidates_for_kind(kind: str, dx: float, dy: float) -> list[str]:
    kind_name = str(kind or "").lower()
    if kind_name in {"straightconnector1", "line", "connector"}:
        return ["H" if abs(dx) >= abs(dy) else "V"]
    if kind_name == "bentconnector2":
        return ["HV", "VH", "H", "V", "HVHV", "VHVH"]
    if kind_name in {"bentconnector3", "bentconnector4"}:
        return ["HVHV", "VHVH", "HVH", "VHV", "HV", "VH"]
    return ["HVHV", "HVH", "VHV", "HV", "VH", "H", "V"]


def choose_route_signature(kind: str, page_type: str, start_dir: str, end_dir: str, dx: float, dy: float) -> str:
    start_axis = axis_from_direction(start_dir)
    end_axis = axis_from_direction(end_dir)
    pool = route_candidates_for_kind(kind, dx, dy)
    case_prefs = connector_route_case_preferences(page_type, kind, start_dir, end_dir)
    if case_prefs:
        ranked = sorted(case_prefs.items(), key=lambda item: item[1], reverse=True)
        for signature, _ in ranked:
            if signature in pool:
                return signature
    prefs = connector_route_preferences(page_type)
    if prefs:
        best_signature = ""
        best_score = -10**9
        for signature in pool:
            score = prefs.get(signature, 0) * 10
            if end_axis and signature[-1] == end_axis:
                score += 6
            if start_axis and signature[0] == start_axis:
                score += 3
            if len(signature) >= 3 and abs(dx) > 24 and abs(dy) > 18:
                score += 2
            if len(signature) == 1 and abs(dx) > 24 and abs(dy) > 18:
                score -= 8
            if score > best_score:
                best_score = score
                best_signature = signature
        if best_signature:
            return best_signature
    for signature in pool:
        if end_axis and signature[-1] == end_axis:
            return signature
    for signature in pool:
        if start_axis and signature[0] == start_axis:
            return signature
    if pool:
        return pool[0]
    if abs(dx) >= abs(dy):
        return "HVH" if start_axis == "H" and end_axis == "H" else "H"
    return "VHV" if start_axis == "V" and end_axis == "V" else "V"


def unique_points(points: list[dict[str, float]]) -> list[dict[str, float]]:
    output: list[dict[str, float]] = []
    for point in points:
        if not output:
            output.append(point)
            continue
        previous = output[-1]
        if math.isclose(previous["x"], point["x"], abs_tol=0.01) and math.isclose(previous["y"], point["y"], abs_tol=0.01):
            continue
        output.append(point)
    return output


def same_axis(a: dict[str, float], b: dict[str, float], c: dict[str, float]) -> bool:
    return (
        math.isclose(a["x"], b["x"], abs_tol=0.01) and math.isclose(b["x"], c["x"], abs_tol=0.01)
    ) or (
        math.isclose(a["y"], b["y"], abs_tol=0.01) and math.isclose(b["y"], c["y"], abs_tol=0.01)
    )


def simplifies_middle(a: dict[str, float], b: dict[str, float], c: dict[str, float]) -> bool:
    if math.isclose(a["x"], b["x"], abs_tol=0.01) and math.isclose(b["x"], c["x"], abs_tol=0.01):
        return min(a["y"], c["y"]) - 0.01 <= b["y"] <= max(a["y"], c["y"]) + 0.01
    if math.isclose(a["y"], b["y"], abs_tol=0.01) and math.isclose(b["y"], c["y"], abs_tol=0.01):
        return min(a["x"], c["x"]) - 0.01 <= b["x"] <= max(a["x"], c["x"]) + 0.01
    return False


def simplify_orthogonal_points(points: list[dict[str, float]]) -> list[dict[str, float]]:
    simplified = unique_points(points)
    changed = True
    while changed and len(simplified) >= 3:
        changed = False
        output: list[dict[str, float]] = [simplified[0]]
        for idx in range(1, len(simplified) - 1):
            left = output[-1]
            current = simplified[idx]
            right = simplified[idx + 1]
            if same_axis(left, current, right) and simplifies_middle(left, current, right):
                changed = True
                continue
            output.append(current)
        output.append(simplified[-1])
        simplified = unique_points(output)
    return simplified


def maybe_shorten_signature(
    signature: str,
    dx: float,
    dy: float,
    start_dir: str,
    end_dir: str,
) -> str:
    start_axis = axis_from_direction(start_dir)
    end_axis = axis_from_direction(end_dir)
    if signature in {"HVHV", "HVH"} and abs(dy) <= 24:
        return "H"
    if signature in {"VHVH", "VHV"} and abs(dx) <= 24:
        return "V"
    if signature in {"HVHV", "HVH"} and abs(dx) > max(abs(dy) * 1.8, 72):
        return "H"
    if signature in {"VHVH", "VHV"} and abs(dy) > max(abs(dx) * 1.8, 72):
        return "V"
    if signature in {"HVHV", "VHVH"}:
        if abs(dx) < 60 or abs(dy) < 60:
            if start_axis == "H" and end_axis == "H":
                return "HVH"
            if start_axis == "V" and end_axis == "V":
                return "VHV"
            if start_axis == "H":
                return "HV"
            if start_axis == "V":
                return "VH"
        if abs(dx) < 30 or abs(dy) < 30:
            return "H" if abs(dx) >= abs(dy) else "V"
    return signature


def orthogonal_points_from_signature(
    start: dict[str, float],
    end: dict[str, float],
    signature: str,
    adjusts: dict[str, Any] | None = None,
) -> list[dict[str, float]]:
    adjusts = adjusts or {}
    if signature == "H":
        if not math.isclose(start["y"], end["y"], abs_tol=0.01):
            return [start, {"x": end["x"], "y": start["y"]}, end]
        return [start, end]
    if signature == "V":
        if not math.isclose(start["x"], end["x"], abs_tol=0.01):
            return [start, {"x": start["x"], "y": end["y"]}, end]
        return [start, end]
    if signature == "HV":
        return [start, {"x": end["x"], "y": start["y"]}, end]
    if signature == "VH":
        return [start, {"x": start["x"], "y": end["y"]}, end]
    if signature == "HVH":
        mid_x = round(start["x"] + (end["x"] - start["x"]) * clamp_ratio_from_adjust(adjusts.get("adj1"), 0.5), 2)
        return [start, {"x": mid_x, "y": start["y"]}, {"x": mid_x, "y": end["y"]}, end]
    if signature == "VHV":
        mid_y = round(start["y"] + (end["y"] - start["y"]) * clamp_ratio_from_adjust(adjusts.get("adj1"), 0.5), 2)
        return [start, {"x": start["x"], "y": mid_y}, {"x": end["x"], "y": mid_y}, end]
    if signature == "HVHV":
        pivot_x = round(start["x"] + (end["x"] - start["x"]) * clamp_ratio_from_adjust(adjusts.get("adj1"), 0.62), 2)
        mid_y = round(start["y"] + (end["y"] - start["y"]) * clamp_ratio_from_adjust(adjusts.get("adj2"), 0.5), 2)
        return [start, {"x": pivot_x, "y": start["y"]}, {"x": pivot_x, "y": mid_y}, {"x": end["x"], "y": mid_y}, end]
    if signature == "VHVH":
        pivot_y = round(start["y"] + (end["y"] - start["y"]) * clamp_ratio_from_adjust(adjusts.get("adj1"), 0.62), 2)
        mid_x = round(start["x"] + (end["x"] - start["x"]) * clamp_ratio_from_adjust(adjusts.get("adj2"), 0.5), 2)
        return [start, {"x": start["x"], "y": pivot_y}, {"x": mid_x, "y": pivot_y}, {"x": mid_x, "y": end["y"]}, end]
    return [start, end]


def preferred_signature_for_connector_kind(
    kind: str,
    start_dir: str,
    end_dir: str,
    dx: float,
    dy: float,
) -> str:
    start_axis = axis_from_direction(start_dir)
    end_axis = axis_from_direction(end_dir)
    kind = (kind or "").lower()
    if kind == "straightconnector1" or kind == "line":
        return "H" if abs(dx) >= abs(dy) else "V"
    if kind == "bentconnector2":
        if start_axis == "H" and end_axis == "V":
            return "HV"
        if start_axis == "V" and end_axis == "H":
            return "VH"
        return "H" if abs(dx) >= abs(dy) else "V"
    if kind == "bentconnector3":
        if start_axis == "H" and end_axis == "H":
            return "HVH"
        if start_axis == "V" and end_axis == "V":
            return "VHV"
        if start_axis == "H" and end_axis == "V":
            return "HV"
        if start_axis == "V" and end_axis == "H":
            return "VH"
        return "HVH" if abs(dx) >= abs(dy) else "VHV"
    if kind == "bentconnector4":
        if start_axis == "H" and end_axis == "H":
            return "HVHV"
        if start_axis == "V" and end_axis == "V":
            return "VHVH"
        if start_axis == "H" and end_axis == "V":
            return "HVH"
        if start_axis == "V" and end_axis == "H":
            return "VHV"
        return "HVHV" if abs(dx) >= abs(dy) else "VHVH"
    return ""


def build_connector_path_points(
    start: dict[str, float],
    end: dict[str, float],
    *,
    kind: str,
    page_type: str,
    start_dir: str,
    end_dir: str,
    adjusts: dict[str, Any] | None = None,
) -> list[dict[str, float]]:
    dx = end["x"] - start["x"]
    dy = end["y"] - start["y"]
    start_dir, end_dir = effective_connector_directions(kind, start_dir, end_dir, dx, dy)
    signature = preferred_signature_for_connector_kind(kind, start_dir, end_dir, dx, dy)
    if not signature:
        signature = choose_route_signature(kind, page_type, start_dir, end_dir, dx, dy)
        signature = maybe_shorten_signature(signature, dx, dy, start_dir, end_dir)
    lead = max(min(abs(dx), abs(dy), 18.0), 8.0)
    horizontal_clearance = max(abs(dx) / 2.0 - 1.0, 0.0)
    vertical_clearance = max(abs(dy) / 2.0 - 1.0, 0.0)
    start_lead = lead
    end_lead = lead
    if start_dir in {"left", "right"}:
        start_lead = min(start_lead, horizontal_clearance)
    if start_dir in {"up", "down"}:
        start_lead = min(start_lead, vertical_clearance)
    if end_dir in {"left", "right"}:
        end_lead = min(end_lead, horizontal_clearance)
    if end_dir in {"up", "down"}:
        end_lead = min(end_lead, vertical_clearance)
    start_anchor = offset_point(start, start_dir, start_lead) if start_dir and start_lead > 0.5 else start
    end_anchor = offset_point(end, end_dir, -end_lead) if end_dir and end_lead > 0.5 else end
    points = [start]
    if start_anchor != start:
        points.append(start_anchor)
    points.extend(orthogonal_points_from_signature(start_anchor, end_anchor, signature, adjusts)[1:-1])
    if end_anchor != end:
        points.append(end_anchor)
    points.append(end)
    return simplify_orthogonal_points(points)


def segment_direction(previous: dict[str, float], current: dict[str, float]) -> str:
    dx = current["x"] - previous["x"]
    dy = current["y"] - previous["y"]
    if abs(dx) >= abs(dy):
        return "right" if dx >= 0 else "left"
    return "down" if dy >= 0 else "up"


def build_connector_route_debug(
    candidate: dict[str, Any],
    block: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any] | None:
    extra = candidate.get("extra") or {}
    start_px = scale_point(extra.get("start_point_px"), context["scale_x"], context["scale_y"])
    end_px = scale_point(extra.get("end_point_px"), context["scale_x"], context["scale_y"])
    if not start_px or not end_px:
        return None
    start = transform_point_into_block(start_px, block)
    end = transform_point_into_block(end_px, block)
    start_conn = extra.get("start_connection") or {}
    end_conn = extra.get("end_connection") or {}
    start_dir = direction_from_connection(extra.get("start_point_px"), extra.get("start_target_bounds_px"), start_conn.get("idx"))
    end_dir = opposite_direction(
        direction_from_connection(extra.get("end_point_px"), extra.get("end_target_bounds_px"), end_conn.get("idx"))
    )
    page_type = str(((context.get("visual_strategy") or {}).get("page_type")) or "generic")
    kind = str(extra.get("shape_kind") or "straightConnector1")
    dx = end["x"] - start["x"]
    dy = end["y"] - start["y"]
    effective_start_dir, effective_end_dir = effective_connector_directions(kind, start_dir, end_dir, dx, dy)
    signature = choose_route_signature(kind, page_type, effective_start_dir, effective_end_dir, dx, dy)
    signature = maybe_shorten_signature(signature, dx, dy, effective_start_dir, effective_end_dir)
    points = build_connector_path_points(
        start,
        end,
        kind=kind,
        page_type=page_type,
        start_dir=start_dir,
        end_dir=end_dir,
        adjusts=extra.get("connector_adjusts"),
    )
    final_direction = ""
    if len(points) >= 2:
        final_direction = segment_direction(points[-2], points[-1])
    expected_end_direction = effective_end_dir or segment_direction(start, end)
    bend_points = points[1:-1] if len(points) > 2 else []
    route_preferences = connector_route_preferences(page_type)
    ranked_preferences = [
        {"signature": signature_key, "count": count}
        for signature_key, count in sorted(route_preferences.items(), key=lambda item: item[1], reverse=True)[:5]
    ]
    return {
        "candidate_id": candidate.get("candidate_id"),
        "shape_kind": kind,
        "page_type": page_type,
        "block_id": block.get("block_id"),
        "block_type": block.get("block_type"),
        "start_point": start,
        "end_point": end,
        "start_connection_idx": start_conn.get("idx"),
        "end_connection_idx": end_conn.get("idx"),
        "start_direction": start_dir,
        "end_direction": end_dir,
        "effective_start_direction": effective_start_dir,
        "effective_end_direction": effective_end_dir,
        "chosen_signature": signature,
        "route_points": points,
        "bend_points": bend_points,
        "bend_count": len(bend_points),
        "final_direction": final_direction,
        "expected_end_direction": expected_end_direction,
        "direction_match": final_direction == expected_end_direction if expected_end_direction else True,
        "reference_route_preferences": ranked_preferences,
    }


def text_svg_markup(
    text_value: str,
    bounds: dict[str, float],
    *,
    font_size: float,
    fill_hex: str,
    fill_opacity: float,
    font_family: str,
    horizontal_align: str = "LEFT",
    vertical_align: str = "TOP",
    l_ins: float = 2.0,
    r_ins: float = 2.0,
    t_ins: float = 2.0,
    b_ins: float = 2.0,
    max_lines: int | None = None,
) -> str:
    lines = wrap_text_lines(text_value, max(1, int(max((bounds["width"] - l_ins - r_ins) / max(font_size * 0.62, 4), 1))))
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
    line_height = font_size * 1.25
    content_height = len(lines) * line_height
    x = bounds["x"] + l_ins
    anchor = "start"
    if horizontal_align == "CENTER":
        x = bounds["x"] + bounds["width"] / 2
        anchor = "middle"
    elif horizontal_align == "RIGHT":
        x = bounds["x"] + bounds["width"] - r_ins
        anchor = "end"
    y = bounds["y"] + t_ins + font_size
    if vertical_align == "CENTER":
        y = bounds["y"] + (bounds["height"] - content_height) / 2 + font_size
    elif vertical_align == "BOTTOM":
        y = bounds["y"] + bounds["height"] - b_ins - content_height + font_size
    parts = [
        f'<text x="{round(x,2)}" y="{round(y,2)}" font-size="{font_size}" fill="{fill_hex}" fill-opacity="{fill_opacity}" text-anchor="{anchor}" font-family="{svg_escape(font_family)}">'
    ]
    for idx, line in enumerate(lines):
        dy = 0 if idx == 0 else line_height
        parts.append(f'<tspan x="{round(x,2)}" dy="{round(dy,2)}">{svg_escape(line)}</tspan>')
    parts.append("</text>")
    return "".join(parts)


def render_candidate_svg(
    candidate: dict[str, Any],
    abs_bounds: dict[str, Any],
    block: dict[str, Any],
    context: dict[str, Any],
    *,
    block_type: str | None = None,
) -> str:
    subtype = candidate.get("subtype")
    extra = candidate.get("extra") or {}
    local = local_bounds_in_block(abs_bounds, block)
    if subtype == "text_block":
        if should_skip_layout_placeholder_text(candidate):
            return ""
        text_value = str(candidate.get("text") or candidate.get("title") or "").strip()
        if not text_value:
            return ""
        style = build_text_style(candidate, abs_bounds, scale=min(context["scale_x"], context["scale_y"]))
        fill_hex, fill_opacity = style_color_to_svg((extra.get("text_style") or {}).get("fill"), "#111111")
        text_style = extra.get("text_style") or {}
        policy = block_text_policy(str(((context.get("visual_strategy") or {}).get("page_type")) or "generic"), block_type or "content_block")
        font_size = resolve_block_font_size(candidate, style, policy, block_type=block_type or "content_block")
        padding = policy["padding"]
        max_lines = None
        if block_type == "right_panel_block":
            max_lines = max(2, int(local["height"] / max(font_size * 1.25, 1)))
        return text_svg_markup(
            text_value,
            local,
            font_size=font_size,
            fill_hex=fill_hex,
            fill_opacity=fill_opacity,
            font_family=style.get("fontFamily") or "Arial",
            horizontal_align=style.get("textAlignHorizontal") or "LEFT",
            vertical_align=style.get("textAlignVertical") or "TOP",
            l_ins=float(text_style.get("lIns") or padding["l"]),
            r_ins=float(text_style.get("rIns") or padding["r"]),
            t_ins=float(text_style.get("tIns") or padding["t"]),
            b_ins=float(text_style.get("bIns") or padding["b"]),
            max_lines=max_lines,
        )
    if subtype == "connector":
        stroke_hex, stroke_opacity = style_color_to_svg(((extra.get("shape_style") or {}).get("line") or {}), "#777777")
        if block_type == "header_block":
            width = max(local["width"], 1.0)
            height = max(local["height"], 1.0)
            x0 = local["x"]
            y0 = local["y"]
            x1 = local["x"] + width
            y1 = local["y"] + height
            if width >= height:
                return f'<line x1="{round(x0,2)}" y1="{round(y0 + height/2,2)}" x2="{round(x1,2)}" y2="{round(y0 + height/2,2)}" stroke="{stroke_hex}" stroke-opacity="{stroke_opacity}" stroke-width="1.2" />'
            return f'<line x1="{round(x0 + width/2,2)}" y1="{round(y0,2)}" x2="{round(x0 + width/2,2)}" y2="{round(y1,2)}" stroke="{stroke_hex}" stroke-opacity="{stroke_opacity}" stroke-width="1.2" />'
        route_debug = build_connector_route_debug(candidate, block, context)
        if route_debug:
            points = route_debug["route_points"]
            path = "M " + " L ".join(f"{p['x']} {p['y']}" for p in points)
            arrow_svg = ""
            if len(points) >= 2:
                p1 = points[-2]
                p2 = points[-1]
                forced_direction = route_debug.get("expected_end_direction") or route_debug.get("effective_end_direction") or ""
                dx = p2["x"] - p1["x"]
                dy = p2["y"] - p1["y"]
                size = 6
                if forced_direction in {"left", "right", "up", "down"}:
                    final_direction = forced_direction
                elif abs(dx) >= abs(dy):
                    final_direction = "right" if dx >= 0 else "left"
                else:
                    final_direction = "down" if dy >= 0 else "up"
                if final_direction in {"left", "right"}:
                    if final_direction == "right":
                        arrow = [(p2["x"], p2["y"]), (p2["x"] - size, p2["y"] - size / 2), (p2["x"] - size, p2["y"] + size / 2)]
                    else:
                        arrow = [(p2["x"], p2["y"]), (p2["x"] + size, p2["y"] - size / 2), (p2["x"] + size, p2["y"] + size / 2)]
                else:
                    if final_direction == "down":
                        arrow = [(p2["x"], p2["y"]), (p2["x"] - size / 2, p2["y"] - size), (p2["x"] + size / 2, p2["y"] - size)]
                    else:
                        arrow = [(p2["x"], p2["y"]), (p2["x"] - size / 2, p2["y"] + size), (p2["x"] + size / 2, p2["y"] + size)]
                arrow_points = " ".join(f"{round(x,2)},{round(y,2)}" for x, y in arrow)
                arrow_svg = f'<polygon points="{arrow_points}" fill="{stroke_hex}" fill-opacity="{stroke_opacity}" />'
            return f'<path d="{path}" fill="none" stroke="{stroke_hex}" stroke-opacity="{stroke_opacity}" stroke-width="1.5" />{arrow_svg}'
        width = max(local["width"], 1.0)
        height = max(local["height"], 1.0)
        x0 = local["x"]
        y0 = local["y"]
        x1 = local["x"] + width
        y1 = local["y"] + height
        if width >= height:
            return f'<line x1="{round(x0,2)}" y1="{round(y0 + height/2,2)}" x2="{round(x1,2)}" y2="{round(y0 + height/2,2)}" stroke="{stroke_hex}" stroke-opacity="{stroke_opacity}" stroke-width="1.5" />'
        return f'<line x1="{round(x0 + width/2,2)}" y1="{round(y0,2)}" x2="{round(x0 + width/2,2)}" y2="{round(y1,2)}" stroke="{stroke_hex}" stroke-opacity="{stroke_opacity}" stroke-width="1.5" />'
    if subtype == "image":
        image_base64 = extra.get("image_base64")
        mime_type = extra.get("mime_type") or "image/png"
        if image_base64:
            return (
                f'<image x="{round(local["x"],2)}" y="{round(local["y"],2)}" '
                f'width="{round(local["width"],2)}" height="{round(local["height"],2)}" '
                f'href="data:{mime_type};base64,{image_base64}" preserveAspectRatio="xMidYMid meet" />'
            )
        return ""
    if subtype in {"shape", "labeled_shape"}:
        shape_kind = str(extra.get("shape_kind") or "").lower()
        fill_hex, fill_opacity = style_color_to_svg(((extra.get("shape_style") or {}).get("fill") or {}), "#ffffff")
        line_hex, line_opacity = style_color_to_svg(((extra.get("shape_style") or {}).get("line") or {}), "#444444")
        fill_style = ((extra.get("shape_style") or {}).get("fill") or {})
        line_style = ((extra.get("shape_style") or {}).get("line") or {})
        has_fill = fill_style.get("kind") != "none"
        line_kind = line_style.get("kind")
        has_stroke = line_kind not in {"none", "default"}
        if not has_stroke and line_kind == "default" and block_type in {"right_panel_block", "content_block", "table_block"}:
            has_stroke = True
            line_hex = "#C7C7C7"
            line_opacity = 0.85
        fill_attr = f'fill="{fill_hex}" fill-opacity="{fill_opacity}"' if has_fill else 'fill="none"'
        stroke_attr = f'stroke="{line_hex}" stroke-opacity="{line_opacity}" stroke-width="1"' if has_stroke else 'stroke="none"'
        if shape_kind == "flowchartdecision":
            cx = local["x"] + local["width"] / 2
            cy = local["y"] + local["height"] / 2
            points = [
                f"{round(cx,2)},{round(local['y'],2)}",
                f"{round(local['x'] + local['width'],2)},{round(cy,2)}",
                f"{round(cx,2)},{round(local['y'] + local['height'],2)}",
                f"{round(local['x'],2)},{round(cy,2)}",
            ]
            shape_svg = f'<polygon points="{" ".join(points)}" {fill_attr} {stroke_attr} />'
        elif shape_kind == "ellipse":
            shape_svg = f'<ellipse cx="{round(local["x"] + local["width"]/2,2)}" cy="{round(local["y"] + local["height"]/2,2)}" rx="{round(local["width"]/2,2)}" ry="{round(local["height"]/2,2)}" {fill_attr} {stroke_attr} />'
        else:
            rx = 8 if shape_kind == "roundrect" else 0
            shape_svg = f'<rect x="{round(local["x"],2)}" y="{round(local["y"],2)}" width="{round(local["width"],2)}" height="{round(local["height"],2)}" rx="{rx}" ry="{rx}" {fill_attr} {stroke_attr} />'
        if subtype == "labeled_shape":
            text_svg = render_candidate_svg(
                {
                    "subtype": "text_block",
                    "text": candidate.get("text") or candidate.get("title") or "",
                    "title": candidate.get("title"),
                    "extra": {
                        "text_style": (extra.get("text_style") or {}),
                        "source_scope": extra.get("source_scope"),
                        "placeholder": extra.get("placeholder"),
                    },
                },
                abs_bounds,
                block,
                context,
                block_type=block_type,
            )
            return shape_svg + text_svg
        return shape_svg
    return ""


def render_generated_node_svg(node: dict[str, Any], block: dict[str, Any]) -> str:
    node_type = node.get("type")
    bounds = node.get("absoluteBoundingBox") or block["bounds"]
    local = local_bounds_in_block(bounds, block)
    if node_type in {"GROUP", "FRAME"}:
        return "".join(render_generated_node_svg(child, block) for child in node.get("children", []))
    if node_type == "RECTANGLE":
        fills = node.get("fills") or []
        strokes = node.get("strokes") or []
        fill_hex, fill_opacity = style_color_to_svg(fills[0] if fills else None, "#ffffff")
        stroke_hex, stroke_opacity = style_color_to_svg(strokes[0] if strokes else None, "#c7c7c7")
        fill_attr = f'fill="{fill_hex}" fill-opacity="{fill_opacity}"' if fills else 'fill="none"'
        stroke_attr = f'stroke="{stroke_hex}" stroke-opacity="{stroke_opacity}" stroke-width="{node.get("strokeWeight") or 1}"' if strokes else 'stroke="none"'
        radius = float(node.get("cornerRadius") or 0)
        return (
            f'<rect x="{round(local["x"],2)}" y="{round(local["y"],2)}" width="{round(local["width"],2)}" '
            f'height="{round(local["height"],2)}" rx="{round(radius,2)}" ry="{round(radius,2)}" {fill_attr} {stroke_attr} />'
        )
    if node_type == "TEXT":
        text_value = str(node.get("characters") or node.get("name") or "")
        style = node.get("style") or {}
        fills = node.get("fills") or []
        fill_hex, fill_opacity = style_color_to_svg(fills[0] if fills else None, "#111111")
        debug = node.get("debug") or {}
        font_size = float(style.get("fontSize") or 12)
        l_ins = r_ins = 0.0
        t_ins = b_ins = 0.0
        max_lines = None
        if debug.get("role") in {"table_cell", "header_text_fragment"} or debug.get("source_subtype") == "table_cell":
            l_ins = r_ins = 4.0
            t_ins = b_ins = 2.0
            max_lines = max(1, int(max(local["height"] - t_ins - b_ins, 1) / max(font_size * 1.25, 1)))
        elif block.get("block_type") == "right_panel_block":
            l_ins = r_ins = 4.0
            t_ins = b_ins = 2.0
            max_lines = max(1, int(max(local["height"] - t_ins - b_ins, 1) / max(font_size * 1.25, 1)))
        return text_svg_markup(
            text_value,
            local,
            font_size=font_size,
            fill_hex=fill_hex,
            fill_opacity=fill_opacity,
            font_family=style.get("fontFamily") or "Arial",
            horizontal_align=style.get("textAlignHorizontal") or "LEFT",
            vertical_align=style.get("textAlignVertical") or "TOP",
            l_ins=l_ins,
            r_ins=r_ins,
            t_ins=t_ins,
            b_ins=b_ins,
            max_lines=max_lines,
        )
    return ""


def bounds_overlap_ratio(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1 = float(a["x"])
    ay1 = float(a["y"])
    ax2 = ax1 + float(a["width"])
    ay2 = ay1 + float(a["height"])
    bx1 = float(b["x"])
    by1 = float(b["y"])
    bx2 = bx1 + float(b["width"])
    by2 = by1 + float(b["height"])
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area = max((ax2 - ax1) * (ay2 - ay1), 1.0)
    return inter / area


def should_skip_table_child_for_overlays(node: dict[str, Any], overlay_bounds: list[dict[str, Any]]) -> bool:
    if not overlay_bounds:
        return False
    bounds = node.get("absoluteBoundingBox")
    if not bounds:
        return False
    threshold = 0.35 if node.get("type") == "TEXT" else 0.6
    return any(bounds_overlap_ratio(bounds, overlay) >= threshold for overlay in overlay_bounds)


def build_svg_block_node(block: dict[str, Any], markup: str, role: str) -> dict[str, Any]:
    width = round(block["bounds"]["width"], 2)
    height = round(block["bounds"]["height"], 2)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">{markup}</svg>'
    )
    return {
        "id": block["block_id"],
        "type": "SVG_BLOCK",
        "name": block["block_type"],
        "absoluteBoundingBox": block["bounds"],
        "relativeTransform": identity_affine(),
        "svgMarkup": svg,
        "children": [],
        "debug": {
            "generator": "block-prototype-v1",
            "block_type": block["block_type"],
            "render_mode": block["render_mode"],
            "page_type": block["page_type"],
            "role": role,
            "root_candidate_ids": block["root_candidate_ids"],
        },
    }


def build_svg_block_child_node(block: dict[str, Any], markup: str, role: str, suffix: str) -> dict[str, Any]:
    node = build_svg_block_node(block, markup, role)
    node["id"] = f"{block['block_id']}:{suffix}"
    node["name"] = f"{block['block_type']}:{suffix}"
    node["debug"] = dict(node.get("debug") or {}, child_suffix=suffix)
    return node


def build_svg_child_node_with_bounds(
    parent_block: dict[str, Any],
    child_bounds: dict[str, float],
    markup: str,
    role: str,
    suffix: str,
) -> dict[str, Any]:
    temp_block = dict(parent_block)
    temp_block["bounds"] = child_bounds
    node = build_svg_block_node(temp_block, markup, role)
    node["id"] = f"{parent_block['block_id']}:{suffix}"
    node["name"] = f"{parent_block['block_type']}:{suffix}"
    node["debug"] = dict(node.get("debug") or {}, child_suffix=suffix)
    return node


def iter_table_cell_layouts(table_candidate: dict[str, Any], context: dict[str, Any]):
    scale_x = context["scale_x"]
    scale_y = context["scale_y"]
    bounds = table_candidate.get("bounds_px") or {"x": 0, "y": 0, "width": 120, "height": 40}
    abs_bounds = make_bounds(
        scale_value(bounds["x"], scale_x),
        scale_value(bounds["y"], scale_y),
        scale_value(bounds["width"], scale_x),
        scale_value(bounds["height"], scale_y),
    )
    children_map = context["children_map"]
    rows = sorted(
        [child for child in children_map.get(table_candidate["candidate_id"], []) if child.get("subtype") == "table_row"],
        key=sort_by_position_key,
    )
    grid_columns = (table_candidate.get("extra") or {}).get("grid_columns") or []
    if grid_columns:
        column_widths = [scale_value(column.get("width_px") or 0, scale_x) for column in grid_columns]
    else:
        max_cols = max(
            (len([child for child in children_map.get(row["candidate_id"], []) if child.get("subtype") == "table_cell"]) for row in rows),
            default=1,
        )
        column_widths = [abs_bounds["width"] / max(max_cols, 1)] * max_cols
    column_x = [abs_bounds["x"]]
    for width in column_widths:
        column_x.append(column_x[-1] + width)
    row_heights: list[float] = []
    for row in rows:
        base_height = scale_value(((row.get("extra") or {}).get("row_height_px") or 28), scale_y)
        row_heights.append(max(base_height, 18.0))
    row_y = [abs_bounds["y"]]
    for height in row_heights:
        row_y.append(row_y[-1] + height)
    for row_index, row in enumerate(rows):
        row_cells = [child for child in children_map.get(row["candidate_id"], []) if child.get("subtype") == "table_cell"]
        for cell in row_cells:
            cell_extra = cell.get("extra") or {}
            if cell_extra.get("h_merge") or cell_extra.get("v_merge"):
                continue
            start_column_index = max(int(cell_extra.get("start_column_index") or 1), 1)
            col_span = max(int(cell_extra.get("col_span") or cell_extra.get("grid_span") or 1), 1)
            row_span = max(int(cell_extra.get("row_span") or 1), 1)
            left = column_x[start_column_index - 1]
            right_index = min(start_column_index - 1 + col_span, len(column_x) - 1)
            right = column_x[right_index]
            top = row_y[row_index]
            bottom_index = min(row_index + row_span, len(row_y) - 1)
            bottom = row_y[bottom_index]
            cell_bounds = make_bounds(left, top, max(right - left, 1.0), max(bottom - top, 1.0))
            yield row_index, cell, cell_extra, cell_bounds, start_column_index, row_span


def ui_mockup_layer_role(candidate: dict[str, Any], abs_bounds: dict[str, Any], *, block_type: str) -> int:
    subtype = str(candidate.get("subtype") or "")
    extra = candidate.get("extra") or {}
    shape_kind = str(extra.get("shape_kind") or "").lower()
    text_value = str(candidate.get("text") or "").strip()
    width = float(abs_bounds.get("width") or 0.0)
    height = float(abs_bounds.get("height") or 0.0)

    if subtype == "image":
        return 5
    if subtype == "connector":
        return 6
    if subtype == "text_block":
        return 7
    if subtype == "shape":
        if width <= 48 and height <= 48:
            return 4
        return 0
    if subtype == "labeled_shape":
        # Big cards first, small badges/icons above them.
        if width >= 140 and height >= 40 and len(text_value) >= 20:
            return 0
        if width <= 48 and height <= 48:
            return 4
        if width <= 90 and height <= 36:
            return 3
        if shape_kind == "rect" and width >= 120 and height >= 20:
            return 1
        return 2
    return 2


def build_header_block_node(block: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    ownership = filter_block_candidates(
        collect_block_candidates(block, context),
        context,
        dominant_owner_subtypes=None,
        text_owner_subtypes={"labeled_shape"},
        candidate_owner_subtypes=set(),
    )
    parts: list[str] = []
    parts.append(
        f'<rect x="0" y="0" width="{round(block["bounds"]["width"],2)}" height="{round(min(block["bounds"]["height"], 42),2)}" fill="white" fill-opacity="0" />'
    )
    for candidate in ownership["filtered_candidates"]:
        if candidate.get("subtype") == "table":
            table_group = build_table_visual_group(candidate, context, assets)
            parts.append(render_generated_node_svg(table_group, block))
            continue
        if candidate.get("subtype") == "connector":
            source_scope = str(((candidate.get("extra") or {}).get("source_scope")) or "slide").lower()
            abs_bounds = candidate_abs_bounds(candidate, context)
            if source_scope not in {"layout", "master"} and abs_bounds["y"] + abs_bounds["height"] > block["bounds"]["height"] + 6:
                continue
        abs_bounds = candidate_abs_bounds(candidate, context)
        svg = render_candidate_svg(candidate, abs_bounds, block, context, block_type="header_block")
        if not svg:
            continue
        parts.append(svg)
    return build_svg_block_node(block, "".join(parts), "header_block_svg")


def build_top_meta_block_node(block: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    ownership = filter_block_candidates(
        collect_block_candidates(block, context),
        context,
        dominant_owner_subtypes=None,
        text_owner_subtypes={"labeled_shape"},
        candidate_owner_subtypes=set(),
    )
    layers: list[tuple[int, float, float, str]] = []
    for candidate in ownership["filtered_candidates"]:
        abs_bounds = candidate_abs_bounds(candidate, context)
        svg = render_candidate_svg(candidate, abs_bounds, block, context, block_type="top_meta_block")
        if not svg:
            continue
        subtype = candidate.get("subtype")
        role = ui_mockup_layer_role(candidate, abs_bounds, block_type="top_meta_block")
        if subtype == "text_block":
            role = max(role, 7)
        layers.append((role, abs_bounds["y"], abs_bounds["x"], svg))
    markup = "".join(svg for _, _, _, svg in sorted(layers, key=lambda row: (row[0], row[1], row[2])))
    return build_svg_block_node(block, markup, "top_meta_block_svg")


def build_table_visual_group(table_candidate: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    scale_x = context["scale_x"]
    scale_y = context["scale_y"]
    bounds = table_candidate.get("bounds_px") or {"x": 0, "y": 0, "width": 120, "height": 40}
    abs_bounds = make_bounds(
        scale_value(bounds["x"], scale_x),
        scale_value(bounds["y"], scale_y),
        scale_value(bounds["width"], scale_x),
        scale_value(bounds["height"], scale_y),
    )
    table_group = {
        "id": table_candidate["candidate_id"],
        "type": "GROUP",
        "name": table_candidate.get("title") or "table_block_table",
        "absoluteBoundingBox": abs_bounds,
        "relativeTransform": identity_affine(),
        "children": [],
        "debug": {
            "generator": "block-prototype-v1",
            "role": "table_visual_group",
            "source_candidate_id": table_candidate["candidate_id"],
        },
    }

    line_color = {"r": 0.78, "g": 0.78, "b": 0.78}
    header_fill = {"r": 0.92, "g": 0.92, "b": 0.92}
    for row_index, cell, cell_extra, cell_bounds, start_column_index, row_span in iter_table_cell_layouts(table_candidate, context):
            cell_style = cell_extra.get("cell_style") or {}
            rect_candidate = dict(cell)
            rect_candidate["extra"] = dict(cell.get("extra") or {})
            rect_candidate["extra"]["shape_style"] = {
                "fill": cell_style.get("fill"),
                "line": {"type": "srgb", "value": "C7C7C7", "alpha": 1.0, "width_px": 1},
            }
            rect_candidate["extra"]["shape_kind"] = "rect"
            rect = build_rectangle_node(rect_candidate, cell_bounds, min(scale_x, scale_y))
            if not cell_style.get("fill"):
                rect["fills"] = []
            rect["strokes"] = [{"type": "SOLID", "color": line_color, "opacity": 1.0}]
            rect["strokeWeight"] = 1
            if row_index == 0 and not rect["fills"]:
                rect["fills"] = [{"type": "SOLID", "color": header_fill, "opacity": 1.0}]
            rect["name"] = f"cell {row_index + 1}-{start_column_index}"
            rect["debug"] = dict(rect.get("debug") or {}, role="table_cell_rect", source_candidate_id=cell.get("candidate_id"))
            table_group["children"].append(rect)

            if cell.get("text"):
                text_node = build_text_node(
                    cell,
                    cell_bounds,
                    context=context,
                    force_wrap=True,
                    table_cell=True,
                    horizontal_fallback="ctr" if row_index == 0 else "l",
                    vertical_fallback=(cell_style.get("anchor") or ("ctr" if row_index == 0 else "t")),
                    scale=min(scale_x, scale_y),
                )
                if start_column_index == 1 and row_span >= 3:
                    text_node["style"] = dict(text_node.get("style") or {})
                    base_font = float(text_node["style"].get("fontSize") or 8.0)
                    text_node["style"]["fontSize"] = round(min(max(base_font, 8.0), 10.0), 2)
                    text_node["style"]["textAlignHorizontal"] = "LEFT"
                    text_node["style"]["textAlignVertical"] = "CENTER"
                    text_node["debug"] = dict(text_node.get("debug") or {}, table_role="merged_label_cell")
                text_node["debug"] = dict(text_node.get("debug") or {}, source_candidate_id=cell.get("candidate_id"))
                table_group["children"].append(text_node)

    return table_group


def consolidate_table_group_cells(table_group: dict[str, Any]) -> dict[str, Any]:
    grouped_children: list[dict[str, Any]] = []
    bucketed: dict[str, list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []

    for child in table_group.get("children", []):
        name = str(child.get("name") or "")
        if name.startswith("cell "):
            bucketed.setdefault(name, []).append(child)
        else:
            passthrough.append(child)

    for name, items in bucketed.items():
        if len(items) == 1 and items[0].get("type") == "GROUP":
            grouped_children.append(items[0])
            continue
        bounds = union_bounds([item.get("absoluteBoundingBox") or table_group["absoluteBoundingBox"] for item in items])
        grouped_children.append(
            {
                "id": f"{table_group['id']}:{name.replace(' ', '_')}",
                "type": "GROUP",
                "name": name,
                "absoluteBoundingBox": bounds,
                "relativeTransform": identity_affine(),
                "children": items,
                "debug": {
                    "generator": "block-prototype-v1",
                    "role": "table_cell_group",
                    "source_candidate_id": ((items[0].get("debug") or {}).get("source_candidate_id")),
                },
            }
        )

    table_group["children"] = grouped_children + passthrough
    return table_group


def build_owner_lane_group(
    parent_id: str,
    name: str,
    children: list[dict[str, Any]],
    role: str,
    source_candidate_id: str | None = None,
) -> dict[str, Any]:
    bounds = union_bounds(
        [
            child.get("absoluteBoundingBox") or make_bounds(0.0, 0.0, 1.0, 1.0)
            for child in children
        ]
    )
    return {
        "id": f"{parent_id}:{name}",
        "type": "GROUP",
        "name": name,
        "absoluteBoundingBox": bounds,
        "relativeTransform": identity_affine(),
        "children": children,
        "debug": {
            "generator": "block-prototype-v1",
            "role": role,
            "source_candidate_id": source_candidate_id,
        },
    }


def build_direct_lane_text_node(
    candidate: dict[str, Any],
    abs_bounds: dict[str, Any],
    context: dict[str, Any],
    *,
    font_size: float = 8.0,
    vertical_align: str = "TOP",
) -> dict[str, Any]:
    style = build_text_style(
        candidate,
        abs_bounds,
        force_wrap=True,
        table_cell=True,
        horizontal_fallback="l",
        vertical_fallback="t",
        scale=min(float(context["scale_x"]), float(context["scale_y"])),
    )
    style["fontSize"] = font_size
    style["textAlignHorizontal"] = "LEFT"
    style["textAlignVertical"] = vertical_align
    style["textAutoResize"] = "HEIGHT"
    return {
        "id": f"{candidate['candidate_id']}:direct_text",
        "type": "TEXT",
        "name": candidate.get("title") or candidate.get("subtype") or "text",
        "characters": str(candidate.get("text") or candidate.get("title") or ""),
        "absoluteBoundingBox": abs_bounds,
        "relativeTransform": identity_affine(),
        "fills": [solid_paint(((candidate.get("extra") or {}).get("text_style") or {}).get("fill"), {"r": 0.12, "g": 0.12, "b": 0.12}, 1.0)],
        "style": style,
        "children": [],
        "debug": {
            "generator": "block-prototype-v1",
            "role": "description_lane_text_native",
            "source_candidate_id": candidate["candidate_id"],
        },
    }


def build_direct_lane_text_group(
    candidate: dict[str, Any],
    abs_bounds: dict[str, Any],
    context: dict[str, Any],
    *,
    font_size: float = 8.0,
    max_chars: int = 42,
    line_gap: float = 2.0,
) -> dict[str, Any]:
    text_value = str(candidate.get("text") or candidate.get("title") or "").strip()
    if not text_value:
        return build_owner_lane_group(
            candidate["candidate_id"],
            "empty_text_group",
            [],
            role="description_lane_text_group",
            source_candidate_id=candidate["candidate_id"],
        )
    lines = textwrap.wrap(
        text_value,
        width=max_chars,
        break_long_words=False,
        break_on_hyphens=False,
    )
    line_height = font_size + line_gap
    max_lines = max(1, int(abs_bounds["height"] / max(line_height, 1.0)))
    lines = lines[:max_lines]
    children: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        line_y = abs_bounds["y"] + index * line_height
        line_bounds = {
            "x": abs_bounds["x"],
            "y": round(line_y, 2),
            "width": abs_bounds["width"],
            "height": min(line_height, abs_bounds["height"]),
        }
        line_candidate = dict(candidate)
        line_candidate["candidate_id"] = f"{candidate['candidate_id']}:line{index+1}"
        line_candidate["text"] = line
        line_candidate["title"] = f"{candidate.get('title') or candidate.get('subtype') or 'text'}_line_{index+1}"
        children.append(
            build_direct_lane_text_node(
                line_candidate,
                line_bounds,
                context,
                font_size=font_size,
                vertical_align="TOP",
            )
        )
    return build_owner_lane_group(
        candidate["candidate_id"],
        f"{candidate.get('title') or candidate.get('subtype') or 'text'}_lines",
        children,
        role="description_lane_text_group",
        source_candidate_id=candidate["candidate_id"],
    )


def estimate_lane_wrap_chars(width: float, font_size: float) -> int:
    # Approximate average Hangul/Latin glyph width for narrow dense-ui lanes.
    glyph_width = max(font_size * 0.95, 6.4)
    return max(10, int(max(width - 8.0, 24.0) / glyph_width))


def build_native_description_lane_text_group(
    candidate: dict[str, Any],
    abs_bounds: dict[str, Any],
    context: dict[str, Any],
    *,
    font_size: float = 8.0,
    line_gap: float = 2.0,
) -> dict[str, Any]:
    return build_direct_lane_text_group(
        candidate,
        abs_bounds,
        context,
        font_size=font_size,
        max_chars=estimate_lane_wrap_chars(float(abs_bounds["width"]), font_size),
        line_gap=line_gap,
    )


def build_top_lane_background_node(
    candidate: dict[str, Any],
    abs_bounds: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    rect_candidate = dict(candidate)
    rect_candidate["candidate_id"] = f"{candidate['candidate_id']}:lane_bg"
    rect_candidate["title"] = f"{candidate.get('title') or candidate.get('subtype') or 'cell'}_lane_bg"
    extra = dict(candidate.get("extra") or {})
    cell_style = dict((extra.get("cell_style") or {}))
    extra["shape_kind"] = "rect"
    extra["shape_style"] = {
        "fill": cell_style.get("fill"),
        "line": {"type": "srgb", "value": "C7C7C7", "alpha": 1.0, "width_px": 1},
    }
    rect_candidate["extra"] = extra
    node = build_rectangle_node(rect_candidate, abs_bounds, min(float(context["scale_x"]), float(context["scale_y"])))
    if not node.get("fills"):
        node["fills"] = [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}, "opacity": 1.0}]
    node["strokes"] = [{"type": "SOLID", "color": {"r": 0.78, "g": 0.78, "b": 0.78}, "opacity": 1.0}]
    node["strokeWeight"] = 1
    node["name"] = f"{candidate.get('title') or candidate.get('subtype') or 'cell'}_lane_bg"
    node["debug"] = dict(node.get("debug") or {}, role="description_lane_background")
    return node


def build_table_block_node(block: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    policy = block_text_policy(block["page_type"], "table_block")
    root_candidates = {candidate["candidate_id"]: candidate for candidate in context["roots"]}
    table_roots = [root_candidates[candidate_id] for candidate_id in block["root_candidate_ids"] if candidate_id in root_candidates]
    ownership = filter_block_candidates(
        table_roots,
        context,
        dominant_owner_subtypes={"table"},
        candidate_owner_subtypes={"table", "table_cell"},
        duplicate_subtypes={"labeled_shape", "text_block", "shape", "section_block", "group"},
    )
    parts: list[str] = []
    for candidate in ownership["filtered_candidates"]:
        if candidate.get("subtype") == "table":
            table_group = build_table_visual_group(candidate, context, assets)
            for child in table_group["children"]:
                bounds = child.get("absoluteBoundingBox") or table_group["absoluteBoundingBox"]
                if child.get("type") == "TEXT":
                    text_value = child.get("characters") or child.get("name") or ""
                    style = child.get("style") or {}
                    local = local_bounds_in_block(bounds, block)
                    fill = (child.get("fills") or [{}])[0]
                    fill_hex, fill_opacity = style_color_to_svg(fill, "#111111")
                    debug = child.get("debug") or {}
                    if debug.get("table_role") == "merged_label_cell":
                        font_size = float(style.get("fontSize") or 18)
                        horizontal_align = "CENTER"
                        vertical_align = "CENTER"
                        l_ins = 2.0
                        r_ins = 2.0
                        t_ins = 2.0
                        b_ins = 2.0
                    else:
                        font_size = resolve_block_font_size(
                            {
                                "extra": {
                                    "text_style": {
                                        "font_size_max": style.get("fontSize"),
                                        "font_size_avg": style.get("fontSize"),
                                    }
                                }
                            },
                            style,
                            policy,
                            block_type="table_block",
                        )
                        horizontal_align = style.get("textAlignHorizontal") or "LEFT"
                        vertical_align = style.get("textAlignVertical") or "TOP"
                        l_ins = 0.0
                        r_ins = 0.0
                        t_ins = 0.0
                        b_ins = 0.0
                    parts.append(
                        text_svg_markup(
                            text_value,
                            local,
                            font_size=font_size,
                            fill_hex=fill_hex,
                            fill_opacity=fill_opacity,
                            font_family=style.get("fontFamily") or "Arial",
                            horizontal_align=horizontal_align,
                            vertical_align=vertical_align,
                            l_ins=l_ins,
                            r_ins=r_ins,
                            t_ins=t_ins,
                            b_ins=b_ins,
                        )
                    )
                elif child.get("type") == "RECTANGLE":
                    local = local_bounds_in_block(bounds, block)
                    fills = child.get("fills") or []
                    strokes = child.get("strokes") or []
                    fill_hex, fill_opacity = style_color_to_svg(fills[0] if fills else None, "#ffffff")
                    stroke_hex, stroke_opacity = style_color_to_svg(strokes[0] if strokes else None, "#c7c7c7")
                    fill_attr = f'fill="{fill_hex}" fill-opacity="{fill_opacity}"' if fills else 'fill="none"'
                    stroke_attr = f'stroke="{stroke_hex}" stroke-opacity="{stroke_opacity}" stroke-width="1"' if strokes else 'stroke="none"'
                    parts.append(
                        f'<rect x="{round(local["x"],2)}" y="{round(local["y"],2)}" width="{round(local["width"],2)}" height="{round(local["height"],2)}" {fill_attr} {stroke_attr} />'
                    )
        else:
            child = build_visual_node_from_candidate(candidate, context, assets)
            if child:
                bounds = child.get("absoluteBoundingBox") or block["bounds"]
                if child.get("type") == "TEXT":
                    local = local_bounds_in_block(bounds, block)
                    style = child.get("style") or {}
                    fill = (child.get("fills") or [{}])[0]
                    fill_hex, fill_opacity = style_color_to_svg(fill, "#111111")
                    parts.append(
                        text_svg_markup(
                            child.get("characters") or child.get("name") or "",
                            local,
                            font_size=resolve_block_font_size(
                                {
                                    "extra": {
                                        "text_style": {
                                            "font_size_max": style.get("fontSize"),
                                            "font_size_avg": style.get("fontSize"),
                                        }
                                    }
                                },
                                style,
                                policy,
                                block_type="table_block",
                            ),
                            fill_hex=fill_hex,
                            fill_opacity=fill_opacity,
                            font_family=style.get("fontFamily") or "Arial",
                            horizontal_align=style.get("textAlignHorizontal") or "LEFT",
                            vertical_align=style.get("textAlignVertical") or "TOP",
                            l_ins=0.0,
                            r_ins=0.0,
                            t_ins=0.0,
                            b_ins=0.0,
                        )
                    )
    return build_svg_block_node(block, "".join(parts), "table_block_svg")


def build_flow_block_node(block: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    ownership = filter_block_candidates(
        collect_block_candidates(block, context),
        context,
        dominant_owner_subtypes={"table"},
        candidate_owner_subtypes={"table", "table_cell"},
    )
    layers: list[tuple[int, float, float, str]] = []
    for candidate in ownership["filtered_candidates"]:
        subtype = candidate.get("subtype")
        abs_bounds = candidate_abs_bounds(candidate, context)
        svg = render_candidate_svg(candidate, abs_bounds, block, context, block_type="flow_block")
        if not svg:
            continue
        role = 1
        if subtype == "shape":
            role = 0
        elif subtype == "connector":
            role = 2
        elif subtype == "text_block":
            role = 3
        layers.append((role, abs_bounds["y"], abs_bounds["x"], svg))
    markup = "".join(svg for _, _, _, svg in sorted(layers, key=lambda row: (row[0], row[1], row[2])))
    return build_svg_block_node(block, markup, "flow_block_svg")


def select_right_panel_candidates(block: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    ordered_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in collect_block_candidates(block, context) + collect_candidates_in_block_bounds(block, context):
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in seen:
            continue
        seen.add(candidate_id)
        ordered_candidates.append(candidate)

    candidates = ordered_candidates
    ownership = filter_block_candidates(
        candidates,
        context,
        dominant_owner_subtypes={"table"},
        candidate_owner_subtypes=None,
        duplicate_subtypes={"group", "section_block"},
        overlap_threshold=0.75,
    )
    dominant_table = ownership["dominant_owner"]
    dominant_table_bounds = ownership["dominant_owner_bounds"]
    text_owner_map = build_text_owner_map(candidates)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=sort_by_position_key):
        candidate_id = str(candidate.get("candidate_id") or "")
        subtype = str(candidate.get("subtype") or "")
        if not candidate_id or candidate_id in seen:
            continue
        if subtype in {"group", "section_block", "table_row", "table_cell"}:
            continue
        if subtype == "text_block" and should_skip_layout_placeholder_text(candidate):
            continue
        if dominant_table and candidate_id == str(dominant_table.get("candidate_id") or ""):
            selected.append(candidate)
            seen.add(candidate_id)
            continue

        abs_bounds = candidate_abs_bounds(candidate, context)
        if subtype == "text_block":
            owner = text_owner_map.get(candidate_id) or {}
            if owner.get("owner_subtype") in {"table", "table_cell", "labeled_shape", "shape"}:
                continue
            if not abs_bounds:
                continue
            if float(abs_bounds["width"]) < 40 or float(abs_bounds["height"]) < 10:
                continue
            selected.append(candidate)
            seen.add(candidate_id)
            continue

        if subtype in {"labeled_shape", "shape"}:
            if abs_bounds and float(abs_bounds["width"]) >= 70 and float(abs_bounds["height"]) >= 12:
                selected.append(candidate)
                seen.add(candidate_id)
                continue

        if subtype in {"image", "connector"}:
            selected.append(candidate)
            seen.add(candidate_id)

    return {
        "dominant_owner": dominant_table,
        "dominant_owner_bounds": dominant_table_bounds,
        "filtered_candidates": selected,
        "text_owner_map": text_owner_map,
    }


def collect_table_description_cells(table_candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    description_cells: list[dict[str, Any]] = []
    for row_index, cell, cell_extra, cell_bounds, start_column_index, row_span in iter_table_cell_layouts(table_candidate, context):
        text_value = str(cell.get("text") or "").strip()
        if not text_value:
            continue
        if row_index < 2:
            continue
        if len(text_value) < 20 and "계속" not in text_value:
            continue
        description_cells.append({
            "cell": cell,
            "cell_extra": cell_extra,
            "cell_bounds": cell_bounds,
            "row_index": row_index,
            "start_column_index": start_column_index,
            "row_span": row_span,
        })
    return description_cells


def collect_table_row_candidate_ids(
    table_candidate: dict[str, Any],
    context: dict[str, Any],
    row_indices: set[int],
) -> set[str]:
    candidate_ids: set[str] = set()
    for row_index, cell, _cell_extra, _cell_bounds, _start_column_index, _row_span in iter_table_cell_layouts(table_candidate, context):
        one_based = int(row_index) + 1
        if one_based not in row_indices:
            continue
        candidate_id = str(cell.get("candidate_id") or "")
        if candidate_id:
            candidate_ids.add(candidate_id)
    return candidate_ids


def classify_dense_ui_panel_owners(selected_candidates: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    issue_card: dict[str, Any] | None = None
    version_stack_cards: list[dict[str, Any]] = []
    description_cards: list[dict[str, Any]] = []
    small_assets: list[dict[str, Any]] = []
    misc_cards: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        subtype = str(candidate.get("subtype") or "")
        bounds = candidate_abs_bounds(candidate, context)
        if not bounds:
            continue
        text_value = str(candidate.get("text") or "").strip()
        width = float(bounds["width"])
        height = float(bounds["height"])
        x = float(bounds["x"])
        y = float(bounds["y"])
        if subtype in {"image", "connector"} or (width <= 40 and height <= 40):
            small_assets.append(candidate)
            continue
        if subtype != "labeled_shape":
            continue
        if text_value.startswith("ISSUE"):
            issue_card = candidate
            continue
        if text_value.startswith("V ") or text_value.startswith("V."):
            if width < 220 and y < 220:
                version_stack_cards.append(candidate)
                continue
            if width >= 230 and x >= 680:
                description_cards.append(candidate)
                continue
        if width >= 180 and height >= 24:
            misc_cards.append(candidate)
    description_cards.sort(key=lambda c: (candidate_abs_bounds(c, context)["y"], candidate_abs_bounds(c, context)["x"]))
    version_stack_cards.sort(key=lambda c: (candidate_abs_bounds(c, context)["y"], candidate_abs_bounds(c, context)["x"]))
    small_assets.sort(key=lambda c: (candidate_abs_bounds(c, context)["y"], candidate_abs_bounds(c, context)["x"]))
    misc_cards.sort(key=lambda c: (candidate_abs_bounds(c, context)["y"], candidate_abs_bounds(c, context)["x"]))
    return {
        "issue_card": issue_card,
        "version_stack_cards": version_stack_cards,
        "description_cards": description_cards,
        "small_assets": small_assets,
        "misc_cards": misc_cards,
    }


def build_right_panel_lane_sections(
    block: dict[str, Any],
    context: dict[str, Any],
    selected_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    cards = classify_dense_ui_panel_owners(selected_candidates, context)
    issue_card = cards["issue_card"]
    description_cards = cards["description_cards"]
    if not issue_card or not description_cards:
        return []

    issue_bounds = local_bounds_in_block(candidate_abs_bounds(issue_card, context), block)
    local_cards = [local_bounds_in_block(candidate_abs_bounds(card, context), block) for card in description_cards]
    lane_sections: list[dict[str, Any]] = []

    first_card = local_cards[0]
    white_top = issue_bounds["y"] + issue_bounds["height"] + 6.0
    white_bottom = max(first_card["y"] - 6.0, white_top + 24.0)
    lane_sections.append({
        "name": "white_lane",
        "bounds": {
            "x": first_card["x"] + 8.0,
            "y": white_top,
            "width": max(first_card["width"] - 16.0, 40.0),
            "height": max(white_bottom - white_top, 24.0),
        },
    })
    for index, card_bounds in enumerate(local_cards):
        lane_sections.append({
            "name": f"card_lane_{index+1}",
            "bounds": {
                "x": card_bounds["x"] + 10.0,
                "y": card_bounds["y"] + 8.0,
                "width": max(card_bounds["width"] - 20.0, 40.0),
                "height": max(card_bounds["height"] - 12.0, 18.0),
            },
        })
    return lane_sections


def select_primary_description_cards(description_cards: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    primary: list[dict[str, Any]] = []
    for card in description_cards:
        bounds = candidate_abs_bounds(card, context)
        fill = (((card.get("extra") or {}).get("shape_style") or {}).get("fill") or {})
        resolved_fill = str(fill.get("resolved_value") or fill.get("value") or "").upper()
        if float(bounds["height"]) < 20.0:
            continue
        # The bright green V5.x card is a separate callout block, not part of the description lane stack.
        if resolved_fill == "00FF00":
            continue
        primary.append(card)
    primary.sort(key=lambda card: float(candidate_abs_bounds(card, context)["y"]))
    primary = primary[:3]
    return primary


def build_description_lane_specs(
    block: dict[str, Any],
    context: dict[str, Any],
    dense_panel: dict[str, Any],
    description_rows: list[dict[str, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    issue_card = dense_panel["issue_card"]
    primary_cards = select_primary_description_cards(dense_panel["description_cards"], context)
    if not issue_card or len(primary_cards) < 3:
        return {}

    issue_bounds = local_bounds_in_block(candidate_abs_bounds(issue_card, context), block)
    local_cards = [local_bounds_in_block(candidate_abs_bounds(card, context), block) for card in primary_cards]
    lane_specs: dict[int, dict[str, Any]] = {}
    row_locals = {
        int(row["row_index"]) + 1: local_bounds_in_block(row["cell_bounds"], block)
        for row in (description_rows or [])
    }

    first_card = local_cards[0]
    row3_local = row_locals.get(3)
    row4_local = row_locals.get(4)
    if row3_local and row4_local:
        sticky_y = row3_local["y"]
        sticky_height = max(row4_local["y"] - row3_local["y"] - 2.0, row3_local["height"])
        key_y = row4_local["y"]
        key_bottom = max(first_card["y"] - 8.0, key_y + 36.0)
        key_height = max(key_bottom - key_y, 24.0)
        top_x = row3_local["x"]
        top_width = max(min(row3_local["width"], row4_local["width"]) - 6.0, 40.0)
    else:
        white_top = issue_bounds["y"] + issue_bounds["height"] + 6.0
        white_bottom = max(first_card["y"] - 6.0, white_top + 20.0)
        white_height = max(white_bottom - white_top, 24.0)
        sticky_y = white_top
        sticky_height = max(min(34.0, white_height - 14.0), 16.0)
        key_y = white_top + sticky_height + 8.0
        key_height = max(white_height - sticky_height - 8.0, 18.0)
        top_x = first_card["x"] + 6.0
        top_width = max(first_card["width"] - 12.0, 40.0)
    lane_specs[3] = {
        "name": "sticky_lane",
        "bounds": {
            "x": top_x,
            "y": sticky_y,
            "width": top_width,
            "height": sticky_height,
        },
        "card_candidate": None,
    }
    lane_specs[4] = {
        "name": "key_visual_lane",
        "bounds": {
            "x": top_x,
            "y": key_y,
            "width": top_width,
            "height": key_height,
        },
        "card_candidate": None,
    }
    second_card = local_cards[1]
    third_card = local_cards[2]
    lane_specs[5] = {
        "name": "body_lane",
        "bounds": {
            "x": second_card["x"] + 10.0,
            "y": second_card["y"] + 8.0,
            "width": max(second_card["width"] - 20.0, 40.0),
            "height": max(second_card["height"] - 12.0, 18.0),
        },
        "card_candidate": primary_cards[1],
    }
    lane_specs[6] = {
        "name": "footer_lane",
        "bounds": {
            "x": third_card["x"] + 10.0,
            "y": third_card["y"] + 8.0,
            "width": max(third_card["width"] - 20.0, 40.0),
            "height": max(third_card["height"] - 12.0, 18.0),
        },
        "card_candidate": primary_cards[2],
    }
    return lane_specs


def build_dense_panel_background_node(candidate: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    abs_bounds = candidate_abs_bounds(candidate, context)
    if not abs_bounds:
        return None
    scale = min(float(context["scale_x"]), float(context["scale_y"]))
    shape_kind = str(((candidate.get("extra") or {}).get("shape_kind") or "")).lower()
    if shape_kind in {"rect", "roundrect"}:
        node = build_rectangle_node(candidate, abs_bounds, scale)
    else:
        node = build_shape_node(candidate, abs_bounds, scale)
    node["id"] = f"{candidate['candidate_id']}:panel_bg"
    node["name"] = f"{candidate.get('title') or candidate.get('subtype') or 'shape'}:panel_bg"
    node["debug"] = dict(node.get("debug") or {}, role="dense_ui_panel_background")
    return node


def build_dense_panel_card_label_markup(candidate: dict[str, Any], block: dict[str, Any], context: dict[str, Any]) -> str:
    text_value = str(candidate.get("text") or "").strip()
    if not text_value:
        return ""
    label = text_value.splitlines()[0].strip()
    if not label:
        return ""
    abs_bounds = candidate_abs_bounds(candidate, context)
    if not abs_bounds:
        return ""
    local = local_bounds_in_block(abs_bounds, block)
    width = min(max(local["width"] - 12.0, 24.0), 72.0)
    height = min(max(local["height"] - 8.0, 12.0), 16.0)
    return text_svg_markup(
        label,
        {
            "x": local["x"] + max(local["width"] - width - 6.0, 4.0),
            "y": local["y"] + 4.0,
            "width": width,
            "height": height,
        },
        font_size=7.0,
        fill_hex="#FFFFFF",
        fill_opacity=0.95,
        font_family="LG스마트체 Regular",
        horizontal_align="RIGHT",
        vertical_align="TOP",
        l_ins=0.0,
        r_ins=0.0,
        t_ins=0.0,
        b_ins=0.0,
        max_lines=1,
    )


def build_right_panel_description_overlay(
    block: dict[str, Any],
    context: dict[str, Any],
    primary_table: dict[str, Any] | None,
    selected_candidates: list[dict[str, Any]],
    variant: str = "v1",
) -> str:
    if not primary_table:
        return ""
    description_cells = collect_table_description_cells(primary_table, context)
    if not description_cells:
        return ""
    text_blocks: list[str] = []
    lane_sections = build_right_panel_lane_sections(block, context, selected_candidates) if variant == "v2" else []
    for row in description_cells:
        cell = row["cell"]
        text_value = str(cell.get("text") or "").strip()
        cell_extra = row["cell_extra"] or {}
        style = cell_extra.get("text_style") or {}
        if lane_sections:
            lane_index = min(max(int(row["row_index"]) - 2, 0), len(lane_sections) - 1)
            local_cell = lane_sections[lane_index]["bounds"]
        else:
            local_cell = local_bounds_in_block(row["cell_bounds"], block)
        font_size = float(style.get("font_size_max") or style.get("font_size_avg") or 8.0)
        if lane_sections and row["row_index"] >= 3:
            font_size = min(font_size, 7.0)
        max_lines = max(1, int(max(local_cell["height"] - 6.0, 10.0) / max(font_size * 1.25, 8.0)))
        valign = "TOP"
        if "계속" in text_value:
            valign = "BOTTOM"
        text_blocks.append(
            text_svg_markup(
                text_value,
                {
                    "x": local_cell["x"] + 6.0,
                    "y": local_cell["y"] + 4.0,
                    "width": max(local_cell["width"] - 12.0, 24.0),
                    "height": max(local_cell["height"] - 8.0, 12.0),
                },
                font_size=font_size,
                fill_hex="#111111",
                fill_opacity=1.0,
                font_family=str(style.get("font_family") or "LG스마트체 Regular"),
                horizontal_align="LEFT",
                vertical_align=valign,
                l_ins=0.0,
                r_ins=0.0,
                t_ins=0.0,
                b_ins=0.0,
                max_lines=max_lines,
            )
        )
    return "".join(text_blocks)


def build_right_panel_description_lane_nodes(
    primary_table: dict[str, Any] | None,
    context: dict[str, Any],
    assets: dict[str, Any],
) -> list[dict[str, Any]]:
    if not primary_table:
        return []
    description_cell_ids = {
        str(row["cell"].get("candidate_id") or "")
        for row in collect_table_description_cells(primary_table, context)
    }
    if not description_cell_ids:
        return []

    table_group = build_table_visual_group(primary_table, context, assets)
    lane_nodes: list[dict[str, Any]] = []
    for child in table_group.get("children", []):
        source_candidate_id = str(((child.get("debug") or {}).get("source_candidate_id")) or "")
        if source_candidate_id not in description_cell_ids:
            continue
        if child.get("type") not in {"TEXT", "GROUP"}:
            continue
        lane_nodes.append(child)
    return lane_nodes


def build_right_panel_block_node(
    block: dict[str, Any],
    context: dict[str, Any],
    assets: dict[str, Any],
    right_panel_variant: str,
) -> dict[str, Any]:
    variant = right_panel_variant if right_panel_variant in RIGHT_PANEL_VARIANTS else "v1"
    ownership = select_right_panel_candidates(block, context)
    dense_panel = classify_dense_ui_panel_owners(ownership["filtered_candidates"], context)
    primary_table = ownership["dominant_owner"]
    description_cell_ids = {
        str(row["cell"].get("candidate_id") or "")
        for row in (collect_table_description_cells(primary_table, context) if primary_table else [])
    }
    render_block = dict(block)
    render_block["coordinate_mode"] = "viewport_clip"
    visible_bounds = intersect_bounds(
        render_block["bounds"],
        make_bounds(0.0, 0.0, TARGET_SLIDE_WIDTH, TARGET_SLIDE_HEIGHT),
    )
    render_block["bounds"] = visible_bounds
    frame = build_block_frame(render_block)
    frame["clipsContent"] = True
    frame["debug"] = dict(frame.get("debug") or {}, role="right_panel_block_frame", variant=variant)
    seen_tables: set[str] = set()
    background_layers: list[tuple[float, float, str]] = []
    background_nodes: list[dict[str, Any]] = []
    foreground_nodes: list[dict[str, Any]] = []
    background_label_markup_parts: list[str] = []
    overlay_bounds: list[dict[str, Any]] = []
    description_lane_nodes: list[dict[str, Any]] = build_right_panel_description_lane_nodes(primary_table, context, assets) if variant == "v2" else []
    description_rows = collect_table_description_cells(primary_table, context) if primary_table else []
    description_lane_specs = build_description_lane_specs(render_block, context, dense_panel, description_rows) if variant == "v1" else {}
    lane_card_candidate_ids = {
        str(spec["card_candidate"].get("candidate_id") or "")
        for spec in description_lane_specs.values()
        if spec.get("card_candidate")
    }
    lane_row_candidate_ids = collect_table_row_candidate_ids(
        primary_table,
        context,
        set(description_lane_specs.keys()),
    ) if primary_table and description_lane_specs else set()
    for candidate in ownership["filtered_candidates"]:
        if candidate.get("subtype") not in {"labeled_shape", "shape"}:
            continue
        abs_bounds = candidate_abs_bounds(candidate, context)
        if float(abs_bounds["width"]) >= 120 and float(abs_bounds["height"]) >= 20:
            overlay_bounds.append(abs_bounds)
    for candidate in ownership["filtered_candidates"]:
        subtype = candidate.get("subtype")
        if subtype == "table":
            if candidate["candidate_id"] in seen_tables:
                continue
            seen_tables.add(candidate["candidate_id"])
            if variant == "v1":
                table_group = build_table_visual_group(candidate, context, assets)
                table_group = consolidate_table_group_cells(table_group)
                table_children: list[dict[str, Any]] = []
                for child in table_group.get("children", []):
                    source_candidate_id = str(((child.get("debug") or {}).get("source_candidate_id")) or "")
                    if source_candidate_id in description_cell_ids:
                        continue
                    if source_candidate_id in lane_row_candidate_ids:
                        continue
                    if child.get("type") == "RECTANGLE" and should_skip_table_child_for_overlays(child, overlay_bounds):
                        continue
                    table_children.append(child)
                if table_children:
                    table_group["children"] = table_children
                    frame["children"].append(table_group)
            continue
        abs_bounds = candidate_abs_bounds(candidate, context)
        is_large_overlay = subtype in {"labeled_shape", "shape"} and float(abs_bounds["width"]) >= 120 and float(abs_bounds["height"]) >= 20
        if is_large_overlay:
            if variant == "v1" and candidate in dense_panel["description_cards"]:
                if str(candidate.get("candidate_id") or "") in lane_card_candidate_ids:
                    continue
                child = build_dense_panel_background_node(candidate, context)
                if child:
                    background_nodes.append(child)
                    label_markup = build_dense_panel_card_label_markup(candidate, render_block, context)
                    if label_markup:
                        background_label_markup_parts.append(label_markup)
                    continue
            if variant == "v2":
                if candidate in dense_panel["description_cards"]:
                    child = build_dense_panel_background_node(candidate, context)
                    if child:
                        background_nodes.append(child)
                        label_markup = build_dense_panel_card_label_markup(candidate, render_block, context)
                        if label_markup:
                            background_label_markup_parts.append(label_markup)
                        continue
                child = build_visual_node_from_candidate(candidate, context, assets)
                if child and child.get("type") != "TEXT":
                    background_nodes.append(child)
                    continue
                svg = render_candidate_svg(candidate, abs_bounds, render_block, context, block_type="right_panel_block")
                if svg:
                    suffix = str(candidate.get("candidate_id") or f"bg-{len(background_nodes)+1}").replace(":", "_").replace("/", "_")
                    background_nodes.append(build_svg_block_child_node(render_block, svg, "right_panel_background_svg", suffix))
            else:
                svg = render_candidate_svg(candidate, abs_bounds, render_block, context, block_type="right_panel_block")
                if svg:
                    background_layers.append((abs_bounds["y"], abs_bounds["x"], svg))
            continue
        if variant == "v2" and candidate not in dense_panel["small_assets"] and subtype not in {"text_block", "image", "connector"}:
            continue
        child = build_visual_node_from_candidate(candidate, context, assets)
        if child:
            foreground_nodes.append(child)
            continue
        svg = render_candidate_svg(candidate, abs_bounds, render_block, context, block_type="right_panel_block")
        if svg:
            foreground_nodes.append(build_svg_block_child_node(render_block, svg, "right_panel_foreground_svg", f"fg-{len(foreground_nodes)+1}"))
    description_overlay = build_right_panel_description_overlay(
        render_block,
        context,
        primary_table,
        ownership["filtered_candidates"],
        variant,
    )
    if background_layers:
        bg = f'<rect x="0" y="0" width="{round(render_block["bounds"]["width"],2)}" height="{round(render_block["bounds"]["height"],2)}" fill="white" fill-opacity="0" />'
        markup = bg + "".join(svg for _, _, svg in sorted(background_layers, key=lambda row: (row[0], row[1])))
        frame["children"].append(build_svg_block_child_node(render_block, markup, "right_panel_background_svg", "background"))
    frame["children"].extend(background_nodes)
    if variant == "v1" and description_rows and description_lane_specs:
        for row in description_rows:
            row_index = int(row["row_index"]) + 1
            spec = description_lane_specs.get(row_index)
            if not spec:
                continue
            lane_children: list[dict[str, Any]] = []
            card_candidate = spec.get("card_candidate")
            if card_candidate:
                card_node = build_dense_panel_background_node(card_candidate, context)
                if card_node:
                    lane_children.append(card_node)
            cell = row["cell"]
            cell_extra = row["cell_extra"] or {}
            style = cell_extra.get("text_style") or {}
            text_value = str(cell.get("text") or "").strip()
            font_size = float(style.get("font_size_max") or style.get("font_size_avg") or 8.0)
            font_size = min(max(font_size, 7.0), 8.0)
            lane_bounds = spec["bounds"]
            max_lines = max(1, int(max(lane_bounds["height"] - 6.0, 10.0) / max(font_size * 1.25, 8.0)))
            lane_child_bounds = {
                "x": round(render_block["bounds"]["x"] + lane_bounds["x"], 2),
                "y": round(render_block["bounds"]["y"] + lane_bounds["y"], 2),
                "width": round(lane_bounds["width"], 2),
                "height": round(lane_bounds["height"], 2),
            }
            if spec["name"] in {"sticky_lane", "key_visual_lane"}:
                lane_children.append(build_top_lane_background_node(cell, lane_child_bounds, context))
                text_candidate = dict(cell)
                text_candidate["candidate_id"] = f"{cell['candidate_id']}:{spec['name']}"
                text_candidate["title"] = f"description_{spec['name']}"
                text_candidate["bounds_px"] = {
                    "x": lane_child_bounds["x"],
                    "y": lane_child_bounds["y"],
                    "width": lane_child_bounds["width"],
                    "height": lane_child_bounds["height"],
                    "rotation": 0,
                }
                if spec["name"] == "key_visual_lane":
                    text_node = build_direct_lane_text_group(
                        text_candidate,
                        lane_child_bounds,
                        context,
                        font_size=8.0,
                        max_chars=48,
                        line_gap=2.0,
                    )
                else:
                    text_node = build_direct_lane_text_group(
                        text_candidate,
                        lane_child_bounds,
                        context,
                        font_size=8.0,
                        max_chars=64,
                        line_gap=2.0,
                    )
                text_node["name"] = f"description_lane_text_{row_index}"
                lane_children.append(text_node)
            else:
                text_candidate = dict(cell)
                text_candidate["candidate_id"] = f"{cell['candidate_id']}:{spec['name']}"
                text_candidate["title"] = f"description_{spec['name']}"
                text_candidate["bounds_px"] = lane_child_bounds
                text_node = build_native_description_lane_text_group(
                    text_candidate,
                    lane_child_bounds,
                    context,
                    font_size=font_size,
                    line_gap=2.0 if spec["name"] != "footer_lane" else 1.0,
                )
                text_node["name"] = f"description_lane_text_{row_index}"
                lane_children.append(text_node)
            frame["children"].append(
                build_owner_lane_group(
                    frame["id"],
                    f"description_lane_{row['row_index']+1}",
                    lane_children,
                    role="description_card_lane_group",
                    source_candidate_id=str(cell.get("candidate_id") or ""),
                )
            )
    if background_label_markup_parts:
        frame["children"].append(
            build_svg_block_child_node(
                render_block,
                "".join(background_label_markup_parts),
                "right_panel_card_labels_svg",
                "card_labels",
            )
        )
    if description_lane_nodes:
        frame["children"].extend(description_lane_nodes)
    elif description_overlay and variant != "v1":
        frame["children"].append(build_svg_block_child_node(render_block, description_overlay, "right_panel_description_svg", "description"))
    frame["children"].extend(foreground_nodes)
    return frame


def build_generic_block_node(block: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    frame = build_block_frame(block)
    roots_by_id = {candidate["candidate_id"]: candidate for candidate in context["roots"]}
    for candidate_id in block["root_candidate_ids"]:
        candidate = roots_by_id.get(candidate_id)
        if not candidate:
            continue
        child = build_visual_node_from_candidate(candidate, context, assets)
        if child:
            frame["children"].append(child)
    return frame


def build_content_svg_block_node(block: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    ownership = filter_block_candidates(
        collect_block_candidates(block, context),
        context,
        dominant_owner_subtypes={"table"},
        candidate_owner_subtypes={"table", "table_cell"},
    )
    layers: list[tuple[int, float, float, str]] = []
    for candidate in ownership["filtered_candidates"]:
        source_scope = str(((candidate.get("extra") or {}).get("source_scope")) or "slide").lower()
        if source_scope in {"layout", "master"}:
            continue
        subtype = candidate.get("subtype")
        abs_bounds = candidate_abs_bounds(candidate, context)
        svg = render_candidate_svg(candidate, abs_bounds, block, context, block_type="content_block")
        if not svg:
            continue
        if block.get("page_type") == "ui-mockup":
            role = ui_mockup_layer_role(candidate, abs_bounds, block_type="content_block")
        else:
            role = 1
            if subtype == "shape":
                role = 0
            elif subtype == "connector":
                role = 2
            elif subtype == "text_block":
                role = 3
        layers.append((role, abs_bounds["y"], abs_bounds["x"], svg))
    markup = "".join(svg for _, _, _, svg in sorted(layers, key=lambda row: (row[0], row[1], row[2])))
    return build_svg_block_node(block, markup, "content_block_svg")


def build_aux_preview_block_node(block: dict[str, Any], context: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any]:
    layers: list[tuple[int, float, float, str]] = []
    for candidate in collect_block_candidates(block, context):
        source_scope = str(((candidate.get("extra") or {}).get("source_scope")) or "slide").lower()
        if source_scope in {"layout", "master"}:
            continue
        abs_bounds = candidate_abs_bounds(candidate, context)
        svg = render_candidate_svg(candidate, abs_bounds, block, context, block_type="content_block")
        if not svg:
            continue
        role = ui_mockup_layer_role(candidate, abs_bounds, block_type="content_block")
        layers.append((role, abs_bounds["y"], abs_bounds["x"], svg))
    markup = "".join(svg for _, _, _, svg in sorted(layers, key=lambda row: (row[0], row[1], row[2])))
    return build_svg_block_node(block, markup, "aux_preview_block_svg")


def build_block_node(
    block: dict[str, Any],
    context: dict[str, Any],
    assets: dict[str, Any],
    right_panel_variant: str = "v1",
) -> dict[str, Any]:
    if block["block_type"] == "header_block":
        return build_header_block_node(block, context, assets)
    if block["block_type"] == "top_meta_block":
        return build_top_meta_block_node(block, context, assets)
    if block["block_type"] == "table_block":
        return build_table_block_node(block, context, assets)
    if block["block_type"] == "flow_block":
        return build_flow_block_node(block, context, assets)
    if block["block_type"] == "right_panel_block":
        return build_right_panel_block_node(block, context, assets, right_panel_variant)
    if block["block_type"] == "aux_preview_block":
        return build_aux_preview_block_node(block, context, assets)
    if block["block_type"] == "content_block" and block["page_type"] in {"ui-mockup", "table-heavy"}:
        return build_content_svg_block_node(block, context, assets)
    return build_generic_block_node(block, context, assets)


def build_page_root(context: dict[str, Any], block_frames: list[dict[str, Any]]) -> dict[str, Any]:
    root_bounds = {
        "x": 0.0,
        "y": 0.0,
        "width": TARGET_SLIDE_WIDTH,
        "height": TARGET_SLIDE_HEIGHT,
    }
    page_name = f"Slide {context['slide_no']} - {context['title']}"
    inner_frame = {
        "id": f"{context['page_id']}:frame",
        "type": "FRAME",
        "name": "Frame",
        "absoluteBoundingBox": root_bounds,
        "relativeTransform": identity_affine(),
        "fills": [],
        "strokes": [],
        "strokeWeight": 0,
        "children": block_frames,
        "debug": {
            "generator": "block-prototype-v1",
            "source_slide_no": context["slide_no"],
            "source_title": context["title"],
            "visual_strategy": (context.get("visual_strategy") or {}).get("page_type"),
            "strategy_signals": (context.get("visual_strategy") or {}).get("signals"),
        },
    }
    return {
        "id": context["page_id"],
        "type": "FRAME",
        "name": page_name,
        "absoluteBoundingBox": root_bounds,
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {
            "generator": "block-prototype-v1",
            "source_slide_no": context["slide_no"],
            "source_title": context["title"],
            "visual_strategy": (context.get("visual_strategy") or {}).get("page_type"),
            "strategy_signals": (context.get("visual_strategy") or {}).get("signals"),
        },
    }


def build_bundle_from_page(page: dict[str, Any], source_file: str, right_panel_variant: str = "v1") -> dict[str, Any]:
    context = build_page_context(page)
    detection = build_blocks_for_page(page)
    assets: dict[str, Any] = {}
    block_frames = []
    for block in detection["blocks"]:
        normalized_block = normalize_block_bounds(block)
        block_frames.append(build_block_node(normalized_block, context, assets, right_panel_variant))

    root = build_page_root(context, block_frames)
    return {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-block-prototype",
        "visual_model_version": "block-v1",
        "source_file": source_file,
        "file_name": Path(source_file).name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": assets,
        "missing_assets": [],
        "debug": {
            "status": "block_prototype_generator",
            "candidate_count": len(context["candidates"]),
            "root_candidate_count": len(context["roots"]),
            "visual_strategy": context["visual_strategy"],
            "right_panel_variant": right_panel_variant,
            "blocks": [normalize_block_bounds(block) for block in detection["blocks"]],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build block-first replay bundle prototype from PPT intermediate JSON.")
    parser.add_argument("--input", required=True, help="Intermediate candidates JSON path")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    parser.add_argument("--slides", nargs="*", type=int, help="Optional slide numbers")
    parser.add_argument("--right-panel-variant", default="v1", choices=sorted(RIGHT_PANEL_VARIANTS), help="Rendering variant for ui-mockup right panel")
    parser.add_argument("--output-suffix", default="", help="Optional suffix appended to output file name before .bundle.json")
    args = parser.parse_args()

    payload = load_intermediate_payload(args.input)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = iter_selected_pages(payload, set(args.slides) if args.slides else None)
    for page in selected:
        bundle = build_bundle_from_page(page, str(Path(args.input).resolve()), args.right_panel_variant)
        suffix = f"-{args.output_suffix}" if args.output_suffix else ""
        output_path = output_dir / f"block-slide-{page['slide_no']}{suffix}.bundle.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, indent=2)
        print(f"saved {output_path}")


if __name__ == "__main__":
    main()
