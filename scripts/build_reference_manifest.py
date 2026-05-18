#!/usr/bin/env python3
import argparse
import json
import math
from pathlib import Path


def identity_affine():
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def multiply_affine(parent, child):
    pa, pc, pe = parent[0]
    pb, pd, pf = parent[1]
    ca, cc, ce = child[0]
    cb, cd, cf = child[1]
    return [
        [pa * ca + pc * cb, pa * cc + pc * cd, pa * ce + pc * cf + pe],
        [pb * ca + pd * cb, pb * cc + pd * cd, pb * ce + pd * cf + pf],
    ]


def transform_signature(matrix):
    a = matrix[0][0]
    d = matrix[1][1]
    b = matrix[1][0]
    rotation = round(math.degrees(math.atan2(b, a)))
    bucket = int(round(rotation / 15.0) * 15)
    return {
        "flip_x": a < 0,
        "flip_y": d < 0,
        "rotation_hint": rotation,
        "rotation_bucket": bucket,
        "signature": f"{'-' if a < 0 else '+'}:{'-' if d < 0 else '+'}:R{bucket}",
    }


def node_bounds(node):
    bounds = node.get("absoluteBoundingBox") or node.get("absoluteRenderBounds")
    if bounds:
        return {
            "x": float(bounds.get("x", 0)),
            "y": float(bounds.get("y", 0)),
            "width": float(bounds.get("width", 0)),
            "height": float(bounds.get("height", 0)),
        }
    size = node.get("size") or {}
    rt = node.get("relativeTransform") or identity_affine()
    return {
        "x": float(rt[0][2]),
        "y": float(rt[1][2]),
        "width": float(size.get("x", 0)),
        "height": float(size.get("y", 0)),
    }


def width_bucket(width):
    if width < 80:
        return "XS"
    if width < 160:
        return "S"
    if width < 320:
        return "M"
    if width < 640:
        return "L"
    return "XL"


def normalize_text(value):
    return " ".join(str(value or "").split())


def text_line_break_signature(text, width):
    raw = str(text or "")
    explicit = raw.count("\n")
    rendered = max(raw.count("\n") + 1, 1)
    return f"NL{explicit}-L{rendered}-W{width_bucket(width)}"


def geometry_count(node):
    return len(node.get("fillGeometry") or []) + len(node.get("strokeGeometry") or [])


def geometry_bucket(count):
    if count <= 1:
        return "G1"
    if count <= 4:
        return "G4"
    if count <= 12:
        return "G12"
    return "G+"


def bbox_aspect_bucket(bounds):
    w = max(bounds["width"], 1)
    h = max(bounds["height"], 1)
    ratio = w / h
    if ratio < 0.5:
        return "TALL"
    if ratio < 1.5:
        return "BALANCED"
    if ratio < 4:
        return "WIDE"
    return "ULTRA_WIDE"


def is_mask_like(node):
    name = str(node.get("name", "")).lower()
    return "mask" in name


def is_clip_like(node):
    name = str(node.get("name", "")).lower()
    return "clip path" in name or (node.get("clipsContent") and bool(node.get("children")))


def is_fullpage_overlay_candidate(node, page_bounds):
    bounds = node_bounds(node)
    fills = node.get("fills") or []
    if page_bounds["width"] <= 0 or page_bounds["height"] <= 0:
        return False
    large = bounds["width"] >= page_bounds["width"] * 0.9 and bounds["height"] >= page_bounds["height"] * 0.9
    if not large:
        return False
    if node.get("children"):
        return False
    for fill in fills:
        if fill.get("type") != "SOLID":
            continue
        color = fill.get("color") or {}
        if color.get("r", 1) < 0.1 and color.get("g", 1) < 0.1 and color.get("b", 1) < 0.1:
            return True
    return False


def comparison_level(node, page_bounds):
    bounds = node_bounds(node)
    node_type = node.get("type")
    if is_fullpage_overlay_candidate(node, page_bounds):
        return "ignore"
    if node_type in {"TEXT", "VECTOR"}:
        return "L2"
    if node_type == "RECTANGLE":
        fills = node.get("fills") or []
        if any(fill.get("type") == "IMAGE" for fill in fills if isinstance(fill, dict)):
            return "L2"
        if bounds["width"] < 24 and bounds["height"] < 24:
            return "L3"
        return "L2"
    if node_type in {"FRAME", "GROUP"}:
        if (
            bounds["width"] >= page_bounds["width"] * 0.2
            or bounds["height"] >= page_bounds["height"] * 0.12
            or len(node.get("children") or []) >= 3
        ):
            return "L1"
        return "L2"
    return "ignore"


def semantic_key(node):
    node_type = node.get("type", "UNKNOWN")
    if node_type == "TEXT":
        return f"TEXT:{normalize_text(node.get('characters', ''))}"
    name = normalize_text(node.get("name", ""))
    return f"{node_type}:{name}"


