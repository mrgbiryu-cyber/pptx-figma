#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def key_by(rows, field):
    result = {}
    for row in rows:
        value = row.get(field)
        if value:
            result[value] = row
    return result


def normalized_text_tokens(value):
    raw = " ".join(str(value or "").replace("\n", " ").split()).strip()
    if not raw:
        return set()
    cleaned = re.sub(r"[\[\]\(\)\-\+\*•★,:;/]+", " ", raw)
    return {token for token in cleaned.split() if len(token) >= 2}


def text_similarity(reference_row, generated_row):
    ref_text = reference_row.get("text_characters") or reference_row.get("node_name") or ""
    gen_text = generated_row.get("text_characters") or generated_row.get("node_name") or ""
    if not ref_text or not gen_text:
        return 0.0
    if ref_text == gen_text:
        return 1.0
    ref_tokens = normalized_text_tokens(ref_text)
    gen_tokens = normalized_text_tokens(gen_text)
    if not ref_tokens or not gen_tokens:
        if ref_text in gen_text or gen_text in ref_text:
            shorter = min(len(ref_text), len(gen_text))
            longer = max(len(ref_text), len(gen_text))
            return shorter / max(longer, 1)
        return 0.0
    overlap = len(ref_tokens.intersection(gen_tokens))
    return overlap / max(1, min(len(ref_tokens), len(gen_tokens)))


def normalize_bbox(bbox, origin):
    return {
        "x": float(bbox.get("x", 0) - origin.get("x", 0)),
        "y": float(bbox.get("y", 0) - origin.get("y", 0)),
        "width": float(bbox.get("width", 0)),
        "height": float(bbox.get("height", 0)),
    }


def bbox_delta(ref_bbox, gen_bbox, ref_origin, gen_origin):
    ref_local = normalize_bbox(ref_bbox, ref_origin)
    gen_local = normalize_bbox(gen_bbox, gen_origin)
    return max(abs(gen_local.get("x", 0) - ref_local.get("x", 0)), abs(gen_local.get("y", 0) - ref_local.get("y", 0)))


def mean(values):
    return round(sum(values) / len(values), 2) if values else None


def bundle_signals(manifest):
    bundle_debug = manifest.get("bundle_debug") or {}
    visual_strategy = bundle_debug.get("visual_strategy") or {}
    if isinstance(visual_strategy, dict):
        return visual_strategy.get("signals") or {}
    document_debug = manifest.get("document_debug") or {}
    return document_debug.get("strategy_signals") or {}


def top_band_rows(rows, page_bounds, ratio=0.18):
    cutoff = float((page_bounds or {}).get("y", 0)) + float((page_bounds or {}).get("height", 0)) * ratio
    result = []
    for row in rows:
        bbox = row.get("bbox_absolute") or {}
        top = float(bbox.get("y", 0))
        if top <= cutoff:
            result.append(row)
    return result


def build_mapping(reference_rows, generated_rows):
    generated_by_semantic = {}
    generated_by_name_type = {}
    generated_text_rows = []
    for row in generated_rows:
        generated_by_semantic.setdefault(row.get("semantic_key"), []).append(row)
        generated_by_name_type.setdefault((row.get("node_type"), row.get("node_name")), []).append(row)
        if row.get("node_type") == "TEXT":
            generated_text_rows.append(row)

    pairs = []
    used_generated = set()

    for ref in reference_rows:
        candidates = []
        semantic = ref.get("semantic_key")
        if semantic and semantic in generated_by_semantic:
            candidates.extend(generated_by_semantic[semantic])
        pair_key = (ref.get("node_type"), ref.get("node_name"))
        if pair_key in generated_by_name_type:
            candidates.extend(generated_by_name_type[pair_key])

        picked = None
        ref_bbox = ref.get("bbox_absolute") or {}
        for candidate in candidates:
            gen_id = candidate.get("reference_node_id")
            if gen_id in used_generated:
                continue
            if picked is None:
                picked = candidate
                continue
            cur_bbox = picked.get("bbox_absolute") or {}
            cand_bbox = candidate.get("bbox_absolute") or {}
            cur_score = abs(cur_bbox.get("x", 0) - ref_bbox.get("x", 0)) + abs(cur_bbox.get("y", 0) - ref_bbox.get("y", 0))
            cand_score = abs(cand_bbox.get("x", 0) - ref_bbox.get("x", 0)) + abs(cand_bbox.get("y", 0) - ref_bbox.get("y", 0))
            if cand_score < cur_score:
                picked = candidate

        if picked is None and ref.get("node_type") == "TEXT":
            best_score = 0.0
            for candidate in generated_text_rows:
                gen_id = candidate.get("reference_node_id")
                if gen_id in used_generated:
                    continue
                similarity = text_similarity(ref, candidate)
                if similarity < 0.55:
                    continue
                if similarity > best_score:
                    best_score = similarity
                    picked = candidate

        if picked:
            used_generated.add(picked.get("reference_node_id"))
            pairs.append((ref, picked))
        else:
            pairs.append((ref, None))

    extra = [row for row in generated_rows if row.get("reference_node_id") not in used_generated]
    return pairs, extra


