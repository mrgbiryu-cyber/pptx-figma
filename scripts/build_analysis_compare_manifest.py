#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def node_bbox(node: dict[str, Any]) -> dict[str, float]:
    bounds = (
        node.get("bounds_absolute")
        or node.get("absoluteBoundingBox")
        or node.get("absoluteRenderBounds")
        or {}
    )
    if bounds:
        return {
            "x": float(bounds.get("x") or 0.0),
            "y": float(bounds.get("y") or 0.0),
            "width": float(bounds.get("width") or 0.0),
            "height": float(bounds.get("height") or 0.0),
        }
    return {
        "x": 0.0,
        "y": 0.0,
        "width": float(node.get("width") or 0.0),
        "height": float(node.get("height") or 0.0),
    }


def identity_affine() -> list[list[float]]:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def flatten_analysis_node(
    node: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    parent_id: str,
    node_path: str,
    depth: int,
    sibling_index: int,
    global_order_ref: list[int],
) -> None:
    global_paint_order = global_order_ref[0]
    global_order_ref[0] += 1
    bbox = node_bbox(node)
    text_style = node.get("text_style") or {}
    chars = str(node.get("characters") or "")
    normalized_chars = normalize_text(chars)
    rows.append(
        {
            "node_id": str(node.get("id") or ""),
            "parent_id": parent_id,
            "node_path": node_path,
            "node_type": str(node.get("type") or ""),
            "node_name": str(node.get("name") or ""),
            "text_characters": chars,
            "normalized_text": normalized_chars,
            "depth": depth,
            "sibling_index": sibling_index,
            "global_paint_order": global_paint_order,
            "bbox_absolute": bbox,
            "bbox_render": dict(bbox),
            "children_count": len(node.get("children") or []),
            "is_text": str(node.get("type") or "") == "TEXT",
            "is_vector": str(node.get("type") or "") in {"VECTOR", "LINE", "ELLIPSE", "POLYGON", "STAR", "BOOLEAN_OPERATION"},
            "is_frame_like": str(node.get("type") or "") in {"FRAME", "GROUP", "SECTION", "COMPONENT", "INSTANCE"},
            "clips_content": bool(node.get("clips_content") or node.get("clipsContent")),
            "font_family": str((text_style.get("fontName") or {}).get("family") if isinstance(text_style.get("fontName"), dict) else ""),
            "font_style": str((text_style.get("fontName") or {}).get("style") if isinstance(text_style.get("fontName"), dict) else ""),
            "font_size": text_style.get("fontSize"),
            "line_height": text_style.get("lineHeight"),
            "letter_spacing": text_style.get("letterSpacing"),
            "text_auto_resize": text_style.get("textAutoResize"),
            "text_align_horizontal": text_style.get("textAlignHorizontal"),
            "text_align_vertical": text_style.get("textAlignVertical"),
            "paragraph_count": max(1, chars.count("\n\n") + 1) if chars else 0,
            "line_count_estimate": max(1, chars.count("\n") + 1) if chars else 0,
            "semantic_key": f"{node.get('type')}:{normalized_chars or normalize_text(node.get('name'))}",
            "content_key": normalized_chars or normalize_text(node.get("name")),
            "style_key": f"{text_style.get('fontSize')}|{text_style.get('textAlignHorizontal')}|{text_style.get('textAlignVertical')}",
        }
    )
    for index, child in enumerate(node.get("children") or []):
        flatten_analysis_node(
            child,
            rows,
            parent_id=str(node.get("id") or ""),
            node_path=f"{node_path}/{index}",
            depth=depth + 1,
            sibling_index=index,
            global_order_ref=global_order_ref,
        )


