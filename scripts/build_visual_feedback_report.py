#!/usr/bin/env python3
import argparse
import json
import math
from pathlib import Path

from build_figma_page_manifest import build_manifest


SLIDE_TO_REFERENCE = {
    12: "reference-page-1.json",
    19: "reference-page-2.json",
    29: "reference-page-3.json",
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def mean(values):
    return round(sum(values) / len(values), 3) if values else None


def bbox_delta(a, b):
    ax = float(a.get("x", 0))
    ay = float(a.get("y", 0))
    aw = float(a.get("width", 0))
    ah = float(a.get("height", 0))
    bx = float(b.get("x", 0))
    by = float(b.get("y", 0))
    bw = float(b.get("width", 0))
    bh = float(b.get("height", 0))
    return {
        "dx": round(bx - ax, 2),
        "dy": round(by - ay, 2),
        "dw": round(bw - aw, 2),
        "dh": round(bh - ah, 2),
    }


def find_candidate(page, candidate_id):
    for candidate in page.get("candidates") or []:
        if candidate.get("candidate_id") == candidate_id:
            return candidate
    return None


def build_reference_index(reference_manifest):
    return {node.get("reference_node_id"): node for node in reference_manifest.get("nodes") or []}


def text_feedback(page, mappings, reference_index):
    rows = []
    font_ratios = []
    width_ratios = []
    weak = []
    for mapping in mappings:
        if mapping.get("subtype") != "text_block" or not mapping.get("matches"):
            continue
        ref_id = mapping["matches"][0]["reference_node_id"]
        ref = reference_index.get(ref_id)
        candidate = find_candidate(page, mapping["candidate_id"])
        if not ref or not candidate:
            continue
        c_style = (candidate.get("extra") or {}).get("text_style") or {}
        c_font = c_style.get("font_size_max") or c_style.get("font_size_avg")
        r_font = ref.get("font_size")
        c_bbox = candidate.get("bounds_px") or {}
        r_bbox = ref.get("bbox_absolute") or {}
        if c_font and r_font:
            font_ratios.append(float(r_font) / float(c_font))
        if c_bbox.get("width") and r_bbox.get("width"):
            width_ratios.append(float(r_bbox["width"]) / float(c_bbox["width"]))
        row = {
            "candidate_id": candidate.get("candidate_id"),
            "title": candidate.get("title"),
            "text": candidate.get("text", ""),
            "score": mapping["matches"][0]["score"],
            "candidate_font_size": c_font,
            "reference_font_size": r_font,
            "candidate_bbox": c_bbox,
            "reference_bbox": r_bbox,
            "bbox_delta": bbox_delta(c_bbox, r_bbox),
        }
        rows.append(row)
        if mapping["matches"][0]["score"] < 0.7:
            weak.append(row)
    return {
        "avg_font_ratio_ref_over_source": mean(font_ratios),
        "avg_width_ratio_ref_over_source": mean(width_ratios),
        "sample_rows": rows[:12],
        "weak_rows": weak[:12],
    }


def connector_feedback(page, mappings, reference_index):
    rows = []
    deltas = []
    for mapping in mappings:
        if mapping.get("subtype") != "connector" or not mapping.get("matches"):
            continue
        ref_id = mapping["matches"][0]["reference_node_id"]
        ref = reference_index.get(ref_id)
        candidate = find_candidate(page, mapping["candidate_id"])
        if not ref or not candidate:
            continue
        c_bbox = candidate.get("bounds_px") or {}
        r_bbox = ref.get("bbox_absolute") or {}
        delta = bbox_delta(c_bbox, r_bbox)
        deltas.append(abs(delta["dx"]) + abs(delta["dy"]))
        rows.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "title": candidate.get("title"),
                "shape_kind": ((candidate.get("extra") or {}).get("shape_kind")),
                "score": mapping["matches"][0]["score"],
                "reference_node_type": ref.get("node_type"),
                "reference_name": ref.get("node_name"),
                "candidate_bbox": c_bbox,
                "reference_bbox": r_bbox,
                "bbox_delta": delta,
            }
        )
    return {
        "avg_xy_error": mean(deltas),
        "sample_rows": rows[:12],
    }


