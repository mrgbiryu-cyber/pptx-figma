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


def center(bounds):
    return (
        float(bounds.get("x", 0)) + float(bounds.get("width", 0)) / 2.0,
        float(bounds.get("y", 0)) + float(bounds.get("height", 0)) / 2.0,
    )


def normalize_text(value):
    return " ".join(str(value or "").split()).strip().lower()


def candidate_bbox(candidate):
    return candidate.get("bounds_px") or {"x": 0, "y": 0, "width": 0, "height": 0}


def area(bounds):
    return max(float(bounds.get("width", 0)), 0) * max(float(bounds.get("height", 0)), 0)


def intersection_area(a, b):
    ax1 = float(a.get("x", 0))
    ay1 = float(a.get("y", 0))
    ax2 = ax1 + float(a.get("width", 0))
    ay2 = ay1 + float(a.get("height", 0))
    bx1 = float(b.get("x", 0))
    by1 = float(b.get("y", 0))
    bx2 = bx1 + float(b.get("width", 0))
    by2 = by1 + float(b.get("height", 0))
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    return (ix2 - ix1) * (iy2 - iy1)


def iou(a, b):
    inter = intersection_area(a, b)
    if inter <= 0:
        return 0.0
    union = area(a) + area(b) - inter
    return inter / union if union > 0 else 0.0


def candidate_text(candidate):
    text = candidate.get("text") or ""
    if text:
        return normalize_text(text)
    return normalize_text((candidate.get("extra") or {}).get("raw_text"))


def candidate_shape_kind(candidate):
    return normalize_text(((candidate.get("extra") or {}).get("shape_kind")))


def subtype_expected_types(candidate):
    subtype = candidate.get("subtype")
    if subtype == "text_block":
        return {"TEXT"}
    if subtype == "connector":
        return {"VECTOR", "GROUP"}
    if subtype == "group":
        return {"GROUP", "FRAME"}
    if subtype == "section_block":
        return {"GROUP", "FRAME"}
    if subtype == "labeled_shape":
        return {"GROUP", "FRAME", "VECTOR"}
    if subtype == "shape":
        return {"RECTANGLE", "VECTOR", "GROUP", "FRAME"}
    return {"GROUP", "FRAME", "RECTANGLE", "TEXT", "VECTOR"}


def score_type(candidate, ref_node):
    expected = subtype_expected_types(candidate)
    node_type = ref_node.get("node_type", "")
    if node_type in expected:
        return 1.0
    if candidate.get("subtype") in {"shape", "labeled_shape"} and node_type == "TEXT":
        return 0.35
    return 0.0


def score_text(candidate, ref_node):
    ctext = candidate_text(candidate)
    rtext = normalize_text(ref_node.get("text_characters", ""))
    if not ctext or not rtext:
        return 0.0
    if ctext == rtext:
        return 1.0
    if ctext in rtext or rtext in ctext:
        return 0.75
    cwords = set(ctext.split())
    rwords = set(rtext.split())
    if not cwords or not rwords:
        return 0.0
    overlap = len(cwords & rwords) / max(len(cwords), len(rwords))
    return overlap


def score_bbox(candidate, ref_node):
    cb = candidate_bbox(candidate)
    rb = ref_node.get("bbox_absolute") or {}
    iou_score = iou(cb, rb)
    cx, cy = center(cb)
    rx, ry = center(rb)
    dist = math.hypot(cx - rx, cy - ry)
    diag = math.hypot(max(cb.get("width", 1), 1), max(cb.get("height", 1), 1))
    if diag <= 0:
        diag = 1.0
    proximity = max(0.0, 1.0 - dist / max(diag * 3.0, 1.0))
    return max(iou_score, proximity * 0.6)


def score_shape(candidate, ref_node):
    shape_kind = candidate_shape_kind(candidate)
    if not shape_kind:
        return 0.0
    node_name = normalize_text(ref_node.get("node_name", ""))
    if "decision" in shape_kind and "decision" in node_name:
        return 1.0
    if "diamond" in shape_kind and "decision" in node_name:
        return 0.9
    if shape_kind in {"rect", "roundrect"} and ref_node.get("node_type") in {"RECTANGLE", "FRAME", "GROUP"}:
        return 0.6
    if shape_kind in {"line", "bentconnector2", "bentconnector3", "bentconnector4"} and ref_node.get("node_type") == "VECTOR":
        return 0.8
    return 0.0