def flatten_reference_node(
    node: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    parent_id: str,
    node_path: str,
    depth: int,
    sibling_index: int,
    global_order_ref: list[int],
) -> None:
    global_paint_order = global_order_ref[0]
    global_order_ref[0] += 1
    bbox = node_bbox(node)
    style = node.get("style") or {}
    chars = str(node.get("characters") or "")
    normalized_chars = normalize_text(chars)
    rows.append(
        {
            "node_id": str(node.get("id") or ""),
            "parent_id": parent_id,
            "node_path": node_path,
            "node_type": str(node.get("type") or ""),
            "node_name": str(node.get("name") or ""),
            "text_characters": chars,
            "normalized_text": normalized_chars,
            "depth": depth,
            "sibling_index": sibling_index,
            "global_paint_order": global_paint_order,
            "bbox_absolute": bbox,
            "bbox_render": dict(bbox),
            "children_count": len(node.get("children") or []),
            "is_text": str(node.get("type") or "") == "TEXT",
            "is_vector": str(node.get("type") or "") in {"VECTOR", "LINE", "ELLIPSE", "POLYGON", "STAR", "BOOLEAN_OPERATION"},
            "is_frame_like": str(node.get("type") or "") in {"FRAME", "GROUP", "SECTION", "COMPONENT", "INSTANCE"},
            "clips_content": bool(node.get("clipsContent")),
            "font_family": str(style.get("fontFamily") or ""),
            "font_style": str(style.get("fontStyle") or ""),
            "font_size": style.get("fontSize"),
            "line_height": style.get("lineHeightPx"),
            "letter_spacing": style.get("letterSpacing"),
            "text_auto_resize": style.get("textAutoResize"),
            "text_align_horizontal": style.get("textAlignHorizontal"),
            "text_align_vertical": style.get("textAlignVertical"),
            "paragraph_count": max(1, chars.count("\n\n") + 1) if chars else 0,
            "line_count_estimate": max(1, chars.count("\n") + 1) if chars else 0,
            "semantic_key": f"{node.get('type')}:{normalized_chars or normalize_text(node.get('name'))}",
            "content_key": normalized_chars or normalize_text(node.get("name")),
            "style_key": f"{style.get('fontSize')}|{style.get('textAlignHorizontal')}|{style.get('textAlignVertical')}",
        }
    )
    for index, child in enumerate(node.get("children") or []):
        flatten_reference_node(
            child,
            rows,
            parent_id=str(node.get("id") or ""),
            node_path=f"{node_path}/{index}",
            depth=depth + 1,
            sibling_index=index,
            global_order_ref=global_order_ref,
        )


def detect_input_kind(payload: dict[str, Any]) -> str:
    if payload.get("kind") == "figma-analysis-export":
        return "figma-analysis-export"
    if isinstance(payload.get("nodes"), dict):
        return "reference-json"
    raise ValueError("unsupported input format")


def build_manifest(input_path: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    kind = detect_input_kind(payload)
    rows: list[dict[str, Any]] = []
    global_order_ref = [0]

    if kind == "figma-analysis-export":
        page = payload.get("page") or {}
        page_bounds = payload.get("scope_bounds") or {"x": 0, "y": 0, "width": 0, "height": 0}
        for index, node in enumerate(payload.get("nodes") or []):
            flatten_analysis_node(
                node,
                rows,
                parent_id="",
                node_path=str(index),
                depth=0,
                sibling_index=index,
                global_order_ref=global_order_ref,
            )
        return {
            "kind": "analysis-compare-manifest",
            "source_kind": kind,
            "source_file": input_path.name,
            "page_id": str(page.get("id") or ""),
            "page_name": str(page.get("name") or ""),
            "page_bounds": page_bounds,
            "node_count": len(rows),
            "nodes": rows,
        }

    page_id, entry = next(iter((payload.get("nodes") or {}).items()))
    document = entry.get("document") or {}
    page_bounds = node_bbox(document)
    flatten_reference_node(
        document,
        rows,
        parent_id="",
        node_path="0",
        depth=0,
        sibling_index=0,
        global_order_ref=global_order_ref,
    )
    return {
        "kind": "analysis-compare-manifest",
        "source_kind": kind,
        "source_file": input_path.name,
        "page_id": str(page_id),
        "page_name": str(document.get("name") or ""),
        "page_bounds": page_bounds,
        "node_count": len(rows),
        "nodes": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a normalized compare manifest from reference/export JSON.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest = build_manifest(Path(args.input).resolve())
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
