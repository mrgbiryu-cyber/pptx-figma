#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path

from ppt_source_extractor import TARGET_SLIDE_HEIGHT, TARGET_SLIDE_WIDTH, identity_affine


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
        "absoluteBoundingBox": {"x": x, "y": y, "width": 260.0, "height": 18.0},
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


def load_bundle(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bundle_assets(*bundles: dict) -> dict:
    merged: dict = {}
    for bundle in bundles:
        merged.update(bundle.get("assets") or {})
    return merged


def find_full_frame(bundle: dict) -> dict:
    return bundle["document"]["children"][0]


def find_ir_logical_panel(bundle: dict) -> dict:
    return bundle["document"]["children"][0]["children"][0]["children"][0]


def load_ir_chunk_policies(repo_root: Path) -> dict[str, dict]:
    ir_path = repo_root / "docs" / "resolved-ppt-ir-12-19-29.json"
    with ir_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    page = next(page for page in payload.get("pages") or [] if int(page.get("slide_no") or 0) == 29)
    return {bucket["chunk_id"]: bucket for bucket in page.get("chunk_buckets") or []}


def find_chunk_children(ir_bundle: dict) -> dict[str, dict]:
    logical = find_ir_logical_panel(ir_bundle)
    return {child.get("id"): copy.deepcopy(child) for child in logical.get("children") or []}


def text_only_description_overlay(description_body_group: dict | None) -> dict | None:
    if description_body_group is None:
        return None
    overlay_children = []
    for child in description_body_group.get("children") or []:
        if str(child.get("name") or "").startswith("lane_row_"):
            overlay_children.append(copy.deepcopy(child))
    if not overlay_children:
        return None
    bounds_list = [child.get("absoluteBoundingBox") for child in overlay_children if child.get("absoluteBoundingBox")]
    if not bounds_list:
        return None
    min_x = min(float(b["x"]) for b in bounds_list)
    min_y = min(float(b["y"]) for b in bounds_list)
    max_x = max(float(b["x"]) + float(b["width"]) for b in bounds_list)
    max_y = max(float(b["y"]) + float(b["height"]) for b in bounds_list)
    return {
        "id": "dense_ui_panel:description_body_text_overlay_chunk",
        "type": "GROUP",
        "name": "description_body_text_overlay_chunk",
        "absoluteBoundingBox": {
            "x": round(min_x, 2),
            "y": round(min_y, 2),
            "width": round(max_x - min_x, 2),
            "height": round(max_y - min_y, 2),
        },
        "relativeTransform": identity_affine(),
        "children": overlay_children,
        "debug": {"role": "description_body_text_overlay_chunk"},
    }


def build_hybrid_frame(
    baseline_bundle: dict,
    ir_bundle: dict,
    chunk_policies: dict[str, dict],
    *,
    include_top_meta: bool = True,
    include_top_meta_band: bool = True,
    include_top_meta_info: bool = True,
    include_top_rows: bool = True,
    include_description_header: bool = True,
    include_description_text_overlay: bool = False,
    include_description_footer: bool = True,
    include_version_stack: bool = True,
    include_issue: bool = True,
    include_annotation_overlay: bool = True,
    include_small_assets: bool = True,
) -> dict:
    baseline_frame = copy.deepcopy(find_full_frame(baseline_bundle))
    children_by_id = find_chunk_children(ir_bundle)
    top_meta_band_group = children_by_id.get("dense_ui_panel:top_meta_band_chunk")
    top_meta_info_group = children_by_id.get("dense_ui_panel:top_meta_info_chunk")
    top_rows_group = children_by_id.get("dense_ui_panel:top_rows_chunk")
    description_header_group = children_by_id.get("dense_ui_panel:description_header_chunk")
    description_body_group = children_by_id.get("dense_ui_panel:description_body_chunk")
    description_text_overlay_group = text_only_description_overlay(description_body_group)
    description_footer_group = children_by_id.get("dense_ui_panel:description_footer_chunk")
    version_stack_group = children_by_id.get("dense_ui_panel:version_stack_chunk")
    issue_group = children_by_id.get("dense_ui_panel:issue_chunk")
    annotation_overlay_group = children_by_id.get("dense_ui_panel:annotation_overlay_chunk")
    small_asset_group = children_by_id.get("dense_ui_panel:panel_small_assets_chunk")
    footer_policy = str((chunk_policies.get("dense_ui_panel:description_footer_chunk") or {}).get("composition_policy") or "preserve")
    version_policy = str((chunk_policies.get("dense_ui_panel:version_stack_chunk") or {}).get("composition_policy") or "preserve")
    issue_policy = str((chunk_policies.get("dense_ui_panel:issue_chunk") or {}).get("composition_policy") or "preserve")
    annotation_policy = str((chunk_policies.get("dense_ui_panel:annotation_overlay_chunk") or {}).get("composition_policy") or "preserve")
    assets_policy = str((chunk_policies.get("dense_ui_panel:panel_small_assets_chunk") or {}).get("composition_policy") or "preserve")
    top_meta_band_policy = str((chunk_policies.get("dense_ui_panel:top_meta_band_chunk") or {}).get("composition_policy") or "preserve")
    top_meta_info_policy = str((chunk_policies.get("dense_ui_panel:top_meta_info_chunk") or {}).get("composition_policy") or "preserve")
    top_rows_policy = str((chunk_policies.get("dense_ui_panel:top_rows_chunk") or {}).get("composition_policy") or "preserve")
    desc_header_policy = str((chunk_policies.get("dense_ui_panel:description_header_chunk") or {}).get("composition_policy") or "preserve")

    name_parts = ["hybrid_full"]
    if include_top_meta:
        name_parts.append("meta")
        if include_top_meta_band and not include_top_meta_info:
            name_parts.append("band")
        if include_top_meta_info and not include_top_meta_band:
            name_parts.append("info")
    if include_top_rows:
        name_parts.append("rows")
    if include_description_header:
        name_parts.append("desc_header")
    if include_description_text_overlay:
        name_parts.append("desc_text")
    if include_description_footer:
        name_parts.append("footer")
    if include_version_stack:
        name_parts.append("version")
    if include_issue:
        name_parts.append("issue")
    if include_annotation_overlay:
        name_parts.append("notes")
    if include_small_assets:
        name_parts.append("assets")
    baseline_frame["name"] = "_".join(name_parts)
    rebuilt_children = []
    for child in baseline_frame.get("children") or []:
        child_name = child.get("name")
        if child_name == "top_meta_block":
            keep_baseline_top_meta = (
                (not include_top_meta or top_meta_band_policy == "preserve" or top_meta_info_policy == "preserve")
                and (not include_top_rows or top_rows_policy == "preserve")
            )
            if keep_baseline_top_meta:
                rebuilt_children.append(child)
            else:
                if include_top_meta and include_top_meta_band and top_meta_band_group is not None:
                    rebuilt_children.append(top_meta_band_group)
                if include_top_meta and include_top_meta_info and top_meta_info_group is not None:
                    rebuilt_children.append(top_meta_info_group)
                if include_top_rows and top_rows_group is not None:
                    rebuilt_children.append(top_rows_group)
            if include_version_stack and version_stack_group is not None and version_policy in {"overlay", "replace"}:
                rebuilt_children.append(version_stack_group)
            continue
        if child_name != "right_panel_block":
            rebuilt_children.append(child)
            continue
        right_panel = copy.deepcopy(child)
        keep_baseline_right_panel = desc_header_policy == "preserve"
        right_panel_children = []
        if keep_baseline_right_panel:
            for panel_child in right_panel.get("children") or []:
                panel_name = str(panel_child.get("name") or "")
                if include_description_text_overlay and panel_name == "right_panel_block:description":
                    continue
                right_panel_children.append(panel_child)
        if not keep_baseline_right_panel:
            for panel_child in right_panel.get("children") or []:
                panel_name = str(panel_child.get("name") or "")
                if panel_name == "표 48":
                    continue
                right_panel_children.append(panel_child)
        if include_description_header and description_header_group is not None and desc_header_policy == "replace":
            right_panel_children.append(description_header_group)
        if include_description_text_overlay and description_text_overlay_group is not None:
            right_panel_children.append(description_text_overlay_group)
        if include_description_footer and description_footer_group is not None and footer_policy in {"overlay", "replace"}:
            right_panel_children.append(description_footer_group)
        if include_issue and issue_group is not None and issue_policy in {"overlay", "replace"}:
            right_panel_children.append(issue_group)
        if include_annotation_overlay and annotation_overlay_group is not None and annotation_policy in {"overlay", "replace"}:
            right_panel_children.append(annotation_overlay_group)
        if include_small_assets and small_asset_group is not None and assets_policy in {"overlay", "replace"}:
            right_panel_children.append(small_asset_group)
        right_panel["children"] = right_panel_children
        rebuilt_children.append(right_panel)
    baseline_frame["children"] = rebuilt_children
    return baseline_frame


def build_hybrid_bundle(
    baseline_bundle: dict,
    ir_bundle: dict,
    chunk_policies: dict[str, dict],
    out_path: Path,
    *,
    include_top_meta: bool = True,
    include_top_meta_band: bool = True,
    include_top_meta_info: bool = True,
    include_top_rows: bool = True,
    include_description_header: bool = True,
    include_description_text_overlay: bool = False,
    include_description_footer: bool = True,
    include_version_stack: bool = True,
    include_issue: bool = True,
    include_annotation_overlay: bool = True,
    include_small_assets: bool = True,
) -> None:
    hybrid_bundle = copy.deepcopy(baseline_bundle)
    hybrid_bundle["page_name"] = "Slide 29 - Full Style Hybrid"
    hybrid_bundle["node_id"] = "page:29:full-style-hybrid"
    hybrid_bundle["visual_model_version"] = "dense-ui-style-hybrid-v1"
    hybrid_bundle["source_kind"] = "ppt-full-style-hybrid"
    hybrid_bundle["file_name"] = out_path.name
    hybrid_bundle["document"]["id"] = "page:29:full-style-hybrid"
    hybrid_bundle["document"]["name"] = "Slide 29 - Full Style Hybrid"
    hybrid_bundle["document"]["children"][0] = build_hybrid_frame(
        baseline_bundle,
        ir_bundle,
        chunk_policies,
        include_top_meta=include_top_meta,
        include_top_meta_band=include_top_meta_band,
        include_top_meta_info=include_top_meta_info,
        include_top_rows=include_top_rows,
        include_description_header=include_description_header,
        include_description_text_overlay=include_description_text_overlay,
        include_description_footer=include_description_footer,
        include_version_stack=include_version_stack,
        include_issue=include_issue,
        include_annotation_overlay=include_annotation_overlay,
        include_small_assets=include_small_assets,
    )
    hybrid_bundle["assets"] = bundle_assets(baseline_bundle, ir_bundle)
    hybrid_bundle["debug"] = {"status": "page29_full_style_hybrid"}
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(hybrid_bundle, handle, ensure_ascii=False, indent=2)


def build_compare_bundle(baseline_bundle: dict, hybrid_bundle: dict, out_path: Path) -> None:
    gap = 40.0
    top_pad = 28.0
    total_width = TARGET_SLIDE_WIDTH * 2 + gap
    total_height = TARGET_SLIDE_HEIGHT + top_pad
    compare_children = [
        make_label_node("compare:baseline:label", "baseline_full", 8.0, 6.0),
        shift_node(find_full_frame(baseline_bundle), "compare:baseline", 0.0, top_pad),
        make_label_node("compare:hybrid:label", hybrid_bundle["document"]["children"][0]["name"], TARGET_SLIDE_WIDTH + gap + 8.0, 6.0),
        shift_node(find_full_frame(hybrid_bundle), "compare:hybrid", TARGET_SLIDE_WIDTH + gap, top_pad),
    ]

    inner_frame = {
        "id": "page:29:full-style-hybrid-compare:frame",
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
        "id": "page:29:full-style-hybrid-compare",
        "type": "FRAME",
        "name": "Slide 29 Full Style Hybrid Compare",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {"generator": "page29-full-style-hybrid-compare"},
    }
    hybrid_assets = bundle_assets(baseline_bundle, hybrid_bundle)
    compare_bundle = {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-full-style-hybrid-compare",
        "visual_model_version": "dense-ui-style-hybrid-compare-v1",
        "source_file": str(out_path),
        "file_name": out_path.name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": hybrid_assets,
        "missing_assets": [],
        "debug": {"status": "page29_full_style_hybrid_compare"},
    }
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(compare_bundle, handle, ensure_ascii=False, indent=2)


def build_axis_compare_bundle(baseline_bundle: dict, ir_bundle: dict, chunk_policies: dict[str, dict], out_path: Path) -> None:
    gap = 40.0
    top_pad = 28.0
    variants = [
        ("baseline_full", find_full_frame(baseline_bundle)),
        (
            "ir_top_meta_band_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=True,
                include_top_meta_band=True,
                include_top_meta_info=False,
                include_top_rows=False,
                include_description_header=False,
                include_description_footer=False,
                include_version_stack=False,
                include_issue=False,
                include_annotation_overlay=False,
                include_small_assets=False,
            ),
        ),
        (
            "ir_top_meta_info_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=True,
                include_top_meta_band=False,
                include_top_meta_info=True,
                include_top_rows=False,
                include_description_header=False,
                include_description_footer=False,
                include_version_stack=False,
                include_issue=False,
                include_annotation_overlay=False,
                include_small_assets=False,
            ),
        ),
        (
            "ir_version_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=False,
                include_top_rows=False,
                include_description_header=False,
                include_description_footer=False,
                include_version_stack=True,
                include_issue=False,
                include_annotation_overlay=False,
                include_small_assets=False,
            ),
        ),
        (
            "ir_desc_header_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=False,
                include_top_rows=False,
                include_description_header=True,
                include_description_footer=False,
                include_version_stack=False,
                include_issue=False,
                include_annotation_overlay=False,
                include_small_assets=False,
            ),
        ),
        (
            "ir_footer_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=False,
                include_top_rows=False,
                include_description_header=False,
                include_description_footer=True,
                include_version_stack=False,
                include_issue=False,
                include_annotation_overlay=False,
                include_small_assets=False,
            ),
        ),
        (
            "ir_issue_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=False,
                include_top_rows=False,
                include_description_header=False,
                include_description_footer=False,
                include_version_stack=False,
                include_issue=True,
                include_annotation_overlay=False,
                include_small_assets=False,
            ),
        ),
        (
            "ir_notes_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=False,
                include_top_rows=False,
                include_description_header=False,
                include_description_footer=False,
                include_version_stack=False,
                include_issue=False,
                include_annotation_overlay=True,
                include_small_assets=False,
            ),
        ),
        (
            "ir_assets_only",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=False,
                include_top_rows=False,
                include_description_header=False,
                include_description_footer=False,
                include_version_stack=False,
                include_issue=False,
                include_annotation_overlay=False,
                include_small_assets=True,
            ),
        ),
        (
            "ir_meta_version_issue_assets",
            build_hybrid_frame(
                baseline_bundle,
                ir_bundle,
                chunk_policies,
                include_top_meta=True,
                include_top_rows=True,
                include_description_header=True,
                include_description_footer=True,
                include_version_stack=True,
                include_issue=True,
                include_annotation_overlay=True,
                include_small_assets=True,
            ),
        ),
    ]
    total_width = TARGET_SLIDE_WIDTH * len(variants) + gap * (len(variants) - 1)
    total_height = TARGET_SLIDE_HEIGHT + top_pad
    compare_children: list[dict[str, Any]] = []
    for index, (label, frame) in enumerate(variants):
        dx = index * (TARGET_SLIDE_WIDTH + gap)
        compare_children.append(make_label_node(f"compare:axis:{index}:label", label, dx + 8.0, 6.0))
        compare_children.append(shift_node(frame, f"compare:axis:{index}", dx, top_pad))

    inner_frame = {
        "id": "page:29:full-style-axis-compare:frame",
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
        "id": "page:29:full-style-axis-compare",
        "type": "FRAME",
        "name": "Slide 29 Full Style Axis Compare",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {"generator": "page29-full-style-axis-compare"},
    }
    compare_bundle = {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-full-style-axis-compare",
        "visual_model_version": "dense-ui-style-axis-compare-v1",
        "source_file": str(out_path),
        "file_name": out_path.name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": bundle_assets(baseline_bundle, ir_bundle),
        "missing_assets": [],
        "debug": {"status": "page29_full_style_axis_compare"},
    }
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(compare_bundle, handle, ensure_ascii=False, indent=2)


