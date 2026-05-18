#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SLIDE_GAP = 120
TARGET_SLIDE_WIDTH = 960.0
TARGET_SLIDE_HEIGHT = 540.0


def identity_affine() -> list[list[float]]:
    return [[1, 0, 0], [0, 1, 0]]


def make_bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {
        "x": round(float(x), 2),
        "y": round(float(y), 2),
        "width": round(max(float(width), 1.0), 2),
        "height": round(max(float(height), 1.0), 2),
    }


def build_page_scale(page: dict[str, Any]) -> tuple[float, float]:
    slide_size = page.get("slide_size") or {}
    width = float(slide_size.get("width_px") or TARGET_SLIDE_WIDTH)
    height = float(slide_size.get("height_px") or TARGET_SLIDE_HEIGHT)
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


def solid_paint(style_color: dict[str, Any] | None, fallback: dict[str, float], default_opacity: float = 1.0) -> dict[str, Any]:
    style_color = style_color or {}
    resolved_hex = style_color.get("resolved_value") or style_color.get("value")
    color = fallback
    if isinstance(resolved_hex, str) and len(resolved_hex) == 6:
        color = {
            "r": round(int(resolved_hex[0:2], 16) / 255, 4),
            "g": round(int(resolved_hex[2:4], 16) / 255, 4),
            "b": round(int(resolved_hex[4:6], 16) / 255, 4),
        }
    opacity = style_color.get("alpha", default_opacity)
    return {
        "type": "SOLID",
        "color": color,
        "opacity": opacity,
    }


def has_renderable_fill(shape_style: dict[str, Any] | None) -> bool:
    if not shape_style:
        return False
    fill = shape_style.get("fill") or {}
    if not fill or fill.get("kind") == "none":
        return False
    alpha = fill.get("alpha")
    return alpha is None or alpha > 0


def has_renderable_line(shape_style: dict[str, Any] | None) -> bool:
    if not shape_style:
        return False
    line = shape_style.get("line") or {}
    if not line or line.get("kind") in {"none", "default"}:
        return False
    alpha = line.get("alpha")
    width_px = line.get("width_px")
    if alpha is not None and alpha <= 0:
        return False
    if width_px is not None and width_px <= 0:
        return False
    return True


def build_fill_array(shape_style: dict[str, Any] | None, fallback: dict[str, float]) -> list[dict[str, Any]]:
    if not has_renderable_fill(shape_style):
        return []
    return [solid_paint((shape_style or {}).get("fill"), fallback, 1.0)]


def build_stroke_array(shape_style: dict[str, Any] | None, fallback: dict[str, float]) -> list[dict[str, Any]]:
    if not has_renderable_line(shape_style):
        return []
    return [solid_paint((shape_style or {}).get("line"), fallback, 1.0)]


def map_horizontal_align(value: str | None, fallback: str = "l") -> str:
    raw = (value or fallback or "l").lower()
    if raw in {"ctr", "center", "middle"}:
        return "CENTER"
    if raw in {"r", "right"}:
        return "RIGHT"
    if raw in {"just", "justify", "justified"}:
        return "JUSTIFIED"
    return "LEFT"


def map_vertical_align(value: str | None, fallback: str = "t") -> str:
    raw = (value or fallback or "t").lower()
    if raw in {"ctr", "center", "middle"}:
        return "CENTER"
    if raw in {"b", "bottom"}:
        return "BOTTOM"
    return "TOP"


def clamp_font_size(value: float) -> int:
    return max(8, min(int(round(value)), 72))


def estimate_text_font_size(text_value: str, text_style: dict[str, Any], bounds: dict[str, Any], *, table_cell: bool = False, scale: float = 1.0) -> int:
    explicit = text_style.get("font_size_max") or text_style.get("font_size_avg") or 0
    if explicit:
        return clamp_font_size(float(explicit) * scale)
    width = max(float(bounds.get("width", 120)), 1.0)
    height = max(float(bounds.get("height", 24)), 1.0)
    base_by_height = height * 0.42
    rough_capacity = max(int((width - 12) / max(base_by_height * 0.55, 4)), 4)
    multiline_penalty = 0.82 if len(text_value or "") > rough_capacity else 1.0
    width_penalty = 0.86 if width < 120 else 0.94 if width < 220 else 1.0
    scale = 0.9 if table_cell else 1.0
    return clamp_font_size(base_by_height * multiline_penalty * width_penalty * scale)


