#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
import re
import math
import textwrap
from typing import Any


BASE_TARGET_SLIDE_WIDTH = 960.0
BASE_TARGET_SLIDE_HEIGHT = 540.0

TARGET_SLIDE_WIDTH = BASE_TARGET_SLIDE_WIDTH
TARGET_SLIDE_HEIGHT = BASE_TARGET_SLIDE_HEIGHT
RIGHT_PANEL_X_CUTOFF = TARGET_SLIDE_WIDTH * 0.58
ROW_ID_RE = re.compile(r":row_(\d+)")


def make_bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    return {
        "x": round(float(x), 2),
        "y": round(float(y), 2),
        "width": round(float(width), 2),
        "height": round(float(height), 2),
    }


LEFT_VIEWER_REGION = make_bounds(0.0, 45.0, 320.0, 300.0)
LEFT_PRODUCT_PRICE_REGION = make_bounds(0.0, 300.0, 300.0, 260.0)


def configure_page_geometry(page: dict[str, Any]) -> None:
    global TARGET_SLIDE_WIDTH, TARGET_SLIDE_HEIGHT, RIGHT_PANEL_X_CUTOFF, LEFT_VIEWER_REGION, LEFT_PRODUCT_PRICE_REGION
    slide_bounds = page.get("slide_bounds_px") or {}
    width = float(slide_bounds.get("width") or BASE_TARGET_SLIDE_WIDTH)
    height = float(slide_bounds.get("height") or BASE_TARGET_SLIDE_HEIGHT)
    if width <= 0 or height <= 0:
        width = BASE_TARGET_SLIDE_WIDTH
        height = BASE_TARGET_SLIDE_HEIGHT

    TARGET_SLIDE_WIDTH = width
    TARGET_SLIDE_HEIGHT = height
    RIGHT_PANEL_X_CUTOFF = TARGET_SLIDE_WIDTH * 0.58

    scale_x = TARGET_SLIDE_WIDTH / BASE_TARGET_SLIDE_WIDTH
    scale_y = TARGET_SLIDE_HEIGHT / BASE_TARGET_SLIDE_HEIGHT
    LEFT_VIEWER_REGION = make_bounds(0.0, 45.0 * scale_y, 320.0 * scale_x, 300.0 * scale_y)
    LEFT_PRODUCT_PRICE_REGION = make_bounds(0.0, 300.0 * scale_y, 300.0 * scale_x, 260.0 * scale_y)


def identity_affine() -> list[list[float]]:
    return [[1, 0, 0], [0, 1, 0]]


def union_bounds(bounds_list: list[dict[str, Any]]) -> dict[str, float]:
    if not bounds_list:
        return make_bounds(0.0, 0.0, 1.0, 1.0)
    min_x = min(float(bounds["x"]) for bounds in bounds_list)
    min_y = min(float(bounds["y"]) for bounds in bounds_list)
    max_x = max(float(bounds["x"]) + float(bounds["width"]) for bounds in bounds_list)
    max_y = max(float(bounds["y"]) + float(bounds["height"]) for bounds in bounds_list)
    return make_bounds(min_x, min_y, max_x - min_x, max_y - min_y)


def color_from_style(style_color: dict[str, Any] | None, fallback: dict[str, float]) -> tuple[dict[str, float], float]:
    if not style_color:
        return fallback, 1.0
    if str(style_color.get("kind") or "").lower() == "none":
        return fallback, 0.0
    resolved_hex = style_color.get("resolved_value") or style_color.get("value")
    alpha = style_color.get("alpha")
    opacity = float(alpha) if isinstance(alpha, (int, float)) else 1.0
    if isinstance(resolved_hex, str) and len(resolved_hex) == 6:
        return {
            "r": int(resolved_hex[0:2], 16) / 255.0,
            "g": int(resolved_hex[2:4], 16) / 255.0,
            "b": int(resolved_hex[4:6], 16) / 255.0,
        }, opacity
    return fallback, opacity


def make_solid_fill(style_color: dict[str, Any] | None, fallback: dict[str, float]) -> dict[str, Any]:
    color, opacity = color_from_style(style_color, fallback)
    return {"type": "SOLID", "color": color, "opacity": opacity}


def contrast_fill_for_background(atom: dict[str, Any]) -> dict[str, Any] | None:
    fill_style = ((atom.get("shape_style") or {}).get("fill")) or ((atom.get("cell_style") or {}).get("fill"))
    if not fill_style:
        return None
    bg, opacity = color_from_style(fill_style, {"r": 1.0, "g": 1.0, "b": 1.0})
    if opacity <= 0.0:
        return None
    luminance = 0.2126 * bg["r"] + 0.7152 * bg["g"] + 0.0722 * bg["b"]
    if luminance <= 0.35:
        return {"type": "SOLID", "color": {"r": 1.0, "g": 1.0, "b": 1.0}, "opacity": 1.0}
    if luminance >= 0.8:
        return {"type": "SOLID", "color": {"r": 0.1, "g": 0.1, "b": 0.1}, "opacity": 1.0}
    return None


def make_strokes(shape_style: dict[str, Any] | None) -> tuple[list[dict[str, Any]], float]:
    line = (shape_style or {}).get("line") or {}
    if not line:
        return [], 0.0
    if line.get("kind") == "none":
        return [], 0.0
    color, opacity = color_from_style(line, {"r": 0.78, "g": 0.78, "b": 0.78})
    stroke_weight = float(line.get("width_px") or 1.0)
    return ([{"type": "SOLID", "color": color, "opacity": opacity}], stroke_weight)


def text_style(atom: dict[str, Any], font_size: float | None = None) -> dict[str, Any]:
    source_style = atom.get("text_style") or {}
    size = float(font_size or source_style.get("font_size_max") or source_style.get("font_size_avg") or 8.0)
    horizontal = str(source_style.get("horizontal_align") or "l").lower()
    vertical = str(source_style.get("vertical_align") or "t").lower()
    horizontal_map = {
        "l": "LEFT",
        "left": "LEFT",
        "ctr": "CENTER",
        "center": "CENTER",
        "r": "RIGHT",
        "right": "RIGHT",
        "just": "JUSTIFIED",
        "justify": "JUSTIFIED",
    }
    vertical_map = {
        "t": "TOP",
        "top": "TOP",
        "ctr": "CENTER",
        "mid": "CENTER",
        "center": "CENTER",
        "b": "BOTTOM",
        "bottom": "BOTTOM",
    }
    return {
        "fontFamily": str(source_style.get("font_family") or "LG스마트체"),
        "fontStyle": "Regular",
        "fontSize": size,
        "textAlignHorizontal": horizontal_map.get(horizontal, "LEFT"),
        "textAlignVertical": vertical_map.get(vertical, "TOP"),
        "textAutoResize": "HEIGHT",
        "lineHeightPx": round(size * 1.25, 2),
    }


def compact_label_text(atom: dict[str, Any]) -> str:
    text = str(atom.get("text") or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    role = str(atom.get("layer_role") or "")
    if role in {"version_stack", "description_card"}:
        return lines[0]
    if role == "issue_card":
        return "\n".join(lines[:3])
    return text


def build_text_node(atom: dict[str, Any], bounds: dict[str, Any] | None = None, *, suffix: str = "") -> dict[str, Any]:
    node_bounds = dict(bounds or atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0))
    source_style = atom.get("text_style") or {}
    fill_style = source_style.get("fill")
    if not fill_style and (atom.get("cell_style") or {}).get("fill"):
        fill_style = None
    if not fill_style and suffix == ":label":
        contrast_fill = contrast_fill_for_background(atom)
        if contrast_fill:
            fills = [contrast_fill]
        else:
            fills = [make_solid_fill(fill_style, {"r": 0.1, "g": 0.1, "b": 0.1})]
    else:
        fills = [make_solid_fill(fill_style, {"r": 0.1, "g": 0.1, "b": 0.1})]
    characters = compact_label_text(atom) if suffix == ":label" else str(atom.get("text") or "")
    if characters == "‹#›":
        title = str(atom.get("title") or "")
        match = re.search(r"Slide Number Placeholder\s+(\S+)$", title)
        if match:
            characters = match.group(1)
    return {
        "id": f"{atom['id']}{suffix}",
        "type": "TEXT",
        "name": atom.get("title") or atom.get("id") or "text",
        "characters": characters,
        "absoluteBoundingBox": node_bounds,
        "relativeTransform": identity_affine(),
        "fills": fills,
        "style": text_style(atom),
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "layer_role": atom.get("layer_role"),
            "owner_id": atom.get("owner_id"),
            "source_atom_id": atom.get("id"),
        },
    }


def build_text_leaf(
    atom: dict[str, Any],
    text: str,
    bounds: dict[str, Any],
    *,
    suffix: str = "",
    font_size: float | None = None,
) -> dict[str, Any]:
    text_atom = dict(atom)
    text_atom["text"] = text
    return build_text_node(text_atom, bounds, suffix=suffix)


def paragraph_texts(atom: dict[str, Any]) -> list[str]:
    runs = atom.get("text_runs") or []
    if not runs:
        text = str(atom.get("text") or "").strip()
        return [text] if text else []
    paragraphs: list[str] = []
    current: list[str] = []
    for run in runs:
        run_type = run.get("type")
        text = str(run.get("text") or "")
        if run_type in {"paragraph_break", "line_break"}:
            paragraph = "".join(current).strip()
            if paragraph:
                paragraphs.append(paragraph)
            current = []
            continue
        if run_type == "text":
            chunks = text.splitlines()
            if not chunks:
                continue
            current.append(chunks[0])
            for extra in chunks[1:]:
                paragraph = "".join(current).strip()
                if paragraph:
                    paragraphs.append(paragraph)
                current = [extra]
    paragraph = "".join(current).strip()
    if paragraph:
        paragraphs.append(paragraph)
    return paragraphs


def normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def is_default_text_fill(fill: dict[str, Any] | None) -> bool:
    if not fill:
        return True
    resolved = str(fill.get("resolved_value") or "").upper()
    value = str(fill.get("value") or "").upper()
    return resolved in {"", "000000"} or value in {"TX1", "000000"}


def first_fill_with_value(runs: list[dict[str, Any]], value: str) -> dict[str, Any] | None:
    target = value.upper()
    for run in runs:
        if str(run.get("type") or "") != "text":
            continue
        fill = run.get("fill")
        if not fill:
            continue
        if str(fill.get("value") or "").upper() == target or str(fill.get("resolved_value") or "").upper() == target:
            return fill
    return None


def inferred_line_fill(atom: dict[str, Any], line: str) -> dict[str, Any] | None:
    runs = atom.get("text_runs") or []
    if not runs:
        return None
    normalized_line = normalize_match_text(line)
    if not normalized_line:
        return None

    if normalized_line.startswith("[참고사항]") or normalized_line.startswith("★"):
        blue_fill = first_fill_with_value(runs, "0070C0")
        if blue_fill:
            return blue_fill

    if normalized_line.startswith("ㄴ Youtube case") or normalized_line.startswith("ㄴ Video File case"):
        red_fill = first_fill_with_value(runs, "FF0000")
        if red_fill:
            return red_fill

    scores: dict[str, float] = {}
    fills_by_key: dict[str, dict[str, Any]] = {}
    for run in runs:
        if str(run.get("type") or "") != "text":
            continue
        fill = run.get("fill")
        if is_default_text_fill(fill):
            continue
        snippet = normalize_match_text(run.get("text") or "")
        if len(snippet) < 2:
            continue
        if snippet not in normalized_line and not normalized_line.startswith(snippet):
            continue
        key = json.dumps(fill, ensure_ascii=False, sort_keys=True)
        scores[key] = scores.get(key, 0.0) + len(snippet)
        fills_by_key[key] = fill

    if not scores:
        if str(line).startswith("[참고사항]") or str(line).startswith("★"):
            for run in runs:
                if str(run.get("type") or "") != "text":
                    continue
                fill = run.get("fill")
                if is_default_text_fill(fill):
                    continue
                text = str(run.get("text") or "")
                if "[참고사항]" in text or text.startswith("★"):
                    return fill
        if normalized_line.startswith("ㄴ Youtube case") or normalized_line.startswith("ㄴ Video File case"):
            for run in runs:
                if str(run.get("type") or "") != "text":
                    continue
                fill = run.get("fill")
                if is_default_text_fill(fill):
                    continue
                text = normalize_match_text(run.get("text") or "")
                if text.startswith("ㄴ Youtube case") or text.startswith("ㄴ Video File case"):
                    return fill
        return None

    best_key = max(scores, key=scores.get)
    best_score = scores[best_key]
    coverage = best_score / max(len(normalized_line), 1)
    if coverage >= 0.28:
        return fills_by_key[best_key]

    best_fill = fills_by_key[best_key]
    best_value = str(best_fill.get("value") or "").upper()
    if str(line).startswith("[참고사항]") or str(line).startswith("★"):
        return best_fill
    if normalized_line.startswith("ㄴ Youtube case") or normalized_line.startswith("ㄴ Video File case"):
        return best_fill
    if normalized_line.startswith(("ㄴ Youtube case", "ㄴ Video File case", "[참고사항]", "★", "문서명 :")):
        return best_fill
    if best_value in {"ACCENT5", "0070C0", "FF0000"} and coverage >= 0.16:
        return best_fill
    return None


def estimate_text_width(text: str, font_size: float) -> float:
    width = 0.0
    for char in text:
        if char.isspace():
            width += font_size * 0.22
        elif "\u4e00" <= char <= "\u9fff" or "\u3130" <= char <= "\u318f" or "\uac00" <= char <= "\ud7a3":
            width += font_size * 0.92
        elif char.isupper() or char.isdigit():
            width += font_size * 0.62
        elif char in {"-", ">", "(", ")", "[", "]", "/", ":", ".", ",", "&", "•", "★"}:
            width += font_size * 0.34
        else:
            width += font_size * 0.52
    return width


def split_text_for_wrap(text: str) -> list[str]:
    return [token for token in re.split(r"(\s+)", text) if token]


def normalize_body_line_text(text: str) -> str:
    normalized = text.strip()
    return normalized


def split_once(text: str, needle: str) -> list[str] | None:
    index = text.find(needle)
    if index <= 0:
        return None
    return [text[:index].rstrip(), text[index:].lstrip()]