def canvas_metrics(reference_manifest, generated_manifest):
    ref = reference_manifest.get("page_bounds") or {}
    gen = generated_manifest.get("page_bounds") or {}
    size_match = round(abs(ref.get("width", 0) - gen.get("width", 0)), 2) == 0 and round(abs(ref.get("height", 0) - gen.get("height", 0)), 2) == 0
    return {
        "reference_page_bounds": ref,
        "generated_page_bounds": gen,
        "size_match": size_match,
        "width_delta": round(gen.get("width", 0) - ref.get("width", 0), 2),
        "height_delta": round(gen.get("height", 0) - ref.get("height", 0), 2),
    }


def score_canvas(metrics):
    return 100 if metrics["size_match"] else 0


def score_text(reference_rows, generated_rows, pairs, reference_origin, generated_origin):
    ref_text = [r for r in reference_rows if r.get("node_type") == "TEXT"]
    pair_text = [(r, g) for r, g in pairs if r.get("node_type") == "TEXT"]
    if not ref_text:
        return {"score": 100, "coverage_ratio": 1.0, "mean_bbox_delta": 0, "mean_font_size_delta": 0}

    coverage_ratio = round(sum(1 for _, g in pair_text if g) / len(ref_text), 2)
    bbox_deltas = []
    font_size_deltas = []
    for ref, gen in pair_text:
        if not gen:
            continue
        rb = ref.get("bbox_absolute") or {}
        gb = gen.get("bbox_absolute") or {}
        bbox_deltas.append(bbox_delta(rb, gb, reference_origin, generated_origin))
        rf = ref.get("font_size")
        gf = gen.get("font_size")
        if rf is not None and gf is not None:
            font_size_deltas.append(abs(gf - rf))
    mean_bbox_delta = mean(bbox_deltas) or 999
    mean_font_size_delta = mean(font_size_deltas) or 999
    score = max(0, min(100, round((coverage_ratio * 55) + max(0, 25 - mean_bbox_delta * 2) + max(0, 20 - mean_font_size_delta * 5))))
    return {
        "score": score,
        "coverage_ratio": coverage_ratio,
        "mean_bbox_delta": mean_bbox_delta,
        "mean_font_size_delta": mean_font_size_delta,
    }


def score_vector(reference_rows, generated_rows):
    ref_vectors = [r for r in reference_rows if r.get("node_type") == "VECTOR"]
    gen_vectors = [r for r in generated_rows if r.get("node_type") == "VECTOR"]
    if not ref_vectors:
        return {"score": 100, "vector_count_ratio": 1.0}
    ratio = round(len(gen_vectors) / len(ref_vectors), 2)
    score = max(0, min(100, round(min(ratio, 1.0) * 100)))
    return {
        "score": score,
        "reference_vector_count": len(ref_vectors),
        "generated_vector_count": len(gen_vectors),
        "vector_count_ratio": ratio,
    }


