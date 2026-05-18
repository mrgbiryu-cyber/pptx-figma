#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
import re
from typing import Any

from ppt_source_extractor import (
    build_page_context,
    iter_selected_pages,
    load_intermediate_payload,
    make_bounds,
    scale_bounds,
    scale_point,
)


def union_bounds(bounds_list: list[dict[str, float]]) -> dict[str, float]:
    if not bounds_list:
        return make_bounds(0.0, 0.0, 1.0, 1.0)
    min_x = min(float(bounds["x"]) for bounds in bounds_list)
    min_y = min(float(bounds["y"]) for bounds in bounds_list)
    max_x = max(float(bounds["x"]) + float(bounds["width"]) for bounds in bounds_list)
    max_y = max(float(bounds["y"]) + float(bounds["height"]) for bounds in bounds_list)
    return make_bounds(min_x, min_y, max_x - min_x, max_y - min_y)


ROW_ID_RE = re.compile(r":row_(\d+)$")
CELL_ID_RE = re.compile(r":row_(\d+):cell_(\d+)$")
RIGHT_PANEL_X_CUTOFF = 960.0 * 0.58


def parse_row_index(candidate_id: str) -> int | None:
    match = ROW_ID_RE.search(candidate_id)
    if not match:
        return None
    return int(match.group(1))


def parse_cell_indices(candidate_id: str) -> tuple[int | None, int | None]:
    match = CELL_ID_RE.search(candidate_id)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def infer_pattern_type(context: dict[str, Any]) -> str:
    base_page_type = str((context.get("visual_strategy") or {}).get("page_type") or "generic")
    if base_page_type != "ui-mockup":
        return base_page_type

    width = float(context.get("width") or 0.0)
    height = float(context.get("height") or 0.0)
    right_cutoff = width * 0.6
    right_table_count = 0
    right_text_count = 0
    right_shape_count = 0
    right_small_asset_count = 0

    for candidate in context.get("candidates") or []:
        bounds = candidate.get("bounds_px") or {}
        x = float(bounds.get("x") or 0.0)
        y = float(bounds.get("y") or 0.0)
        w = float(bounds.get("width") or 0.0)
        h = float(bounds.get("height") or 0.0)
        if x + w < right_cutoff:
            continue
        subtype = str(candidate.get("subtype") or "")
        if subtype == "table" and h >= height * 0.45:
            right_table_count += 1
        elif subtype == "text_block":
            right_text_count += 1
        elif subtype in {"shape", "labeled_shape"} and w >= 60 and h >= 12:
            right_shape_count += 1
        elif subtype == "image" or (w <= 40 and h <= 40):
            right_small_asset_count += 1

    if right_table_count >= 1 and right_text_count >= 2 and right_shape_count >= 4:
        return "dense_ui_panel"
    if right_table_count >= 1 and right_small_asset_count >= 4:
        return "dense_ui_panel"
    return base_page_type


def compute_table_row_bounds(
    candidate: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    children_map: dict[str, list[dict[str, Any]]],
) -> dict[str, float] | None:
    row_id = str(candidate.get("candidate_id") or "")
    table_id = str(candidate.get("parent_candidate_id") or "")
    if not row_id or not table_id:
        return None
    table_candidate = by_id.get(table_id)
    table_bounds = table_candidate.get("bounds_px") if table_candidate else None
    if not table_bounds:
        return None
    row_children = children_map.get(table_id) or []
    ordered_rows = sorted(
        [child for child in row_children if str(child.get("subtype") or "") == "table_row"],
        key=lambda child: parse_row_index(str(child.get("candidate_id") or "")) or 0,
    )
    current_y = float(table_bounds["y"])
    for row_candidate in ordered_rows:
        row_height = float(((row_candidate.get("extra") or {}).get("row_height_px")) or 0.0)
        if str(row_candidate.get("candidate_id") or "") == row_id:
            return make_bounds(float(table_bounds["x"]), current_y, float(table_bounds["width"]), row_height)
        current_y += row_height
    return None