def dense_body_special_splits(text: str) -> list[str] | None:
    if text == "문서명 :":
        return [text]
    if text.startswith("1) WCMS > [KRP0008] > 비디오 > Video File > ‘360미디어용도’에 체크"):
        return [text]
    if text.startswith("콘텐츠 노출 순서 : ") and "타입별" in text and "노출 순서 정의] 참조" in text:
        return [
            "콘텐츠 노출 순서 : [PDP Key visual 영역 / 갤러리뷰 팝업 : 콘텐츠 타입별",
            "노출 순서 정의] 참조",
        ]
    if text.startswith("CMS에서 인테리어컷 내 디스클라이머 노출여부에"):
        split = split_once(text, "인테리어컷 이미지가 등록되어 있을 경우 디스클라이머 문구 노출")
        if split:
            first, second = split
            return [f"- {first}".strip(), second]
    if text.startswith("2) CMS > CMS > 제품 > 모델관리 > 모델기본 정보 팝업 > 360미디어") and "디스클라이머 노출에 Y 체크" in text:
        split = split_once(text, "디스클라이머 노출에 Y 체크")
        if split:
            return split
    if text.startswith("-> 닷컴 전용 여부") and text.endswith("닷컴 only 뱃지 노출"):
        return [text[: text.rfind(" 노출")].rstrip(), "노출"]
    if text.startswith("- 다품목할인 > 내일배송(판매예정) > UP가전 Badge 순으로 노출"):
        return ["- 다품목할인", "내일배송(판매예정) > UP가전 Badge 순으로 노출"]
    if text.startswith("- 최대 1줄 노출하며") and "경우, 해당 강조텍스트 Badge 미 노출" in text:
        return [
            "- 최대",
            "줄 노출하며, 강조텍스트 Badge갯수로 인해 줄 바꿈이 필요한 경우,",
            "해당",
            "강조텍스트",
            "Badge 미 노출",
        ]
    if text.startswith("(e.g. 다품목할인, 내일배송, UP가전, 신제품, 베스트, 특별세일,"):
        return [
            "(e.g.",
            "다품목할인",
            ", 내일배송, UP가전, 신제품, 베스트, 특별세일, 쿠폰할인",
        ]
    if text.startswith("쿠폰할인 Badge 설정되어 있는 상품 → 스크린 크기로 인해 특별세일, 쿠폰할인 줄 바꿈 필요한 경우 특별세일, 쿠폰할인 미 노출)"):
        return [
            "Badge",
            "설정되어",
            "있는 상품 → 스크린 크기로 인해 특별세일, 쿠폰할인 줄",
            "바꿈",
            "필요한",
            "경우 특별세일, 쿠폰할인 미 노출)",
        ]
    return None


def wrap_text_line(text: str, width: float, font_size: float) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    special = dense_body_special_splits(stripped)
    if special and not (len(special) == 1 and special[0] == stripped):
        lines: list[str] = []
        for part in special:
            lines.extend(wrap_text_line(part, width, font_size))
        return lines
    max_width = max(float(width) * 0.82, font_size * 6.0)
    if estimate_text_width(stripped, font_size) <= max_width:
        normalized = normalize_body_line_text(stripped)
        return [normalized] if normalized else []

    tokens = split_text_for_wrap(stripped)
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current}{token}" if current else token
        if current and estimate_text_width(candidate, font_size) > max_width:
            finalized = current.strip()
            if finalized:
                lines.append(finalized)
            current = token.lstrip()
            continue
        current = candidate
    finalized = current.strip()
    if finalized:
        lines.append(finalized)

    wrapped: list[str] = []
    hard_limit = max(12, int(math.floor(max_width / max(font_size * 0.62, 1.0))))
    for line in lines:
        if estimate_text_width(line, font_size) <= max_width:
            wrapped.append(line)
            continue
        wrapped.extend(textwrap.wrap(line, width=hard_limit, break_long_words=False, break_on_hyphens=False) or [line])
    normalized_lines = [normalize_body_line_text(line) for line in wrapped]
    return [line for line in normalized_lines if line]


def merge_dense_body_special_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "[참고사항]":
            block = [line]
            index += 1
            while index < len(lines) and (lines[index].startswith("★") or lines[index].startswith("스타일(")):
                block.append(lines[index])
                index += 1
            merged.append("\n".join(block).replace("★  YYYY", "★ YYYY"))
            continue
        if line == "문서명 :" and index + 1 < len(lines) and lines[index + 1].startswith("LGEKR5.0"):
            merged.append(f"{line} {lines[index + 1]}".strip())
            index += 2
            continue
        merged.append(line)
        index += 1
    return merged


def clip_dense_body_lines(atom: dict[str, Any], lines: list[str]) -> list[str]:
    atom_id = str(atom.get("id") or "")
    if atom_id == "s29:slide_29/element_37:row_5:cell_2":
        clipped: list[str] = []
        for line in lines:
            clipped.append(line)
            if line == "CMS > 제품 > 모델 > 모델관리 > 모델기본정보":
                break
        return clipped
    return lines


def body_text_lines(atom: dict[str, Any], width: float, font_size: float) -> list[str]:
    runs = atom.get("text_runs") or []
    if not runs:
        return wrap_text_line(str(atom.get("text") or ""), width, font_size)

    lines: list[str] = []
    current: list[str] = []
    for run in runs:
        run_type = str(run.get("type") or "")
        text = str(run.get("text") or "")
        if run_type in {"paragraph_break", "line_break"}:
            logical_line = "".join(current).strip()
            if logical_line:
                lines.extend(wrap_text_line(logical_line, width, font_size))
            current = []
            continue
        if run_type == "text":
            pieces = text.splitlines()
            if not pieces:
                continue
            current.append(pieces[0])
            for extra in pieces[1:]:
                logical_line = "".join(current).strip()
                if logical_line:
                    lines.extend(wrap_text_line(logical_line, width, font_size))
                current = [extra]
    logical_line = "".join(current).strip()
    if logical_line:
        lines.extend(wrap_text_line(logical_line, width, font_size))
    return clip_dense_body_lines(atom, merge_dense_body_special_lines(lines))


def version_stack_label_and_detail(atom: dict[str, Any]) -> tuple[str, str]:
    lines = [line.strip() for line in str(atom.get("text") or "").splitlines() if line.strip()]
    if not lines:
        return "", ""
    return lines[0], "\n".join(lines[1:]).strip()


def build_version_stack_block(atom: dict[str, Any], *, use_svg_background: bool = False) -> tuple[dict[str, Any], dict[str, Any] | None]:
    bounds = dict(atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0))
    label_text, detail_text = version_stack_label_and_detail(atom)
    label_height = min(12.0, max(6.0, float(bounds["height"]) * 0.2))
    label_bounds = make_bounds(
        float(bounds["x"]) + 7.0,
        float(bounds["y"]) + 3.5,
        max(float(bounds["width"]) - 14.0, 8.0),
        label_height,
    )
    if use_svg_background:
        block_children = [build_overlay_svg_rect_node(atom, suffix=":bg")]
    else:
        block_children = [build_rect_node(atom, suffix=":bg")]
    if label_text:
        block_children.append(build_text_leaf(atom, label_text, label_bounds, suffix=":label"))
    block_group = build_owner_frame(f"{atom['id']}:version_block", block_children, bounds=bounds)

    detail_node: dict[str, Any] | None = None
    if detail_text:
        detail_bounds = make_bounds(
            float(bounds["x"]) + 7.0,
            float(bounds["y"]) + label_height + 6.0,
            max(float(bounds["width"]) - 14.0, 8.0),
            max(float(bounds["height"]) - label_height - 8.0, 8.0),
        )
        detail_node = build_text_leaf(atom, detail_text, detail_bounds, suffix=":detail")
    return block_group, detail_node


def build_paragraph_text_group(atom: dict[str, Any], bounds: dict[str, Any], *, suffix: str = "") -> dict[str, Any]:
    style = text_style(atom)
    font_size = float(style["fontSize"])
    line_height = float(style["lineHeightPx"])
    if str(atom.get("owner_id") or "") == "dense_ui_panel:description_lanes":
        line_height = min(line_height, round(font_size + 0.9, 2))
    if str(atom.get("id") or "") == "s29:slide_29/element_37:row_5:cell_2":
        line_height = min(line_height, round(font_size + 0.55, 2))
    lines = body_text_lines(atom, float(bounds["width"]), font_size)
    if not lines:
        return build_owner_group(f"{atom['id']}{suffix}:empty", [])
    left = float(bounds["x"])
    top = float(bounds["y"])
    width = float(bounds["width"])
    children: list[dict[str, Any]] = []
    current_y = top
    max_bottom = top + float(bounds["height"])
    for index, line in enumerate(lines):
        if current_y >= max_bottom:
            break
        visual_line_units = max(1, len([part for part in str(line).splitlines() if part.strip()]) or 1)
        multiline_padding = 0.0
        if visual_line_units > 1:
            multiline_padding = 0.8 * (visual_line_units - 1)
        paragraph_height = min((line_height * visual_line_units) + multiline_padding, max_bottom - current_y)
        if paragraph_height <= 1.0:
            break
        paragraph_bounds = make_bounds(left, current_y, width, paragraph_height)
        paragraph_atom = dict(atom)
        paragraph_atom["text"] = line
        fill_override = inferred_line_fill(atom, line)
        if fill_override:
            paragraph_atom["text_style"] = dict(atom.get("text_style") or {}, fill=fill_override)
        children.append(build_text_node(paragraph_atom, paragraph_bounds, suffix=f"{suffix}:p{index + 1}"))
        current_y += paragraph_height
    return build_owner_group(f"{atom['id']}{suffix}:paragraphs", children)


def build_rect_node(atom: dict[str, Any], bounds: dict[str, Any] | None = None, *, suffix: str = "") -> dict[str, Any]:
    node_bounds = dict(bounds or atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0))
    shape_style = atom.get("shape_style") or {}
    role = str(atom.get("layer_role") or "")
    source_path = str(((atom.get("debug_tags") or {}).get("source_path")) or "")
    fill_style = shape_style.get("fill") or (atom.get("cell_style") or {}).get("fill")
    if role in {"top_meta_band_cell", "top_meta_info_cell"} and source_path.endswith(("cell_2", "cell_4")):
        # Value cells in top meta rows are white in the PPT reference.
        fill_style = {"type": "srgb", "value": "FFFFFF", "alpha": 1.0, "kind": "solid"}
    elif role in {"top_meta_band_cell", "top_meta_info_cell", "description_header_cell"}:
        resolved = str((fill_style or {}).get("resolved_value") or "").upper()
        if resolved in {"", "FFFFFF"}:
            fill_style = {"type": "srgb", "value": "F2F2F2", "alpha": 1.0, "kind": "solid"}
    fills = [make_solid_fill(fill_style, {"r": 1.0, "g": 1.0, "b": 1.0})]
    strokes, stroke_weight = make_strokes(shape_style)
    if role in {"top_meta_band_cell", "top_meta_info_cell"} and not strokes:
        strokes = [
            {
                "type": "SOLID",
                "color": {"r": 191 / 255, "g": 191 / 255, "b": 191 / 255},
                "opacity": 1.0,
            }
        ]
        stroke_weight = 0.5
    return {
        "id": f"{atom['id']}{suffix}",
        "type": "RECTANGLE",
        "name": atom.get("title") or atom.get("id") or "rect",
        "absoluteBoundingBox": node_bounds,
        "relativeTransform": identity_affine(),
        "fills": fills,
        "strokes": strokes,
        "strokeWeight": stroke_weight,
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "layer_role": atom.get("layer_role"),
            "owner_id": atom.get("owner_id"),
            "source_atom_id": atom.get("id"),
        },
    }


def svg_color(style_color: dict[str, Any] | None, fallback: str, fallback_opacity: float = 1.0) -> tuple[str, float]:
    color, opacity = color_from_style(style_color, {"r": 0.0, "g": 0.0, "b": 0.0})
    return (
        f"rgb({round(color['r'] * 255)}, {round(color['g'] * 255)}, {round(color['b'] * 255)})" if style_color else fallback,
        opacity if style_color else fallback_opacity,
    )