def derive_wrap_mode(text_value: str, text_style: dict[str, Any], bounds: dict[str, Any], *, force_wrap: bool = False) -> str:
    if force_wrap:
        return "wrap"
    if "\n" in (text_value or ""):
        return "wrap"
    raw_wrap = str((text_style or {}).get("wrap") or "").lower()
    if raw_wrap in {"square", "tight", "through"}:
        return "wrap"
    width = float(bounds.get("width", 120))
    if width < 180 and len(text_value or "") > 12:
        return "wrap"
    return "none"


def build_text_style(candidate: dict[str, Any], bounds: dict[str, Any], *, force_wrap: bool = False, table_cell: bool = False, horizontal_fallback: str = "l", vertical_fallback: str = "t", scale: float = 1.0) -> dict[str, Any]:
    text_style = (candidate.get("extra") or {}).get("text_style") or {}
    text_value = candidate.get("text") or candidate.get("title") or ""
    wrap_mode = derive_wrap_mode(text_value, text_style, bounds, force_wrap=force_wrap)
    return {
        "fontSize": estimate_text_font_size(text_value, text_style, bounds, table_cell=table_cell, scale=scale),
        "fontFamily": text_style.get("font_family") or "Malgun Gothic",
        "textAlignHorizontal": map_horizontal_align(text_style.get("horizontal_align"), horizontal_fallback),
        "textAlignVertical": map_vertical_align(text_style.get("vertical_align"), vertical_fallback),
        "textAutoResize": "HEIGHT" if wrap_mode != "none" else "WIDTH_AND_HEIGHT",
        "lineHeightPx": None,
    }


def estimate_wrapped_height(text_value: str, candidate: dict[str, Any], width: float, min_height: float, scale: float = 1.0) -> float:
    text_style = (candidate.get("extra") or {}).get("text_style") or {}
    font_size = estimate_text_font_size(text_value, text_style, {"width": width, "height": min_height}, table_cell=True, scale=scale)
    average_char_width = max(font_size * 0.55, 4)
    chars_per_line = max(int((width - 10) / average_char_width), 1)
    explicit_lines = str(text_value or "").split("\n")
    rendered_lines = 0
    for line in explicit_lines:
        length = max(len(line), 1)
        rendered_lines += max(math.ceil(length / chars_per_line), 1)
    line_height = font_size * 1.35
    return max(math.ceil(rendered_lines * line_height + 8), int(min_height))


def build_text_node(candidate: dict[str, Any], abs_bounds: dict[str, Any], *, force_wrap: bool = False, table_cell: bool = False, horizontal_fallback: str = "l", vertical_fallback: str = "t", scale: float = 1.0) -> dict[str, Any]:
    return {
        "id": f"{candidate['candidate_id']}:text",
        "type": "TEXT",
        "name": candidate.get("title") or candidate.get("subtype") or "text",
        "characters": candidate.get("text") or candidate.get("title") or "",
        "absoluteBoundingBox": abs_bounds,
        "relativeTransform": relative_transform_from_bounds(candidate.get("bounds_px")),
        "fills": [solid_paint(((candidate.get("extra") or {}).get("text_style") or {}).get("fill"), {"r": 0.12, "g": 0.12, "b": 0.12}, 1.0)],
        "style": build_text_style(candidate, abs_bounds, force_wrap=force_wrap, table_cell=table_cell, horizontal_fallback=horizontal_fallback, vertical_fallback=vertical_fallback, scale=scale),
        "children": [],
        "debug": {
            "source_path": candidate.get("source_path", ""),
            "source_node_id": candidate.get("source_node_id", ""),
            "source_subtype": candidate.get("subtype", ""),
            "rotation_degrees": normalize_degrees((candidate.get("bounds_px") or {}).get("rotation", 0)),
        },
    }