def build_group_spread_bundle(baseline_bundle: dict, ir_bundle: dict, out_path: Path) -> None:
    gap = 40.0
    top_pad = 28.0
    ir_logical = find_ir_logical_panel(ir_bundle)
    groups = [child for child in ir_logical.get("children") or []]
    variants = [("baseline_full", find_full_frame(baseline_bundle))] + [
        (group.get("id", f"group:{index}").split(":")[-1], group) for index, group in enumerate(groups)
    ]
    total_width = TARGET_SLIDE_WIDTH * len(variants) + gap * (len(variants) - 1)
    total_height = TARGET_SLIDE_HEIGHT + top_pad
    compare_children: list[dict] = []
    for index, (label, node) in enumerate(variants):
        dx = index * (TARGET_SLIDE_WIDTH + gap)
        compare_children.append(make_label_node(f"compare:groups:{index}:label", label, dx + 8.0, 6.0))
        if index == 0:
            compare_children.append(shift_node(node, f"compare:groups:{index}", dx, top_pad))
            continue
        frame = {
            "id": f"compare:groups:{index}:frame",
            "type": "FRAME",
            "name": label,
            "absoluteBoundingBox": {"x": dx, "y": top_pad, "width": TARGET_SLIDE_WIDTH, "height": TARGET_SLIDE_HEIGHT},
            "relativeTransform": identity_affine(),
            "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
            "strokes": [{"type": "SOLID", "color": {"r": 0.92, "g": 0.92, "b": 0.92}, "opacity": 1.0}],
            "strokeWeight": 1,
            "children": [shift_node(node, f"compare:groups:{index}", dx, top_pad)],
        }
        compare_children.append(frame)

    inner_frame = {
        "id": "page:29:group-spread:frame",
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
        "id": "page:29:group-spread",
        "type": "FRAME",
        "name": "Slide 29 Group Spread Compare",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {"generator": "page29-group-spread-compare"},
    }
    compare_bundle = {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-full-group-spread-compare",
        "visual_model_version": "dense-ui-group-spread-v1",
        "source_file": str(out_path),
        "file_name": out_path.name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": bundle_assets(baseline_bundle, ir_bundle),
        "missing_assets": [],
        "debug": {"status": "page29_group_spread_compare"},
    }
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(compare_bundle, handle, ensure_ascii=False, indent=2)


