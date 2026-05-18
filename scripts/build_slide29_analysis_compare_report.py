#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def bbox_center(bbox: dict[str, Any]) -> tuple[float, float]:
    return (
        float(bbox.get("x") or 0.0) + float(bbox.get("width") or 0.0) / 2.0,
        float(bbox.get("y") or 0.0) + float(bbox.get("height") or 0.0) / 2.0,
    )


def rel_bbox(page_bounds: dict[str, Any], bbox: dict[str, Any]) -> dict[str, float]:
    return {
        "x": float(bbox.get("x") or 0.0) - float(page_bounds.get("x") or 0.0),
        "y": float(bbox.get("y") or 0.0) - float(page_bounds.get("y") or 0.0),
        "width": float(bbox.get("width") or 0.0),
        "height": float(bbox.get("height") or 0.0),
    }


def abs_bbox(page_bounds: dict[str, Any], relative: dict[str, Any]) -> dict[str, float]:
    return {
        "x": float(page_bounds.get("x") or 0.0) + float(relative.get("x") or 0.0),
        "y": float(page_bounds.get("y") or 0.0) + float(relative.get("y") or 0.0),
        "width": float(relative.get("width") or 0.0),
        "height": float(relative.get("height") or 0.0),
    }


def center_in_bbox(node: dict[str, Any], bbox: dict[str, Any]) -> bool:
    cx, cy = bbox_center(node.get("bbox_absolute") or {})
    x = float(bbox.get("x") or 0.0)
    y = float(bbox.get("y") or 0.0)
    w = float(bbox.get("width") or 0.0)
    h = float(bbox.get("height") or 0.0)
    return x <= cx <= x + w and y <= cy <= y + h


def collect_region_nodes(manifest: dict[str, Any], bbox: dict[str, Any]) -> list[dict[str, Any]]:
    return [node for node in manifest.get("nodes") or [] if center_in_bbox(node, bbox)]