def build_vector_node(node_id: str, name: str, abs_bounds: dict[str, Any], *, fill_geometry: list[dict[str, Any]] | None = None, stroke_geometry: list[dict[str, Any]] | None = None, fills: list[dict[str, Any]] | None = None, strokes: list[dict[str, Any]] | None = None, stroke_weight: float = 1.0, debug: dict[str, Any] | None = None, relative_transform: list[list[float]] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "VECTOR",
        "name": name,
        "absoluteBoundingBox": abs_bounds,
        "relativeTransform": relative_transform or identity_affine(),
        "fillGeometry": fill_geometry or [],
        "strokeGeometry": stroke_geometry or [],
        "fills": fills or [],
        "strokes": strokes or [],
        "strokeWeight": stroke_weight,
        "children": [],
        "debug": debug or {},
    }


def rect_path(width: float, height: float) -> str:
    return f"M 0 0 H {width} V {height} H 0 Z"


def diamond_path(width: float, height: float) -> str:
    mid_x = width / 2
    mid_y = height / 2
    return f"M {mid_x} 0 L {width} {mid_y} L {mid_x} {height} L 0 {mid_y} Z"


def ellipse_path(width: float, height: float) -> str:
    rx = width / 2
    ry = height / 2
    return f"M {rx} 0 A {rx} {ry} 0 1 1 {rx} {height} A {rx} {ry} 0 1 1 {rx} 0 Z"


def build_shape_node(candidate: dict[str, Any], abs_bounds: dict[str, Any], scale: float = 1.0) -> dict[str, Any]:
    extra = candidate.get("extra") or {}
    shape_style = extra.get("shape_style") or {}
    shape_kind = extra.get("shape_kind") or ""
    relative_transform = relative_transform_from_bounds(candidate.get("bounds_px"))
    debug = {
        "source_path": candidate.get("source_path", ""),
        "source_node_id": candidate.get("source_node_id", ""),
        "source_subtype": candidate.get("subtype", ""),
        "full_page_overlay_candidate": bool(extra.get("full_page_overlay_candidate")),
    }
    if shape_kind in {"flowChartDecision", "ellipse", "rightBracket"}:
        path = rect_path(abs_bounds["width"], abs_bounds["height"])
        if shape_kind == "flowChartDecision":
            path = diamond_path(abs_bounds["width"], abs_bounds["height"])
        elif shape_kind == "ellipse":
            path = ellipse_path(abs_bounds["width"], abs_bounds["height"])
        elif shape_kind == "rightBracket":
            w = abs_bounds["width"]
            h = abs_bounds["height"]
            path = f"M {w * 0.2} 0 L {w} 0 L {w} {h} L {w * 0.2} {h}"
        fills = [] if shape_kind == "rightBracket" else build_fill_array(shape_style, {"r": 1, "g": 1, "b": 1})
        strokes = build_stroke_array(shape_style, {"r": 0.28, "g": 0.28, "b": 0.28})
        return build_vector_node(
            candidate["candidate_id"],
            candidate.get("title") or candidate.get("subtype") or "shape",
            abs_bounds,
            fill_geometry=[{"path": path, "windingRule": "NONZERO"}] if fills else [],
            stroke_geometry=[{"path": path}] if strokes else [],
            fills=fills,
            strokes=strokes,
            stroke_weight=max(float(((shape_style.get("line") or {}).get("width_px") or 1)) * scale, 1.0),
            debug=debug,
            relative_transform=relative_transform,
        )
    return {
        "id": candidate["candidate_id"],
        "type": "RECTANGLE",
        "name": candidate.get("title") or candidate.get("subtype") or "shape",
        "absoluteBoundingBox": abs_bounds,
        "relativeTransform": relative_transform,
        "fills": build_fill_array(shape_style, {"r": 0.94, "g": 0.95, "b": 0.97}),
        "strokes": build_stroke_array(shape_style, {"r": 0.75, "g": 0.78, "b": 0.82}),
        "strokeWeight": max(float(((shape_style.get("line") or {}).get("width_px") or 1)) * scale, 1.0),
        "cornerRadius": 8 if shape_kind == "roundRect" else None,
        "children": [],
        "debug": debug,
    }


