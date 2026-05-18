#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


SLIDE_NO_RE = re.compile(r"(?:slide[-_]?|^)(\d+)", re.IGNORECASE)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_bundle_paths(bundle_dir: Path) -> list[Path]:
    paths = []
    for path in bundle_dir.glob("*.json"):
        name = path.name.lower()
        if name.startswith("report") or name.endswith(".partial.json"):
            continue
        if "slide" not in name:
            continue
        paths.append(path)
    return sorted(paths, key=lambda item: (slide_no_from_path(item), item.name))


def slide_no_from_path(path: Path) -> int:
    match = SLIDE_NO_RE.search(path.stem)
    if not match:
        return 0
    return int(match.group(1))


def walk_nodes(node: dict[str, Any], inherited_layer: str | None = None):
    name = str(node.get("name") or "")
    debug = node.get("debug") if isinstance(node.get("debug"), dict) else {}
    layer = debug.get("render_layer") or inherited_layer
    if name in {"visual/reference", "editable/content", "mapping/debug"}:
        layer = name
    yield node, layer
    for child in node.get("children") or []:
        if isinstance(child, dict):
            yield from walk_nodes(child, layer)


def text_run_covered_chars(text_runs: list[dict[str, Any]], text_length: int) -> int:
    if text_length <= 0:
        return 0
    covered = [False] * text_length
    for run in text_runs or []:
        if not isinstance(run, dict):
            continue
        start = run.get("start")
        end = run.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        start = max(0, min(text_length, start))
        end = max(0, min(text_length, end))
        if end <= start:
            continue
        for index in range(start, end):
            covered[index] = True
    return sum(1 for value in covered if value)


def count_text_payload(payload: dict[str, Any]) -> dict[str, int]:
    characters = str(payload.get("characters") or "")
    text_runs = payload.get("textRuns") if isinstance(payload.get("textRuns"), list) else []
    return {
        "objects": 1 if characters else 0,
        "characters": len(characters),
        "objects_with_text_runs": 1 if characters and text_runs else 0,
        "text_run_count": len(text_runs),
        "text_run_covered_characters": text_run_covered_chars(text_runs, len(characters)),
    }


def add_counts(target: dict[str, int], delta: dict[str, int]) -> None:
    for key, value in delta.items():
        target[key] = int(target.get(key, 0)) + int(value or 0)


def collect_table_text(table: dict[str, Any]) -> dict[str, int]:
    counts = {
        "cells": 0,
        "text_cells": 0,
        "text_objects": 0,
        "text_characters": 0,
        "objects_with_text_runs": 0,
        "text_run_count": 0,
        "text_run_covered_characters": 0,
    }
    cells: list[dict[str, Any]] = []
    for cell in table.get("cells") or []:
        if isinstance(cell, dict):
            cells.append(cell)
    if not cells:
        for row in table.get("rows") or []:
            for cell in (row.get("cells") if isinstance(row, dict) else []) or []:
                if isinstance(cell, dict):
                    cells.append(cell)
    for cell in cells:
        counts["cells"] += 1
        text_payload = cell.get("text")
        if not isinstance(text_payload, dict):
            continue
        characters = str(text_payload.get("characters") or "")
        if characters:
            counts["text_cells"] += 1
        text_counts = count_text_payload(text_payload)
        counts["text_objects"] += text_counts["objects"]
        counts["text_characters"] += text_counts["characters"]
        counts["objects_with_text_runs"] += text_counts["objects_with_text_runs"]
        counts["text_run_count"] += text_counts["text_run_count"]
        counts["text_run_covered_characters"] += text_counts["text_run_covered_characters"]
    return counts


