#!/usr/bin/env python3
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


LEVEL_TOLERANCES = {
    "L1": {"pos_ok": 8, "pos_warn": 24, "size_ok": 8, "size_warn": 20},
    "L2": {"pos_ok": 4, "pos_warn": 12, "size_ok": 4, "size_warn": 10},
}


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def manifest_origin(manifest, rows):
    page_bounds = manifest.get("page_bounds") or {}
    if page_bounds and "x" in page_bounds and "y" in page_bounds:
        return {"x": page_bounds.get("x", 0), "y": page_bounds.get("y", 0)}
    xs = []
    ys = []
    for row in rows:
        bbox = row.get("bbox_absolute") or {}
        if "x" in bbox and "y" in bbox:
            xs.append(bbox["x"])
            ys.append(bbox["y"])
    if not xs or not ys:
        return {"x": 0, "y": 0}
    return {"x": min(xs), "y": min(ys)}


def normalize_bbox(bbox, origin):
    return {
        "x": round((bbox.get("x", 0) - origin.get("x", 0)), 2),
        "y": round((bbox.get("y", 0) - origin.get("y", 0)), 2),
        "width": round(bbox.get("width", 0), 2),
        "height": round(bbox.get("height", 0), 2),
    }


def bbox_diff(reference, actual, reference_origin, actual_origin):
    rb = normalize_bbox(reference.get("bbox_absolute") or {}, reference_origin)
    ab = normalize_bbox(actual.get("bbox_absolute") or {}, actual_origin)
    return {
        "dx": round((ab.get("x", 0) - rb.get("x", 0)), 2),
        "dy": round((ab.get("y", 0) - rb.get("y", 0)), 2),
        "dw": round((ab.get("width", 0) - rb.get("width", 0)), 2),
        "dh": round((ab.get("height", 0) - rb.get("height", 0)), 2),
    }


def classify_bbox(diff, level):
    tolerance = LEVEL_TOLERANCES.get(level or "L2", LEVEL_TOLERANCES["L2"])
    pos_delta = max(abs(diff["dx"]), abs(diff["dy"]))
    size_delta = max(abs(diff["dw"]), abs(diff["dh"]))
    if pos_delta <= tolerance["pos_ok"] and size_delta <= tolerance["size_ok"]:
        return "ok"
    if pos_delta <= tolerance["pos_warn"] and size_delta <= tolerance["size_warn"]:
        return "warn"
    return "critical"


def rotation_delta(reference, actual):
    return abs((actual.get("rotation_hint") or 0) - (reference.get("rotation_hint") or 0))


def classify_rotation(delta):
    if delta >= 15:
        return "critical"
    if delta >= 5:
        return "warn"
    return "ok"


def manifest_map(rows, key):
    result = {}
    for row in rows:
        value = row.get(key)
        if value:
            result[value] = row
    return result