def score_connector(reference_rows, generated_rows, generated_manifest):
    ref_connectors = [r for r in reference_rows if r.get("node_type") == "VECTOR" and r.get("bbox_aspect_bucket") in {"ULTRA_WIDE", "WIDE"}]
    signals = bundle_signals(generated_manifest)
    expected_connectors = int(signals.get("connector_count") or 0)
    generated_connector_roots = []
    seen = set()
    for row in generated_rows:
        if row.get("source_subtype") != "connector":
            continue
        role = row.get("replay_role") or ""
        if role not in {"connector_group", "connector_line"}:
            continue
        stable_id = row.get("source_node_id") or row.get("source_path") or row.get("reference_node_id")
        if stable_id in seen:
            continue
        seen.add(stable_id)
        generated_connector_roots.append(row)
    if expected_connectors > 0:
        ratio = round(len(generated_connector_roots) / expected_connectors, 2)
        missing_ratio = round(max(0, 1.0 - ratio), 2)
        score = max(0, min(100, round((1.0 - missing_ratio) * 100)))
    elif not ref_connectors:
        ratio = 1.0
        missing_ratio = 0.0
        score = 100
    else:
        gen_connectors = [r for r in generated_rows if r.get("node_type") == "VECTOR" and r.get("bbox_aspect_bucket") in {"ULTRA_WIDE", "WIDE"}]
        ratio = round(len(gen_connectors) / len(ref_connectors), 2)
        missing_ratio = round(max(0, 1.0 - ratio), 2)
        score = max(0, min(100, round((1.0 - missing_ratio) * 100)))
    return {
        "score": score,
        "reference_connector_proxy_count": len(ref_connectors),
        "generated_connector_root_count": len(generated_connector_roots),
        "expected_connector_count": expected_connectors,
        "missing_connector_ratio": missing_ratio,
    }


def score_table(reference_rows, generated_rows, reference_manifest, generated_manifest):
    signals = bundle_signals(generated_manifest)
    expected_cells = int(signals.get("table_cell_count") or 0)
    expected_text_cells = int(signals.get("table_text_cell_count") or expected_cells)
    generated_cell_frames = [r for r in generated_rows if r.get("node_type") == "FRAME" and str(r.get("node_name", "")).startswith("cell ")]
    generated_cell_text_rows = [r for r in generated_rows if r.get("node_type") == "TEXT" and r.get("source_subtype") == "table_cell"]
    generated_cell_text_sources = {
        (r.get("source_path") or r.get("source_node_id") or r.get("reference_parent_id") or r.get("reference_node_id"))
        for r in generated_cell_text_rows
        if (r.get("source_path") or r.get("source_node_id") or r.get("reference_parent_id") or r.get("reference_node_id"))
    }
    if expected_cells > 0:
        frame_ratio = round(len(generated_cell_frames) / expected_cells, 2)
        text_ratio = round(len(generated_cell_text_sources) / max(expected_text_cells, 1), 2)
        clamped_frame_ratio = min(frame_ratio, 1.0)
        clamped_text_ratio = min(text_ratio, 1.0)
        header_ref = top_band_rows(reference_rows, reference_manifest.get("page_bounds"), ratio=0.2)
        header_gen = top_band_rows(generated_rows, generated_manifest.get("page_bounds"), ratio=0.2)
        header_ref_text = sum(1 for row in header_ref if row.get("node_type") == "TEXT")
        header_gen_text = sum(1 for row in header_gen if row.get("node_type") == "TEXT")
        header_ratio = 1.0 if header_ref_text == 0 else min(round(header_gen_text / header_ref_text, 2), 1.0)
        score = max(0, min(100, round((clamped_frame_ratio * 55 + clamped_text_ratio * 25 + header_ratio * 20) * 100 / 100)))
        ratio = clamped_frame_ratio
    else:
        ref_cells = [r for r in reference_rows if r.get("node_name", "").startswith("cell ")]
        gen_cells = [r for r in generated_rows if r.get("node_name", "").startswith("cell ")]
        if not ref_cells and not gen_cells:
            return {"score": 100, "cell_presence_ratio": 1.0}
        if not ref_cells:
            return {"score": 0, "cell_presence_ratio": 0.0}
        ratio = round(len(gen_cells) / len(ref_cells), 2)
        score = max(0, min(100, round(min(ratio, 1.0) * 100)))
    return {
        "score": score,
        "expected_cell_count": expected_cells,
        "expected_text_cell_count": expected_text_cells,
        "generated_cell_frame_count": len(generated_cell_frames),
        "generated_cell_text_count": len(generated_cell_text_sources),
        "cell_presence_ratio": ratio,
    }