def build_small_asset_svg_node(atom: dict[str, Any], bounds: dict[str, Any] | None = None, *, suffix: str = "") -> dict[str, Any] | None:
    node_bounds = dict(bounds or atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0))
    width = max(float(node_bounds["width"]), 1.0)
    height = max(float(node_bounds["height"]), 1.0)
    shape_kind = str(atom.get("shape_kind") or "")
    shape_style = atom.get("shape_style") or {}
    render_hint = str(atom.get("render_hint") or "")
    title = str(atom.get("title") or "")
    text_value = str(atom.get("text") or "").strip()
    fill_color, fill_opacity = svg_color(shape_style.get("fill"), "rgb(255,255,255)")
    line_style = shape_style.get("line") or {}
    stroke_color, stroke_opacity = svg_color(line_style, "rgb(120,120,120)")
    stroke_width = max(float(line_style.get("width_px") or 1.0), 1.0)

    if title.lower() == "like":
        svg_markup = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<path d="M {width * 0.5:.2f} {height * 0.86:.2f} '
            f'C {width * 0.32:.2f} {height * 0.70:.2f}, {width * 0.12:.2f} {height * 0.54:.2f}, {width * 0.12:.2f} {height * 0.31:.2f} '
            f'C {width * 0.12:.2f} {height * 0.15:.2f}, {width * 0.24:.2f} {height * 0.06:.2f}, {width * 0.37:.2f} {height * 0.06:.2f} '
            f'C {width * 0.45:.2f} {height * 0.06:.2f}, {width * 0.50:.2f} {height * 0.12:.2f}, {width * 0.50:.2f} {height * 0.18:.2f} '
            f'C {width * 0.50:.2f} {height * 0.12:.2f}, {width * 0.55:.2f} {height * 0.06:.2f}, {width * 0.63:.2f} {height * 0.06:.2f} '
            f'C {width * 0.76:.2f} {height * 0.06:.2f}, {width * 0.88:.2f} {height * 0.15:.2f}, {width * 0.88:.2f} {height * 0.31:.2f} '
            f'C {width * 0.88:.2f} {height * 0.54:.2f}, {width * 0.68:.2f} {height * 0.70:.2f}, {width * 0.50:.2f} {height * 0.86:.2f} Z" '
            f'fill="none" stroke="rgb(95,95,95)" stroke-width="{max(stroke_width, 1.4):.2f}" stroke-linejoin="round" stroke-linecap="round"/>'
            "</svg>"
        )
    elif render_hint in {"viewer_diagonal_tl_br", "viewer_diagonal_bl_tr"}:
        x1, y1, x2, y2 = (0, 0, width, height)
        if render_hint == "viewer_diagonal_bl_tr":
            x1, y1, x2, y2 = (0, height, width, 0)
        svg_markup = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="rgb(255,255,255)" stroke-opacity="1" stroke-width="1.5" stroke-linecap="round"/>'
            "</svg>"
        )
    elif shape_kind == "ellipse":
        if text_value == "?" and stroke_color == "rgb(255, 255, 255)":
            stroke_color = "rgb(140,140,140)"
            stroke_opacity = 1.0
        svg_markup = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<ellipse cx="{width / 2}" cy="{height / 2}" rx="{max(width / 2 - stroke_width / 2, 0.5)}" '
            f'ry="{max(height / 2 - stroke_width / 2, 0.5)}" '
            f'fill="{fill_color}" fill-opacity="{fill_opacity}" '
            f'stroke="{stroke_color}" stroke-opacity="{stroke_opacity}" stroke-width="{stroke_width}"/>'
            "</svg>"
        )
    elif shape_kind in {"rect", "roundRect", "shape"} or atom.get("subtype") in {"shape", "labeled_shape"}:
        rx = 0.0
        if shape_kind == "roundRect":
            rx = max(min(width, height) * 0.16, 3.0)
        if fill_opacity <= 0.0 and (not line_style or line_style.get("kind") == "none"):
            return None
        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        ]
        if line_style and line_style.get("kind") != "none":
            inset = stroke_width / 2.0
            svg_parts.append(
                f'<rect x="{inset}" y="{inset}" width="{max(width - stroke_width, 0.5)}" height="{max(height - stroke_width, 0.5)}"'
                f' rx="{max(rx - inset, 0.0)}" ry="{max(rx - inset, 0.0)}"'
                f' fill="{fill_color}" fill-opacity="{fill_opacity}"'
                f' stroke="{stroke_color}" stroke-opacity="{stroke_opacity}" stroke-width="{stroke_width}"/>'
            )
        else:
            svg_parts.append(
                f'<rect x="0" y="0" width="{width}" height="{height}" rx="{rx}" ry="{rx}"'
                f' fill="{fill_color}" fill-opacity="{fill_opacity}"/>'
            )
        svg_parts.append("</svg>")
        svg_markup = "".join(svg_parts)
    elif shape_kind == "straightConnector1" or atom.get("subtype") == "connector":
        mid_y = height / 2
        arrow = max(min(width, height) * 0.35, 3.0)
        line_end_x = max(width - arrow - stroke_width, stroke_width)
        svg_markup = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<line x1="{stroke_width / 2}" y1="{mid_y}" x2="{line_end_x}" y2="{mid_y}" '
            f'stroke="{stroke_color}" stroke-opacity="{stroke_opacity}" stroke-width="{stroke_width}" stroke-linecap="round"/>'
            f'<polygon points="{line_end_x},{max(mid_y - arrow / 2, 0)} {width},{mid_y} {line_end_x},{min(mid_y + arrow / 2, height)}" '
            f'fill="{stroke_color}" fill-opacity="{stroke_opacity}"/>'
            "</svg>"
        )
    else:
        return None

    return {
        "id": f"{atom['id']}{suffix}",
        "type": "SVG_BLOCK",
        "name": atom.get("title") or atom.get("id") or "small_asset_svg",
        "absoluteBoundingBox": node_bounds,
        "relativeTransform": identity_affine(),
        "svgMarkup": svg_markup,
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "layer_role": atom.get("layer_role"),
            "owner_id": atom.get("owner_id"),
            "source_atom_id": atom.get("id"),
        },
    }


def build_overlay_svg_rect_node(atom: dict[str, Any], bounds: dict[str, Any] | None = None, *, suffix: str = "") -> dict[str, Any]:
    node_bounds = dict(bounds or atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0))
    width = max(float(node_bounds["width"]), 1.0)
    height = max(float(node_bounds["height"]), 1.0)
    shape_style = atom.get("shape_style") or {}
    role = str(atom.get("layer_role") or "")
    source_path = str(((atom.get("debug_tags") or {}).get("source_path")) or "")
    fill_style = shape_style.get("fill") or (atom.get("cell_style") or {}).get("fill")
    if role in {"top_meta_band_cell", "top_meta_info_cell"} and source_path.endswith(("cell_2", "cell_4")):
        # Value cells in top meta rows are white in the PPT reference.
        fill_style = {"type": "srgb", "value": "FFFFFF", "alpha": 1.0, "kind": "solid"}
    elif not fill_style and role in {"top_meta_band_cell", "top_meta_info_cell", "description_header_cell"}:
        fill_style = {"type": "srgb", "value": "F2F2F2", "alpha": 1.0, "kind": "solid"}
    elif role in {"top_meta_band_cell", "top_meta_info_cell", "description_header_cell"}:
        resolved = str(fill_style.get("resolved_value") or "").upper()
        if resolved in {"", "FFFFFF"}:
            fill_style = {"type": "srgb", "value": "F2F2F2", "alpha": 1.0, "kind": "solid"}
    fill_color, fill_opacity = svg_color(fill_style, "rgb(255,255,255)", 0.0)
    line_style = shape_style.get("line") or {}
    if not line_style and role in {"top_meta_band_cell", "top_meta_info_cell"}:
        line_style = {
            "type": "solid",
            "width_px": 0.5,
            "resolved_value": "BFBFBF",
            "alpha": 1.0,
        }
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    ]
    if line_style and line_style.get("type"):
        stroke_color, stroke_opacity = svg_color(line_style, "rgb(0,0,0)")
        stroke_width = max(float(line_style.get("width_px") or 1.0), 0.5)
        inset = stroke_width / 2.0
        svg_parts.append(
            f'<rect x="{inset}" y="{inset}" width="{max(width - stroke_width, 0.5)}" height="{max(height - stroke_width, 0.5)}" '
            f'fill="{fill_color}" fill-opacity="{fill_opacity}" '
            f'stroke="{stroke_color}" stroke-opacity="{stroke_opacity}" stroke-width="{stroke_width}"/>'
        )
    else:
        svg_parts.append(
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="{fill_color}" fill-opacity="{fill_opacity}"/>'
        )
    svg_parts.append("</svg>")
    return {
        "id": f"{atom['id']}{suffix}",
        "type": "SVG_BLOCK",
        "name": atom.get("title") or atom.get("id") or "overlay_svg_rect",
        "absoluteBoundingBox": node_bounds,
        "relativeTransform": identity_affine(),
        "svgMarkup": "".join(svg_parts),
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "layer_role": atom.get("layer_role"),
            "owner_id": atom.get("owner_id"),
            "source_atom_id": atom.get("id"),
            "role": "overlay_svg_rect",
        },
    }


def build_svg_rect_node(
    node_id: str,
    name: str,
    bounds: dict[str, Any],
    *,
    fill: dict[str, float] | None = None,
    fill_opacity: float = 1.0,
    stroke: dict[str, float] | None = None,
    stroke_opacity: float = 1.0,
    stroke_weight: float = 0.0,
    owner_id: str = "dense_ui_panel:description_table_grid",
) -> dict[str, Any]:
    width = max(float(bounds["width"]), 1.0)
    height = max(float(bounds["height"]), 1.0)
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    ]
    if stroke is not None and stroke_weight > 0.0:
        stroke_css = f'rgb({round(stroke["r"] * 255)}, {round(stroke["g"] * 255)}, {round(stroke["b"] * 255)})'
        fill_css = "none"
        fill_attr = ""
        if fill is not None:
            fill_css = f'rgb({round(fill["r"] * 255)}, {round(fill["g"] * 255)}, {round(fill["b"] * 255)})'
            fill_attr = f' fill="{fill_css}" fill-opacity="{fill_opacity}"'
        inset = stroke_weight / 2.0
        svg_parts.append(
            f'<rect x="{inset}" y="{inset}" width="{max(width - stroke_weight, 0.5)}" height="{max(height - stroke_weight, 0.5)}"'
            f'{fill_attr} stroke="{stroke_css}" stroke-opacity="{stroke_opacity}" stroke-width="{stroke_weight}"/>'
        )
    else:
        fill_css = "rgb(255,255,255)"
        fill_alpha = 0.0
        if fill is not None:
            fill_css = f'rgb({round(fill["r"] * 255)}, {round(fill["g"] * 255)}, {round(fill["b"] * 255)})'
            fill_alpha = fill_opacity
        svg_parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="{fill_css}" fill-opacity="{fill_alpha}"/>')
    svg_parts.append("</svg>")
    return {
        "id": node_id,
        "type": "SVG_BLOCK",
        "name": name,
        "absoluteBoundingBox": dict(bounds),
        "relativeTransform": identity_affine(),
        "svgMarkup": "".join(svg_parts),
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "owner_id": owner_id,
            "role": "dense_table_grid_svg",
        },
    }


def build_image_node(atom: dict[str, Any], assets: dict[str, Any]) -> dict[str, Any] | None:
    image_base64 = atom.get("image_base64")
    if not image_base64:
        return None
    image_ref = f"asset:{atom['id']}"
    assets[image_ref] = {
        "base64": image_base64,
        "mime_type": atom.get("mime_type") or "image/png",
    }
    return {
        "id": atom["id"],
        "type": "RECTANGLE",
        "name": atom.get("title") or atom.get("id") or "image",
        "absoluteBoundingBox": atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0),
        "relativeTransform": identity_affine(),
        "fills": [{"type": "IMAGE", "imageRef": image_ref, "scaleMode": "FIT"}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "layer_role": atom.get("layer_role"),
            "owner_id": atom.get("owner_id"),
            "source_atom_id": atom.get("id"),
        },
    }


def build_owner_group(owner_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = union_bounds(
        [
            child.get("absoluteBoundingBox") or make_bounds(0.0, 0.0, 1.0, 1.0)
            for child in children
        ]
    )
    return {
        "id": owner_id,
        "type": "GROUP",
        "name": owner_id.split(":")[-1],
        "absoluteBoundingBox": bounds,
        "relativeTransform": identity_affine(),
        "children": children,
        "debug": {
            "generator": "dense-ui-ir-v1",
            "owner_id": owner_id,
        },
    }


def build_owner_frame(
    owner_id: str,
    children: list[dict[str, Any]],
    *,
    bounds: dict[str, Any] | None = None,
    clips_content: bool = True,
) -> dict[str, Any]:
    frame_bounds = dict(
        bounds
        or union_bounds(
            [
                child.get("absoluteBoundingBox") or make_bounds(0.0, 0.0, 1.0, 1.0)
                for child in children
            ]
        )
    )
    return {
        "id": owner_id,
        "type": "FRAME",
        "name": owner_id.split(":")[-1],
        "absoluteBoundingBox": frame_bounds,
        "relativeTransform": identity_affine(),
        "fills": [],
        "strokes": [],
        "strokeWeight": 0,
        "clipsContent": clips_content,
        "children": children,
        "debug": {
            "generator": "dense-ui-ir-v1",
            "owner_id": owner_id,
            "frame_container": True,
        },
    }


def build_group_group(group_id: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    bounds = union_bounds(
        [
            child.get("absoluteBoundingBox") or make_bounds(0.0, 0.0, 1.0, 1.0)
            for child in children
        ]
    )
    return {
        "id": group_id,
        "type": "GROUP",
        "name": group_id.split(":")[-1],
        "absoluteBoundingBox": bounds,
        "relativeTransform": identity_affine(),
        "children": children,
        "debug": {
            "generator": "dense-ui-ir-v1",
            "group_id": group_id,
        },
    }


def build_top_meta_cell_rect(
    atom: dict[str, Any],
    *,
    fill_hex: str,
    stroke_hex: str = "BFBFBF",
    stroke_weight: float = 0.5,
) -> dict[str, Any]:
    color = {
        "r": int(fill_hex[0:2], 16) / 255.0,
        "g": int(fill_hex[2:4], 16) / 255.0,
        "b": int(fill_hex[4:6], 16) / 255.0,
    }
    stroke = {
        "r": int(stroke_hex[0:2], 16) / 255.0,
        "g": int(stroke_hex[2:4], 16) / 255.0,
        "b": int(stroke_hex[4:6], 16) / 255.0,
    }
    return {
        "id": f"{atom['id']}:scaffold",
        "type": "RECTANGLE",
        "name": atom.get("id") or "top_meta_cell",
        "absoluteBoundingBox": dict(atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0)),
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": color, "opacity": 1.0}],
        "strokes": [{"type": "SOLID", "color": stroke, "opacity": 1.0}],
        "strokeWeight": stroke_weight,
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "owner_id": atom.get("owner_id"),
            "source_atom_id": atom.get("id"),
            "role": "top_meta_scaffold",
        },
    }