def summarize_bundle(path: Path) -> dict[str, Any]:
    bundle = load_json(path)
    document = bundle.get("document") if isinstance(bundle.get("document"), dict) else bundle
    by_type: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    editable_text = {
        "objects": 0,
        "characters": 0,
        "objects_with_text_runs": 0,
        "text_run_count": 0,
        "text_run_covered_characters": 0,
    }
    visual_text = dict(editable_text)
    table_counts = {
        "tables": 0,
        "cells": 0,
        "text_cells": 0,
        "text_objects": 0,
        "text_characters": 0,
        "objects_with_text_runs": 0,
        "text_run_count": 0,
        "text_run_covered_characters": 0,
    }
    pdf_reference_backgrounds = 0
    image_reference_assets = 0
    missing_render_layer_nodes = 0

    for node, layer in walk_nodes(document):
        node_type = str(node.get("type") or "<unknown>")
        by_type[node_type] = by_type.get(node_type, 0) + 1
        by_layer[layer or "<none>"] = by_layer.get(layer or "<none>", 0) + 1
        debug = node.get("debug") if isinstance(node.get("debug"), dict) else {}
        if not layer and node is not document:
            missing_render_layer_nodes += 1
        if debug.get("render_intent") == "pdf_reference_background":
            pdf_reference_backgrounds += 1
        fills = node.get("fills") if isinstance(node.get("fills"), list) else []
        if any(isinstance(fill, dict) and fill.get("type") == "IMAGE" and fill.get("imageRef") for fill in fills):
            image_reference_assets += 1
        if node_type == "TEXT":
            target = editable_text if layer == "editable/content" else visual_text
            add_counts(target, count_text_payload(node))
        elif node_type == "EDITABLE_TABLE":
            table_counts["tables"] += 1
            add_counts(table_counts, collect_table_text(node))

    editable_text_objects = editable_text["objects"] + table_counts["text_objects"]
    editable_text_characters = editable_text["characters"] + table_counts["text_characters"]
    editable_objects_with_runs = editable_text["objects_with_text_runs"] + table_counts["objects_with_text_runs"]
    editable_run_count = editable_text["text_run_count"] + table_counts["text_run_count"]
    editable_run_covered_characters = editable_text["text_run_covered_characters"] + table_counts["text_run_covered_characters"]

    return {
        "slide_no": slide_no_from_path(path),
        "bundle": str(path),
        "page_name": bundle.get("page_name") or document.get("name"),
        "has_pdf_reference_background": pdf_reference_backgrounds > 0,
        "pdf_reference_backgrounds": pdf_reference_backgrounds,
        "image_reference_assets": image_reference_assets,
        "nodes_by_layer": dict(sorted(by_layer.items())),
        "nodes_by_type": dict(sorted(by_type.items())),
        "editable_text_objects": editable_text_objects,
        "editable_text_characters": editable_text_characters,
        "editable_text_objects_with_runs": editable_objects_with_runs,
        "editable_text_run_count": editable_run_count,
        "editable_text_run_covered_characters": editable_run_covered_characters,
        "editable_text_run_object_ratio": ratio(editable_objects_with_runs, editable_text_objects),
        "editable_text_run_character_ratio": ratio(editable_run_covered_characters, editable_text_characters),
        "direct_editable_text": editable_text,
        "editable_tables": table_counts,
        "visual_reference_text_objects": visual_text["objects"],
        "visual_reference_text_characters": visual_text["characters"],
        "missing_render_layer_nodes": missing_render_layer_nodes,
    }


def ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def build_report(bundle_dir: Path) -> dict[str, Any]:
    slides = [summarize_bundle(path) for path in iter_bundle_paths(bundle_dir)]
    totals = {
        "slide_count": len(slides),
        "slides_with_pdf_reference_background": sum(1 for slide in slides if slide["has_pdf_reference_background"]),
        "editable_text_objects": sum(slide["editable_text_objects"] for slide in slides),
        "editable_text_characters": sum(slide["editable_text_characters"] for slide in slides),
        "editable_text_objects_with_runs": sum(slide["editable_text_objects_with_runs"] for slide in slides),
        "editable_text_run_count": sum(slide["editable_text_run_count"] for slide in slides),
        "editable_text_run_covered_characters": sum(slide["editable_text_run_covered_characters"] for slide in slides),
        "editable_tables": sum(slide["editable_tables"]["tables"] for slide in slides),
        "editable_table_cells": sum(slide["editable_tables"]["cells"] for slide in slides),
        "editable_table_text_cells": sum(slide["editable_tables"]["text_cells"] for slide in slides),
        "missing_render_layer_nodes": sum(slide["missing_render_layer_nodes"] for slide in slides),
    }
    totals["pdf_reference_background_ratio"] = ratio(
        totals["slides_with_pdf_reference_background"],
        totals["slide_count"],
    )
    totals["editable_text_run_object_ratio"] = ratio(
        totals["editable_text_objects_with_runs"],
        totals["editable_text_objects"],
    )
    totals["editable_text_run_character_ratio"] = ratio(
        totals["editable_text_run_covered_characters"],
        totals["editable_text_characters"],
    )
    return {
        "kind": "editable-coverage-report",
        "bundle_dir": str(bundle_dir),
        "totals": totals,
        "slides": slides,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build editable-structure coverage metrics from replay bundles.")
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=Path("docs/render-diff/current-fulltest-pages-all"),
        help="Directory containing slide replay bundle JSON files.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("sampling/current-test-editable-coverage-report.json"),
        help="Output JSON report path.",
    )
    args = parser.parse_args()

    report = build_report(args.bundle_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