def build_connector_node(candidate: dict[str, Any], abs_bounds: dict[str, Any], scale_x: float, scale_y: float) -> dict[str, Any]:
    extra = candidate.get("extra") or {}
    shape_style = extra.get("shape_style") or {}
    kind = extra.get("shape_kind") or "connector"
    stroke_weight = max(float(((shape_style.get("line") or {}).get("width_px") or 1.5)) * min(scale_x, scale_y), 1.0)
    local_width = max(abs_bounds["width"], 6)
    local_height = max(abs_bounds["height"], 6)
    relative_transform = identity_affine()

    def rel_point(point: dict[str, Any]) -> dict[str, float]:
        return {
            "x": round(float(point["x"]) - abs_bounds["x"], 2),
            "y": round(float(point["y"]) - abs_bounds["y"], 2),
        }

    def readable_elbow(start: dict[str, float], end: dict[str, float], kind_name: str, adjusts: dict[str, Any]) -> list[dict[str, float]]:
        lead_margin = 16
        dx = end["x"] - start["x"]
        dy = end["y"] - start["y"]
        horizontal = abs(dx) >= abs(dy)
        if kind_name == "straightConnector1":
            if abs(start["y"] - end["y"]) <= 3 or abs(start["x"] - end["x"]) <= 3:
                return [start, end]
            if horizontal:
                return [start, {"x": end["x"], "y": start["y"]}, end]
            return [start, {"x": start["x"], "y": end["y"]}, end]
        if kind_name == "bentConnector2":
            return [start, {"x": start["x"], "y": end["y"]}, end]
        if kind_name == "bentConnector4":
            adj1 = adjusts.get("adj1", 50000) / 100000
            mid_x = start["x"] + (end["x"] - start["x"]) * adj1
            return [start, {"x": mid_x, "y": start["y"]}, {"x": mid_x, "y": end["y"]}, end]
        if horizontal:
            route_y = start["y"] + (lead_margin if dy >= 0 else -lead_margin)
            return [start, {"x": start["x"], "y": route_y}, {"x": end["x"], "y": route_y}, end]
        route_x = start["x"] + (lead_margin if dx >= 0 else -lead_margin)
        return [start, {"x": route_x, "y": start["y"]}, {"x": route_x, "y": end["y"]}, end]

    start_px = scale_point(extra.get("start_point_px"), scale_x, scale_y)
    end_px = scale_point(extra.get("end_point_px"), scale_x, scale_y)
    adjusts = extra.get("connector_adjusts") or {}
    if start_px and end_px:
        points = readable_elbow(start_px, end_px, kind, adjusts)
    elif kind == "straightConnector1":
        points = [
            {"x": abs_bounds["x"], "y": abs_bounds["y"] + local_height / 2},
            {"x": abs_bounds["x"] + local_width, "y": abs_bounds["y"] + local_height / 2},
        ]
    elif kind == "bentConnector2":
        points = [
            {"x": abs_bounds["x"], "y": abs_bounds["y"]},
            {"x": abs_bounds["x"], "y": abs_bounds["y"] + local_height},
            {"x": abs_bounds["x"] + local_width, "y": abs_bounds["y"] + local_height},
        ]
    elif kind == "bentConnector4":
        points = [
            {"x": abs_bounds["x"], "y": abs_bounds["y"]},
            {"x": abs_bounds["x"], "y": abs_bounds["y"] + local_height * 0.35},
            {"x": abs_bounds["x"] + local_width * 0.5, "y": abs_bounds["y"] + local_height * 0.35},
            {"x": abs_bounds["x"] + local_width * 0.5, "y": abs_bounds["y"] + local_height},
            {"x": abs_bounds["x"] + local_width, "y": abs_bounds["y"] + local_height},
        ]
    else:
        points = [
            {"x": abs_bounds["x"], "y": abs_bounds["y"]},
            {"x": abs_bounds["x"], "y": abs_bounds["y"] + local_height * 0.5},
            {"x": abs_bounds["x"] + local_width, "y": abs_bounds["y"] + local_height * 0.5},
            {"x": abs_bounds["x"] + local_width, "y": abs_bounds["y"] + local_height},
        ]

    min_x = min(point["x"] for point in points)
    min_y = min(point["y"] for point in points)
    max_x = max(point["x"] for point in points)
    max_y = max(point["y"] for point in points)
    arrow_margin = max(stroke_weight * 6, 8)
    absolute_bounds = make_bounds(
        min_x - arrow_margin / 2,
        min_y - arrow_margin / 2,
        (max_x - min_x) + arrow_margin,
        (max_y - min_y) + arrow_margin,
    )
    localized_points = [
        {
            "x": round(point["x"] - absolute_bounds["x"], 2),
            "y": round(point["y"] - absolute_bounds["y"], 2),
        }
        for point in points
    ]

    path = " ".join(("M" if i == 0 else "L") + f" {round(p['x'],2)} {round(p['y'],2)}" for i, p in enumerate(localized_points))
    fill_geometry: list[dict[str, Any]] = []
    tail_end = (shape_style.get("line") or {}).get("tail_end") or {}
    head_end = (shape_style.get("line") or {}).get("head_end") or {}

    def append_arrow(points_for_head: list[dict[str, float]], point_index: int, prev_index: int) -> None:
        tip = points_for_head[point_index]
        prev = points_for_head[prev_index]
        dx = tip["x"] - prev["x"]
        dy = tip["y"] - prev["y"]
        angle = math.atan2(dy, dx)
        size = max(8 * min(scale_x, scale_y), 6)
        back_x = tip["x"] - math.cos(angle) * size
        back_y = tip["y"] - math.sin(angle) * size
        left_x = back_x + math.cos(angle + math.pi / 2) * (size * 0.5)
        left_y = back_y + math.sin(angle + math.pi / 2) * (size * 0.5)
        right_x = back_x + math.cos(angle - math.pi / 2) * (size * 0.5)
        right_y = back_y + math.sin(angle - math.pi / 2) * (size * 0.5)
        fill_geometry.append({
            "path": f"M {tip['x']} {tip['y']} L {round(left_x,2)} {round(left_y,2)} L {round(right_x,2)} {round(right_y,2)} Z",
            "windingRule": "NONZERO",
        })

    if tail_end.get("type") == "triangle" and len(localized_points) >= 2:
        append_arrow(localized_points, -1, -2)
    if head_end.get("type") == "triangle" and len(localized_points) >= 2:
        append_arrow(localized_points, 0, 1)

    line_paint = build_stroke_array(shape_style, {"r": 0.35, "g": 0.35, "b": 0.35})
    return build_vector_node(
        candidate["candidate_id"],
        candidate.get("title") or candidate.get("subtype") or "connector",
        absolute_bounds,
        fill_geometry=fill_geometry,
        stroke_geometry=[{"path": path}],
        fills=[line_paint[0]] if line_paint else [],
        strokes=line_paint,
        stroke_weight=stroke_weight,
        debug={
            "source_path": candidate.get("source_path", ""),
            "source_node_id": candidate.get("source_node_id", ""),
            "source_subtype": candidate.get("subtype", ""),
            "rotation_degrees": normalize_degrees((candidate.get("bounds_px") or {}).get("rotation", 0)),
        },
        relative_transform=relative_transform,
    )