def build_top_meta_scaffold_groups(page: dict[str, Any]) -> list[dict[str, Any]]:
    atoms = page.get("atoms") or []
    band_cells = sorted(
        [atom for atom in atoms if str(atom.get("owner_id") or "") == "dense_ui_panel:top_meta_band_cells"],
        key=atom_priority,
    )
    info_cells = sorted(
        [atom for atom in atoms if str(atom.get("owner_id") or "") == "dense_ui_panel:top_meta_info_cells"],
        key=atom_priority,
    )

    def scaffold_for_cells(group_id: str, cells: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not cells:
            return None
        children: list[dict[str, Any]] = []
        for atom in cells:
            source_path = str(((atom.get("debug_tags") or {}).get("source_path")) or "")
            is_value = source_path.endswith(("cell_2", "cell_4"))
            children.append(build_top_meta_cell_rect(atom, fill_hex="FFFFFF" if is_value else "F2F2F2"))
            if atom.get("text"):
                children.append(build_text_node(atom, suffix=":label"))
        return build_group_group(group_id, children)

    groups: list[dict[str, Any]] = []
    band_group = scaffold_for_cells("dense_ui_panel:top_meta_band_chunk", band_cells)
    info_group = scaffold_for_cells("dense_ui_panel:top_meta_info_chunk", info_cells)
    if band_group:
        groups.append(band_group)
    if info_group:
        groups.append(info_group)
    return groups


def row_index_from_atom(atom: dict[str, Any]) -> int | None:
    atom_id = str(atom.get("id") or "")
    match = ROW_ID_RE.search(atom_id)
    if not match:
        return None
    return int(match.group(1))


def build_default_lane_background(bounds: dict[str, Any], row_index: int, style_atom: dict[str, Any] | None = None) -> dict[str, Any]:
    fill = {"r": 1.0, "g": 1.0, "b": 1.0}
    opacity = 1.0
    stroke_opacity = 1.0
    if style_atom and (style_atom.get("shape_style") or {}).get("fill"):
        fill, opacity = color_from_style((style_atom.get("shape_style") or {}).get("fill"), fill)
        _, stroke_opacity = color_from_style((style_atom.get("shape_style") or {}).get("line") or {}, {"r": 0.82, "g": 0.82, "b": 0.82})
    elif row_index == 6:
        fill = {"r": 0.96, "g": 0.95, "b": 0.92}
    elif row_index >= 5:
        opacity = 0.0
        stroke_opacity = 0.0
    return {
        "id": f"lane-row-{row_index}:bg",
        "type": "RECTANGLE",
        "name": f"lane_row_{row_index}_bg",
        "absoluteBoundingBox": dict(bounds),
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": fill, "opacity": opacity}],
        "strokes": [{"type": "SOLID", "color": {"r": 0.82, "g": 0.82, "b": 0.82}, "opacity": stroke_opacity}],
        "strokeWeight": 1,
        "children": [],
        "debug": {"generator": "dense-ui-ir-v1", "role": "table_backed_lane_background", "row_index": row_index},
    }


def build_grid_rect(
    node_id: str,
    name: str,
    bounds: dict[str, Any],
    *,
    fill: dict[str, float] | None = None,
    fill_opacity: float = 1.0,
    stroke: dict[str, float] | None = None,
    stroke_opacity: float = 1.0,
    stroke_weight: float = 0.0,
    owner_id: str = "dense_ui_panel:description_table_grid",
) -> dict[str, Any]:
    fills = []
    if fill is not None:
        fills = [{"type": "SOLID", "color": fill, "opacity": fill_opacity}]
    strokes = []
    if stroke is not None and stroke_weight > 0.0:
        strokes = [{"type": "SOLID", "color": stroke, "opacity": stroke_opacity}]
    return {
        "id": node_id,
        "type": "RECTANGLE",
        "name": name,
        "absoluteBoundingBox": dict(bounds),
        "relativeTransform": identity_affine(),
        "fills": fills,
        "strokes": strokes,
        "strokeWeight": stroke_weight,
        "children": [],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "owner_id": owner_id,
            "role": "dense_table_grid",
        },
    }


def build_dense_table_grid_layer(
    lane_layout: dict[int, dict[str, Any]],
    lane_markers: dict[int, dict[str, Any]],
    lane_texts: dict[int, dict[str, Any]],
    footer_atom: dict[str, Any] | None,
) -> dict[str, Any] | None:
    ordered_rows = [row_index for row_index in [3, 4, 5] if row_index in lane_layout]
    if not ordered_rows:
        return None

    marker_fill = {"r": 0.949, "g": 0.949, "b": 0.949}
    content_fill = {"r": 1.0, "g": 1.0, "b": 1.0}
    line_color = {"r": 0.651, "g": 0.651, "b": 0.651}
    line_weight = 0.25
    line_half = line_weight / 2.0
    children: list[dict[str, Any]] = []

    first_lane_bounds = lane_layout[ordered_rows[0]]["lane_bounds"]
    footer_layout = lane_layout.get(6)
    bottom_bounds = (footer_layout or lane_layout[ordered_rows[-1]])["lane_bounds"]
    table_left = float(first_lane_bounds["x"])
    table_top = float(first_lane_bounds["y"])
    table_right = float(bottom_bounds["x"]) + float(bottom_bounds["width"])
    table_bottom = float(bottom_bounds["y"]) + float(bottom_bounds["height"])

    divider_x = float(lane_layout[ordered_rows[0]]["marker_bounds"]["x"]) + float(lane_layout[ordered_rows[0]]["marker_bounds"]["width"])

    for row_index in ordered_rows:
        layout = lane_layout[row_index]
        lane_bounds = layout["lane_bounds"]
        marker_bounds = layout["marker_bounds"]
        text_bounds = layout["text_bounds"]
        if marker_bounds:
            children.append(
                build_svg_rect_node(
                    f"dense-table-grid:row-{row_index}:marker-fill",
                    f"row_{row_index}_marker_fill",
                    make_bounds(marker_bounds["x"], lane_bounds["y"], max(float(marker_bounds["width"]) - 0.4, 8.0), lane_bounds["height"]),
                    fill=marker_fill,
                )
            )
        children.append(
            build_svg_rect_node(
                f"dense-table-grid:row-{row_index}:text-fill",
                f"row_{row_index}_text_fill",
                make_bounds(divider_x, lane_bounds["y"], table_right - divider_x, lane_bounds["height"]),
                fill=content_fill,
            )
        )

    if footer_layout:
        footer_bounds = footer_layout["lane_bounds"]
        children.append(
            build_svg_rect_node(
                "dense-table-grid:footer-fill",
                "footer_fill",
                footer_bounds,
                fill=content_fill,
            )
        )

    horizontal_positions = [table_top]
    for row_index in ordered_rows:
        lane_bounds = lane_layout[row_index]["lane_bounds"]
        horizontal_positions.append(float(lane_bounds["y"]) + float(lane_bounds["height"]))
    if footer_layout:
        horizontal_positions.append(float(footer_layout["lane_bounds"]["y"]) + float(footer_layout["lane_bounds"]["height"]))

    seen_horizontal: set[float] = set()
    for position in horizontal_positions:
        rounded = round(position, 2)
        if rounded in seen_horizontal:
            continue
        seen_horizontal.add(rounded)
        children.append(
            build_svg_rect_node(
                f"dense-table-grid:hline:{rounded}",
                f"hline_{rounded}",
                make_bounds(table_left - 0.12, position - line_half, (table_right - table_left) + 0.24, line_weight),
                stroke=line_color,
                stroke_opacity=1.0,
                stroke_weight=0.0,
                fill=None,
            )
        )
        children[-1] = build_svg_rect_node(
            f"dense-table-grid:hline:{rounded}",
            f"hline_{rounded}",
            make_bounds(table_left - 0.12, position - line_half, (table_right - table_left) + 0.24, line_weight),
            stroke=line_color,
            stroke_opacity=1.0,
            stroke_weight=line_weight,
        )

    for position, label in [(table_left, "left"), (divider_x, "divider"), (table_right, "right")]:
        children.append(
            build_svg_rect_node(
                f"dense-table-grid:vline:{label}",
                f"vline_{label}",
                make_bounds(position - line_half, table_top - 0.13, line_weight, (table_bottom - table_top) + 0.26),
                stroke=line_color,
                stroke_opacity=1.0,
                stroke_weight=line_weight,
            )
        )

    return build_owner_group("dense_ui_panel:description_table_grid", children)


def estimate_wrap_chars(width: float, font_size: float) -> int:
    glyph_width = max(font_size * 0.95, 6.4)
    usable_width = max(width - 8.0, 24.0)
    return max(10, int(usable_width / glyph_width))


def estimate_text_height(text: str, width: float, font_size: float, line_gap: float = 2.0) -> float:
    wrapped = textwrap.wrap(
        text.strip() or " ",
        width=estimate_wrap_chars(width, font_size),
        break_long_words=False,
        break_on_hyphens=False,
    )
    line_count = max(1, len(wrapped))
    line_height = font_size + line_gap
    return line_count * line_height + 6.0


def estimate_paragraph_group_height(atom: dict[str, Any], width: float, font_size: float, line_gap: float = 2.0) -> float:
    if str(atom.get("owner_id") or "") == "dense_ui_panel:description_lanes":
        rendered_line_height = min(float(text_style(atom, font_size).get("lineHeightPx") or (font_size * 1.25)), round(font_size + 0.9, 2))
        total_units = 0
        for line in body_text_lines(atom, width, font_size):
            units = max(1, len([part for part in str(line).splitlines() if part.strip()]) or 1)
            total_units += units
        return max(1, total_units) * rendered_line_height
    paragraphs = paragraph_texts(atom)
    if not paragraphs:
        return estimate_text_height(str(atom.get("text") or ""), width, font_size, line_gap)
    rendered_line_height = float(text_style(atom, font_size).get("lineHeightPx") or (font_size * 1.25))
    return len(paragraphs) * (rendered_line_height + 2.0)


def build_description_lane_layout(
    lane_rows: dict[int, dict[str, Any]],
    lane_markers: dict[int, dict[str, Any]],
    lane_texts: dict[int, dict[str, Any]],
    footer_atom: dict[str, Any] | None,
    issue_bounds: dict[str, Any] | None = None,
) -> dict[int, dict[str, Any]]:
    if not lane_rows:
        return {}
    ordered_rows = sorted(index for index in lane_rows if index in {3, 4, 5})
    if not ordered_rows:
        return {}
    current_y = float(lane_rows[ordered_rows[0]]["visual_bounds_px"]["y"])
    max_bottom = TARGET_SLIDE_HEIGHT - 12.0
    layouts: dict[int, dict[str, Any]] = {}
    for row_index in ordered_rows:
        row_atom = lane_rows[row_index]
        marker_atom = lane_markers.get(row_index)
        text_atom = lane_texts.get(row_index)
        row_bounds = row_atom["visual_bounds_px"]
        marker_bounds = dict(marker_atom["visual_bounds_px"]) if marker_atom else make_bounds(row_bounds["x"], current_y, 24.0, row_bounds["height"])
        text_bounds = dict(text_atom["visual_bounds_px"]) if text_atom else make_bounds(marker_bounds["x"] + marker_bounds["width"], current_y, row_bounds["width"] - marker_bounds["width"], row_bounds["height"])
        text_gutter = 2.8
        text_bounds = make_bounds(
            float(text_bounds["x"]) + text_gutter,
            float(text_bounds["y"]),
            max(float(text_bounds["width"]) - text_gutter, 8.0),
            float(text_bounds["height"]),
        )
        if issue_bounds and row_index in {3, 4}:
            issue_left = float(issue_bounds["x"])
            available_width = issue_left - float(text_bounds["x"]) - 8.0
            if available_width > 40.0:
                text_bounds = make_bounds(float(text_bounds["x"]), current_y, available_width, float(text_bounds["height"]))
        font_size = float((text_atom or {}).get("text_style", {}).get("font_size_max") or 8.0)
        estimated_height = estimate_paragraph_group_height(text_atom or {}, float(text_bounds["width"]), font_size)
        top_padding = 0.0
        bottom_padding = 0.0
        if row_index in {4, 5}:
            top_padding = 2.0
            bottom_padding = 4.0
        lane_height = max(float(row_bounds["height"]), estimated_height + top_padding + bottom_padding)
        remaining_height = max_bottom - current_y
        if remaining_height <= 18.0:
            break
        lane_height = min(lane_height, remaining_height)
        lane_bounds = make_bounds(float(row_bounds["x"]), current_y, float(row_bounds["width"]), lane_height)
        marker_bounds = make_bounds(float(marker_bounds["x"]), current_y, float(marker_bounds["width"]), lane_height)
        text_height = max(8.0, lane_height - top_padding - bottom_padding)
        text_bounds = make_bounds(float(text_bounds["x"]), current_y + top_padding, float(text_bounds["width"]), text_height)
        layouts[row_index] = {
            "lane_bounds": lane_bounds,
            "marker_bounds": marker_bounds,
            "text_bounds": text_bounds,
        }
        current_y += lane_height
    if footer_atom:
        footer_bounds = footer_atom["visual_bounds_px"]
        footer_y = min(current_y + 8.0, max_bottom - 14.0)
        footer_height = max(14.0, min(float(footer_bounds["height"]), max_bottom - footer_y))
        layouts[6] = {
            "lane_bounds": make_bounds(float(footer_bounds["x"]), footer_y, float(footer_bounds["width"]), footer_height),
            "marker_bounds": None,
            "text_bounds": make_bounds(float(footer_bounds["x"]), footer_y, float(footer_bounds["width"]), footer_height),
        }
    return layouts


def max_overlap_area(a: dict[str, Any], b: dict[str, Any]) -> float:
    left = max(float(a["x"]), float(b["x"]))
    top = max(float(a["y"]), float(b["y"]))
    right = min(float(a["x"]) + float(a["width"]), float(b["x"]) + float(b["width"]))
    bottom = min(float(a["y"]) + float(a["height"]), float(b["y"]) + float(b["height"]))
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top)


def mostly_same_bounds(a: dict[str, Any], b: dict[str, Any], *, tolerance: float = 2.0) -> bool:
    return (
        abs(float(a["x"]) - float(b["x"])) <= tolerance
        and abs(float(a["y"]) - float(b["y"])) <= tolerance
        and abs(float(a["width"]) - float(b["width"])) <= tolerance
        and abs(float(a["height"]) - float(b["height"])) <= tolerance
    )