def score_header(reference_rows, generated_rows, reference_manifest, generated_manifest):
    header_ref = top_band_rows(reference_rows, reference_manifest.get("page_bounds"), ratio=0.2)
    header_gen = top_band_rows(generated_rows, generated_manifest.get("page_bounds"), ratio=0.2)
    ref_text = sum(1 for row in header_ref if row.get("node_type") == "TEXT")
    gen_text = sum(1 for row in header_gen if row.get("node_type") == "TEXT")
    ref_shape = sum(1 for row in header_ref if row.get("node_type") in {"VECTOR", "RECTANGLE", "FRAME", "GROUP"} and row.get("node_name") != reference_manifest.get("page_name"))
    gen_shape = sum(1 for row in header_gen if row.get("node_type") in {"VECTOR", "RECTANGLE", "FRAME", "GROUP"} and row.get("node_name") != generated_manifest.get("page_name"))
    text_ratio = 1.0 if ref_text == 0 else min(round(gen_text / ref_text, 2), 1.0)
    shape_ratio = 1.0 if ref_shape == 0 else min(round(gen_shape / ref_shape, 2), 1.0)
    score = round((text_ratio * 60 + shape_ratio * 40) * 100 / 100)
    return {
        "score": score,
        "reference_header_text_count": ref_text,
        "generated_header_text_count": gen_text,
        "reference_header_shape_count": ref_shape,
        "generated_header_shape_count": gen_shape,
        "header_text_ratio": text_ratio,
        "header_shape_ratio": shape_ratio,
    }


def score_shape(reference_rows, generated_rows, reference_origin, generated_origin):
    ref_diamond = [r for r in reference_rows if r.get("node_type") == "VECTOR" and "Google Shape;472" in (r.get("node_name") or "")]
    gen_diamond = [r for r in generated_rows if "Google Shape;472" in (r.get("node_name") or "")]
    if not ref_diamond:
        return {"score": 100, "decision_shape_present": True}
    present = bool(gen_diamond)
    center_delta = None
    if present:
        rb = normalize_bbox(ref_diamond[0].get("bbox_absolute") or {}, reference_origin)
        gb = normalize_bbox(gen_diamond[0].get("bbox_absolute") or {}, generated_origin)
        rcx = rb.get("x", 0) + rb.get("width", 0) / 2
        rcy = rb.get("y", 0) + rb.get("height", 0) / 2
        gcx = gb.get("x", 0) + gb.get("width", 0) / 2
        gcy = gb.get("y", 0) + gb.get("height", 0) / 2
        center_delta = round(max(abs(gcx - rcx), abs(gcy - rcy)), 2)
    score = 100 if present and (center_delta is None or center_delta <= 10) else 40 if present else 0
    return {
        "score": score,
        "decision_shape_present": present,
        "decision_center_delta": center_delta,
    }


def score_overlay(reference_rows, generated_rows):
    overlays = [r for r in generated_rows if r.get("is_fullpage_overlay_candidate")]
    blocking = len(overlays)
    return {
        "score": 100 if blocking == 0 else 0,
        "blocking_overlay_count": blocking,
    }