def find_variant_label(actual_manifest: dict[str, Any], panel_node: dict[str, Any]) -> str:
    panel_bbox = panel_node.get("bbox_absolute") or {}
    panel_x = float(panel_bbox.get("x") or 0.0)
    column_start = float(int(panel_x // 1000.0) * 1000)
    column_end = column_start + 1000.0
    candidates = []
    for node in actual_manifest.get("nodes") or []:
        if not node.get("is_text"):
            continue
        if node.get("depth") != 1:
            continue
        name = (node.get("node_name") or "").strip()
        if not name:
            continue
        if name.startswith("직사각형 "):
            continue
        bbox = node.get("bbox_absolute") or {}
        x = float(bbox.get("x") or 0.0)
        y = float(bbox.get("y") or 0.0)
        if not (column_start <= x < column_end):
            continue
        if y > 50.0:
            continue
        if float(bbox.get("y") or 0.0) > float(panel_bbox.get("y") or 0.0):
            continue
        distance = abs(float(bbox.get("x") or 0.0) - float(panel_bbox.get("x") or 0.0))
        candidates.append((distance, name))
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1] if candidates else panel_node.get("node_name") or "unknown"


def short_text(node: dict[str, Any]) -> bool:
    text = (node.get("normalized_text") or "").strip()
    return bool(text) and len(text) <= 4


def summarize_region(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    type_counts = Counter(node.get("node_type") or "" for node in nodes)
    text_nodes = [node for node in nodes if node.get("is_text")]
    frame_like_nodes = [node for node in nodes if node.get("is_frame_like")]
    short_text_nodes = [node for node in text_nodes if short_text(node)]
    ordered = sorted(nodes, key=lambda node: int(node.get("global_paint_order") or 0))
    return {
        "node_count": len(nodes),
        "text_count": len(text_nodes),
        "frame_like_count": len(frame_like_nodes),
        "vector_count": sum(1 for node in nodes if node.get("is_vector")),
        "short_text_count": len(short_text_nodes),
        "short_text_ratio": round(len(short_text_nodes) / len(text_nodes), 4) if text_nodes else 0.0,
        "type_counts": dict(type_counts),
        "paint_order_sample": [
            {
                "order": node.get("global_paint_order"),
                "type": node.get("node_type"),
                "name": node.get("node_name"),
                "text": (node.get("normalized_text") or "")[:40],
            }
            for node in ordered[:20]
        ],
    }


def build_text_index(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if not node.get("is_text"):
            continue
        key = (node.get("normalized_text") or "").strip()
        if not key:
            continue
        index.setdefault(key, []).append(node)
    return index


def best_match_delta(
    reference_nodes: list[dict[str, Any]],
    actual_nodes: list[dict[str, Any]],
    *,
    reference_origin: dict[str, Any],
    actual_origin: dict[str, Any],
) -> list[dict[str, Any]]:
    reference_remaining = reference_nodes[:]
    deltas: list[dict[str, Any]] = []
    reference_origin_x = float(reference_origin.get("x") or 0.0)
    reference_origin_y = float(reference_origin.get("y") or 0.0)
    actual_origin_x = float(actual_origin.get("x") or 0.0)
    actual_origin_y = float(actual_origin.get("y") or 0.0)
    for actual in actual_nodes:
        if not reference_remaining:
            break
        ax, ay = bbox_center(actual.get("bbox_absolute") or {})
        ax -= actual_origin_x
        ay -= actual_origin_y
        best_index = 0
        best_distance = None
        for index, reference in enumerate(reference_remaining):
            rx, ry = bbox_center(reference.get("bbox_absolute") or {})
            rx -= reference_origin_x
            ry -= reference_origin_y
            distance = abs(ax - rx) + abs(ay - ry)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_index = index
        reference = reference_remaining.pop(best_index)
        actual_bbox = actual.get("bbox_absolute") or {}
        reference_bbox = reference.get("bbox_absolute") or {}
        deltas.append(
            {
                "text": actual.get("normalized_text"),
                "dx": round(
                    (float(actual_bbox.get("x") or 0.0) - actual_origin_x)
                    - (float(reference_bbox.get("x") or 0.0) - reference_origin_x),
                    2,
                ),
                "dy": round(
                    (float(actual_bbox.get("y") or 0.0) - actual_origin_y)
                    - (float(reference_bbox.get("y") or 0.0) - reference_origin_y),
                    2,
                ),
                "dw": round(float(actual_bbox.get("width") or 0.0) - float(reference_bbox.get("width") or 0.0), 2),
                "dh": round(float(actual_bbox.get("height") or 0.0) - float(reference_bbox.get("height") or 0.0), 2),
            }
        )
    return deltas


def compare_texts(
    reference_nodes: list[dict[str, Any]],
    actual_nodes: list[dict[str, Any]],
    *,
    reference_origin: dict[str, Any],
    actual_origin: dict[str, Any],
) -> dict[str, Any]:
    reference_index = build_text_index(reference_nodes)
    actual_index = build_text_index(actual_nodes)
    shared_texts = sorted(set(reference_index) & set(actual_index))
    missing_texts = sorted(set(reference_index) - set(actual_index))
    extra_texts = sorted(set(actual_index) - set(reference_index))
    delta_rows = []
    for text in shared_texts:
        delta_rows.extend(
            best_match_delta(
                reference_index[text],
                actual_index[text],
                reference_origin=reference_origin,
                actual_origin=actual_origin,
            )
        )
    large_deltas = sorted(
        delta_rows,
        key=lambda row: abs(row["dx"]) + abs(row["dy"]) + abs(row["dw"]) + abs(row["dh"]),
        reverse=True,
    )
    return {
        "shared_text_count": len(shared_texts),
        "missing_text_count": len(missing_texts),
        "extra_text_count": len(extra_texts),
        "missing_text_sample": missing_texts[:20],
        "extra_text_sample": extra_texts[:20],
        "largest_bbox_deltas": large_deltas[:20],
    }


def build_report(reference_manifest: dict[str, Any], actual_manifest: dict[str, Any]) -> dict[str, Any]:
    actual_panels = [
        node for node in actual_manifest.get("nodes") or [] if node.get("node_name") == "right_panel_block"
    ]
    actual_panels.sort(key=lambda node: float((node.get("bbox_absolute") or {}).get("x") or 0.0))
    baseline_panel = actual_panels[0]
    baseline_rel_bbox = rel_bbox(actual_manifest.get("page_bounds") or {}, baseline_panel.get("bbox_absolute") or {})
    reference_region_bbox = abs_bbox(reference_manifest.get("page_bounds") or {}, baseline_rel_bbox)
    reference_region_nodes = collect_region_nodes(reference_manifest, reference_region_bbox)
    reference_summary = summarize_region(reference_region_nodes)

    variants = []
    for panel_node in actual_panels:
        variant_name = find_variant_label(actual_manifest, panel_node)
        panel_bbox = panel_node.get("bbox_absolute") or {}
        panel_nodes = collect_region_nodes(actual_manifest, panel_bbox)
        variants.append(
            {
                "variant_name": variant_name,
                "panel_bbox": panel_bbox,
                "summary": summarize_region(panel_nodes),
                "text_compare_to_reference": compare_texts(
                    reference_region_nodes,
                    panel_nodes,
                    reference_origin=reference_region_bbox,
                    actual_origin=panel_bbox,
                ),
            }
        )

    return {
        "kind": "slide-29-analysis-compare-report",
        "reference_source": reference_manifest.get("source_file"),
        "actual_source": actual_manifest.get("source_file"),
        "focus_region": {
            "name": "right_panel_block",
            "relative_bbox_from_actual_baseline": baseline_rel_bbox,
            "reference_region_bbox": reference_region_bbox,
        },
        "reference_region_summary": reference_summary,
        "variants": variants,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a slide 29 compare report from normalized manifests.")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--actual", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = build_report(load_manifest(Path(args.reference)), load_manifest(Path(args.actual)))
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