def best_overlapping_card(bounds: dict[str, Any], card_atoms: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not card_atoms:
        return None
    ranked = sorted(card_atoms, key=lambda atom: max_overlap_area(bounds, atom.get("visual_bounds_px") or bounds), reverse=True)
    top = ranked[0]
    if max_overlap_area(bounds, top.get("visual_bounds_px") or bounds) <= 0:
        return None
    return top


def dense_panel_bounds(page: dict[str, Any]) -> dict[str, float]:
    relevant = []
    for atom in page.get("atoms") or []:
        role = str(atom.get("layer_role") or "")
        owner_id = str(atom.get("owner_id") or "")
        if role in {
            "top_meta_info_cell",
            "version_stack",
            "issue_card",
            "description_header_cell",
            "description_text_lane",
            "description_footer",
            "description_marker",
        }:
            bounds = atom.get("visual_bounds_px")
            if bounds and float(bounds["x"]) + float(bounds["width"]) >= RIGHT_PANEL_X_CUTOFF:
                relevant.append(bounds)
        if role in {"small_asset", "overlay_note"} and owner_id in {"dense_ui_panel:panel_small_assets", "dense_ui_panel:panel_overlay_notes"}:
            bounds = atom.get("visual_bounds_px")
            if bounds:
                relevant.append(bounds)
    if not relevant:
        return make_bounds(TARGET_SLIDE_WIDTH * 0.6, 0.0, TARGET_SLIDE_WIDTH * 0.4, TARGET_SLIDE_HEIGHT)
    return union_bounds(relevant)


def is_right_panel_atom(atom: dict[str, Any]) -> bool:
    bounds = atom.get("visual_bounds_px") or {}
    x = float(bounds.get("x") or 0.0)
    width = float(bounds.get("width") or 0.0)
    role = str(atom.get("layer_role") or "")
    if role in {"description_card", "description_text_lane", "description_footer", "description_marker", "issue_card", "version_stack"}:
        return True
    return x + width >= RIGHT_PANEL_X_CUTOFF


def is_zero_bounds_placeholder_text(atom: dict[str, Any]) -> bool:
    if str(atom.get("owner_id") or "") != "dense_ui_panel:global_ui_assets":
        return False
    if str(atom.get("layer_role") or "") != "small_asset":
        return False
    if not str(atom.get("text") or "").strip():
        return False
    bounds = atom.get("visual_bounds_px") or {}
    return (
        float(bounds.get("x") or 0.0) == 0.0
        and float(bounds.get("y") or 0.0) == 0.0
        and atom.get("placeholder") is not None
    )


def placeholder_sort_key(atom: dict[str, Any]) -> tuple[int, int, str]:
    placeholder = atom.get("placeholder") or {}
    idx = placeholder.get("idx")
    try:
        placeholder_idx = int(idx)
    except (TypeError, ValueError):
        placeholder_idx = 9999
    try:
        source_node_id = int(atom.get("source_node_id") or 0)
    except (TypeError, ValueError):
        source_node_id = 0
    return (placeholder_idx, source_node_id, str(atom.get("id") or ""))


def is_empty_top_meta_value_cell(atom: dict[str, Any]) -> bool:
    return (
        str(atom.get("owner_id") or "") in {"dense_ui_panel:top_meta_band_cells", "dense_ui_panel:top_meta_info_cells"}
        and not str(atom.get("text") or "").strip()
    )


def inset_bounds(bounds: dict[str, Any], *, dx: float = 0.0, dy: float = 0.0, dw: float = 0.0, dh: float = 0.0) -> dict[str, float]:
    return make_bounds(
        float(bounds["x"]) + dx,
        float(bounds["y"]) + dy,
        max(float(bounds["width"]) + dw, 1.0),
        max(float(bounds["height"]) + dh, 1.0),
    )


def anchored_placeholder_bounds(atom: dict[str, Any], cell_bounds: dict[str, Any]) -> dict[str, float]:
    text = str(atom.get("text") or "")
    line_count = max(len(text.splitlines()), 1)
    if line_count > 1:
        line_height = 6.2
        return inset_bounds(
            cell_bounds,
            dx=2.0,
            dy=1.2,
            dw=-4.0,
            dh=max(line_count * line_height - float(cell_bounds["height"]) + 2.0, 0.0),
        )
    return inset_bounds(cell_bounds, dx=2.0, dy=0.6, dw=-4.0, dh=0.0)


def build_top_meta_placeholder_nodes(page: dict[str, Any]) -> list[dict[str, Any]]:
    atoms = page.get("atoms") or []
    band_value_cells = sorted(
        [
            atom
            for atom in atoms
            if str(atom.get("owner_id") or "") == "dense_ui_panel:top_meta_band_cells" and is_empty_top_meta_value_cell(atom)
        ],
        key=atom_priority,
    )
    info_value_cells = sorted(
        [
            atom
            for atom in atoms
            if str(atom.get("owner_id") or "") == "dense_ui_panel:top_meta_info_cells" and is_empty_top_meta_value_cell(atom)
        ],
        key=atom_priority,
    )
    zero_bounds_placeholders = sorted(
        [atom for atom in atoms if is_zero_bounds_placeholder_text(atom)],
        key=placeholder_sort_key,
    )
    nodes: list[dict[str, Any]] = []
    occupied_info_cells = {
        str(cell.get("id") or "")
        for cell in info_value_cells
        for atom in atoms
        if str(atom.get("owner_id") or "") == "dense_ui_panel:global_ui_assets"
        and str(atom.get("layer_role") or "") == "small_asset"
        and str(atom.get("text") or "").strip()
        and not is_zero_bounds_placeholder_text(atom)
        and max_overlap_area(atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 0.0, 0.0), cell.get("visual_bounds_px") or make_bounds(0.0, 0.0, 0.0, 0.0)) > 0
    }
    placeholder_targets = band_value_cells + [cell for cell in info_value_cells if str(cell.get("id") or "") not in occupied_info_cells]
    for atom, cell in zip(zero_bounds_placeholders, placeholder_targets):
        nodes.append(build_text_node(atom, anchored_placeholder_bounds(atom, cell["visual_bounds_px"])))
    return nodes


def owner_priority(owner_id: str) -> int:
    order = {
        "dense_ui_panel:top_meta_rows": 10,
        "dense_ui_panel:top_meta_band_cells": 12,
        "dense_ui_panel:top_meta_info_cells": 12,
        "dense_ui_panel:version_stack": 14,
        "dense_ui_panel:issue_card": 16,
        "dense_ui_panel:description_cards": 18,
        "dense_ui_panel:description_lane_rows": 20,
        "dense_ui_panel:description_markers": 22,
        "dense_ui_panel:description_lanes": 24,
        "dense_ui_panel:description_footer": 28,
        "dense_ui_panel:panel_overlay_notes": 29,
        "dense_ui_panel:panel_small_assets": 30,
        "dense_ui_panel:global_ui_assets": 40,
    }
    return order.get(owner_id, 50)


def chunk_priority(chunk_id: str) -> int:
    order = {
        "dense_ui_panel:top_meta_band_chunk": 10,
        "dense_ui_panel:top_meta_info_chunk": 10,
        "dense_ui_panel:top_rows_chunk": 11,
        "dense_ui_panel:description_header_chunk": 12,
        "dense_ui_panel:version_stack_chunk": 12,
        "dense_ui_panel:issue_chunk": 14,
        "dense_ui_panel:description_body_chunk": 18,
        "dense_ui_panel:description_footer_chunk": 28,
        "dense_ui_panel:annotation_overlay_chunk": 29,
        "dense_ui_panel:panel_small_assets_chunk": 30,
        "dense_ui_panel:global_ui_assets_chunk": 31,
    }
    return order.get(chunk_id, 50)


def chunk_bucket(page: dict[str, Any], chunk_id: str) -> dict[str, Any] | None:
    for bucket in page.get("chunk_buckets") or []:
        if str(bucket.get("chunk_id") or "") == chunk_id:
            return bucket
    return None


def use_svg_dense_panel_stack(page: dict[str, Any]) -> bool:
    if str(page.get("page_type") or "") != "dense_ui_panel":
        return False
    description_body_bucket = chunk_bucket(page, "dense_ui_panel:description_body_chunk") or {}
    return (
        str(description_body_bucket.get("render_strategy") or "") == "chunk_container_leaf_text"
        and str(description_body_bucket.get("style_policy") or "") == "preserve_dense_background_overlay_text"
    )


def atom_priority(atom: dict[str, Any]) -> tuple[int, tuple[int, ...], float, float]:
    return (
        int(atom.get("z_index") or 0),
        tuple(int(v) for v in (atom.get("source_order_path") or [])),
        float((atom.get("visual_bounds_px") or {}).get("y") or 0.0),
        float((atom.get("visual_bounds_px") or {}).get("x") or 0.0),
    )


def page_atom_priority(atom: dict[str, Any]) -> tuple[tuple[int, ...], int, float, float]:
    return (
        tuple(int(v) for v in (atom.get("source_order_path") or [])),
        int(atom.get("z_index") or 0),
        float((atom.get("visual_bounds_px") or {}).get("y") or 0.0),
        float((atom.get("visual_bounds_px") or {}).get("x") or 0.0),
    )


def cluster_order_key(atoms_in_cluster: list[dict[str, Any]]) -> tuple[tuple[int, ...], float, float]:
    source_orders = [
        tuple(int(v) for v in (atom.get("source_order_path") or []))
        for atom in atoms_in_cluster
        if atom.get("source_order_path")
    ]
    min_source_order = min(source_orders) if source_orders else ()
    min_y = min(float((atom.get("visual_bounds_px") or {}).get("y") or 0.0) for atom in atoms_in_cluster)
    min_x = min(float((atom.get("visual_bounds_px") or {}).get("x") or 0.0) for atom in atoms_in_cluster)
    return (min_source_order, min_y, min_x)


def bounds_gap(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, float]:
    ax1 = float(a["x"])
    ay1 = float(a["y"])
    ax2 = ax1 + float(a["width"])
    ay2 = ay1 + float(a["height"])
    bx1 = float(b["x"])
    by1 = float(b["y"])
    bx2 = bx1 + float(b["width"])
    by2 = by1 + float(b["height"])
    gap_x = max(0.0, max(ax1 - bx2, bx1 - ax2))
    gap_y = max(0.0, max(ay1 - by2, by1 - ay2))
    return gap_x, gap_y


def global_asset_source_key(atom: dict[str, Any]) -> str:
    source_path = str(((atom.get("debug_tags") or {}).get("source_path")) or "")
    parts = source_path.split("/")
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return source_path or str(atom.get("id") or "")


def page_semantic_source_key(atom: dict[str, Any]) -> str:
    source_path = str(((atom.get("debug_tags") or {}).get("source_path")) or "")
    owner_id = str(atom.get("owner_id") or "")
    parts = source_path.split("/")
    if owner_id.startswith("owner:s29:") and len(parts) >= 3:
        return "/".join(parts[:3])
    if len(parts) >= 2:
        return "/".join(parts[:2])
    return source_path or str(atom.get("id") or "")


def cluster_signature(atoms: list[dict[str, Any]]) -> str:
    texts = [str(atom.get("text") or "").strip() for atom in atoms if str(atom.get("text") or "").strip()]
    joined = " ".join(texts[:3]).lower()
    if any(text in joined for text in ["fold 영역", "pdp", "cmpdpg", "페이지", "화면id"]):
        return "top_meta"
    if any(text in joined for text in ["닷컴", "only", "10%", "?", "< 1 / 5 >", "영상 가로"]):
        return "callout"
    if any(str(atom.get("subtype") or "") == "image" for atom in atoms):
        return "media"
    if any(str(atom.get("subtype") or "") == "connector" for atom in atoms):
        return "connector"
    if any(str(atom.get("shape_kind") or "") == "ellipse" for atom in atoms):
        return "marker"
    return "misc"