def content_key(node, bounds):
    node_type = node.get("type", "UNKNOWN")
    if node_type == "TEXT":
        return normalize_text(node.get("characters", ""))
    return f"{geometry_bucket(geometry_count(node))}:{bbox_aspect_bucket(bounds)}"


def structure_key(parent_id, depth, sibling_index):
    return f"{parent_id}|d{depth}|s{sibling_index}"


def walk(node, page_id, page_bounds, rows, parent_id="", depth=0, sibling_index=0, parent_composed=None):
    relative = node.get("relativeTransform") or identity_affine()
    parent_composed = parent_composed or identity_affine()
    composed = multiply_affine(parent_composed, relative)
    bounds = node_bounds(node)
    t = transform_signature(composed)
    fills = node.get("fills") or []
    strokes = node.get("strokes") or []
    row = {
        "page_id": page_id,
        "reference_node_id": node.get("id", ""),
        "reference_parent_id": parent_id,
        "node_type": node.get("type", ""),
        "node_name": node.get("name", ""),
        "depth": depth,
        "child_count": len(node.get("children") or []),
        "bbox_absolute": bounds,
        "bbox_parent_relative": {
            "x": float(relative[0][2]),
            "y": float(relative[1][2]),
            "width": bounds["width"],
            "height": bounds["height"],
        },
        "relative_transform": relative,
        "composed_transform": composed,
        "transform_signature": t["signature"],
        "flip_x": t["flip_x"],
        "flip_y": t["flip_y"],
        "rotation_hint": t["rotation_hint"],
        "has_fill": bool(fills),
        "has_stroke": bool(strokes),
        "has_image_fill": any(fill.get("type") == "IMAGE" for fill in fills if isinstance(fill, dict)),
        "has_vector_geometry": bool(node.get("fillGeometry") or node.get("strokeGeometry")),
        "text_characters": node.get("characters", "") if node.get("type") == "TEXT" else "",
        "text_line_break_signature": text_line_break_signature(node.get("characters", ""), bounds["width"]) if node.get("type") == "TEXT" else "",
        "font_family": ((node.get("style") or {}).get("fontFamily")) if node.get("type") == "TEXT" else "",
        "font_style": ((node.get("style") or {}).get("fontStyle")) if node.get("type") == "TEXT" else "",
        "font_size": ((node.get("style") or {}).get("fontSize")) if node.get("type") == "TEXT" else None,
        "is_mask_like": is_mask_like(node),
        "is_clip_like": is_clip_like(node),
        "is_fullpage_overlay_candidate": is_fullpage_overlay_candidate(node, page_bounds),
        "comparison_level": comparison_level(node, page_bounds),
        "comparison_target": comparison_level(node, page_bounds) in {"L1", "L2"},
        "semantic_key": semantic_key(node),
        "content_key": content_key(node, bounds),
        "structure_key": structure_key(parent_id, depth, sibling_index),
        "geometry_count_bucket": geometry_bucket(geometry_count(node)),
        "bbox_aspect_bucket": bbox_aspect_bucket(bounds),
    }
    rows.append(row)
    for idx, child in enumerate(node.get("children") or []):
        walk(child, page_id, page_bounds, rows, node.get("id", ""), depth + 1, idx, composed)


def build_manifest(page_json_path):
    with page_json_path.open("r", encoding="utf-8") as handle:
        page_data = json.load(handle)
    nodes = page_data.get("nodes") or {}
    if not nodes:
        raise ValueError(f"{page_json_path.name} does not contain nodes")
    page_id, entry = next(iter(nodes.items()))
    document = entry.get("document")
    if not document:
        raise ValueError(f"{page_json_path.name} does not contain document")
    page_bounds = node_bounds(document)
    rows = []
    walk(document, page_id, page_bounds, rows)
    return {
        "kind": "reference-manifest",
        "source_file": page_json_path.name,
        "page_id": page_id,
        "page_name": document.get("name", ""),
        "page_bounds": page_bounds,
        "nodes": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="Build reference manifests from exported Figma page JSON files.")
    parser.add_argument("--base-dir", default=".", help="Project root containing figma-page-*.json")
    parser.add_argument("--pages", nargs="*", default=["figma-page-1.json", "figma-page-2.json", "figma-page-3.json"])
    parser.add_argument("--output-dir", default="docs", help="Output directory")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    output_dir = (base_dir / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    for page_name in args.pages:
        page_path = (base_dir / page_name).resolve()
        manifest = build_manifest(page_path)
        output_path = output_dir / f"{page_path.stem}.reference-manifest.json"
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        print(f"saved {output_path}")


if __name__ == "__main__":
    main()