def compute_table_cell_bounds(
    candidate: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    children_map: dict[str, list[dict[str, Any]]],
) -> dict[str, float] | None:
    cell_id = str(candidate.get("candidate_id") or "")
    row_id = str(candidate.get("parent_candidate_id") or "")
    if not cell_id or not row_id:
        return None
    row_candidate = by_id.get(row_id)
    table_id = str(row_candidate.get("parent_candidate_id") or "") if row_candidate else ""
    table_candidate = by_id.get(table_id) if table_id else None
    table_bounds = table_candidate.get("bounds_px") if table_candidate else None
    table_extra = table_candidate.get("extra") if table_candidate else {}
    if not table_bounds or not table_extra:
        return None

    row_bounds = compute_table_row_bounds(row_candidate, by_id, children_map) if row_candidate else None
    if not row_bounds:
        return None

    row_index, _ = parse_cell_indices(cell_id)
    cell_extra = candidate.get("extra") or {}
    start_column_index = int(cell_extra.get("start_column_index") or 1)
    grid_span = int(cell_extra.get("grid_span") or 1)
    row_span = int(cell_extra.get("row_span") or 1)
    grid_columns = table_extra.get("grid_columns") or []

    current_x = float(table_bounds["x"])
    cell_x = current_x
    cell_width = 0.0
    for column in grid_columns:
        column_index = int(column.get("column_index") or 0)
        width_px = float(column.get("width_px") or 0.0)
        if column_index < start_column_index:
            current_x += width_px
            continue
        if column_index == start_column_index:
            cell_x = current_x
        if start_column_index <= column_index < start_column_index + grid_span:
            cell_width += width_px
            current_x += width_px
            continue
        break

    row_children = children_map.get(table_id) or []
    ordered_rows = sorted(
        [child for child in row_children if str(child.get("subtype") or "") == "table_row"],
        key=lambda child: parse_row_index(str(child.get("candidate_id") or "")) or 0,
    )
    row_height = 0.0
    current_row_index = row_index or (parse_row_index(row_id) or 1)
    for row_candidate in ordered_rows:
        ordered_index = parse_row_index(str(row_candidate.get("candidate_id") or "")) or 0
        if current_row_index <= ordered_index < current_row_index + row_span:
            row_height += float(((row_candidate.get("extra") or {}).get("row_height_px")) or 0.0)

    return make_bounds(cell_x, float(row_bounds["y"]), cell_width, row_height)