def build_global_asset_semantic_groups(
    atoms: list[dict[str, Any]],
    assets: dict[str, Any],
    *,
    use_svg_shape_cells: bool,
    include_dense_body_overlays: bool,
    include_version_last: bool,
    excluded_source_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for atom in atoms:
        source_key = global_asset_source_key(atom)
        if excluded_source_keys and source_key in excluded_source_keys:
            continue
        source_groups[source_key].append(atom)
    source_items: list[tuple[str, list[dict[str, Any]], dict[str, float]]] = []
    for key, source_atoms in source_groups.items():
        source_atoms_sorted = sorted(source_atoms, key=atom_priority)
        bounds = union_bounds([atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0) for atom in source_atoms_sorted])
        source_items.append((key, source_atoms_sorted, bounds))

    parent = list(range(len(source_items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for i, (_, atoms_i, bounds_i) in enumerate(source_items):
        sig_i = cluster_signature(atoms_i)
        for j in range(i + 1, len(source_items)):
            _, atoms_j, bounds_j = source_items[j]
            sig_j = cluster_signature(atoms_j)
            if sig_i != sig_j:
                continue
            gap_x, gap_y = bounds_gap(bounds_i, bounds_j)
            if gap_x <= 56.0 and gap_y <= 44.0:
                union(i, j)

    merged: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, (_, group_atoms, _) in enumerate(source_items):
        merged[find(index)].extend(group_atoms)

    semantic_groups: list[dict[str, Any]] = []
    for cluster_index, cluster_atoms in enumerate(
        sorted(
            merged.values(),
            key=cluster_order_key,
        ),
        start=1,
    ):
        rendered_children: list[dict[str, Any]] = []
        for atom in sorted(cluster_atoms, key=atom_priority):
            rendered_children.extend(
                render_dense_atom_nodes(
                    atom,
                    assets,
                    use_svg_shape_cells=use_svg_shape_cells,
                    include_dense_body_overlays=include_dense_body_overlays,
                    include_version_last=include_version_last,
                )
            )
        if not rendered_children:
            continue
        semantic_groups.append(
            build_owner_group(
                f"dense_ui_panel:global_cluster:{cluster_index:02d}",
                rendered_children,
            )
        )
    return semantic_groups


def is_left_side_page_atom(atom: dict[str, Any]) -> bool:
    owner_id = str(atom.get("owner_id") or "")
    if not owner_id.startswith("owner:"):
        return False
    bounds = atom.get("visual_bounds_px") or {}
    x = float(bounds.get("x") or 0.0)
    width = float(bounds.get("width") or 0.0)
    return x + width < RIGHT_PANEL_X_CUTOFF


def is_left_product_price_global_atom(atom: dict[str, Any]) -> bool:
    if str(atom.get("owner_id") or "") != "dense_ui_panel:global_ui_assets":
        return False
    bounds = atom.get("visual_bounds_px") or {}
    if not bounds:
        return False
    return bbox_intersects(bounds, LEFT_PRODUCT_PRICE_REGION) or bbox_intersects(bounds, LEFT_VIEWER_REGION)


def render_page_atom_nodes(
    atom: dict[str, Any],
    assets: dict[str, Any],
) -> list[dict[str, Any]]:
    role = str(atom.get("layer_role") or "")
    subtype = str(atom.get("subtype") or "")
    nodes: list[dict[str, Any]] = []
    if role in {"text", "flow_label"}:
        nodes.append(build_text_node(atom))
        return nodes
    if subtype == "image":
        image_node = build_image_node(atom, assets)
        if image_node:
            nodes.append(image_node)
        return nodes
    if subtype == "group" or role == "section_block":
        return nodes
    svg_node = build_small_asset_svg_node(atom)
    if svg_node:
        nodes.append(svg_node)
        if atom.get("text"):
            nodes.append(build_text_node(atom, suffix=":label"))
        return nodes
    if atom.get("text"):
        nodes.append(build_text_node(atom))
    else:
        nodes.append(build_rect_node(atom))
    return nodes


def build_page_owner_semantic_groups(page: dict[str, Any], assets: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    source_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for atom in page.get("atoms") or []:
        if is_left_side_page_atom(atom) or is_left_product_price_global_atom(atom):
            source_groups[page_semantic_source_key(atom)].append(atom)

    def root_source_key(atom: dict[str, Any]) -> str:
        source_path = str(((atom.get("debug_tags") or {}).get("source_path")) or "")
        parts = source_path.split("/")
        if len(parts) >= 2:
            return "/".join(parts[:2])
        return source_path or str(atom.get("id") or "")

    def set_placeholder_fill(atom: dict[str, Any], srgb_hex: str) -> None:
        atom["shape_style"] = dict(atom.get("shape_style") or {})
        atom["shape_style"]["fill"] = {
            "type": "srgb",
            "value": srgb_hex,
            "resolved_value": srgb_hex,
            "alpha": 1.0,
            "kind": "solid",
        }
        atom["shape_style"].setdefault(
            "line",
            {
                "type": "srgb",
                "value": "BFBFBF",
                "resolved_value": "BFBFBF",
                "alpha": 1.0,
                "kind": "solid",
                "width_px": 1.0,
            },
        )

    def set_outline_only(atom: dict[str, Any]) -> None:
        atom["shape_style"] = dict(atom.get("shape_style") or {})
        atom["shape_style"]["fill"] = {
            "type": "srgb",
            "value": "FFFFFF",
            "resolved_value": "FFFFFF",
            "alpha": 0.0,
            "kind": "solid",
        }
        atom["shape_style"]["line"] = {
            "type": "srgb",
            "value": "BFBFBF",
            "resolved_value": "BFBFBF",
            "alpha": 1.0,
            "kind": "solid",
            "width_px": 1.0,
        }

    def mostly_contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
        overlap = max_overlap_area(outer, inner)
        inner_area = max(float(inner["width"]) * float(inner["height"]), 1.0)
        return overlap / inner_area >= 0.9

    root_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for atoms in source_groups.values():
        for atom in atoms:
            root_groups[root_source_key(atom)].append(atom)

    viewer_placeholder_keys: set[str] = set()
    viewer_placeholder_bounds: dict[str, dict[str, float]] = {}
    for key, atoms in list(root_groups.items()):
        roles = {str(atom.get("layer_role") or "") for atom in atoms}
        if "group" not in roles:
            continue
        background_cards = [atom for atom in atoms if str(atom.get("layer_role") or "") == "background_card"]
        overlay_lines = [atom for atom in atoms if str(atom.get("layer_role") or "") == "overlay_mark"]
        if len(background_cards) != 1 or len(overlay_lines) < 2:
            continue
        bg_bounds = background_cards[0].get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0)
        matching_lines = [
            atom
            for atom in overlay_lines
            if mostly_same_bounds(bg_bounds, atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0), tolerance=4.0)
        ]
        if len(matching_lines) < 2:
            continue
        viewer_placeholder_keys.add(key)
        viewer_placeholder_bounds[key] = bg_bounds
        # PPT image placeholders (X diagonals over card) should stay transparent/white body.
        # Filling these cards with gray causes broad visual drift on dense pages.
        set_outline_only(background_cards[0])
        ordered_lines = sorted(
            matching_lines,
            key=lambda atom: tuple((atom.get("debug_tags") or {}).get("source_order_path") or []),
        )
        if ordered_lines:
            ordered_lines[0]["render_hint"] = "viewer_diagonal_tl_br"
        if len(ordered_lines) > 1:
            ordered_lines[1]["render_hint"] = "viewer_diagonal_bl_tr"

    for key, atoms in list(root_groups.items()):
        if key in viewer_placeholder_keys:
            continue
        if len(atoms) != 1:
            continue
        atom = atoms[0]
        if str(atom.get("layer_role") or "") != "background_card":
            continue
        atom_bounds = atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0)
        atom_area = float(atom_bounds["width"]) * float(atom_bounds["height"])
        for viewer_bounds in viewer_placeholder_bounds.values():
            viewer_area = float(viewer_bounds["width"]) * float(viewer_bounds["height"])
            if viewer_area <= 0.0:
                continue
            if atom_area < viewer_area * 1.4:
                continue
            if mostly_contains(atom_bounds, viewer_bounds):
                set_outline_only(atom)
                break

    source_items: list[tuple[str, list[dict[str, Any]], dict[str, float]]] = []
    for key, atoms in source_groups.items():
        atoms_sorted = sorted(atoms, key=page_atom_priority)
        bounds = union_bounds([atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0) for atom in atoms_sorted])
        source_items.append((key, atoms_sorted, bounds))

    def is_large_left_container(atoms: list[dict[str, Any]], bounds: dict[str, float]) -> bool:
        roles = {str(atom.get("layer_role") or "") for atom in atoms}
        if not (roles & {"background_card", "section_block"}):
            return False
        return float(bounds["width"]) >= 180.0 and float(bounds["height"]) >= 40.0

    def is_compact_left_control(atoms: list[dict[str, Any]], bounds: dict[str, float]) -> bool:
        roles = {str(atom.get("layer_role") or "") for atom in atoms}
        subtypes = {str(atom.get("subtype") or "") for atom in atoms}
        width = float(bounds["width"])
        height = float(bounds["height"])
        if roles & {"background_card", "section_block", "version_stack"}:
            return False
        if len(atoms) == 1 and "text" in roles:
            return False
        if width > 80.0 or height > 90.0:
            return False
        if "labeled_shape" in roles:
            return True
        if "group" in roles and roles <= {"group", "overlay_mark", "small_asset", "text"}:
            return True
        if "small_asset" in roles and subtypes & {"labeled_shape", "group"}:
            return True
        return False

    def bounds_center(bounds: dict[str, float]) -> tuple[float, float]:
        return (float(bounds["x"]) + float(bounds["width"]) * 0.5, float(bounds["y"]) + float(bounds["height"]) * 0.5)

    def near_viewer_placeholder(bounds: dict[str, float]) -> str | None:
        cx, cy = bounds_center(bounds)
        for key, viewer_bounds in viewer_placeholder_bounds.items():
            vx = float(viewer_bounds["x"]) - 24.0
            vy = float(viewer_bounds["y"]) - 12.0
            vw = float(viewer_bounds["width"]) + 72.0
            vh = float(viewer_bounds["height"]) + 84.0
            if vx <= cx <= vx + vw and vy <= cy <= vy + vh:
                return key
        return None

    semantic_groups: list[dict[str, Any]] = []
    large_container_indices = [
        index
        for index, (_, atoms, bounds) in enumerate(source_items)
        if is_large_left_container(atoms, bounds)
    ]

    merged: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, (key, atoms, bounds) in enumerate(source_items):
        root_key = root_source_key(atoms[0]) if atoms else key
        if root_key in viewer_placeholder_keys:
            merged[f"container:{root_key}"].extend(atoms)
            continue
        if is_compact_left_control(atoms, bounds):
            viewer_key = near_viewer_placeholder(bounds)
            if viewer_key:
                merged[f"container:{viewer_key}"].extend(atoms)
                continue
            merged[f"self:{key}"].extend(atoms)
            continue
        container_candidates: list[tuple[float, int]] = []
        for container_index in large_container_indices:
            if container_index == index:
                continue
            _, _, container_bounds = source_items[container_index]
            if mostly_contains(container_bounds, bounds):
                container_area = float(container_bounds["width"]) * float(container_bounds["height"])
                container_candidates.append((container_area, container_index))

        if container_candidates:
            _, chosen_index = min(container_candidates, key=lambda item: item[0])
            chosen_key, _, _ = source_items[chosen_index]
            merged[f"container:{chosen_key}"].extend(atoms)
        else:
            merged[f"self:{key}"].extend(atoms)

    merged_entries: list[list[dict[str, Any]]] = list(merged.values())

    def entry_bounds(atoms: list[dict[str, Any]]) -> dict[str, float]:
        return union_bounds([atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0) for atom in atoms])

    def entry_roles(atoms: list[dict[str, Any]]) -> set[str]:
        return {str(atom.get("layer_role") or "") for atom in atoms}

    def is_purchase_container_entry(atoms: list[dict[str, Any]]) -> bool:
        texts = " ".join(str(atom.get("text") or "").strip() for atom in atoms if atom.get("text")).lower()
        roles = entry_roles(atoms)
        if "background_card" not in roles:
            return False
        return "구매하기" in texts

    def is_price_overlay_entry(atoms: list[dict[str, Any]]) -> bool:
        texts = " ".join(str(atom.get("text") or "").strip() for atom in atoms if atom.get("text"))
        if not any(token in texts for token in ["회원할인가", "최대할인가", "9,900,000", "9,400,000", "10%"]):
            return False
        roles = entry_roles(atoms)
        return roles <= {"text", "small_asset", "group"}

    absorbed_entry_indices: set[int] = set()
    for index, atoms in enumerate(merged_entries):
        if not is_purchase_container_entry(atoms):
            continue
        container_bounds = entry_bounds(atoms)
        for other_index, other_atoms in enumerate(merged_entries):
            if other_index == index or other_index in absorbed_entry_indices:
                continue
            if not is_price_overlay_entry(other_atoms):
                continue
            other_bounds = entry_bounds(other_atoms)
            if mostly_contains(container_bounds, other_bounds):
                atoms.extend(other_atoms)
                absorbed_entry_indices.add(other_index)

    absorbed_global_source_keys: set[str] = set()

    def text_specificity(node: dict[str, Any]) -> tuple[int, int]:
        debug = node.get("debug") or {}
        source_atom_id = str(debug.get("source_atom_id") or "")
        owner_id = str(debug.get("owner_id") or "")
        return (source_atom_id.count("/"), owner_id.count("/"))

    def similar_text_position(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_box = left.get("absoluteBoundingBox") or {}
        right_box = right.get("absoluteBoundingBox") or {}
        left_y = float(left_box.get("y") or 0.0)
        right_y = float(right_box.get("y") or 0.0)
        left_h = float(left_box.get("height") or 0.0)
        right_h = float(right_box.get("height") or 0.0)
        if abs(left_y - right_y) <= max(left_h, right_h, 12.0):
            return True
        return max_overlap_area(left_box, right_box) > 0.0

    def dedupe_group_text_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for node in nodes:
            if node.get("type") != "TEXT":
                kept.append(node)
                continue
            chars = str(node.get("characters") or "").strip()
            if not chars:
                kept.append(node)
                continue

            duplicate_index: int | None = None
            for index, existing in enumerate(kept):
                if existing.get("type") != "TEXT":
                    continue
                if str(existing.get("characters") or "").strip() != chars:
                    continue
                if similar_text_position(existing, node):
                    duplicate_index = index
                    break

            if duplicate_index is None:
                kept.append(node)
                continue

            existing = kept[duplicate_index]
            if text_specificity(node) > text_specificity(existing):
                kept[duplicate_index] = node
        return kept

    def split_semantic_subcontainers(atoms: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        purchase_atom = next(
            (
                atom
                for atom in atoms
                if "구매하기" in str(atom.get("text") or "")
                and str(atom.get("layer_role") or "") == "background_card"
            ),
            None,
        )
        if not purchase_atom:
            return [atoms]

        source_path = str(((purchase_atom.get("debug_tags") or {}).get("source_path")) or "")
        root_parts = source_path.split("/")
        if len(root_parts) < 2:
            return [atoms]
        purchase_root = "/".join(root_parts[:2])
        purchase_bounds = purchase_atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0)

        top_atoms: list[dict[str, Any]] = []
        clip_background_atoms: list[dict[str, Any]] = []
        summary_atoms: list[dict[str, Any]] = []
        for atom in atoms:
            atom_source = str(((atom.get("debug_tags") or {}).get("source_path")) or "")
            atom_bounds = atom.get("visual_bounds_px") or make_bounds(0.0, 0.0, 1.0, 1.0)
            atom_role = str(atom.get("layer_role") or "")
            if atom_source.startswith(purchase_root):
                top_atoms.append(atom)
                continue
            if atom_role == "background_card":
                clip_background_atoms.append(atom)
                continue
            summary_atoms.append(atom)

        if not top_atoms:
            return [atoms]

        ordered_groups: list[list[dict[str, Any]]] = []
        # Later children render above earlier children. Keep bottom-most layers first.
        if clip_background_atoms:
            ordered_groups.append(clip_background_atoms)
        if summary_atoms:
            ordered_groups.append(summary_atoms)
        ordered_groups.append(top_atoms)
        return [group for group in ordered_groups if group]

    for index, atoms in enumerate(
        sorted(
            [atoms for entry_index, atoms in enumerate(merged_entries) if entry_index not in absorbed_entry_indices],
            key=cluster_order_key,
        ),
        start=1,
    ):
        rendered_children: list[dict[str, Any]] = []
        atom_subgroups = split_semantic_subcontainers(atoms)
        for subgroup_index, subgroup_atoms in enumerate(atom_subgroups, start=1):
            subgroup_children: list[dict[str, Any]] = []
            for atom in sorted(subgroup_atoms, key=page_atom_priority):
                subgroup_children.extend(render_page_atom_nodes(atom, assets))
                if str(atom.get("owner_id") or "") == "dense_ui_panel:global_ui_assets":
                    absorbed_global_source_keys.add(global_asset_source_key(atom))
            subgroup_children = dedupe_group_text_nodes(subgroup_children)
            if len(atom_subgroups) > 1:
                rendered_children.append(
                    build_group_group(
                        f"page_left_cluster:{index:02d}:subgroup:{subgroup_index:02d}",
                        subgroup_children,
                    )
                )
            else:
                rendered_children.extend(subgroup_children)
        if not rendered_children:
            continue
        semantic_groups.append(build_owner_group(f"page_left_cluster:{index:02d}", rendered_children))
    return semantic_groups, absorbed_global_source_keys


def render_dense_atom_nodes(
    atom: dict[str, Any],
    assets: dict[str, Any],
    *,
    use_svg_shape_cells: bool,
    include_dense_body_overlays: bool,
    include_version_last: bool,
) -> list[dict[str, Any]]:
    role = str(atom.get("layer_role") or "")
    subtype = str(atom.get("subtype") or "")
    owner_children: list[dict[str, Any]] = []
    if role == "version_stack":
        block_group, detail_node = build_version_stack_block(atom, use_svg_background=include_version_last)
        owner_children.append(block_group)
        if detail_node:
            owner_children.append(detail_node)
        return owner_children
    if include_dense_body_overlays and role in {"description_card", "issue_card"}:
        owner_children.append(build_overlay_svg_rect_node(atom, suffix=":bg"))
        if role == "issue_card" and atom.get("text"):
            owner_children.append(build_text_node(atom, suffix=":label"))
        return owner_children
    if role in {"top_meta_band_cell", "top_meta_info_cell", "top_meta_row"}:
        return owner_children
    if role in {"description_header_cell", "description_card", "issue_card"}:
        if use_svg_shape_cells:
            owner_children.append(build_overlay_svg_rect_node(atom, suffix=":bg"))
        else:
            owner_children.append(build_rect_node(atom, suffix=":bg"))
        if atom.get("text"):
            owner_children.append(build_text_node(atom, suffix=":label"))
        return owner_children
    if role == "top_text_row":
        owner_children.append(build_paragraph_text_group(atom, atom["visual_bounds_px"]))
        return owner_children
    if role == "overlay_note":
        owner_children.append(build_paragraph_text_group(atom, atom["visual_bounds_px"]))
        return owner_children
    if role in {"description_text_lane", "description_footer", "description_marker"}:
        owner_children.append(build_text_node(atom))
        return owner_children
    if role == "small_asset":
        if subtype == "image":
            image_node = build_image_node(atom, assets)
            if image_node:
                owner_children.append(image_node)
            return owner_children
        if subtype == "group":
            return owner_children
        svg_node = build_small_asset_svg_node(atom)
        if svg_node:
            owner_children.append(svg_node)
            if atom.get("text"):
                owner_children.append(build_text_node(atom, suffix=":label"))
            return owner_children
        if atom.get("text"):
            owner_children.append(build_text_node(atom))
        else:
            owner_children.append(build_rect_node(atom))
        return owner_children
    return owner_children


def build_dense_ui_panel_nodes(
    page: dict[str, Any],
    assets: dict[str, Any],
    *,
    include_dense_body_boxes: bool = False,
    include_dense_body_grid: bool = False,
    include_dense_body_overlays: bool = False,
    include_version_last: bool = False,
) -> list[dict[str, Any]]:
    panel_bounds = dense_panel_bounds(page)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chunk_bucket_map = {bucket["chunk_id"]: bucket for bucket in page.get("chunk_buckets") or []}
    description_body_bucket = chunk_bucket_map.get("dense_ui_panel:description_body_chunk")
    description_body_strategy = str((description_body_bucket or {}).get("render_strategy") or "")
    description_body_style_policy = str((description_body_bucket or {}).get("style_policy") or "")
    preserve_dense_body_background = (
        description_body_strategy == "chunk_container_leaf_text"
        and description_body_style_policy == "preserve_dense_background_overlay_text"
    )
    if include_dense_body_boxes:
        preserve_dense_body_background = False
    if include_dense_body_grid:
        preserve_dense_body_background = True
    use_svg_shape_cells = include_dense_body_grid or include_dense_body_overlays or include_version_last
    for atom in page.get("atoms") or []:
        owner_id = str(atom.get("owner_id") or "")
        if not owner_id.startswith("dense_ui_panel:"):
            continue
        if is_zero_bounds_placeholder_text(atom):
            continue
        grouped[owner_id].append(atom)

    lane_groups: list[dict[str, Any]] = []
    owner_groups: dict[str, dict[str, Any]] = {}
    lane_rows = {row_index_from_atom(atom): atom for atom in grouped.get("dense_ui_panel:description_lane_rows", []) if row_index_from_atom(atom)}
    lane_markers = {row_index_from_atom(atom): atom for atom in grouped.get("dense_ui_panel:description_markers", []) if row_index_from_atom(atom)}
    lane_texts = {row_index_from_atom(atom): atom for atom in grouped.get("dense_ui_panel:description_lanes", []) if row_index_from_atom(atom)}
    footer_atom = next(iter(grouped.get("dense_ui_panel:description_footer", [])), None)
    description_cards = list(grouped.get("dense_ui_panel:description_cards", []))
    issue_atom = next(iter(grouped.get("dense_ui_panel:issue_card", [])), None)
    lane_layout = build_description_lane_layout(
        lane_rows,
        lane_markers,
        lane_texts,
        footer_atom,
        issue_atom.get("visual_bounds_px") if issue_atom else None,
    )
    table_grid_group = build_dense_table_grid_layer(lane_layout, lane_markers, lane_texts, footer_atom) if include_dense_body_grid else None

    for row_index in [3, 4, 5]:
        row_atom = lane_rows.get(row_index)
        text_atom = lane_texts.get(row_index)
        marker_atom = lane_markers.get(row_index)
        if not row_atom or not text_atom:
            continue
        layout = lane_layout.get(row_index) or {}
        lane_bounds = layout.get("lane_bounds") or row_atom["visual_bounds_px"]
        marker_bounds = layout.get("marker_bounds") or (marker_atom["visual_bounds_px"] if marker_atom else None)
        text_bounds = layout.get("text_bounds") or text_atom["visual_bounds_px"]
        background_atom = best_overlapping_card(lane_bounds, description_cards if row_index >= 5 else [])
        lane_children: list[dict[str, Any]] = []
        if not preserve_dense_body_background:
            lane_children.append(build_default_lane_background(lane_bounds, row_index, background_atom))
        if marker_atom and marker_bounds:
            lane_children.append(build_text_node(marker_atom, marker_bounds))
        lane_children.append(build_paragraph_text_group(text_atom, text_bounds))
        lane_groups.append(build_owner_frame(f"dense_ui_panel:lane_row_{row_index}", lane_children, bounds=lane_bounds))
    footer_group: dict[str, Any] | None = None
    if footer_atom:
        layout = lane_layout.get(6) or {}
        footer_bounds = layout.get("lane_bounds") or footer_atom["visual_bounds_px"]
        footer_children: list[dict[str, Any]] = []
        if not preserve_dense_body_background:
            footer_children.append(build_default_lane_background(footer_bounds, 6))
        footer_children.append(build_paragraph_text_group(footer_atom, layout.get("text_bounds") or footer_bounds))
        footer_group = build_owner_frame("dense_ui_panel:description_footer", footer_children, bounds=footer_bounds)

    for owner_id in sorted(grouped.keys(), key=owner_priority):
        if owner_id in {
            "dense_ui_panel:description_lane_rows",
            "dense_ui_panel:description_markers",
            "dense_ui_panel:description_lanes",
            "dense_ui_panel:description_footer",
            "dense_ui_panel:global_ui_assets",
        }:
            continue
        atoms = sorted(grouped[owner_id], key=atom_priority)
        owner_children: list[dict[str, Any]] = []
        for atom in atoms:
            owner_children.extend(
                render_dense_atom_nodes(
                    atom,
                    assets,
                    use_svg_shape_cells=use_svg_shape_cells,
                    include_dense_body_overlays=include_dense_body_overlays,
                    include_version_last=include_version_last,
                )
            )
        if owner_children:
            owner_groups[owner_id] = build_owner_group(owner_id, owner_children)

    chunk_children: list[dict[str, Any]] = []

    if "dense_ui_panel:top_meta_band_chunk" in chunk_bucket_map or "dense_ui_panel:top_meta_info_chunk" in chunk_bucket_map:
        chunk_children.extend(build_top_meta_scaffold_groups(page))

    description_header_children: list[dict[str, Any]] = []
    for owner_id in ["dense_ui_panel:description_header_rows", "dense_ui_panel:description_headers"]:
        if owner_id in owner_groups:
            description_header_children.append(owner_groups[owner_id])
    if description_header_children and "dense_ui_panel:description_header_chunk" in chunk_bucket_map:
        chunk_children.append(build_group_group("dense_ui_panel:description_header_chunk", description_header_children))

    if "dense_ui_panel:version_stack" in owner_groups and "dense_ui_panel:version_stack_chunk" in chunk_bucket_map:
        chunk_children.append(build_group_group("dense_ui_panel:version_stack_chunk", [owner_groups["dense_ui_panel:version_stack"]]))

    if "dense_ui_panel:issue_card" in owner_groups and "dense_ui_panel:issue_chunk" in chunk_bucket_map:
        chunk_children.append(build_group_group("dense_ui_panel:issue_chunk", [owner_groups["dense_ui_panel:issue_card"]]))

    description_body_children: list[dict[str, Any]] = []
    if description_body_bucket:
        if description_body_strategy == "chunk_container_leaf_text":
            if table_grid_group is not None:
                description_body_children.append(table_grid_group)
            if not preserve_dense_body_background:
                for owner_id in ["dense_ui_panel:description_cards"]:
                    if owner_id in owner_groups:
                        description_body_children.append(owner_groups[owner_id])
            description_body_children.extend(lane_groups)
            if include_dense_body_overlays:
                for owner_id in ["dense_ui_panel:description_cards"]:
                    if owner_id in owner_groups:
                        description_body_children.append(owner_groups[owner_id])
            # In leaf-text mode the lane groups already contain row backgrounds,
            # markers, footer text and paragraph leaves. Re-adding semantic/text
            # owner groups here causes duplicate text layers and muddies the
            # baseline-like dense region.
            if description_body_style_policy != "preserve_dense_background_overlay_text":
                for owner_id in ["dense_ui_panel:description_footer", "dense_ui_panel:description_markers", "dense_ui_panel:description_lanes"]:
                    if owner_id in owner_groups:
                        description_body_children.append(owner_groups[owner_id])
        else:
            for owner_id in [
                "dense_ui_panel:description_cards",
                "dense_ui_panel:description_footer",
                "dense_ui_panel:description_markers",
                "dense_ui_panel:description_lanes",
            ]:
                if owner_id in owner_groups:
                    description_body_children.append(owner_groups[owner_id])
            description_body_children.extend(lane_groups)
    if description_body_children and description_body_bucket is not None:
        chunk_children.append(build_group_group("dense_ui_panel:description_body_chunk", description_body_children))

    if footer_group and "dense_ui_panel:description_footer_chunk" in chunk_bucket_map:
        chunk_children.append(build_group_group("dense_ui_panel:description_footer_chunk", [footer_group]))

    annotation_overlay_children: list[dict[str, Any]] = []
    for owner_id in ["dense_ui_panel:panel_overlay_notes"]:
        if owner_id in owner_groups:
            annotation_overlay_children.append(owner_groups[owner_id])
    if annotation_overlay_children and "dense_ui_panel:annotation_overlay_chunk" in chunk_bucket_map:
        chunk_children.append(build_group_group("dense_ui_panel:annotation_overlay_chunk", annotation_overlay_children))

    small_asset_children: list[dict[str, Any]] = []
    for owner_id in ["dense_ui_panel:panel_small_assets"]:
        if owner_id in owner_groups:
            small_asset_children.append(owner_groups[owner_id])
    if small_asset_children and "dense_ui_panel:panel_small_assets_chunk" in chunk_bucket_map:
        chunk_children.append(build_group_group("dense_ui_panel:panel_small_assets_chunk", small_asset_children))

    page_owner_children, absorbed_global_source_keys = build_page_owner_semantic_groups(page, assets)

    global_asset_children: list[dict[str, Any]] = []
    global_atoms = sorted(grouped.get("dense_ui_panel:global_ui_assets", []), key=atom_priority)
    if global_atoms:
        global_asset_children.extend(
            build_global_asset_semantic_groups(
                global_atoms,
                assets,
                use_svg_shape_cells=use_svg_shape_cells,
                include_dense_body_overlays=include_dense_body_overlays,
                include_version_last=include_version_last,
                excluded_source_keys=absorbed_global_source_keys,
            )
        )
    placeholder_nodes = build_top_meta_placeholder_nodes(page)
    if placeholder_nodes:
        global_asset_children.append(build_owner_group("dense_ui_panel:global_ui_assets:anchored_placeholders", placeholder_nodes))
    if global_asset_children and "dense_ui_panel:global_ui_assets_chunk" in chunk_bucket_map:
        chunk_children.append(build_group_group("dense_ui_panel:global_ui_assets_chunk", global_asset_children))

    children = sorted(chunk_children, key=lambda node: chunk_priority(str(node.get("id") or "")))
    if include_dense_body_overlays:
        overlay_order = {
            "dense_ui_panel:description_body_chunk": 18,
            "dense_ui_panel:description_footer_chunk": 19,
            "dense_ui_panel:issue_chunk": 20,
        }
        children = sorted(children, key=lambda node: overlay_order.get(str(node.get("id") or ""), chunk_priority(str(node.get("id") or ""))))
    if include_version_last:
        version_order = {
            "dense_ui_panel:description_body_chunk": 18,
            "dense_ui_panel:description_footer_chunk": 19,
            "dense_ui_panel:issue_chunk": 20,
            "dense_ui_panel:version_stack_chunk": 21,
        }
        children = sorted(children, key=lambda node: version_order.get(str(node.get("id") or ""), chunk_priority(str(node.get("id") or ""))))

    page_level_chunk_ids = {
        "dense_ui_panel:top_meta_band_chunk",
        "dense_ui_panel:top_meta_info_chunk",
        "dense_ui_panel:global_ui_assets_chunk",
    }
    page_level_children = [child for child in children if str(child.get("id") or "") in page_level_chunk_ids]
    panel_children = [child for child in children if str(child.get("id") or "") not in page_level_chunk_ids]

    content_bounds = union_bounds(
        [child.get("absoluteBoundingBox") or make_bounds(0.0, 0.0, 1.0, 1.0) for child in panel_children]
    )
    expanded_panel_bounds = make_bounds(
        min(float(panel_bounds["x"]), float(content_bounds["x"])),
        min(float(panel_bounds["y"]), float(content_bounds["y"])),
        max(float(panel_bounds["x"]) + float(panel_bounds["width"]), float(content_bounds["x"]) + float(content_bounds["width"]))
        - min(float(panel_bounds["x"]), float(content_bounds["x"])),
        max(float(panel_bounds["y"]) + float(panel_bounds["height"]), float(content_bounds["y"]) + float(content_bounds["height"]) + 12.0)
        - min(float(panel_bounds["y"]), float(content_bounds["y"])),
    )
    visible_panel_bounds = make_bounds(
        float(expanded_panel_bounds["x"]),
        float(expanded_panel_bounds["y"]),
        min(float(expanded_panel_bounds["width"]), TARGET_SLIDE_WIDTH - float(expanded_panel_bounds["x"])),
        min(float(expanded_panel_bounds["height"]), TARGET_SLIDE_HEIGHT - float(expanded_panel_bounds["y"])),
    )

    logical_panel = {
        "id": f"{page['page_id']}:dense_ui_panel:logical",
        "type": "FRAME",
        "name": "dense_ui_panel_logical",
        "absoluteBoundingBox": expanded_panel_bounds,
        "relativeTransform": identity_affine(),
        "fills": [],
        "strokes": [],
        "strokeWeight": 0,
        "children": panel_children,
        "debug": {
            "generator": "dense-ui-ir-v1",
            "page_id": page["page_id"],
            "page_type": page["page_type"],
            "logical_panel": True,
        },
    }

    panel_frame = {
        "id": f"{page['page_id']}:dense_ui_panel",
        "type": "FRAME",
        "name": "dense_ui_panel",
        "absoluteBoundingBox": visible_panel_bounds,
        "relativeTransform": identity_affine(),
        "fills": [],
        "strokes": [],
        "strokeWeight": 1,
        "clipsContent": True,
        "children": [logical_panel],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "page_id": page["page_id"],
            "page_type": page["page_type"],
        },
    }
    return page_level_children + page_owner_children + [panel_frame]


