#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from build_figma_page_manifest import build_manifest


PAGE_TYPE_BY_NAME = {
    "reference-page-1": "flow-process",
    "reference-page-2": "table-heavy",
    "reference-page-3": "ui-mockup",
}

DEFAULT_MAPPING_REPORT = Path(__file__).resolve().parents[1] / "docs" / "ppt-to-reference-mapping-report.json"
DEFAULT_INTERMEDIATE = Path(__file__).resolve().parents[1] / "docs" / "ppt-intermediate-candidates-12-19-29.json"


def load_page_document(page_path: Path) -> tuple[str, dict[str, Any]]:
    with page_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    nodes = payload.get("nodes") or {}
    if not nodes:
        raise ValueError(f"{page_path.name} does not contain nodes")
    page_id, entry = next(iter(nodes.items()))
    document = entry.get("document")
    if not document:
        raise ValueError(f"{page_path.name} does not contain document")
    return page_id, document


def bounds_of(node: dict[str, Any]) -> dict[str, float]:
    bounds = node.get("absoluteBoundingBox") or node.get("absoluteRenderBounds") or {}
    return {
        "x": float(bounds.get("x", 0)),
        "y": float(bounds.get("y", 0)),
        "width": float(bounds.get("width", 0)),
        "height": float(bounds.get("height", 0)),
    }


def area(bounds: dict[str, float]) -> float:
    return max(bounds["width"], 0) * max(bounds["height"], 0)