def build_image_node(candidate: dict[str, Any], abs_bounds: dict[str, Any], assets: dict[str, Any], scale: float = 1.0) -> dict[str, Any]:
    extra = candidate.get("extra") or {}
    image_ref = f"pptx-image:{candidate['candidate_id']}"
    image_base64 = extra.get("image_base64")
    if image_base64:
        assets[image_ref] = {
            "filename": f"{image_ref}.png",
            "mime_type": extra.get("mime_type") or "image/png",
            "base64": image_base64,
        }
    fills = [{"type": "IMAGE", "imageRef": image_ref, "scaleMode": "FILL"}] if image_base64 else [{"type": "SOLID", "color": {"r": 0.93, "g": 0.94, "b": 0.96}}]
    return {
        "id": candidate["candidate_id"],
        "type": "RECTANGLE",
        "name": candidate.get("title") or candidate.get("subtype") or "image",
        "absoluteBoundingBox": abs_bounds,
        "relativeTransform": relative_transform_from_bounds(candidate.get("bounds_px")),
        "fills": fills,
        "strokes": [{"type": "SOLID", "color": {"r": 0.64, "g": 0.68, "b": 0.74}}],
        "strokeWeight": max(scale, 1),
        "children": [],
        "debug": {
            "source_path": candidate.get("source_path", ""),
            "source_node_id": candidate.get("source_node_id", ""),
            "source_subtype": candidate.get("subtype", ""),
            "resolved_target": extra.get("resolved_target", ""),
        },
    }