def score_candidate(candidate, ref_node):
    subtype = candidate.get("subtype")
    bbox_score = score_bbox(candidate, ref_node)
    type_score = score_type(candidate, ref_node)
    text_score = score_text(candidate, ref_node)
    shape_score = score_shape(candidate, ref_node)
    if subtype == "text_block":
        return 0.45 * type_score + 0.35 * text_score + 0.20 * bbox_score
    if subtype == "connector":
        return 0.50 * type_score + 0.35 * bbox_score + 0.15 * shape_score
    if subtype in {"shape", "labeled_shape"}:
        return 0.35 * type_score + 0.35 * bbox_score + 0.20 * text_score + 0.10 * shape_score
    if subtype in {"group", "section_block"}:
        return 0.50 * type_score + 0.50 * bbox_score
    return 0.50 * bbox_score + 0.30 * type_score + 0.20 * text_score


def choose_candidates(candidate, ref_nodes, top_n):
    scored = []
    for ref_node in ref_nodes:
        score = score_candidate(candidate, ref_node)
        if score <= 0.12:
            continue
        scored.append(
            {
                "score": round(score, 4),
                "reference_node_id": ref_node.get("reference_node_id"),
                "reference_parent_id": ref_node.get("reference_parent_id"),
                "node_type": ref_node.get("node_type"),
                "node_name": ref_node.get("node_name"),
                "bbox_absolute": ref_node.get("bbox_absolute"),
                "text_characters": ref_node.get("text_characters"),
                "comparison_level": ref_node.get("comparison_level"),
            }
        )
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored[:top_n]


def reference_nodes_for_matching(reference_manifest):
    nodes = reference_manifest.get("nodes") or []
    allowed = []
    for node in nodes:
        level = node.get("comparison_level")
        if level in {"L1", "L2"}:
            allowed.append(node)
    return allowed


def slide_summary(candidates, mappings):
    subtype_counts = {}
    matched_counts = {}
    weak = 0
    for candidate, mapping in zip(candidates, mappings):
        subtype = candidate.get("subtype", "unknown")
        subtype_counts[subtype] = subtype_counts.get(subtype, 0) + 1
        if mapping["matches"]:
            matched_counts[subtype] = matched_counts.get(subtype, 0) + 1
            if mapping["matches"][0]["score"] < 0.45:
                weak += 1
    return {
        "candidate_count": len(candidates),
        "by_subtype": subtype_counts,
        "matched_by_subtype": matched_counts,
        "weak_top_match_count": weak,
    }


def build_mapping_report(intermediate_path, reference_dir, top_n):
    with intermediate_path.open("r", encoding="utf-8") as handle:
        intermediate = json.load(handle)
    report_pages = []
    for page in intermediate.get("pages") or []:
        slide_no = page.get("slide_no")
        reference_name = SLIDE_TO_REFERENCE.get(slide_no)
        if not reference_name:
            continue
        reference_path = reference_dir / reference_name
        reference_manifest = build_manifest(reference_path)
        reference_nodes = reference_nodes_for_matching(reference_manifest)
        mappings = []
        for candidate in page.get("candidates") or []:
            mappings.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "subtype": candidate.get("subtype"),
                    "title": candidate.get("title"),
                    "text": candidate.get("text", ""),
                    "bounds_px": candidate_bbox(candidate),
                    "shape_kind": (candidate.get("extra") or {}).get("shape_kind"),
                    "source_scope": (candidate.get("extra") or {}).get("source_scope"),
                    "matches": choose_candidates(candidate, reference_nodes, top_n),
                }
            )
        report_pages.append(
            {
                "slide_no": slide_no,
                "reference_file": reference_name,
                "reference_page_name": reference_manifest.get("page_name"),
                "summary": slide_summary(page.get("candidates") or [], mappings),
                "mappings": mappings,
            }
        )
    return {
        "kind": "ppt-to-reference-mapping-report",
        "source_intermediate": intermediate_path.name,
        "pages": report_pages,
    }


def main():
    parser = argparse.ArgumentParser(description="Map PPT intermediate candidates to reference Figma nodes.")
    parser.add_argument("--intermediate", required=True, help="PPT intermediate candidates JSON")
    parser.add_argument("--reference-dir", required=True, help="Directory containing reference-page-*.json")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--top-n", type=int, default=5, help="Top matches per candidate")
    args = parser.parse_args()

    report = build_mapping_report(
        Path(args.intermediate),
        Path(args.reference_dir),
        args.top_n,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