def find_right_panel_node(bundle: dict) -> dict | None:
    frame = find_full_frame(bundle)
    for child in frame.get("children") or []:
        if child.get("name") == "right_panel_block":
            return child
    return None


def build_panel_axis_compare_bundle(variants: list[tuple[str, dict]], out_path: Path) -> None:
    gap = 28.0
    outer_pad_x = 12.0
    outer_pad_y = 28.0
    panels: list[tuple[str, dict]] = []
    merged_assets: dict = {}

    for label, bundle in variants:
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
        shifted = shift_node(panel, f"panel-axis:{label}", dx, dy)
        shifted["name"] = label
        compare_children.append(make_label_node(f"panel-axis:{label}:label", label, outer_pad_x + index * (panel_width + gap) + 4.0, 6.0))
        compare_children.append(shifted)

    inner_frame = {
        "id": "page:29:panel-axis-compare:frame",
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
        "id": "page:29:panel-axis-compare",
        "type": "FRAME",
        "name": "Slide 29 Right Panel Axis Compare",
        "absoluteBoundingBox": {"x": 0.0, "y": 0.0, "width": total_width, "height": total_height},
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {"generator": "page29-panel-axis-compare"},
    }
    compare_bundle = {
        "kind": "figma-replay-bundle",
        "source_kind": "ppt-full-style-panel-axis-compare",
        "visual_model_version": "dense-ui-style-panel-axis-compare-v1",
        "source_file": str(out_path),
        "file_name": out_path.name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": merged_assets,
        "missing_assets": [],
        "debug": {"status": "page29_panel_axis_compare"},
    }
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(compare_bundle, handle, ensure_ascii=False, indent=2)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    baseline_path = repo_root / "docs" / "block-bundles" / "block-slide-29.bundle.json"
    ir_path = repo_root / "docs" / "block-bundles" / "ir-dense-ui-panel-29.bundle.json"
    out_path = repo_root / "docs" / "block-bundles" / "block-slide-29-full-style-hybrid-compare.bundle.json"
    hybrid_out_path = repo_root / "docs" / "block-bundles" / "block-slide-29-full-style-hybrid.bundle.json"
    axis_compare_out_path = repo_root / "docs" / "block-bundles" / "block-slide-29-full-style-axis-compare.bundle.json"
    group_spread_out_path = repo_root / "docs" / "block-bundles" / "block-slide-29-group-spread.bundle.json"
    panel_axis_compare_out_path = repo_root / "docs" / "block-bundles" / "block-slide-29-right-panel-axis-compare.bundle.json"

    baseline_bundle = load_bundle(baseline_path)
    ir_bundle = load_bundle(ir_path)
    chunk_policies = load_ir_chunk_policies(repo_root)
    hybrid_bundle = copy.deepcopy(baseline_bundle)
    hybrid_frame = build_hybrid_frame(
        baseline_bundle,
        ir_bundle,
        chunk_policies,
        include_top_meta=False,
        include_top_rows=False,
        include_description_header=False,
        include_description_text_overlay=True,
        include_version_stack=False,
        include_issue=False,
        include_small_assets=False,
    )
    hybrid_bundle["document"]["children"][0] = hybrid_frame
    build_compare_bundle(baseline_bundle, hybrid_bundle, out_path)
    build_hybrid_bundle(
        baseline_bundle,
        ir_bundle,
        chunk_policies,
        hybrid_out_path,
        include_top_meta=False,
        include_top_rows=False,
        include_description_header=False,
        include_description_text_overlay=True,
        include_version_stack=False,
        include_issue=False,
        include_small_assets=False,
    )
    variants = [
        ("baseline_full", baseline_bundle),
        ("ir_top_meta_band_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=True, include_top_meta_band=True, include_top_meta_info=False, include_top_rows=False, include_description_header=False, include_description_footer=False, include_version_stack=False, include_issue=False, include_annotation_overlay=False, include_small_assets=False
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_top_meta_info_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=True, include_top_meta_band=False, include_top_meta_info=True, include_top_rows=False, include_description_header=False, include_description_footer=False, include_version_stack=False, include_issue=False, include_annotation_overlay=False, include_small_assets=False
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_version_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=False, include_top_rows=False, include_description_header=False, include_description_footer=False, include_version_stack=True, include_issue=False, include_annotation_overlay=False, include_small_assets=False
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_desc_header_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=False, include_top_rows=False, include_description_header=True, include_description_footer=False, include_version_stack=False, include_issue=False, include_annotation_overlay=False, include_small_assets=False
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_footer_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=False, include_top_rows=False, include_description_header=False, include_description_footer=True, include_version_stack=False, include_issue=False, include_annotation_overlay=False, include_small_assets=False
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_issue_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=False, include_top_rows=False, include_description_header=False, include_description_footer=False, include_version_stack=False, include_issue=True, include_annotation_overlay=False, include_small_assets=False
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_notes_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=False, include_top_rows=False, include_description_header=False, include_description_footer=False, include_version_stack=False, include_issue=False, include_annotation_overlay=True, include_small_assets=False
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_assets_only", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=False, include_top_rows=False, include_description_header=False, include_description_footer=False, include_version_stack=False, include_issue=False, include_annotation_overlay=False, include_small_assets=True
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
        ("ir_meta_version_issue_assets", {"document": {"children": [build_hybrid_frame(
            baseline_bundle, ir_bundle, chunk_policies, include_top_meta=True, include_top_rows=True, include_description_header=True, include_description_footer=True, include_version_stack=True, include_issue=True, include_annotation_overlay=True, include_small_assets=True
        )] }, "assets": bundle_assets(baseline_bundle, ir_bundle)}),
    ]
    build_axis_compare_bundle(baseline_bundle, ir_bundle, chunk_policies, axis_compare_out_path)
    build_panel_axis_compare_bundle(variants, panel_axis_compare_out_path)
    build_group_spread_bundle(baseline_bundle, ir_bundle, group_spread_out_path)
    print(f"saved {out_path}")
    print(f"saved {hybrid_out_path}")
    print(f"saved {axis_compare_out_path}")
    print(f"saved {panel_axis_compare_out_path}")
    print(f"saved {group_spread_out_path}")


if __name__ == "__main__":
    main()