def resolve_candidate_bounds(
    candidate: dict[str, Any],
    context: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    children_map: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    direct_bounds = candidate.get("bounds_px")
    if direct_bounds:
        return direct_bounds

    subtype = str(candidate.get("subtype") or "")
    if subtype == "table_row":
        return compute_table_row_bounds(candidate, by_id, children_map)
    if subtype == "table_cell":
        return compute_table_cell_bounds(candidate, by_id, children_map)
    return None


def atom_type(candidate: dict[str, Any]) -> str:
    subtype = str(candidate.get("subtype") or "")
    if subtype == "text_block":
        return "text_row"
    if subtype == "table":
        return "semantic_table"
    if subtype == "table_cell":
        return "table_cell"
    if subtype == "connector":
        return "connector"
    if subtype == "image":
        return "image_asset"
    if subtype in {"shape", "labeled_shape"}:
        return "background_card"
    if subtype in {"group", "section_block"}:
        return "container"
    return subtype or "unknown"


def render_mode(candidate: dict[str, Any], page_type: str) -> str:
    subtype = str(candidate.get("subtype") or "")
    if subtype == "text_block":
        return "native_text"
    if subtype == "connector":
        return "vector"
    if subtype == "image":
        return "image_asset"
    if subtype == "table":
        if page_type in {"table-heavy", "dense_ui_panel"}:
            return "semantic_table"
        return "svg_block"
    if subtype == "table_cell":
        return "lane_text" if page_type == "dense_ui_panel" else "semantic_cell"
    if subtype in {"shape", "labeled_shape"}:
        return "native_shape"
    return "native_shape"


def layer_role(candidate: dict[str, Any], page_type: str) -> str:
    subtype = str(candidate.get("subtype") or "")
    text_value = str(candidate.get("text") or "").strip()
    bounds = candidate.get("bounds_px") or {}
    width = float(bounds.get("width") or 0)
    height = float(bounds.get("height") or 0)
    x = float(bounds.get("x") or 0)
    y = float(bounds.get("y") or 0)
    source_scope = str(((candidate.get("extra") or {}).get("source_scope") or "slide")).lower()

    candidate_id = str(candidate.get("candidate_id") or "")

    if page_type == "dense_ui_panel":
        if subtype == "table":
            # Dense UI panels can contain both:
            # - small layout/master meta tables spanning the header band
            # - oversized slide-level description tables on the right panel
            if source_scope in {"master", "layout"} or (y < 80 and width > 500 and height < 120):
                return "top_meta_table"
            return "description_table"
        if subtype == "table_row":
            row_index = parse_row_index(candidate_id) or 0
            if source_scope in {"master", "layout"} or (y < 80 and width > 500):
                return "top_meta_row"
            return "description_header_row" if row_index <= 2 else "description_lane_row"
        if subtype == "table_cell":
            row_index, cell_index = parse_cell_indices(candidate_id)
            if source_scope in {"master", "layout"}:
                if (cell_index or 0) >= 3:
                    return "top_meta_info_cell"
                return "top_meta_band_cell"
            if row_index in {1, 2}:
                return "description_header_cell"
            if row_index in {3, 4, 5} and cell_index == 1:
                return "description_marker"
            if row_index in {3, 4, 5} and cell_index == 2:
                return "description_text_lane"
            if row_index == 6:
                return "description_footer"
        if subtype in {"image"} or (width <= 40 and height <= 40):
            return "small_asset"
        if subtype == "connector":
            return "overlay_mark"
        if subtype == "labeled_shape" and text_value.startswith("ISSUE"):
            return "issue_card"
        if subtype == "labeled_shape" and (text_value.startswith("V ") or text_value.startswith("V.")):
            if width < 220 and y < 220:
                return "version_stack"
            if width >= 230 and x >= 680:
                return "description_card"
        if subtype == "text_block":
            if x >= 650 and y < 260 and width >= 180:
                return "top_text_row"
            if x >= 650 and width >= 180:
                return "description_text_lane"
            if x >= 650:
                return "overlay_note"
        if subtype in {"shape", "labeled_shape"} and width >= 120 and height >= 20:
            return "background_card"

    if page_type == "table-heavy":
        if subtype == "table":
            return "table_root"
        if subtype == "table_cell":
            return "table_cell"
        if subtype == "text_block":
            return "table_text" if x > 200 else "section_label"
        if subtype == "connector":
            return "overlay_mark"

    if page_type == "flow-process":
        if subtype == "connector":
            return "connector"
        if subtype == "text_block":
            return "flow_label"
        if subtype in {"shape", "labeled_shape"}:
            return "flow_shape"

    if subtype == "text_block":
        return "text"
    if subtype == "connector":
        return "connector"
    if subtype == "image":
        return "image"
    return subtype or "unknown"


def z_index(layer_role_value: str) -> int:
    order = {
        "background_card": 10,
        "description_card": 12,
        "version_stack": 14,
        "issue_card": 16,
        "top_meta_table": 16,
        "top_meta_row": 16,
        "top_meta_band_cell": 18,
        "top_meta_info_cell": 18,
        "description_header_row": 18,
        "description_header_cell": 20,
        "top_text_row": 20,
        "description_text_lane": 22,
        "description_footer": 22,
        "description_marker": 24,
        "description_lane_row": 24,
        "overlay_note": 26,
        "table_root": 24,
        "table_cell": 26,
        "table_text": 28,
        "small_asset": 30,
        "overlay_mark": 32,
        "connector": 34,
        "flow_shape": 12,
        "flow_label": 18,
        "text": 20,
        "image": 24,
        "unknown": 20,
    }
    return order.get(layer_role_value, 20)


def clip_scope(candidate: dict[str, Any], page_type: str) -> str:
    subtype = str(candidate.get("subtype") or "")
    if page_type == "dense_ui_panel" and subtype in {"table", "table_cell", "text_block", "shape", "labeled_shape", "image"}:
        return "dense_ui_panel"
    return "page"


def dense_panel_asset_scope(visual_bounds: dict[str, Any] | None, context: dict[str, Any]) -> str:
    if not visual_bounds:
        return "global_ui"
    width = float(context.get("width") or 960.0)
    height = float(context.get("height") or 540.0)
    x = float(visual_bounds.get("x") or 0.0)
    y = float(visual_bounds.get("y") or 0.0)
    w = float(visual_bounds.get("width") or 0.0)
    h = float(visual_bounds.get("height") or 0.0)
    cx = x + (w / 2.0)
    cy = y + (h / 2.0)
    panel_left = width * 0.66
    panel_top = height * 0.04
    panel_bottom = height * 0.97
    # Dense right-side panel assets should sit inside the right third of the
    # slide, but previous heuristics leaked a few panel badges/icons into the
    # global bucket because their source bounds were slightly inset.
    if (x >= panel_left or cx >= width * 0.72) and (y + h) >= panel_top and cy <= panel_bottom:
        return "panel_local"
    return "global_ui"


def owner_key(
    candidate: dict[str, Any],
    page_type: str,
    *,
    role: str | None = None,
    visual_bounds: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    subtype = str(candidate.get("subtype") or "")
    candidate_id = str(candidate.get("candidate_id") or "")
    parent_id = str(candidate.get("parent_candidate_id") or "")
    role = role or layer_role(candidate, page_type)
    bounds = candidate.get("bounds_px") or {}
    x = float(bounds.get("x") or 0.0)

    if page_type == "dense_ui_panel":
        if role == "top_meta_table":
            return "dense_ui_panel:top_meta_group"
        if role == "top_meta_row":
            return "dense_ui_panel:top_meta_rows"
        if role == "top_meta_band_cell":
            return "dense_ui_panel:top_meta_band_cells"
        if role == "top_meta_info_cell":
            return "dense_ui_panel:top_meta_info_cells"
        if role == "description_header_row":
            return "dense_ui_panel:description_header_rows"
        if role == "description_header_cell":
            return "dense_ui_panel:description_headers"
        if role == "top_text_row":
            return "dense_ui_panel:top_rows"
        if role == "version_stack":
            return "dense_ui_panel:version_stack"
        if role == "issue_card":
            return "dense_ui_panel:issue_card"
        if role == "description_card":
            return "dense_ui_panel:description_cards"
        if role == "description_text_lane":
            return "dense_ui_panel:description_lanes"
        if role == "description_footer":
            return "dense_ui_panel:description_footer"
        if role == "description_marker":
            return "dense_ui_panel:description_markers"
        if role == "description_lane_row":
            return "dense_ui_panel:description_lane_rows"
        if role == "small_asset":
            if context is not None and dense_panel_asset_scope(visual_bounds, context) == "panel_local":
                return "dense_ui_panel:panel_small_assets"
            return "dense_ui_panel:global_ui_assets"
        if role == "description_table":
            return "dense_ui_panel:description_table"
        if role == "overlay_note":
            if context is not None and dense_panel_asset_scope(visual_bounds, context) == "panel_local":
                return "dense_ui_panel:panel_overlay_notes"
            return "dense_ui_panel:global_ui_assets"

    if subtype == "table_cell" and parent_id:
        return f"owner:{parent_id}"
    if parent_id:
        return f"owner:{parent_id}"
    return f"owner:{candidate_id}"


def group_key(atom: dict[str, Any]) -> str:
    page_type = str(atom.get("pattern_type") or "")
    role = str(atom.get("layer_role") or "")
    owner_id = str(atom.get("owner_id") or "")

    if page_type == "dense_ui_panel":
        if role in {"top_meta_table", "top_meta_row", "top_meta_band_cell"}:
            return "dense_ui_panel:top_meta_group"
        if role == "top_meta_info_cell":
            return "dense_ui_panel:top_meta_info_group"
        if role == "version_stack":
            return "dense_ui_panel:version_stack_group"
        if role == "issue_card":
            return "dense_ui_panel:issue_group"
        if role == "top_text_row":
            return "dense_ui_panel:top_rows_group"
        if role in {"description_header_row", "description_header_cell"}:
            return "dense_ui_panel:description_header_group"
        if role in {
            "description_card",
        }:
            return "dense_ui_panel:description_card_group"
        if role in {
            "description_text_lane",
            "description_footer",
            "description_marker",
        }:
            return "dense_ui_panel:description_body_text_group"
        if role in {
            "description_lane_row",
            "description_table",
        }:
            return "dense_ui_panel:description_body_semantic_group"
        if role == "overlay_note":
            if owner_id == "dense_ui_panel:panel_overlay_notes":
                return "dense_ui_panel:panel_small_asset_group"
            return "dense_ui_panel:global_ui_asset_group"
        if role in {"small_asset", "overlay_mark"}:
            if owner_id == "dense_ui_panel:panel_small_assets":
                return "dense_ui_panel:panel_small_asset_group"
            return "dense_ui_panel:global_ui_asset_group"

    if owner_id:
        return owner_id
    return f"group:{atom.get('id')}"


def classify_chunk_type(chunk_id: str, roles: list[str]) -> str:
    if chunk_id.endswith("top_meta_band_chunk"):
        return "header_band"
    if chunk_id.endswith("top_meta_info_chunk"):
        return "meta_grid"
    if chunk_id.endswith("top_rows_chunk"):
        return "top_text_rows"
    if chunk_id.endswith("description_header_chunk"):
        return "description_header"
    if chunk_id.endswith("description_body_chunk"):
        return "body_text_region"
    if chunk_id.endswith("description_footer_chunk"):
        return "footer_note_overlay"
    if chunk_id.endswith("issue_chunk"):
        return "issue_card"
    if chunk_id.endswith("version_stack_chunk"):
        return "stacked_badges"
    if chunk_id.endswith("annotation_overlay_chunk"):
        return "annotation_overlay"
    if chunk_id.endswith("panel_small_assets_chunk"):
        return "panel_local_assets"
    if chunk_id.endswith("global_ui_assets_chunk"):
        return "global_ui_assets"
    if any(role in {"description_text_lane", "description_footer", "description_marker"} for role in roles):
        return "body_text_region"
    return "generic_chunk"


def chunk_features(atoms: list[dict[str, Any]], bounds: dict[str, Any]) -> dict[str, Any]:
    atom_types = {str(atom.get("atom_type") or "") for atom in atoms}
    roles = [str(atom.get("layer_role") or "") for atom in atoms]
    width = float(bounds.get("width") or 0.0)
    height = float(bounds.get("height") or 0.0)
    fill_count = 0
    stroke_count = 0
    text_char_count = 0
    small_atom_count = 0
    panel_local_count = 0
    global_asset_count = 0
    for atom in atoms:
        shape_style = atom.get("shape_style") or {}
        cell_style = atom.get("cell_style") or {}
        if shape_style.get("fill") or cell_style.get("fill"):
            fill_count += 1
        if (shape_style.get("line") or {}).get("kind") not in {None, "none"}:
            stroke_count += 1
        text_char_count += len(str(atom.get("text") or "").strip())
        atom_bounds = atom.get("visual_bounds_px") or {}
        if float(atom_bounds.get("width") or 0.0) <= 40.0 and float(atom_bounds.get("height") or 0.0) <= 40.0:
            small_atom_count += 1
        owner_id = str(atom.get("owner_id") or "")
        if owner_id in {"dense_ui_panel:panel_small_assets", "dense_ui_panel:panel_overlay_notes"}:
            panel_local_count += 1
        if owner_id == "dense_ui_panel:global_ui_assets":
            global_asset_count += 1
    return {
        "atom_type_count": len(atom_types),
        "text_char_count": text_char_count,
        "shape_fill_count": fill_count,
        "stroke_count": stroke_count,
        "small_atom_count": small_atom_count,
        "panel_local_asset_count": panel_local_count,
        "global_asset_count": global_asset_count,
        "has_table_backing": any(role.startswith("description_") or role.startswith("top_meta_") for role in roles),
        "is_narrow_panel_region": width <= 240.0,
        "repeated_colored_band_pattern": fill_count >= 3 and width <= 240.0 and height >= 80.0,
    }


def chunk_render_strategy(chunk_type: str) -> str:
    mapping = {
        "header_band": "frame_text_grid",
        "meta_grid": "frame_text_grid",
        "top_text_rows": "leaf_text_overlay",
        "description_header": "frame_text_grid",
        "body_text_region": "chunk_container_leaf_text",
        "footer_note_overlay": "leaf_text_overlay",
        "annotation_overlay": "leaf_text_overlay",
        "issue_card": "frame_vector_text",
        "stacked_badges": "group_vector_text",
        "panel_local_assets": "absolute_atom_overlay",
        "global_ui_assets": "page_level_assets",
    }
    return mapping.get(chunk_type, "generic_native")


def chunk_text_composition(chunk_type: str) -> str:
    mapping = {
        "header_band": "single_or_line",
        "meta_grid": "cell_leaf",
        "top_text_rows": "paragraph_or_line",
        "description_header": "cell_leaf",
        "body_text_region": "paragraph_or_line_leaf",
        "footer_note_overlay": "paragraph_or_line_leaf",
        "annotation_overlay": "paragraph_or_line_leaf",
        "issue_card": "short_label",
        "stacked_badges": "badge_label",
        "panel_local_assets": "none",
        "global_ui_assets": "none",
    }
    return mapping.get(chunk_type, "single_node")


def chunk_style_policy(chunk_type: str) -> str:
    mapping = {
        "header_band": "native_shape_first",
        "meta_grid": "native_shape_first",
        "top_text_rows": "text_only_overlay",
        "description_header": "native_shape_first",
        "body_text_region": "preserve_dense_background_overlay_text",
        "footer_note_overlay": "preserve_dense_background_overlay_text",
        "annotation_overlay": "text_only_overlay",
        "issue_card": "source_shape_style_priority",
        "stacked_badges": "source_shape_style_priority",
        "panel_local_assets": "source_asset_style_priority",
        "global_ui_assets": "source_asset_style_priority",
    }
    return mapping.get(chunk_type, "source_first")


def chunk_asset_scope(chunk_type: str) -> str:
    mapping = {
        "panel_local_assets": "panel_local",
        "global_ui_assets": "global_ui",
    }
    return mapping.get(chunk_type, "panel")


def chunk_composition_policy(chunk_type: str) -> str:
    mapping = {
        "header_band": "preserve",
        "meta_grid": "preserve",
        "top_text_rows": "preserve",
        "description_header": "preserve",
        "body_text_region": "preserve",
        "footer_note_overlay": "overlay",
        "annotation_overlay": "overlay",
        "issue_card": "overlay",
        "stacked_badges": "overlay",
        "panel_local_assets": "overlay",
        "global_ui_assets": "exclude",
    }
    return mapping.get(chunk_type, "preserve")


def chunk_key_from_role(role: str, owner_id: str, page_type: str) -> str:
    if page_type == "dense_ui_panel":
        if role in {"top_meta_table", "top_meta_row", "top_meta_band_cell"}:
            return "dense_ui_panel:top_meta_band_chunk"
        if role == "top_meta_info_cell":
            return "dense_ui_panel:top_meta_info_chunk"
        if role == "top_text_row":
            return "dense_ui_panel:top_rows_chunk"
        if role in {"description_header_row", "description_header_cell"}:
            return "dense_ui_panel:description_header_chunk"
        if role == "description_footer":
            return "dense_ui_panel:description_footer_chunk"
        if role in {
            "description_card",
            "description_text_lane",
            "description_marker",
            "description_lane_row",
            "description_table",
        }:
            return "dense_ui_panel:description_body_chunk"
        if role == "issue_card":
            return "dense_ui_panel:issue_chunk"
        if role == "version_stack":
            return "dense_ui_panel:version_stack_chunk"
        if role == "overlay_note":
            if owner_id == "dense_ui_panel:panel_overlay_notes":
                return "dense_ui_panel:annotation_overlay_chunk"
            return "dense_ui_panel:global_ui_assets_chunk"
        if role in {"small_asset", "overlay_mark"}:
            if owner_id in {"dense_ui_panel:panel_small_assets", "dense_ui_panel:panel_overlay_notes"}:
                return "dense_ui_panel:panel_small_assets_chunk"
            return "dense_ui_panel:global_ui_assets_chunk"
    return owner_id or f"chunk:{role or 'unknown'}"


def build_atom(
    candidate: dict[str, Any],
    context: dict[str, Any],
    pattern_type: str,
    by_id: dict[str, dict[str, Any]],
    children_map: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    raw_bounds = resolve_candidate_bounds(candidate, context, by_id, children_map)
    scaled = scale_bounds(raw_bounds, context["scale_x"], context["scale_y"])
    start_point = scale_point(((candidate.get("extra") or {}).get("start_point_px")), context["scale_x"], context["scale_y"])
    end_point = scale_point(((candidate.get("extra") or {}).get("end_point_px")), context["scale_x"], context["scale_y"])
    extra = candidate.get("extra") or {}
    role = layer_role(candidate, pattern_type)
    owner_id = owner_key(candidate, pattern_type, role=role, visual_bounds=scaled, context=context)
    source_order_path = list(candidate.get("source_order_path") or [])
    return {
        "id": str(candidate.get("candidate_id") or ""),
        "parent_id": str(candidate.get("parent_candidate_id") or ""),
        "source_scope": str(extra.get("source_scope") or "slide"),
        "source_node_id": str(candidate.get("source_node_id") or ""),
        "atom_type": atom_type(candidate),
        "subtype": str(candidate.get("subtype") or ""),
        "pattern_type": pattern_type,
        "owner_id": owner_id,
        "chunk_id": chunk_key_from_role(
            role,
            owner_id,
            pattern_type,
        ),
        "layer_role": role,
        "z_index": z_index(role),
        "source_order_path": source_order_path,
        "clip_scope": clip_scope(candidate, pattern_type),
        "render_mode": render_mode(candidate, pattern_type),
        "text": str(candidate.get("text") or ""),
        "title": str(candidate.get("title") or ""),
        "source_bounds_px": raw_bounds,
        "visual_bounds_px": scaled,
        "start_point_px": start_point,
        "end_point_px": end_point,
        "shape_kind": str(extra.get("shape_kind") or ""),
        "placeholder": extra.get("placeholder"),
        "connector_adjusts": (extra.get("connector_adjusts") or []),
        "grid_columns": (extra.get("grid_columns") or []),
        "row_span": extra.get("row_span"),
        "grid_span": extra.get("grid_span"),
        "start_column_index": extra.get("start_column_index"),
        "row_height_px": extra.get("row_height_px"),
        "shape_style": (extra.get("shape_style") or {}),
        "image_base64": extra.get("image_base64"),
        "image_target": extra.get("image_target"),
        "resolved_target": extra.get("resolved_target"),
        "mime_type": extra.get("mime_type"),
        "text_style": (extra.get("text_style") or {}),
        "text_runs": (extra.get("text_runs") or []),
        "text_alignment": (extra.get("text_alignment") or {}),
        "cell_style": (extra.get("cell_style") or {}),
        "rendering": candidate.get("rendering") or {},
        "debug_tags": {
            "page_type": pattern_type,
            "source_page_type": str((context.get("visual_strategy") or {}).get("page_type") or "generic"),
            "source_path": candidate.get("source_path"),
            "source_order_path": source_order_path,
        },
    }


def build_owner_bucket(owner_id: str, atoms: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = union_bounds([atom["visual_bounds_px"] for atom in atoms if atom.get("visual_bounds_px")])
    roles = sorted({str(atom.get("layer_role") or "") for atom in atoms})
    render_modes = sorted({str(atom.get("render_mode") or "") for atom in atoms})
    return {
        "owner_id": owner_id,
        "pattern_type": atoms[0]["pattern_type"] if atoms else "generic",
        "layer_roles": roles,
        "render_modes": render_modes,
        "visual_bounds_px": bounds,
        "atom_ids": [atom["id"] for atom in atoms],
        "atom_count": len(atoms),
    }


def build_group_bucket(group_id: str, atoms: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = union_bounds([atom["visual_bounds_px"] for atom in atoms if atom.get("visual_bounds_px")])
    owner_ids = sorted({str(atom.get("owner_id") or "") for atom in atoms})
    roles = sorted({str(atom.get("layer_role") or "") for atom in atoms})
    return {
        "group_id": group_id,
        "pattern_type": atoms[0]["pattern_type"] if atoms else "generic",
        "owner_ids": owner_ids,
        "layer_roles": roles,
        "visual_bounds_px": bounds,
        "atom_ids": [atom["id"] for atom in atoms],
        "atom_count": len(atoms),
    }


def build_chunk_bucket(chunk_id: str, atoms: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = union_bounds([atom["visual_bounds_px"] for atom in atoms if atom.get("visual_bounds_px")])
    owner_ids = sorted({str(atom.get("owner_id") or "") for atom in atoms})
    roles = sorted({str(atom.get("layer_role") or "") for atom in atoms})
    chunk_type = classify_chunk_type(chunk_id, roles)
    render_strategy = chunk_render_strategy(chunk_type)
    text_composition = chunk_text_composition(chunk_type)
    style_policy = chunk_style_policy(chunk_type)
    asset_scope = chunk_asset_scope(chunk_type)
    composition_policy = chunk_composition_policy(chunk_type)
    features = chunk_features(atoms, bounds)
    return {
        "chunk_id": chunk_id,
        "chunk_type": chunk_type,
        "pattern_type": atoms[0]["pattern_type"] if atoms else "generic",
        "owner_ids": owner_ids,
        "layer_roles": roles,
        "visual_bounds_px": bounds,
        "atom_ids": [atom["id"] for atom in atoms],
        "atom_count": len(atoms),
        "render_strategy": render_strategy,
        "text_composition": text_composition,
        "style_policy": style_policy,
        "asset_scope": asset_scope,
        "composition_policy": composition_policy,
        "features": features,
    }


def build_page_ir(page: dict[str, Any], *, preserve_native_size: bool = False) -> dict[str, Any]:
    context = build_page_context(page, preserve_native_size=preserve_native_size)
    pattern_type = infer_pattern_type(context)
    by_id = {str(candidate.get("candidate_id") or ""): candidate for candidate in context["candidates"]}
    children_map = context["children_map"]
    atoms = [build_atom(candidate, context, pattern_type, by_id, children_map) for candidate in context["candidates"]]
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chunks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for atom in atoms:
        buckets[atom["owner_id"]].append(atom)
        groups[group_key(atom)].append(atom)
        chunks[str(atom.get("chunk_id") or "")].append(atom)
    owner_buckets = [build_owner_bucket(owner_id, grouped) for owner_id, grouped in sorted(buckets.items())]
    group_buckets = [build_group_bucket(group_id, grouped) for group_id, grouped in sorted(groups.items())]
    chunk_buckets = [build_chunk_bucket(chunk_id, grouped) for chunk_id, grouped in sorted(chunks.items())]
    return {
        "page_id": context["page_id"],
        "slide_no": context["slide_no"],
        "title": context["title"],
        "page_type": pattern_type,
        "source_page_type": (context.get("visual_strategy") or {}).get("page_type"),
        "slide_bounds_px": make_bounds(0.0, 0.0, context["width"], context["height"]),
        "signals": context["visual_strategy"]["signals"],
        "atoms": atoms,
        "owner_buckets": owner_buckets,
        "group_buckets": group_buckets,
        "chunk_buckets": chunk_buckets,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build resolved PPT IR from current intermediate payload.")
    parser.add_argument(
        "--input",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "ppt-intermediate-candidates-12-19-29.json"),
        help="Intermediate candidates JSON path",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "resolved-ppt-ir-12-19-29.json"),
        help="Output JSON path",
    )
    parser.add_argument("--slides", default="12,19,29", help="Comma-separated slide numbers")
    args = parser.parse_args()

    slide_numbers = {int(token.strip()) for token in args.slides.split(",") if token.strip()}
    payload = load_intermediate_payload(args.input)
    pages = list(iter_selected_pages(payload, slide_numbers))
    result = {
        "ir_version": "resolved-ppt-ir-v1",
        "source_kind": "ppt-intermediate",
        "source_file": str(Path(args.input).resolve()),
        "pages": [build_page_ir(page, preserve_native_size=True) for page in pages],
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(f"saved {output_path}")
    for page in result["pages"]:
        print(
            f"slide {page['slide_no']}: page_type={page['page_type']} atoms={len(page['atoms'])} owners={len(page['owner_buckets'])}"
        )


if __name__ == "__main__":
    main()
