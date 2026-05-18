#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from build_page29_right_panel_report import classify_source_right_panel
from ppt_source_extractor import load_intermediate_payload


TEXT_TAG_PATTERN = re.compile(r">([^<>]+)<")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9가-힣]+")


def flatten_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    queue = [node]
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        queue.extend(current.get("children") or [])
    return ordered


def extract_svg_text(svg_markup: str) -> list[str]:
    if not svg_markup:
        return []
    values: list[str] = []
    for raw in TEXT_TAG_PATTERN.findall(svg_markup):
        text = html.unescape(str(raw or "")).strip()
        if text:
            values.append(text)
    return values


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def tokenize(value: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(normalize_text(value)) if len(token) >= 2}


def source_text_matches(source_text: str, generated_texts: list[str], generated_tokens: set[str]) -> bool:
    source_norm = normalize_text(source_text)
    if not source_norm:
        return False
    source_tokens = tokenize(source_norm)
    if source_norm in generated_texts:
        return True
    prefix = source_norm[:18]
    if prefix and any(prefix in candidate for candidate in generated_texts):
        return True
    if not source_tokens:
        return False
    overlap = len(source_tokens & generated_tokens)
    return overlap >= max(1, min(3, len(source_tokens) // 2))


def collect_generated_right_panel(bundle_json: dict[str, Any]) -> dict[str, Any]:
    root = bundle_json.get("document") or {}
    inner = (root.get("children") or [None])[0]
    if not inner:
        return {"missing": True}
    right_panel = None
    for child in inner.get("children") or []:
        if child.get("name") == "right_panel_block":
            right_panel = child
            break
    if not right_panel:
        return {"missing": True}

    nodes = flatten_nodes(right_panel)
    generated_texts: list[str] = []
    for node in nodes:
        if node.get("type") == "TEXT":
            value = normalize_text(node.get("characters") or node.get("name") or "")
            if value:
                generated_texts.append(value)
        elif node.get("type") == "SVG_BLOCK":
            generated_texts.extend(normalize_text(value) for value in extract_svg_text(node.get("svgMarkup") or "") if normalize_text(value))
    generated_tokens = set()
    for value in generated_texts:
        generated_tokens.update(tokenize(value))

    direct_children = right_panel.get("children") or []
    return {
        "bounds": right_panel.get("absoluteBoundingBox"),
        "types": dict(Counter(str(node.get("type") or "") for node in nodes)),
        "direct_children": [
            {
                "type": child.get("type"),
                "name": child.get("name"),
                "bounds": child.get("absoluteBoundingBox"),
                "role": (child.get("debug") or {}).get("role"),
            }
            for child in direct_children
        ],
        "texts": generated_texts,
        "text_tokens": sorted(generated_tokens),
        "background_card_count": sum(1 for child in direct_children if str(child.get("name") or "").endswith(":panel_bg")),
        "has_card_labels": any(str(child.get("name") or "") == "right_panel_block:card_labels" for child in direct_children),
        "has_description_overlay": any(str(child.get("name") or "") == "right_panel_block:description" for child in direct_children),
    }


def build_source_baseline(intermediate_payload: dict[str, Any]) -> dict[str, Any]:
    page = next(page for page in intermediate_payload["pages"] if int(page.get("slide_no") or 0) == 29)
    source = classify_source_right_panel(page)
    source_text_rows: list[dict[str, Any]] = []
    for owner_name in ("version_stack", "issue_card", "description_cards", "other"):
        for row in source.get(owner_name) or []:
            text_value = normalize_text(row.get("text") or "")
            if text_value:
                source_text_rows.append(
                    {
                        "owner": owner_name,
                        "candidate_id": row.get("candidate_id"),
                        "text": text_value,
                    }
                )
    return {
        "owner_counts": {name: len(rows) for name, rows in source.items()},
        "text_rows": source_text_rows,
    }


def summarize_variant(label: str, bundle_json: dict[str, Any], source_baseline: dict[str, Any]) -> dict[str, Any]:
    generated = collect_generated_right_panel(bundle_json)
    if generated.get("missing"):
        return {"label": label, "missing": True}

    generated_texts = generated["texts"]
    generated_tokens = set(generated["text_tokens"])
    source_rows = source_baseline["text_rows"]
    matched_rows = [
        row
        for row in source_rows
        if source_text_matches(str(row["text"]), generated_texts, generated_tokens)
    ]
    coverage_by_owner = Counter(row["owner"] for row in matched_rows)

    return {
        "label": label,
        "coverage": {
            "matched_text_rows": len(matched_rows),
            "total_text_rows": len(source_rows),
            "coverage_ratio": round(len(matched_rows) / max(len(source_rows), 1), 3),
            "by_owner": dict(coverage_by_owner),
        },
        "structure": {
            "background_card_count": generated["background_card_count"],
            "has_card_labels": generated["has_card_labels"],
            "has_description_overlay": generated["has_description_overlay"],
            "direct_child_count": len(generated["direct_children"]),
            "types": generated["types"],
        },
        "direct_children": generated["direct_children"],
        "sample_texts": generated_texts[:30],
    }


def build_report(intermediate_payload: dict[str, Any], bundle_29_1: dict[str, Any], bundle_29_2: dict[str, Any]) -> dict[str, Any]:
    source_baseline = build_source_baseline(intermediate_payload)
    return {
        "kind": "page29-variant-compare-report",
        "source_baseline": source_baseline,
        "variant_29_1": summarize_variant("29-1", bundle_29_1, source_baseline),
        "variant_29_2": summarize_variant("29-2", bundle_29_2, source_baseline),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare page 29 variant coverage/structure between 29-1 and 29-2.")
    parser.add_argument("--input", required=True, help="Intermediate candidates JSON path")
    parser.add_argument("--variant-a", required=True, help="29-1 bundle JSON path")
    parser.add_argument("--variant-b", required=True, help="29-2 bundle JSON path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    args = parser.parse_args()

    intermediate_payload = load_intermediate_payload(args.input)
    bundle_29_1 = json.loads(Path(args.variant_a).read_text(encoding="utf-8"))
    bundle_29_2 = json.loads(Path(args.variant_b).read_text(encoding="utf-8"))
    report = build_report(intermediate_payload, bundle_29_1, bundle_29_2)

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