def union_bounds(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    min_x = min(row["x"] for row in rows)
    min_y = min(row["y"] for row in rows)
    max_x = max(row["x"] + row["width"] for row in rows)
    max_y = max(row["y"] + row["height"] for row in rows)
    return {
        "x": round(min_x, 2),
        "y": round(min_y, 2),
        "width": round(max_x - min_x, 2),
        "height": round(max_y - min_y, 2),
    }


def normalize_scope(bounds: dict[str, float], page_bounds: dict[str, float]) -> dict[str, float]:
    width = page_bounds["width"] or 1.0
    height = page_bounds["height"] or 1.0
    return {
        "x_ratio": round((bounds["x"] - page_bounds["x"]) / width, 4),
        "y_ratio": round((bounds["y"] - page_bounds["y"]) / height, 4),
        "width_ratio": round(bounds["width"] / width, 4),
        "height_ratio": round(bounds["height"] / height, 4),
    }


def walk_tree(node: dict[str, Any], parent_id: str, depth: int, rows: list[dict[str, Any]]) -> None:
    node_id = str(node.get("id") or "")
    bounds = bounds_of(node)
    rows.append(
        {
            "id": node_id,
            "parent_id": parent_id,
            "type": node.get("type", ""),
            "name": node.get("name", ""),
            "bounds": bounds,
            "depth": depth,
            "children": node.get("children") or [],
            "text": node.get("characters", "") if node.get("type") == "TEXT" else "",
            "style": node.get("style") or {},
            "fillGeometry": node.get("fillGeometry") or [],
            "strokeGeometry": node.get("strokeGeometry") or [],
        }
    )
    for child in node.get("children") or []:
        walk_tree(child, node_id, depth + 1, rows)


def tokenize_path(path: str) -> list[str]:
    return re.findall(r"[MLZmlz]|-?\d+(?:\.\d+)?", path or "")


def extract_path_points(path: str) -> list[dict[str, float]]:
    tokens = tokenize_path(path)
    points: list[dict[str, float]] = []
    idx = 0
    command = ""
    while idx < len(tokens):
        token = tokens[idx]
        if token.isalpha():
            command = token.upper()
            idx += 1
            continue
        if command not in {"M", "L"} or idx + 1 >= len(tokens):
            idx += 1
            continue
        try:
            x = float(tokens[idx])
            y = float(tokens[idx + 1])
        except ValueError:
            idx += 1
            continue
        points.append({"x": x, "y": y})
        idx += 2
    return points


def simplify_route_signature(points: list[dict[str, float]], bounds: dict[str, float]) -> dict[str, Any]:
    if len(points) < 2:
        fallback = "H" if bounds["width"] >= bounds["height"] else "V"
        return {
            "segment_signature": fallback,
            "turn_count": 0,
            "dominant_axis": "horizontal" if fallback == "H" else "vertical",
            "segment_count": 1,
        }
    significant: list[str] = []
    widths = max(bounds["width"], 1.0)
    heights = max(bounds["height"], 1.0)
    min_length = max(min(widths, heights) * 0.08, 3.0)
    for previous, current in zip(points, points[1:]):
        dx = current["x"] - previous["x"]
        dy = current["y"] - previous["y"]
        length = math.hypot(dx, dy)
        if length < min_length:
            continue
        if abs(dx) >= abs(dy) * 2:
            direction = "H"
        elif abs(dy) >= abs(dx) * 2:
            direction = "V"
        else:
            continue
        if not significant or significant[-1] != direction:
            significant.append(direction)
    if not significant:
        significant = ["H" if bounds["width"] >= bounds["height"] else "V"]
    signature = "".join(significant)
    return {
        "segment_signature": signature,
        "turn_count": max(len(significant) - 1, 0),
        "dominant_axis": "horizontal" if signature.count("H") >= signature.count("V") else "vertical",
        "segment_count": len(significant),
    }


def is_background_vector(row: dict[str, Any], page_bounds: dict[str, float]) -> bool:
    if row["type"] != "VECTOR":
        return False
    bounds = row["bounds"]
    return bounds["width"] >= page_bounds["width"] * 0.95 and bounds["height"] >= page_bounds["height"] * 0.95


def is_page_container(row: dict[str, Any], page_bounds: dict[str, float]) -> bool:
    if row["type"] not in {"FRAME", "GROUP"}:
        return False
    bounds = row["bounds"]
    return bounds["width"] >= page_bounds["width"] * 0.95 and bounds["height"] >= page_bounds["height"] * 0.9


def looks_like_connector(row: dict[str, Any], page_bounds: dict[str, float]) -> bool:
    if row["type"] != "VECTOR":
        return False
    if is_background_vector(row, page_bounds):
        return False
    bounds = row["bounds"]
    if bounds["width"] < 18 and bounds["height"] < 18:
        return False
    aspect = max(bounds["width"], bounds["height"]) / max(min(bounds["width"], bounds["height"]), 1.0)
    if aspect < 2.4:
        return False
    geometries = row["strokeGeometry"] or row["fillGeometry"]
    if not geometries:
        return False
    route = simplify_route_signature(extract_path_points(geometries[0].get("path", "")), bounds)
    return route["segment_count"] <= 4


def build_parent_map(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    parent_map: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        parent_map.setdefault(row["parent_id"], []).append(row)
    return parent_map


def descendants_of(node_id: str, parent_map: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    queue = list(parent_map.get(node_id, []))
    output: list[dict[str, Any]] = []
    while queue:
        row = queue.pop(0)
        output.append(row)
        queue.extend(parent_map.get(row["id"], []))
    return output


def summarize_block(rows: list[dict[str, Any]], page_bounds: dict[str, float], block_type: str) -> dict[str, Any]:
    type_counts: dict[str, int] = {}
    font_sizes: list[float] = []
    text_samples: list[str] = []
    bounds_rows = []
    for row in rows:
        type_counts[row["type"]] = type_counts.get(row["type"], 0) + 1
        bounds_rows.append(row["bounds"])
        if row["type"] == "TEXT":
            font_size = row["style"].get("fontSize")
            if isinstance(font_size, (int, float)):
                font_sizes.append(float(font_size))
            text = str(row.get("text") or "").strip()
            if text and len(text_samples) < 8:
                text_samples.append(text)
    bounds = union_bounds(bounds_rows)
    return {
        "block_type": block_type,
        "bounds": bounds,
        "bounds_ratio": normalize_scope(bounds, page_bounds),
        "node_count": len(rows),
        "type_counts": type_counts,
        "text_sample_count": len(text_samples),
        "text_samples": text_samples,
        "font_size_min": round(min(font_sizes), 2) if font_sizes else None,
        "font_size_max": round(max(font_sizes), 2) if font_sizes else None,
        "font_size_avg": round(sum(font_sizes) / len(font_sizes), 2) if font_sizes else None,
    }


def load_mapped_connector_ids(mapping_report_path: Path | None) -> dict[str, set[str]]:
    if not mapping_report_path or not mapping_report_path.exists():
        return {}
    with mapping_report_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    mapped: dict[str, set[str]] = {}
    for page in payload.get("pages") or []:
        reference_file = str(page.get("reference_file") or "")
        ref_stem = Path(reference_file).stem
        bucket = mapped.setdefault(ref_stem, set())
        for row in page.get("mappings") or []:
            if row.get("subtype") != "connector":
                continue
            matches = row.get("matches") or []
            if not matches:
                continue
            top = matches[0]
            if float(top.get("score") or 0.0) < 0.55:
                continue
            ref_id = str(top.get("reference_node_id") or "")
            if ref_id:
                bucket.add(ref_id)
    return mapped


def connector_direction_from_idx(value: Any) -> str:
    mapping = {0: "up", 1: "left", 2: "down", 3: "right", 4: "left", 5: "right", 6: "left", 7: "right"}
    try:
        return mapping.get(int(value), "")
    except Exception:
        return ""


def opposite_direction(direction: str) -> str:
    return {
        "up": "down",
        "down": "up",
        "left": "right",
        "right": "left",
    }.get(direction, "")


def connector_case_key(shape_kind: str, start_direction: str, end_direction: str) -> str:
    return f"{(shape_kind or '').lower()}|{start_direction or '-'}|{end_direction or '-'}"


def load_intermediate_connector_facts(intermediate_path: Path | None) -> dict[int, dict[str, dict[str, str]]]:
    if not intermediate_path or not intermediate_path.exists():
        return {}
    with intermediate_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    pages: dict[int, dict[str, dict[str, str]]] = {}
    for page in payload.get("pages") or []:
        slide_no = int(page.get("slide_no") or 0)
        bucket: dict[str, dict[str, str]] = {}
        for candidate in page.get("candidates") or []:
            if candidate.get("subtype") != "connector":
                continue
            extra = candidate.get("extra") or {}
            bucket[str(candidate.get("candidate_id") or "")] = {
                "shape_kind": str(extra.get("shape_kind") or ""),
                "start_direction": connector_direction_from_idx((extra.get("start_connection") or {}).get("idx")),
                "end_direction": opposite_direction(
                    connector_direction_from_idx((extra.get("end_connection") or {}).get("idx"))
                ),
            }
        pages[slide_no] = bucket
    return pages


def build_connector_route_cases(
    pages: list[dict[str, Any]],
    mapping_report_path: Path | None,
    intermediate_path: Path | None,
) -> dict[str, Any]:
    if not mapping_report_path or not mapping_report_path.exists():
        return {}
    with mapping_report_path.open("r", encoding="utf-8") as handle:
        mapping_payload = json.load(handle)
    intermediate_facts = load_intermediate_connector_facts(intermediate_path)
    page_by_name = {page["page_name"]: page for page in pages}
    cases: dict[str, dict[str, Any]] = {}
    for page in mapping_payload.get("pages") or []:
        slide_no = int(page.get("slide_no") or 0)
        reference_name = Path(str(page.get("reference_file") or "")).stem
        page_template = page_by_name.get(reference_name)
        if not page_template:
            continue
        page_type = str(page_template.get("page_type") or "generic")
        route_lookup = {
            str(template.get("reference_node_id") or ""): str(template.get("segment_signature") or "")
            for template in page_template.get("connector_templates") or []
        }
        slide_facts = intermediate_facts.get(slide_no) or {}
        for row in page.get("mappings") or []:
            if row.get("subtype") != "connector":
                continue
            candidate_id = str(row.get("candidate_id") or "")
            facts = slide_facts.get(candidate_id)
            if not facts:
                continue
            matches = row.get("matches") or []
            if not matches:
                continue
            top_match = matches[0]
            if float(top_match.get("score") or 0.0) < 0.55:
                continue
            signature = route_lookup.get(str(top_match.get("reference_node_id") or ""))
            if not signature:
                continue
            key = connector_case_key(facts["shape_kind"], facts["start_direction"], facts["end_direction"])
            page_bucket = cases.setdefault(page_type, {})
            case = page_bucket.setdefault(
                key,
                {
                    "shape_kind": facts["shape_kind"],
                    "start_direction": facts["start_direction"],
                    "end_direction": facts["end_direction"],
                    "route_signatures": {},
                    "sample_count": 0,
                },
            )
            case["sample_count"] += 1
            case["route_signatures"][signature] = case["route_signatures"].get(signature, 0) + 1
    return cases


def extract_page_templates(page_path: Path, allowed_connector_ids: set[str] | None = None) -> dict[str, Any]:
    _, document = load_page_document(page_path)
    manifest = build_manifest(page_path)
    page_bounds = manifest["page_bounds"]
    rows: list[dict[str, Any]] = []
    walk_tree(document, "", 0, rows)
    parent_map = build_parent_map(rows)
    page_name = page_path.stem
    page_type = PAGE_TYPE_BY_NAME.get(page_name, "generic")

    connector_templates = []
    for row in rows:
        if not looks_like_connector(row, page_bounds):
            continue
        if allowed_connector_ids and row["id"] not in allowed_connector_ids:
            continue
        geometry = (row["strokeGeometry"] or row["fillGeometry"])[0]
        route = simplify_route_signature(extract_path_points(geometry.get("path", "")), row["bounds"])
        connector_templates.append(
            {
                "reference_node_id": row["id"],
                "page_name": page_name,
                "page_type": page_type,
                "parent_id": row["parent_id"],
                "bounds": row["bounds"],
                "bounds_ratio": normalize_scope(row["bounds"], page_bounds),
                "segment_signature": route["segment_signature"],
                "segment_count": route["segment_count"],
                "turn_count": route["turn_count"],
                "dominant_axis": route["dominant_axis"],
                "node_name": row["name"],
            }
        )

    top_cutoff = page_bounds["y"] + (58 if page_type == "ui-mockup" else 72)
    header_rows = [
        row
        for row in rows
        if not is_background_vector(row, page_bounds)
        and not is_page_container(row, page_bounds)
        and row["bounds"]["y"] <= top_cutoff
        and row["bounds"]["y"] + row["bounds"]["height"] <= page_bounds["y"] + (90 if page_type == "ui-mockup" else 96)
        and area(row["bounds"]) > 0
    ]
    right_panel_rows = [
        row
        for row in rows
        if row["bounds"]["x"] >= page_bounds["x"] + page_bounds["width"] * 0.58
        and row["bounds"]["y"] >= page_bounds["y"] + 30
        and not is_background_vector(row, page_bounds)
        and not is_page_container(row, page_bounds)
    ]

    table_group = None
    if page_type in {"table-heavy", "ui-mockup"}:
        scored_groups = []
        for row in rows:
            if row["type"] not in {"GROUP", "FRAME"}:
                continue
            if is_page_container(row, page_bounds):
                continue
            descendants = descendants_of(row["id"], parent_map)
            text_count = sum(1 for child in descendants if child["type"] == "TEXT")
            vector_count = sum(1 for child in descendants if child["type"] == "VECTOR")
            if text_count < 6 or vector_count < 4:
                continue
            bounds = row["bounds"]
            if bounds["width"] < page_bounds["width"] * 0.18 or bounds["height"] < page_bounds["height"] * 0.18:
                continue
            if bounds["y"] <= page_bounds["y"] + 90:
                continue
            score = text_count * 2 + vector_count + len(descendants) * 0.1
            scored_groups.append((score, row, descendants))
        if scored_groups:
            scored_groups.sort(key=lambda item: item[0], reverse=True)
            _, selected, descendants = scored_groups[0]
            table_group = summarize_block([selected] + descendants, page_bounds, "table_block")
            table_group["reference_node_id"] = selected["id"]
        elif page_type == "table-heavy":
            region_rows = [
                row
                for row in rows
                if row["type"] in {"TEXT", "VECTOR", "GROUP"}
                and not is_background_vector(row, page_bounds)
                and not is_page_container(row, page_bounds)
                and row["bounds"]["x"] <= page_bounds["x"] + page_bounds["width"] * 0.52
                and row["bounds"]["y"] >= page_bounds["y"] + page_bounds["height"] * 0.12
            ]
            if region_rows:
                table_group = summarize_block(region_rows, page_bounds, "table_block")
                table_group["reference_node_id"] = "region:fallback"

    page_templates = {
        "page_name": page_name,
        "page_type": page_type,
        "page_bounds": page_bounds,
        "header_block": summarize_block(header_rows, page_bounds, "header_block") if header_rows else None,
        "right_panel_block": summarize_block(right_panel_rows, page_bounds, "right_panel_block") if right_panel_rows else None,
        "table_block": table_group,
        "connector_templates": connector_templates,
    }
    return page_templates


def aggregate_connector_templates(pages: list[dict[str, Any]]) -> dict[str, Any]:
    by_page_type: dict[str, dict[str, Any]] = {}
    for page in pages:
        page_type = page["page_type"]
        bucket = by_page_type.setdefault(page_type, {"route_signatures": {}, "sample_routes": []})
        for template in page["connector_templates"]:
            signature = template["segment_signature"]
            bucket["route_signatures"][signature] = bucket["route_signatures"].get(signature, 0) + 1
            if len(bucket["sample_routes"]) < 12:
                bucket["sample_routes"].append(template)
    return by_page_type


def build_template_report(
    reference_paths: list[Path],
    mapping_report_path: Path | None = None,
    intermediate_path: Path | None = None,
) -> dict[str, Any]:
    mapped_connector_ids = load_mapped_connector_ids(mapping_report_path)
    pages = [
        extract_page_templates(path, mapped_connector_ids.get(path.stem))
        for path in reference_paths
    ]
    return {
        "kind": "reference-visual-templates",
        "pages": pages,
        "connector_route_templates": aggregate_connector_templates(pages),
        "connector_route_cases": build_connector_route_cases(pages, mapping_report_path, intermediate_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract reusable visual templates from reference Figma JSON pages.")
    parser.add_argument("--input", nargs="+", required=True, help="Reference page JSON paths")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--mapping-report", help="Optional PPT-to-reference mapping report for connector filtering")
    parser.add_argument("--intermediate", help="Optional PPT intermediate candidates JSON for connector case extraction")
    args = parser.parse_args()

    mapping_report_path = Path(args.mapping_report).resolve() if args.mapping_report else DEFAULT_MAPPING_REPORT
    intermediate_path = Path(args.intermediate).resolve() if args.intermediate else DEFAULT_INTERMEDIATE
    report = build_template_report([Path(name).resolve() for name in args.input], mapping_report_path, intermediate_path)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