def build_table_node(candidate: dict[str, Any], page_offset_x: float, page_offset_y: float, children_map: dict[str, list[dict[str, Any]]], scale_x: float, scale_y: float) -> dict[str, Any]:
    bounds = candidate.get("bounds_px") or {"x": 0, "y": 0, "width": 400, "height": 240}
    abs_bounds = make_bounds(page_offset_x + scale_value(bounds["x"], scale_x), page_offset_y + scale_value(bounds["y"], scale_y), scale_value(bounds["width"], scale_x), scale_value(bounds["height"], scale_y))
    extra = candidate.get("extra") or {}
    shape_style = extra.get("shape_style") or {}
    table_node = {
        "id": candidate["candidate_id"],
        "type": "FRAME",
        "name": candidate.get("title") or "table",
        "absoluteBoundingBox": abs_bounds,
        "relativeTransform": relative_transform_from_bounds(candidate.get("bounds_px")),
        "fills": build_fill_array(shape_style, {"r": 1, "g": 1, "b": 1}),
        "strokes": build_stroke_array(shape_style, {"r": 0.45, "g": 0.45, "b": 0.45}),
        "strokeWeight": max(float(((shape_style.get("line") or {}).get("width_px") or 1)) * min(scale_x, scale_y), 1.0),
        "children": [],
        "debug": {
            "source_path": candidate.get("source_path", ""),
            "source_node_id": candidate.get("source_node_id", ""),
            "source_subtype": candidate.get("subtype", ""),
        },
    }
    rows = sorted([child for child in children_map.get(candidate["candidate_id"], []) if child.get("subtype") == "table_row"], key=sort_by_position_key)
    grid_columns = extra.get("grid_columns") or []
    row_cursor_y = abs_bounds["y"]
    scaled_row_heights: dict[str, float] = {}
    for row_candidate in rows:
        scaled_row_heights[row_candidate["candidate_id"]] = max(scale_value((row_candidate.get("extra") or {}).get("row_height_px") or 28, scale_y), 21.0)

    for row_candidate in rows:
        cell_candidates = [child for child in children_map.get(row_candidate["candidate_id"], []) if child.get("subtype") == "table_cell"]
        row_height = scaled_row_heights[row_candidate["candidate_id"]]
        for cell_candidate in cell_candidates:
            cell_extra = cell_candidate.get("extra") or {}
            if cell_extra.get("h_merge") or cell_extra.get("v_merge"):
                continue
            cell_width = scale_value(cell_extra.get("width_px") or (abs_bounds["width"] / max(len(cell_candidates), 1)), scale_x if cell_extra.get("width_px") else 1.0)
            row_height = max(row_height, estimate_wrapped_height(cell_candidate.get("text") or cell_candidate.get("title") or "", cell_candidate, cell_width, row_height, min(scale_x, scale_y)))
        row_node = {
            "id": row_candidate["candidate_id"],
            "type": "FRAME",
            "name": row_candidate.get("title") or "row",
            "absoluteBoundingBox": make_bounds(abs_bounds["x"], row_cursor_y, abs_bounds["width"], row_height),
            "relativeTransform": relative_transform_from_bounds(row_candidate.get("bounds_px")),
            "fills": [],
            "strokes": [],
            "strokeWeight": 0,
            "children": [],
            "debug": {
                "source_path": row_candidate.get("source_path", ""),
                "source_node_id": row_candidate.get("source_node_id", ""),
                "source_subtype": row_candidate.get("subtype", ""),
            },
        }
        for cell_candidate in cell_candidates:
            cell_extra = cell_candidate.get("extra") or {}
            if cell_extra.get("h_merge") or cell_extra.get("v_merge"):
                continue
            start_column_index = int(cell_extra.get("start_column_index") or 1)
            cell_width = scale_value(cell_extra.get("width_px") or (row_node["absoluteBoundingBox"]["width"] / max(len(cell_candidates), 1)), scale_x if cell_extra.get("width_px") else 1.0)
            if grid_columns:
                cell_x = sum(scale_value(column.get("width_px") or 0, scale_x) for column in grid_columns if int(column.get("column_index") or 0) < start_column_index)
            else:
                cell_x = sum(float(child["absoluteBoundingBox"]["width"]) for child in row_node["children"])
            row_span = int(cell_extra.get("row_span") or 1)
            spanned_height = row_height
            if row_span > 1:
                current_index = rows.index(row_candidate)
                spanned_height = sum(scaled_row_heights[rows[i]["candidate_id"]] for i in range(current_index, min(current_index + row_span, len(rows))))
            cell_abs_bounds = make_bounds(row_node["absoluteBoundingBox"]["x"] + cell_x, row_node["absoluteBoundingBox"]["y"], cell_width, spanned_height)
            cell_style = cell_extra.get("cell_style") or {}
            fills = [solid_paint(cell_style.get("fill"), {"r": 1, "g": 1, "b": 1}, 1.0)] if cell_style.get("fill") else [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}]
            cell_node = {
                "id": cell_candidate["candidate_id"],
                "type": "FRAME",
                "name": cell_candidate.get("title") or "cell",
                "absoluteBoundingBox": cell_abs_bounds,
                "relativeTransform": relative_transform_from_bounds(cell_candidate.get("bounds_px")),
                "fills": fills,
                "strokes": [{"type": "SOLID", "color": {"r": 0.75, "g": 0.75, "b": 0.75}}],
                "strokeWeight": max(min(scale_x, scale_y), 1),
                "children": [],
                "debug": {
                    "source_path": cell_candidate.get("source_path", ""),
                    "source_node_id": cell_candidate.get("source_node_id", ""),
                    "source_subtype": cell_candidate.get("subtype", ""),
                },
            }
            if cell_candidate.get("text"):
                cell_node["children"].append(build_text_node(cell_candidate, cell_abs_bounds, force_wrap=True, table_cell=True, horizontal_fallback="l", vertical_fallback=(cell_style.get("anchor") or "ctr"), scale=min(scale_x, scale_y)))
            row_node["children"].append(cell_node)
        table_node["children"].append(row_node)
        row_cursor_y += row_height
    return table_node