def build_diff(reference_manifest, actual_manifest):
    reference_rows = [row for row in reference_manifest.get("nodes", []) if row.get("comparison_target")]
    actual_rows = [row for row in actual_manifest.get("nodes", []) if row.get("comparison_level") in {"L1", "L2"}]
    actual_by_ref = manifest_map(actual_rows, "reference_node_id")
    reference_origin = manifest_origin(reference_manifest, reference_rows)
    actual_origin = manifest_origin(actual_manifest, actual_rows)

    page_summary = {
        "page_id": reference_manifest.get("page_id"),
        "page_name": reference_manifest.get("page_name"),
        "reference_origin": reference_origin,
        "actual_origin": actual_origin,
        "matched_nodes": 0,
        "missing_nodes": 0,
        "extra_nodes": 0,
        "flip_mismatches": 0,
        "rotation_mismatches": 0,
        "bbox_critical_mismatches": 0,
        "parent_mismatches": 0,
        "parent_uncomparable_nodes": 0,
    }

    diffs = []
    vector_clusters = defaultdict(list)

    for ref in reference_rows:
        actual = actual_by_ref.get(ref.get("reference_node_id"))
        if not actual:
            page_summary["missing_nodes"] += 1
            diffs.append({
                "reference_node_id": ref.get("reference_node_id"),
                "status": "missing_in_actual",
                "node_type": ref.get("node_type"),
                "node_name": ref.get("node_name"),
                "comparison_level": ref.get("comparison_level"),
            })
            continue

        page_summary["matched_nodes"] += 1
        bbox = bbox_diff(ref, actual, reference_origin, actual_origin)
        bbox_status = classify_bbox(bbox, ref.get("comparison_level"))
        flip_x_mismatch = bool(ref.get("flip_x")) != bool(actual.get("flip_x"))
        flip_y_mismatch = bool(ref.get("flip_y")) != bool(actual.get("flip_y"))
        actual_reference_parent_id = actual.get("reference_parent_id") or ""
        parent_comparable = bool(actual_reference_parent_id)
        parent_mismatch = parent_comparable and ((ref.get("reference_parent_id") or "") != actual_reference_parent_id)
        rotation = rotation_delta(ref, actual)
        rotation_status = classify_rotation(rotation)

        if flip_x_mismatch or flip_y_mismatch:
            page_summary["flip_mismatches"] += 1
        if rotation_status != "ok":
            page_summary["rotation_mismatches"] += 1
        if bbox_status == "critical":
            page_summary["bbox_critical_mismatches"] += 1
        if parent_mismatch:
            page_summary["parent_mismatches"] += 1
        if not parent_comparable:
            page_summary["parent_uncomparable_nodes"] += 1

        diff_row = {
            "reference_node_id": ref.get("reference_node_id"),
            "actual_node_id": actual.get("actual_node_id"),
            "node_type": ref.get("node_type"),
            "node_name": ref.get("node_name"),
            "comparison_level": ref.get("comparison_level"),
            "bbox_diff": bbox,
            "bbox_status": bbox_status,
            "flip_x_mismatch": flip_x_mismatch,
            "flip_y_mismatch": flip_y_mismatch,
            "rotation_delta": rotation,
            "rotation_status": rotation_status,
            "parent_comparable": parent_comparable,
            "parent_mismatch": parent_mismatch,
        }
        diffs.append(diff_row)

        if ref.get("node_type") == "VECTOR":
            cluster_key = (
                ref.get("reference_parent_id") or "",
                ref.get("transform_signature") or "",
                ref.get("geometry_count_bucket") or "",
                ref.get("bbox_aspect_bucket") or "",
            )
            vector_clusters[cluster_key].append(diff_row)

    actual_ref_ids = {row.get("reference_node_id") for row in actual_rows if row.get("reference_node_id")}
    reference_ref_ids = {row.get("reference_node_id") for row in reference_rows if row.get("reference_node_id")}
    page_summary["extra_nodes"] = len(actual_ref_ids - reference_ref_ids)

    pattern_summary = []
    flip_counter = Counter()
    for row in diffs:
        if row.get("flip_y_mismatch"):
            flip_counter["flip_y_mismatch"] += 1
        if row.get("flip_x_mismatch"):
            flip_counter["flip_x_mismatch"] += 1
        if row.get("parent_mismatch"):
            flip_counter["parent_mismatch"] += 1
        if not row.get("parent_comparable", True):
            flip_counter["parent_uncomparable"] += 1
    for key, count in flip_counter.items():
        pattern_summary.append({"pattern": key, "count": count})

    vector_cluster_report = []
    for key, rows in vector_clusters.items():
        if len(rows) < 2:
            continue
        avg_dx = round(sum(row["bbox_diff"]["dx"] for row in rows) / len(rows), 2)
        avg_dy = round(sum(row["bbox_diff"]["dy"] for row in rows) / len(rows), 2)
        vector_cluster_report.append({
            "cluster_key": key,
            "member_count": len(rows),
            "avg_dx": avg_dx,
            "avg_dy": avg_dy,
            "flip_y_ratio": round(sum(1 for row in rows if row["flip_y_mismatch"]) / len(rows), 2),
        })

    return {
        "kind": "visual-replay-diff",
        "page_summary": page_summary,
        "pattern_summary": pattern_summary,
        "vector_cluster_report": vector_cluster_report,
        "diffs": diffs,
    }


def main():
    parser = argparse.ArgumentParser(description="Diff reference and actual replay manifests.")
    parser.add_argument("--reference", required=True, help="Reference manifest JSON path")
    parser.add_argument("--actual", required=True, help="Actual manifest JSON path")
    parser.add_argument("--output", required=True, help="Output diff JSON path")
    args = parser.parse_args()

    reference = load_json(args.reference)
    actual = load_json(args.actual)
    diff = build_diff(reference, actual)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(diff, handle, ensure_ascii=False, indent=2)
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
