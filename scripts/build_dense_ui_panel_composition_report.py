#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_json(path: str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bbox(node: dict[str, Any]) -> tuple[float, float, float, float]:
    bounds = node.get("absoluteBoundingBox") or node.get("visual_bounds_px") or {}
    x = float(bounds.get("x") or 0.0)
    y = float(bounds.get("y") or 0.0)
    w = float(bounds.get("width") or 0.0)
    h = float(bounds.get("height") or 0.0)
    return x, y, x + w, y + h


def iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1, ax2, ay2 = bbox(a)
    bx1, by1, bx2, by2 = bbox(b)
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(0.0, (bx2 - bx1) * (by2 - by1))
    denom = area_a + area_b - inter
    return inter / denom if denom else 0.0


def overlap_ratio(inner: dict[str, Any], outer: dict[str, Any]) -> float:
    ix1, iy1, ix2, iy2 = bbox(inner)
    ox1, oy1, ox2, oy2 = bbox(outer)
    cx1 = max(ix1, ox1)
    cy1 = max(iy1, oy1)
    cx2 = min(ix2, ox2)
    cy2 = min(iy2, oy2)
    if cx2 <= cx1 or cy2 <= cy1:
        return 0.0
    inter = (cx2 - cx1) * (cy2 - cy1)
    area = max(0.0, (ix2 - ix1) * (iy2 - iy1))
    return inter / area if area else 0.0


def recommendation_for_chunk(chunk_bucket: dict[str, Any], baseline_hits: list[dict[str, Any]]) -> str:
    chunk_type = str(chunk_bucket.get("chunk_type") or "")
    if chunk_type == "body_text_region":
        return "preserve_or_overlay_text_only"
    if chunk_type in {"footer_note_overlay", "annotation_overlay"}:
        return "overlay_candidate"
    if chunk_type in {"header_band", "meta_grid"}:
        return "replace_only_if_baseline_meta_is_weaker"
    if chunk_type in {"stacked_badges", "issue_card"}:
        return "overlay_or_replace_candidate"
    if chunk_type == "panel_local_assets":
        return "overlay_candidate"
    return "review"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a dense_ui_panel composition report")
    parser.add_argument(
        "--baseline",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "block-bundles" / "block-slide-29.bundle.json"),
    )
    parser.add_argument(
        "--ir-bundle",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "block-bundles" / "ir-dense-ui-panel-29.bundle.json"),
    )
    parser.add_argument(
        "--ir",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "resolved-ppt-ir-12-19-29.json"),
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "dense-ui-panel-composition-report.json"),
    )
    args = parser.parse_args()

    baseline_bundle = load_json(args.baseline)
    ir_bundle = load_json(args.ir_bundle)
    ir_payload = load_json(args.ir)

    baseline_frame = baseline_bundle["document"]["children"][0]
    right_panel = next(child for child in baseline_frame["children"] if child.get("name") == "right_panel_block")
    baseline_layers = [
        {
            "id": str(child.get("id") or ""),
            "name": str(child.get("name") or ""),
            "type": str(child.get("type") or ""),
            "absoluteBoundingBox": child.get("absoluteBoundingBox") or {},
        }
        for child in right_panel.get("children") or []
    ]

    logical_panel = ir_bundle["document"]["children"][0]["children"][0]["children"][0]
    chunk_nodes = [
        {
            "id": str(child.get("id") or ""),
            "name": str(child.get("name") or ""),
            "type": str(child.get("type") or ""),
            "absoluteBoundingBox": child.get("absoluteBoundingBox") or {},
        }
        for child in logical_panel.get("children") or []
    ]

    page = next(pg for pg in ir_payload["pages"] if int(pg.get("slide_no") or 0) == 29)
    chunk_bucket_map = {bucket["chunk_id"]: bucket for bucket in page.get("chunk_buckets") or []}

    chunk_analysis = []
    for chunk in chunk_nodes:
        chunk_id = chunk["id"]
        bucket = chunk_bucket_map.get(chunk_id, {})
        overlaps = []
        for baseline_layer in baseline_layers:
            iou_score = iou(chunk, baseline_layer)
            contain_score = overlap_ratio(chunk, baseline_layer)
            if iou_score > 0.01 or contain_score > 0.2:
                overlaps.append(
                    {
                        "baseline_name": baseline_layer["name"],
                        "baseline_type": baseline_layer["type"],
                        "iou": round(iou_score, 3),
                        "chunk_inside_ratio": round(contain_score, 3),
                    }
                )
        overlaps.sort(key=lambda item: (item["chunk_inside_ratio"], item["iou"]), reverse=True)
        chunk_analysis.append(
            {
                "chunk_id": chunk_id,
                "chunk_type": bucket.get("chunk_type"),
                "render_strategy": bucket.get("render_strategy"),
                "text_composition": bucket.get("text_composition"),
                "style_policy": bucket.get("style_policy"),
                "asset_scope": bucket.get("asset_scope"),
                "atom_count": bucket.get("atom_count"),
                "features": bucket.get("features") or {},
                "recommended_composition_policy": recommendation_for_chunk(bucket, overlaps),
                "baseline_overlaps": overlaps,
            }
        )

    report = {
        "report_version": "dense-ui-panel-composition-report-v1",
        "baseline_right_panel_layers": baseline_layers,
        "chunk_analysis": chunk_analysis,
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