def sort_by_position_key(candidate: dict[str, Any]) -> tuple[float, float]:
    bounds = candidate.get("bounds_px") or {}
    return (float(bounds.get("y", 0)), float(bounds.get("x", 0)))


def build_children_map(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        by_parent.setdefault(candidate.get("parent_candidate_id", ""), []).append(candidate)
    return by_parent


def build_node_from_candidate(candidate: dict[str, Any], page_offset_x: float, page_offset_y: float, children_map: dict[str, list[dict[str, Any]]], assets: dict[str, Any], scale_x: float, scale_y: float) -> dict[str, Any] | None:
    bounds = candidate.get("bounds_px") or {"x": 0, "y": 0, "width": 120, "height": 24}
    abs_bounds = make_bounds(page_offset_x + scale_value(bounds["x"], scale_x), page_offset_y + scale_value(bounds["y"], scale_y), scale_value(bounds["width"], scale_x), scale_value(bounds["height"], scale_y))
    subtype = candidate.get("subtype")
    node_type = candidate.get("node_type")

    if node_type == "asset" and subtype == "image":
        return build_image_node(candidate, abs_bounds, assets, min(scale_x, scale_y))
    if subtype == "text_block":
        return build_text_node(candidate, abs_bounds, force_wrap=False, table_cell=False, horizontal_fallback="l", vertical_fallback="t", scale=min(scale_x, scale_y))
    if subtype == "connector":
        return build_connector_node(candidate, abs_bounds, scale_x, scale_y)
    if subtype == "table":
        return build_table_node(candidate, page_offset_x, page_offset_y, children_map, scale_x, scale_y)
    if subtype in {"table_row", "table_cell"}:
        return None
    if subtype in {"group", "section_block"}:
        node = {
            "id": candidate["candidate_id"],
            "type": "GROUP",
            "name": candidate.get("title") or subtype or "group",
            "absoluteBoundingBox": abs_bounds,
            "relativeTransform": relative_transform_from_bounds(candidate.get("bounds_px")),
            "children": [],
            "debug": {
                "source_path": candidate.get("source_path", ""),
                "source_node_id": candidate.get("source_node_id", ""),
                "source_subtype": subtype,
            },
        }
        for child in sorted(children_map.get(candidate["candidate_id"], []), key=sort_by_position_key):
            child_node = build_node_from_candidate(child, page_offset_x, page_offset_y, children_map, assets, scale_x, scale_y)
            if child_node:
                node["children"].append(child_node)
        return node
    if subtype == "labeled_shape":
        extra = candidate.get("extra") or {}
        shape_kind = extra.get("shape_kind") or ""
        child_text = build_text_node(candidate, abs_bounds, force_wrap=False, table_cell=False, horizontal_fallback="ctr", vertical_fallback="ctr", scale=min(scale_x, scale_y))
        if shape_kind == "flowChartDecision":
            return {
                "id": candidate["candidate_id"],
                "type": "GROUP",
                "name": candidate.get("title") or subtype or "labeled_shape",
                "absoluteBoundingBox": abs_bounds,
                "relativeTransform": relative_transform_from_bounds(candidate.get("bounds_px")),
                "children": [build_shape_node(candidate, abs_bounds, min(scale_x, scale_y)), child_text],
                "debug": {
                    "source_path": candidate.get("source_path", ""),
                    "source_node_id": candidate.get("source_node_id", ""),
                    "source_subtype": subtype,
                },
            }
        shape_style = extra.get("shape_style") or {}
        return {
            "id": candidate["candidate_id"],
            "type": "FRAME",
            "name": candidate.get("title") or subtype or "labeled_shape",
            "absoluteBoundingBox": abs_bounds,
            "relativeTransform": relative_transform_from_bounds(candidate.get("bounds_px")),
            "fills": build_fill_array(shape_style, {"r": 1, "g": 1, "b": 1}),
            "strokes": build_stroke_array(shape_style, {"r": 0.28, "g": 0.28, "b": 0.28}),
            "strokeWeight": max(float(((shape_style.get("line") or {}).get("width_px") or 1)) * min(scale_x, scale_y), 1.0),
            "cornerRadius": 8 if shape_kind == "roundRect" else None,
            "children": [child_text],
            "debug": {
                "source_path": candidate.get("source_path", ""),
                "source_node_id": candidate.get("source_node_id", ""),
                "source_subtype": subtype,
            },
        }
    if subtype == "shape":
        return build_shape_node(candidate, abs_bounds, min(scale_x, scale_y))
    return None


def build_page_bundle(page: dict[str, Any], source_file: str) -> dict[str, Any]:
    assets: dict[str, Any] = {}
    scale_x, scale_y = build_page_scale(page)
    width = TARGET_SLIDE_WIDTH
    height = TARGET_SLIDE_HEIGHT
    page_frame = {
        "id": page.get("page_id") or f"page:{page.get('slide_no')}",
        "type": "FRAME",
        "name": f"Slide {page.get('slide_no')} - {page.get('title_or_label')}",
        "absoluteBoundingBox": make_bounds(0, 0, width, height),
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}}],
        "strokes": [{"type": "SOLID", "color": {"r": 0.82, "g": 0.82, "b": 0.82}}],
        "strokeWeight": 1,
        "children": [],
        "debug": {
            "source_path": page.get("source_path", ""),
            "source_node_id": page.get("page_id", ""),
            "source_subtype": "page",
        },
    }
    children_map = build_children_map(page.get("candidates") or [])
    roots = sorted(children_map.get(page.get("page_id"), []), key=sort_by_position_key)
    for candidate in roots:
        child = build_node_from_candidate(candidate, 0, 0, children_map, assets, scale_x, scale_y)
        if child:
            page_frame["children"].append(child)
    return {
        "kind": "figma-replay-bundle",
        "source_file": source_file,
        "file_name": Path(source_file).name,
        "page_name": page_frame["name"],
        "node_id": page_frame["id"],
        "document": page_frame,
        "assets": assets,
        "missing_assets": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build figma-replay-bundle JSON from PPT intermediate candidates.")
    parser.add_argument("--input", default="docs/ppt-intermediate-candidates-12-19-29.json", help="Intermediate candidates JSON path")
    parser.add_argument("--output-dir", default="docs/generated-replay-bundles", help="Output directory")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    source_file = str(input_path)
    for page in payload.get("pages") or []:
        bundle = build_page_bundle(page, source_file)
        slide_no = page.get("slide_no")
        output_path = output_dir / f"ppt-slide-{slide_no}.bundle.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, indent=2)
        print(f"saved {output_path}")


if __name__ == "__main__":
    main()
