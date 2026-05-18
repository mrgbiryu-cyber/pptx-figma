#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def bundle_signals(manifest):
    bundle_debug = manifest.get("bundle_debug") or {}
    visual_strategy = bundle_debug.get("visual_strategy") or {}
    if isinstance(visual_strategy, dict):
        return visual_strategy.get("signals") or {}
    document_debug = manifest.get("document_debug") or {}
    return document_debug.get("strategy_signals") or {}


def top_band_texts(manifest, ratio=0.2):
    page_bounds = manifest.get("page_bounds") or {}
    cutoff = float(page_bounds.get("y", 0)) + float(page_bounds.get("height", 0)) * ratio
    rows = []
    for row in manifest.get("nodes") or []:
        if row.get("node_type") != "TEXT":
            continue
        bbox = row.get("bbox_absolute") or {}
        if float(bbox.get("y", 0)) > cutoff:
            continue
        text = str(row.get("text_characters") or row.get("node_name") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "text": text,
                "bbox": bbox,
                "source_scope": row.get("source_scope", ""),
                "source_subtype": row.get("source_subtype", ""),
            }
        )
    return rows


def normalized_text_variants(value):
    raw = str(value or "").replace("\n", " ")
    collapsed = " ".join(raw.split()).strip()
    variants = {collapsed}
    if "+" in collapsed:
        variants.update(part.strip() for part in collapsed.split("+") if part.strip())
    if "(" in collapsed and ")" in collapsed:
        variants.update(part.strip() for part in collapsed.replace("(", " ").replace(")", " ").split() if part.strip())
    return {variant for variant in variants if variant}


def normalized_text_tokens(value):
    raw = " ".join(str(value or "").replace("\n", " ").split()).strip()
    if not raw:
        return set()
    cleaned = re.sub(r"[\[\]\(\)\-\+\*•★,:;/]+", " ", raw)
    return {token for token in cleaned.split() if len(token) >= 2}


def header_detail(reference_manifest, generated_manifest):
    reference_rows = top_band_texts(reference_manifest)
    generated_rows = top_band_texts(generated_manifest)
    generated_variants = set()
    generated_tokens = set()
    for row in generated_rows:
        generated_variants.update(normalized_text_variants(row["text"]))
        generated_tokens.update(normalized_text_tokens(row["text"]))
    missing = []
    for row in reference_rows:
        variants = normalized_text_variants(row["text"])
        tokens = normalized_text_tokens(row["text"])
        token_overlap = bool(tokens) and len(tokens.intersection(generated_tokens)) >= max(1, len(tokens) // 2)
        if variants.isdisjoint(generated_variants) and not token_overlap:
            missing.append(row)
    return {
        "reference_top_text_count": len(reference_rows),
        "generated_top_text_count": len(generated_rows),
        "missing_reference_text_sample": missing[:20],
        "generated_top_text_sample": generated_rows[:20],
    }


def connector_detail(reference_manifest, generated_manifest):
    ref_rows = [
        row
        for row in reference_manifest.get("nodes") or []
        if row.get("node_type") == "VECTOR" and row.get("bbox_aspect_bucket") in {"ULTRA_WIDE", "WIDE"}
    ]
    gen_rows = generated_manifest.get("nodes") or []
    connector_roots = [
        row
        for row in gen_rows
        if row.get("source_subtype") == "connector" and row.get("replay_role") in {"connector_group", "connector_line"}
    ]
    connector_segments = [row for row in gen_rows if row.get("replay_role") == "connector_segment"]
    connector_arrows = [row for row in gen_rows if row.get("replay_role") == "connector_arrow"]
    return {
        "reference_connector_proxy_count": len(ref_rows),
        "generated_connector_root_count": len(connector_roots),
        "generated_connector_segment_count": len(connector_segments),
        "generated_connector_arrow_count": len(connector_arrows),
        "generated_connector_sample": connector_roots[:20],
    }


def table_detail(generated_manifest):
    rows = generated_manifest.get("nodes") or []
    signals = bundle_signals(generated_manifest)
    cell_frames = [row for row in rows if row.get("node_type") == "FRAME" and str(row.get("node_name", "")).startswith("cell ")]
    cell_text = [row for row in rows if row.get("node_type") == "TEXT" and row.get("source_subtype") == "table_cell"]
    cell_text_sources = {
        (row.get("source_path") or row.get("source_node_id") or row.get("reference_parent_id") or row.get("reference_node_id"))
        for row in cell_text
        if (row.get("source_path") or row.get("source_node_id") or row.get("reference_parent_id") or row.get("reference_node_id"))
    }
    return {
        "expected_table_cell_count": int(signals.get("table_cell_count") or 0),
        "expected_text_cell_count": int(signals.get("table_text_cell_count") or signals.get("table_cell_count") or 0),
        "generated_cell_frame_count": len(cell_frames),
        "generated_cell_text_count": len(cell_text_sources),
        "cell_frame_sample": cell_frames[:12],
        "cell_text_sample": cell_text[:12],
    }


def build_report(reference_paths, generated_paths):
    pages = []
    for reference_path, generated_path in zip(reference_paths, generated_paths):
        reference_manifest = load_json(reference_path)
        generated_manifest = load_json(generated_path)
        pages.append(
            {
                "reference_file": Path(reference_path).name,
                "generated_file": Path(generated_path).name,
                "page_name": generated_manifest.get("page_name") or reference_manifest.get("page_name"),
                "header": header_detail(reference_manifest, generated_manifest),
                "connector": connector_detail(reference_manifest, generated_manifest),
                "table": table_detail(generated_manifest),
            }
        )
    return {
        "kind": "visual-detail-report",
        "pages": pages,
    }


def main():
    parser = argparse.ArgumentParser(description="Build detailed visual diff report for bundle manifests.")
    parser.add_argument("--reference", nargs="+", required=True)
    parser.add_argument("--generated", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if len(args.reference) != len(args.generated):
        raise SystemExit("reference/generated counts must match")

    report = build_report(args.reference, args.generated)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