def shape_feedback(page, mappings):
    distribution = {}
    weak = []
    for mapping in mappings:
        if mapping.get("subtype") not in {"shape", "labeled_shape"} or not mapping.get("matches"):
            continue
        candidate = find_candidate(page, mapping["candidate_id"])
        if not candidate:
            continue
        shape_kind = str(((candidate.get("extra") or {}).get("shape_kind")) or "unknown")
        node_type = mapping["matches"][0]["node_type"]
        distribution.setdefault(shape_kind, {})
        distribution[shape_kind][node_type] = distribution[shape_kind].get(node_type, 0) + 1
        if mapping["matches"][0]["score"] < 0.5:
            weak.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "shape_kind": shape_kind,
                    "title": candidate.get("title"),
                    "score": mapping["matches"][0]["score"],
                    "top_match_type": node_type,
                    "top_match_name": mapping["matches"][0]["node_name"],
                }
            )
    return {
        "shape_kind_distribution": distribution,
        "weak_rows": weak[:20],
    }


def table_feedback(page, mappings):
    table_rows = []
    table_cells = []
    for mapping in mappings:
        subtype = mapping.get("subtype")
        if subtype not in {"table", "table_row", "table_cell"} or not mapping.get("matches"):
            continue
        candidate = find_candidate(page, mapping["candidate_id"])
        if not candidate:
            continue
        row = {
            "candidate_id": candidate.get("candidate_id"),
            "subtype": subtype,
            "title": candidate.get("title"),
            "text": candidate.get("text", ""),
            "score": mapping["matches"][0]["score"],
            "top_match_type": mapping["matches"][0]["node_type"],
            "top_match_name": mapping["matches"][0]["node_name"],
        }
        if subtype == "table_cell":
            table_cells.append(row)
        else:
            table_rows.append(row)
    return {
        "avg_table_score": mean([r["score"] for r in table_rows]),
        "avg_table_cell_score": mean([r["score"] for r in table_cells]),
        "weak_table_rows": [r for r in table_rows if r["score"] < 0.45][:20],
        "weak_table_cells": [r for r in table_cells if r["score"] < 0.45][:20],
    }


def header_feedback(page, mappings):
    top_band = []
    for mapping in mappings:
        candidate = find_candidate(page, mapping["candidate_id"])
        if not candidate:
            continue
        bbox = candidate.get("bounds_px") or {}
        if float(bbox.get("y", 9999)) > 90:
            continue
        top_match = (mapping.get("matches") or [{}])[0]
        top_band.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "subtype": candidate.get("subtype"),
                "title": candidate.get("title"),
                "text": candidate.get("text", ""),
                "shape_kind": ((candidate.get("extra") or {}).get("shape_kind")),
                "source_scope": ((candidate.get("extra") or {}).get("source_scope")),
                "bounds_px": bbox,
                "top_match_type": top_match.get("node_type"),
                "top_match_name": top_match.get("node_name"),
                "top_match_score": top_match.get("score"),
            }
        )
    top_band.sort(key=lambda row: (row["bounds_px"].get("y", 0), row["bounds_px"].get("x", 0)))
    return {
        "top_band_rows": top_band[:20],
    }


def build_report(intermediate_path: Path, mapping_report_path: Path, reference_dir: Path):
    intermediate = load_json(intermediate_path)
    mapping_report = load_json(mapping_report_path)
    pages_by_slide = {int(page["slide_no"]): page for page in intermediate.get("pages") or []}

    result_pages = []
    for page_report in mapping_report.get("pages") or []:
        slide_no = int(page_report["slide_no"])
        page = pages_by_slide[slide_no]
        reference_manifest = build_manifest(reference_dir / SLIDE_TO_REFERENCE[slide_no])
        reference_index = build_reference_index(reference_manifest)
        result_pages.append(
            {
                "slide_no": slide_no,
                "reference_file": SLIDE_TO_REFERENCE[slide_no],
                "text_feedback": text_feedback(page, page_report["mappings"], reference_index),
                "connector_feedback": connector_feedback(page, page_report["mappings"], reference_index),
                "shape_feedback": shape_feedback(page, page_report["mappings"]),
                "table_feedback": table_feedback(page, page_report["mappings"]),
                "header_feedback": header_feedback(page, page_report["mappings"]),
            }
        )
    return {
        "kind": "visual-feedback-report",
        "source_intermediate": intermediate_path.name,
        "source_mapping_report": mapping_report_path.name,
        "pages": result_pages,
    }


def main():
    parser = argparse.ArgumentParser(description="Build detailed visual feedback report from PPT-to-reference mappings.")
    parser.add_argument("--intermediate", required=True)
    parser.add_argument("--mapping-report", required=True)
    parser.add_argument("--reference-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = build_report(
        Path(args.intermediate),
        Path(args.mapping_report),
        Path(args.reference_dir),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