def evaluate_page(reference_manifest, generated_manifest):
    reference_rows = [row for row in reference_manifest.get("nodes", []) if row.get("comparison_target")]
    generated_rows = [row for row in generated_manifest.get("nodes", []) if row.get("comparison_target")]
    pairs, extra_rows = build_mapping(reference_rows, generated_rows)

    reference_origin = reference_manifest.get("page_bounds") or {}
    generated_origin = generated_manifest.get("page_bounds") or {}
    metrics = {
        "canvas": canvas_metrics(reference_manifest, generated_manifest),
        "text": score_text(reference_rows, generated_rows, pairs, reference_origin, generated_origin),
        "vector": score_vector(reference_rows, generated_rows),
        "connector": score_connector(reference_rows, generated_rows, generated_manifest),
        "table": score_table(reference_rows, generated_rows, reference_manifest, generated_manifest),
        "header": score_header(reference_rows, generated_rows, reference_manifest, generated_manifest),
        "shape": score_shape(reference_rows, generated_rows, reference_origin, generated_origin),
        "overlay": score_overlay(reference_rows, generated_rows),
    }
    metrics["canvas"]["score"] = score_canvas(metrics["canvas"])

    weights = {
        "canvas": 10,
        "text": 20,
        "vector": 10,
        "connector": 20,
        "table": 20,
        "header": 10,
        "shape": 5,
        "overlay": 5,
    }
    total_score = round(sum(metrics[name]["score"] * weight for name, weight in weights.items()) / 100, 2)

    fail_reasons = []
    if not metrics["canvas"]["size_match"]:
        fail_reasons.append("canvas_size_mismatch")
    if metrics["overlay"]["blocking_overlay_count"] > 0:
        fail_reasons.append("blocking_overlay_present")
    if metrics["connector"]["missing_connector_ratio"] > 0.2:
        fail_reasons.append("connector_missing_ratio_high")
    if metrics["table"].get("cell_presence_ratio", 1.0) < 0.9:
        fail_reasons.append("table_cell_presence_low")
    if metrics["header"].get("header_text_ratio", 1.0) < 0.8:
        fail_reasons.append("header_text_presence_low")
    if not metrics["shape"]["decision_shape_present"]:
        fail_reasons.append("decision_shape_missing")

    if fail_reasons:
        status = "FAIL"
    elif total_score < 80:
        status = "HOLD"
    else:
        status = "PASS"

    return {
        "page_id": reference_manifest.get("page_id"),
        "page_name": reference_manifest.get("page_name"),
        "status": status,
        "score": total_score,
        "fail_reasons": fail_reasons,
        "counts": {
            "reference_target_nodes": len(reference_rows),
            "generated_target_nodes": len(generated_rows),
            "extra_generated_nodes": len(extra_rows),
            "matched_proxy_count": sum(1 for _, row in pairs if row),
        },
        "metrics": metrics,
    }


def main():
    parser = argparse.ArgumentParser(description="Run replay generator QA gate against reference/generated page manifests.")
    parser.add_argument("--reference", nargs="+", required=True, help="Reference manifest JSON paths")
    parser.add_argument("--generated", nargs="+", required=True, help="Generated manifest JSON paths")
    parser.add_argument("--output", required=True, help="Output QA report path")
    args = parser.parse_args()

    reference_paths = [Path(path).resolve() for path in args.reference]
    generated_paths = [Path(path).resolve() for path in args.generated]
    if len(reference_paths) != len(generated_paths):
        raise SystemExit("reference/generated file count must match")

    page_reports = []
    for ref_path, gen_path in zip(reference_paths, generated_paths):
        reference_manifest = load_json(ref_path)
        generated_manifest = load_json(gen_path)
        page_reports.append(evaluate_page(reference_manifest, generated_manifest))

    status = "PASS"
    if any(page["status"] == "FAIL" for page in page_reports):
        status = "FAIL"
    elif any(page["status"] == "HOLD" for page in page_reports):
        status = "HOLD"

    report = {
        "kind": "replay-generator-qa-gate",
        "status": status,
        "page_reports": page_reports,
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"saved {output_path}")
    print(f"status={status}")
    for page in page_reports:
        print(f"{page['page_name']}: {page['status']} score={page['score']}")


if __name__ == "__main__":
    main()
