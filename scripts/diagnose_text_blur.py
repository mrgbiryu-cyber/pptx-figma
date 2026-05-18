#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from build_intermediate_candidates import build_intermediate_model
from pptx_inspector import extract_slide_details


def flatten_tree(node: dict[str, Any], ancestors: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    ancestors = ancestors or []
    items = [{"node": node, "ancestors": list(ancestors)}]
    for child in node.get("children") or []:
        items.extend(flatten_tree(child, ancestors + [node]))
    return items


def frac(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return abs(numeric - round(numeric)) > 1e-6


def node_bounds(node: dict[str, Any]) -> dict[str, float]:
    raw = node.get("bounds_absolute") or {}
    return {
        "x": float(raw.get("x") or 0.0),
        "y": float(raw.get("y") or 0.0),
        "width": float(raw.get("width") or 0.0),
        "height": float(raw.get("height") or 0.0),
    }


def overlap_ratio(a: dict[str, float], b: dict[str, float]) -> float:
    area_a = max(a["width"] * a["height"], 1.0)
    x1 = max(a["x"], b["x"])
    y1 = max(a["y"], b["y"])
    x2 = min(a["x"] + a["width"], b["x"] + b["width"])
    y2 = min(a["y"] + a["height"], b["y"] + b["height"])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return ((x2 - x1) * (y2 - y1)) / area_a


def matrix_scale_and_rotation(relative_transform: list[list[float]] | None) -> tuple[float, float, float]:
    matrix = relative_transform or [[1, 0, 0], [0, 1, 0]]
    a11, a12, _ = matrix[0]
    a21, a22, _ = matrix[1]
    scale_x = math.sqrt(a11 * a11 + a21 * a21)
    scale_y = math.sqrt(a12 * a12 + a22 * a22)
    rotation_deg = math.degrees(math.atan2(a21, a11)) if scale_x > 0 else 0.0
    return scale_x, scale_y, rotation_deg


def blur_risk_score(factors: dict[str, bool]) -> int:
    score = 0
    score += 4 if factors.get("overlap_non_text") else 0
    score += 4 if factors.get("parent_scale") else 0
    score += 2 if factors.get("frac_relative_xy") else 0
    score += 1 if factors.get("frac_absolute_xy") else 0
    score += 2 if factors.get("opacity_or_blend") else 0
    score += 2 if factors.get("has_effect") else 0
    return score


def fidelity_risk_score(factors: dict[str, bool]) -> int:
    score = 0
    score += 3 if factors.get("font_substituted") else 0
    score += 2 if factors.get("overlap_non_text") else 0
    score += 1 if factors.get("overlap_image_or_vector") else 0
    score += 1 if factors.get("mixed_image_text") else 0
    return score


def find_slide_no_from_name(name: str) -> int | None:
    match = re.match(r"Slide\s+(\d+)\s*-", name or "")
    if not match:
        return None
    return int(match.group(1))


def resolve_source_font_family(
    pptx_path: Path,
    slide_no: int,
    target_text: str,
    local_bounds: dict[str, float],
) -> dict[str, Any] | None:
    detail = extract_slide_details(pptx_path, [slide_no])
    intermediate = build_intermediate_model(detail)
    page = next((item for item in intermediate.get("pages") or [] if int(item.get("slide_no") or 0) == slide_no), None)
    if not page:
        return None
    candidates = []
    target_cx = local_bounds["x"] + (local_bounds["width"] / 2)
    target_cy = local_bounds["y"] + (local_bounds["height"] / 2)
    for candidate in page.get("candidates") or []:
        text_value = str(candidate.get("text") or "").strip()
        if text_value != target_text.strip():
            continue
        bounds = candidate.get("bounds_px") or {}
        cx = float(bounds.get("x") or 0.0) + (float(bounds.get("width") or 0.0) / 2)
        cy = float(bounds.get("y") or 0.0) + (float(bounds.get("height") or 0.0) / 2)
        distance = math.hypot(target_cx - cx, target_cy - cy)
        style = (candidate.get("extra") or {}).get("text_style") or {}
        candidates.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "distance": round(distance, 3),
                "font_family": style.get("font_family"),
                "font_size_max": style.get("font_size_max"),
                "source_bounds_px": bounds,
            }
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item["distance"])[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose editable-but-blurry text risks from Figma analysis export.")
    parser.add_argument("--figma-export", required=True, help="Path to figma-analysis-export JSON")
    parser.add_argument("--pptx", required=True, help="Source PPTX path (for font substitution check)")
    parser.add_argument("--slide", type=int, required=True, help="Slide number in frame name, e.g. 12")
    parser.add_argument("--text", required=True, help="Target text value to inspect")
    parser.add_argument("--output", required=True, help="Output report JSON path")
    args = parser.parse_args()

    payload = json.loads(Path(args.figma_export).read_text(encoding="utf-8"))
    slide_frame = None
    for node in payload.get("nodes") or []:
        if find_slide_no_from_name(str(node.get("name") or "")) == args.slide:
            slide_frame = node
            break
    if slide_frame is None:
        raise SystemExit(f"slide frame not found: slide={args.slide}")

    flat = flatten_tree(slide_frame)
    target_entries: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(flat):
        node = item["node"]
        if node.get("type") != "TEXT":
            continue
        if str(node.get("characters") or "").strip() != args.text.strip():
            continue
        target_entries.append((index, item))
    if not target_entries:
        raise SystemExit(f"target text not found: '{args.text}' on slide {args.slide}")

    # pick the most suspicious one: highest overlap by non-text above
    scored_targets: list[tuple[float, int, dict[str, Any]]] = []
    for index, item in target_entries:
        bounds = node_bounds(item["node"])
        overlap_non_text_count = 0
        for upper_idx in range(index + 1, len(flat)):
            upper = flat[upper_idx]["node"]
            ratio = overlap_ratio(bounds, node_bounds(upper))
            if ratio < 0.3:
                continue
            if upper.get("type") != "TEXT":
                overlap_non_text_count += 1
        scored_targets.append((float(overlap_non_text_count), index, item))
    _, target_index, target_item = sorted(scored_targets, key=lambda row: (-row[0], row[1]))[0]

    target_node = target_item["node"]
    target_bounds_abs = node_bounds(target_node)
    frame_bounds_abs = node_bounds(slide_frame)
    target_bounds_local = {
        "x": round(target_bounds_abs["x"] - frame_bounds_abs["x"], 3),
        "y": round(target_bounds_abs["y"] - frame_bounds_abs["y"], 3),
        "width": round(target_bounds_abs["width"], 3),
        "height": round(target_bounds_abs["height"], 3),
    }

    overlaps: list[dict[str, Any]] = []
    for upper_idx in range(target_index + 1, len(flat)):
        upper = flat[upper_idx]["node"]
        ratio = overlap_ratio(target_bounds_abs, node_bounds(upper))
        if ratio < 0.3:
            continue
        fills = upper.get("fills") or []
        fill_types = [fill.get("type") for fill in fills if isinstance(fill, dict)]
        overlaps.append(
            {
                "draw_index": upper_idx,
                "id": upper.get("id"),
                "type": upper.get("type"),
                "name": upper.get("name"),
                "overlap_ratio": round(ratio, 4),
                "opacity": upper.get("opacity"),
                "blend_mode": upper.get("blend_mode"),
                "effects_count": len(upper.get("effects") or []),
                "fill_types": fill_types,
            }
        )

    parent_transform_rows: list[dict[str, Any]] = []
    has_parent_scale = False
    for ancestor in target_item["ancestors"]:
        scale_x, scale_y, rotation_deg = matrix_scale_and_rotation(ancestor.get("relative_transform"))
        row = {
            "id": ancestor.get("id"),
            "type": ancestor.get("type"),
            "name": ancestor.get("name"),
            "scale_x": round(scale_x, 6),
            "scale_y": round(scale_y, 6),
            "rotation_deg": round(rotation_deg, 6),
        }
        if abs(scale_x - 1.0) > 1e-6 or abs(scale_y - 1.0) > 1e-6:
            has_parent_scale = True
        parent_transform_rows.append(row)

    text_style = target_node.get("text_style") or {}
    font_name = text_style.get("fontName") or {}
    effects = target_node.get("effects") or []
    fills = target_node.get("fills") or []
    has_image_fill = any(fill.get("type") == "IMAGE" for fill in fills if isinstance(fill, dict))

    source_font_match = resolve_source_font_family(
        Path(args.pptx),
        args.slide,
        args.text,
        target_bounds_local,
    )
    current_font_family = font_name.get("family")
    source_font_family = (source_font_match or {}).get("font_family")
    font_substituted = bool(source_font_family and current_font_family and source_font_family != current_font_family)

    factors = {
        "font_substituted": font_substituted,
        "frac_absolute_xy": frac(target_bounds_abs["x"]) or frac(target_bounds_abs["y"]) or frac(target_bounds_abs["width"]) or frac(target_bounds_abs["height"]),
        "frac_relative_xy": frac(((target_node.get("relative_transform") or [[1, 0, 0], [0, 1, 0]])[0][2])) or frac(((target_node.get("relative_transform") or [[1, 0, 0], [0, 1, 0]])[1][2])),
        "parent_scale": has_parent_scale,
        "opacity_or_blend": (float(target_node.get("opacity") or 1.0) < 0.999) or bool(target_node.get("blend_mode") and target_node.get("blend_mode") != "NORMAL"),
        "has_effect": bool(effects),
        "overlap_non_text": any(row["type"] != "TEXT" for row in overlaps),
        "overlap_image_or_vector": any(("IMAGE" in row.get("fill_types", [])) or row.get("type") in {"VECTOR", "BOOLEAN_OPERATION"} for row in overlaps),
        "mixed_image_text": bool(has_image_fill) or any("IMAGE" in row.get("fill_types", []) for row in overlaps),
    }

    baseline_blur = blur_risk_score(factors)
    baseline_fidelity = fidelity_risk_score(factors)

    experiments = [
        {
            "name": "기존 변환 텍스트",
            "factors": dict(factors),
        },
        {
            "name": "같은 폰트/크기/문구로 Figma 새 텍스트 생성(대조군)",
            "factors": {
                **factors,
                "frac_absolute_xy": False,
                "frac_relative_xy": False,
                "parent_scale": False,
                "opacity_or_blend": False,
                "has_effect": False,
                "overlap_non_text": False,
                "overlap_image_or_vector": False,
                "mixed_image_text": False,
            },
        },
        {
            "name": "효과/effect 제거",
            "factors": {
                **factors,
                "opacity_or_blend": False,
                "has_effect": False,
            },
        },
        {
            "name": "소수점 좌표 정수 보정",
            "factors": {
                **factors,
                "frac_absolute_xy": False,
                "frac_relative_xy": False,
            },
        },
        {
            "name": "부모 scale 제거",
            "factors": {
                **factors,
                "parent_scale": False,
            },
        },
        {
            "name": "겹친 레이어 숨김",
            "factors": {
                **factors,
                "overlap_non_text": False,
                "overlap_image_or_vector": False,
                "mixed_image_text": False,
            },
        },
    ]

    for row in experiments:
        row["blur_risk_score"] = blur_risk_score(row["factors"])
        row["fidelity_risk_score"] = fidelity_risk_score(row["factors"])
        row["blur_improvement"] = baseline_blur - row["blur_risk_score"]

    report = {
        "input": {
            "figma_export": str(Path(args.figma_export).resolve()),
            "pptx": str(Path(args.pptx).resolve()),
            "slide": args.slide,
            "text": args.text,
        },
        "target": {
            "node_id": target_node.get("id"),
            "node_name": target_node.get("name"),
            "characters": target_node.get("characters"),
            "editable_text": target_node.get("type") == "TEXT",
            "text_style": text_style,
            "absolute_bounds": target_bounds_abs,
            "local_bounds_in_slide": target_bounds_local,
            "opacity": target_node.get("opacity"),
            "blend_mode": target_node.get("blend_mode"),
            "effects": effects,
            "parent_transforms": parent_transform_rows,
        },
        "overlaps_above_target": overlaps,
        "font_check": {
            "figma_font_family": current_font_family,
            "source_match": source_font_match,
            "font_substituted": font_substituted,
        },
        "factors": factors,
        "baseline": {
            "blur_risk_score": baseline_blur,
            "fidelity_risk_score": baseline_fidelity,
        },
        "experiments": experiments,
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")
    print(
        f"target={target_node.get('id')} editable={report['target']['editable_text']} "
        f"blur_risk={baseline_blur} fidelity_risk={baseline_fidelity} overlaps={len(overlaps)}"
    )


if __name__ == "__main__":
    main()