def build_bundle(
    page: dict[str, Any],
    source_file: str,
    *,
    include_dense_body_boxes: bool = False,
    include_dense_body_grid: bool = False,
    include_dense_body_overlays: bool = False,
    include_version_last: bool = False,
) -> dict[str, Any]:
    configure_page_geometry(page)
    inferred_svg_stack = use_svg_dense_panel_stack(page)
    include_dense_body_grid = include_dense_body_grid or inferred_svg_stack
    include_dense_body_overlays = include_dense_body_overlays or inferred_svg_stack
    include_version_last = include_version_last or inferred_svg_stack
    assets: dict[str, Any] = {}
    page_children = build_dense_ui_panel_nodes(
        page,
        assets,
        include_dense_body_boxes=include_dense_body_boxes,
        include_dense_body_grid=include_dense_body_grid,
        include_dense_body_overlays=include_dense_body_overlays,
        include_version_last=include_version_last,
    )
    root_bounds = make_bounds(0.0, 0.0, TARGET_SLIDE_WIDTH, TARGET_SLIDE_HEIGHT)
    inner_frame = {
        "id": f"{page['page_id']}:frame",
        "type": "FRAME",
        "name": "Frame",
        "absoluteBoundingBox": root_bounds,
        "relativeTransform": identity_affine(),
        "fills": [],
        "strokes": [],
        "strokeWeight": 0,
        "children": page_children,
        "debug": {
            "generator": "dense-ui-ir-v1",
            "page_id": page["page_id"],
            "page_type": page["page_type"],
        },
    }
    root = {
        "id": page["page_id"],
        "type": "FRAME",
        "name": f"Slide {page['slide_no']} - Dense UI Panel",
        "absoluteBoundingBox": root_bounds,
        "relativeTransform": identity_affine(),
        "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1}, "opacity": 1.0}],
        "strokes": [],
        "strokeWeight": 0,
        "children": [inner_frame],
        "debug": {
            "generator": "dense-ui-ir-v1",
            "page_id": page["page_id"],
            "page_type": page["page_type"],
        },
    }
    return {
        "kind": "figma-replay-bundle",
        "source_kind": "resolved-ppt-ir",
        "visual_model_version": "dense-ui-ir-v1",
        "source_file": source_file,
        "file_name": Path(source_file).name,
        "page_name": root["name"],
        "node_id": root["id"],
        "document": root,
        "assets": assets,
        "missing_assets": [],
        "debug": {
            "status": "dense_ui_ir_bundle",
            "page_type": page["page_type"],
            "owner_count": len(page.get("owner_buckets") or []),
            "atom_count": len(page.get("atoms") or []),
        },
    }


LOWER_BODY_TEXT_CHUNK_IDS = {
    "dense_ui_panel:description_body_chunk",
    "dense_ui_panel:description_footer_chunk",
}

LOWER_BODY_OVERLAY_CHUNK_IDS = {
    "dense_ui_panel:description_body_chunk",
    "dense_ui_panel:description_footer_chunk",
    "dense_ui_panel:issue_chunk",
}

LOWER_BODY_OVERLAY_VERSION_CHUNK_IDS = {
    "dense_ui_panel:description_body_chunk",
    "dense_ui_panel:description_footer_chunk",
    "dense_ui_panel:issue_chunk",
    "dense_ui_panel:version_stack_chunk",
}

LOWER_BODY_TEXT_OWNER_IDS = {
    "dense_ui_panel:description_lanes",
    "dense_ui_panel:description_footer",
}

LOWER_BODY_OVERLAY_OWNER_IDS = {
    "dense_ui_panel:description_lanes",
    "dense_ui_panel:description_footer",
    "dense_ui_panel:issue_card",
}

LOWER_BODY_OVERLAY_VERSION_OWNER_IDS = {
    "dense_ui_panel:description_lanes",
    "dense_ui_panel:description_footer",
    "dense_ui_panel:issue_card",
    "dense_ui_panel:version_stack",
}

def prune_lower_body_text_layer(node: dict[str, Any]) -> dict[str, Any] | None:
    node_type = str(node.get("type") or "")
    node_id = str(node.get("id") or "")
    debug = node.get("debug") or {}
    owner_id = str(debug.get("owner_id") or "")

    if node_type == "TEXT":
        if owner_id in LOWER_BODY_TEXT_OWNER_IDS:
            pruned = dict(node)
            pruned["children"] = []
            return pruned
        return None

    pruned_children: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        pruned_child = prune_lower_body_text_layer(child)
        if pruned_child is not None:
            pruned_children.append(pruned_child)

    if node_id in LOWER_BODY_TEXT_CHUNK_IDS:
        if not pruned_children:
            return None
        pruned = dict(node)
        pruned["children"] = pruned_children
        return pruned

    if pruned_children:
        pruned = dict(node)
        pruned["children"] = pruned_children
        return pruned

    return None


def extract_lower_body_text_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    document = bundle.get("document") or {}
    pruned_document = prune_lower_body_text_layer(document)
    if pruned_document is None:
        raise SystemExit("lower body text layer extraction produced an empty bundle")

    extracted = dict(bundle)
    extracted["page_name"] = f"{bundle.get('page_name')} - Lower Body Text Layer"
    extracted["node_id"] = pruned_document.get("id")
    extracted["document"] = pruned_document
    extracted["debug"] = dict(bundle.get("debug") or {}, export_mode="lower_body_text_only")
    return extracted


def prune_lower_body_text_box_layer(
    node: dict[str, Any],
    in_lower_body_chunk: bool = False,
    *,
    allowed_chunk_ids: set[str] | None = None,
    allowed_text_owner_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    node_type = str(node.get("type") or "")
    node_id = str(node.get("id") or "")
    debug = node.get("debug") or {}
    owner_id = str(debug.get("owner_id") or "")
    chunk_ids = allowed_chunk_ids or LOWER_BODY_TEXT_CHUNK_IDS
    text_owner_ids = allowed_text_owner_ids or LOWER_BODY_TEXT_OWNER_IDS
    child_in_lower_body_chunk = in_lower_body_chunk or node_id in chunk_ids

    if node_type == "TEXT":
        if owner_id in text_owner_ids:
            pruned = dict(node)
            pruned["children"] = []
            return pruned
        return None

    if node_type in {"RECTANGLE", "SVG_BLOCK"} and child_in_lower_body_chunk:
        pruned = dict(node)
        pruned["children"] = []
        return pruned

    pruned_children: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        pruned_child = prune_lower_body_text_box_layer(
            child,
            child_in_lower_body_chunk,
            allowed_chunk_ids=chunk_ids,
            allowed_text_owner_ids=text_owner_ids,
        )
        if pruned_child is not None:
            pruned_children.append(pruned_child)

    if node_id in chunk_ids:
        if not pruned_children:
            return None
        pruned = dict(node)
        pruned["children"] = pruned_children
        return pruned

    if pruned_children:
        pruned = dict(node)
        pruned["children"] = pruned_children
        return pruned

    return None


def extract_lower_body_text_box_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    document = bundle.get("document") or {}
    pruned_document = prune_lower_body_text_box_layer(document)
    if pruned_document is None:
        raise SystemExit("lower body text/box extraction produced an empty bundle")

    extracted = dict(bundle)
    extracted["page_name"] = f"{bundle.get('page_name')} - Lower Body Text And Boxes"
    extracted["node_id"] = pruned_document.get("id")
    extracted["document"] = pruned_document
    extracted["debug"] = dict(bundle.get("debug") or {}, export_mode="lower_body_text_and_boxes")
    return extracted


def extract_lower_body_text_grid_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    document = bundle.get("document") or {}
    pruned_document = prune_lower_body_text_box_layer(document)
    if pruned_document is None:
        raise SystemExit("lower body text/grid extraction produced an empty bundle")

    extracted = dict(bundle)
    extracted["page_name"] = f"{bundle.get('page_name')} - Lower Body Text And Grid"
    extracted["node_id"] = pruned_document.get("id")
    extracted["document"] = pruned_document
    extracted["debug"] = dict(bundle.get("debug") or {}, export_mode="lower_body_text_and_grid")
    return extracted


def extract_lower_body_text_grid_overlay_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    document = bundle.get("document") or {}
    pruned_document = prune_lower_body_text_box_layer(
        document,
        allowed_chunk_ids=LOWER_BODY_OVERLAY_CHUNK_IDS,
        allowed_text_owner_ids=LOWER_BODY_OVERLAY_OWNER_IDS,
    )
    if pruned_document is None:
        raise SystemExit("lower body text/grid/overlay extraction produced an empty bundle")

    extracted = dict(bundle)
    extracted["page_name"] = f"{bundle.get('page_name')} - Lower Body Text Grid And Overlays"
    extracted["node_id"] = pruned_document.get("id")
    extracted["document"] = pruned_document
    extracted["debug"] = dict(bundle.get("debug") or {}, export_mode="lower_body_text_grid_and_overlays")
    return extracted


def extract_lower_body_text_grid_overlay_version_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    document = bundle.get("document") or {}
    pruned_document = prune_lower_body_text_box_layer(
        document,
        allowed_chunk_ids=LOWER_BODY_OVERLAY_VERSION_CHUNK_IDS,
        allowed_text_owner_ids=LOWER_BODY_OVERLAY_VERSION_OWNER_IDS,
    )
    if pruned_document is None:
        raise SystemExit("lower body text/grid/overlay/version extraction produced an empty bundle")

    extracted = dict(bundle)
    extracted["page_name"] = f"{bundle.get('page_name')} - Lower Body Text Grid Overlays And Versions"
    extracted["node_id"] = pruned_document.get("id")
    extracted["document"] = pruned_document
    extracted["debug"] = dict(bundle.get("debug") or {}, export_mode="lower_body_text_grid_overlays_and_versions")
    return extracted


def bbox_intersects(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ax1 = float(a.get("x") or 0.0)
    ay1 = float(a.get("y") or 0.0)
    ax2 = ax1 + float(a.get("width") or 0.0)
    ay2 = ay1 + float(a.get("height") or 0.0)
    bx1 = float(b.get("x") or 0.0)
    by1 = float(b.get("y") or 0.0)
    bx2 = bx1 + float(b.get("width") or 0.0)
    by2 = by1 + float(b.get("height") or 0.0)
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def prune_to_region(node: dict[str, Any], region: dict[str, Any]) -> dict[str, Any] | None:
    bbox = node.get("absoluteBoundingBox") or {}
    node_type = str(node.get("type") or "")

    pruned_children: list[dict[str, Any]] = []
    for child in node.get("children") or []:
        pruned_child = prune_to_region(child, region)
        if pruned_child is not None:
            pruned_children.append(pruned_child)

    if pruned_children:
        pruned = dict(node)
        pruned["children"] = pruned_children
        if node_type in {"GROUP", "FRAME"}:
            pruned["absoluteBoundingBox"] = union_bounds(
                [child.get("absoluteBoundingBox") or make_bounds(0.0, 0.0, 1.0, 1.0) for child in pruned_children]
            )
        return pruned

    if bbox and bbox_intersects(bbox, region):
        pruned = dict(node)
        pruned["children"] = []
        return pruned

    return None


def extract_left_product_price_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    document = bundle.get("document") or {}
    pruned_document = prune_to_region(document, LEFT_PRODUCT_PRICE_REGION)
    if pruned_document is None:
        raise SystemExit("left product/price extraction produced an empty bundle")

    extracted = dict(bundle)
    extracted["page_name"] = f"{bundle.get('page_name')} - Left Product Price"
    extracted["node_id"] = pruned_document.get("id")
    extracted["document"] = pruned_document
    extracted["debug"] = dict(bundle.get("debug") or {}, export_mode="left_product_price_only")
    return extracted


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a dense-ui-panel replay bundle from resolved PPT IR.")
    parser.add_argument("--input", required=True, help="Resolved IR JSON path")
    parser.add_argument("--output", required=True, help="Output bundle path")
    parser.add_argument("--slide", type=int, default=29, help="Slide number to render")
    parser.add_argument(
        "--export-mode",
        choices=["full", "lower_body_text_only", "lower_body_text_and_boxes", "lower_body_text_and_grid", "lower_body_text_grid_and_overlays", "lower_body_text_grid_overlays_and_versions", "left_product_price_only"],
        default="full",
        help="Optional post-processing mode for the generated bundle",
    )
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    pages = data.get("pages") or []
    page = next((page for page in pages if int(page.get("slide_no") or 0) == args.slide), None)
    if not page:
        raise SystemExit(f"slide {args.slide} not found in {input_path}")
    if str(page.get("page_type") or "") != "dense_ui_panel":
        raise SystemExit(f"slide {args.slide} is not dense_ui_panel (got {page.get('page_type')})")

    bundle = build_bundle(
        page,
        str(input_path),
        include_dense_body_boxes=args.export_mode == "lower_body_text_and_boxes",
        include_dense_body_grid=args.export_mode == "lower_body_text_and_grid",
        include_dense_body_overlays=args.export_mode == "lower_body_text_grid_and_overlays",
    )
    if args.export_mode == "lower_body_text_grid_and_overlays":
        bundle = build_bundle(
            page,
            str(input_path),
            include_dense_body_grid=True,
            include_dense_body_overlays=True,
        )
    elif args.export_mode == "lower_body_text_grid_overlays_and_versions":
        bundle = build_bundle(
            page,
            str(input_path),
            include_dense_body_grid=True,
            include_dense_body_overlays=True,
            include_version_last=True,
        )
    if args.export_mode == "lower_body_text_only":
        bundle = extract_lower_body_text_bundle(bundle)
    elif args.export_mode == "lower_body_text_and_boxes":
        bundle = extract_lower_body_text_box_bundle(bundle)
    elif args.export_mode == "lower_body_text_and_grid":
        bundle = extract_lower_body_text_grid_bundle(bundle)
    elif args.export_mode == "lower_body_text_grid_and_overlays":
        bundle = extract_lower_body_text_grid_overlay_bundle(bundle)
    elif args.export_mode == "lower_body_text_grid_overlays_and_versions":
        bundle = extract_lower_body_text_grid_overlay_version_bundle(bundle)
    elif args.export_mode == "left_product_price_only":
        bundle = extract_left_product_price_bundle(bundle)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
