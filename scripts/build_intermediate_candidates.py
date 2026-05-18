#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


EMU_PER_PIXEL = 9525
SOURCE_ORDER_RE = re.compile(r"(?:element|child|row|cell)_(\d+)")


def emu_bounds_to_px(bounds: dict[str, int] | None) -> dict[str, float] | None:
    if not bounds:
        return None
    payload = {
        "x": round(bounds.get("x", 0) / EMU_PER_PIXEL, 2),
        "y": round(bounds.get("y", 0) / EMU_PER_PIXEL, 2),
        "width": round(bounds.get("cx", 0) / EMU_PER_PIXEL, 2),
        "height": round(bounds.get("cy", 0) / EMU_PER_PIXEL, 2),
    }
    if bounds.get("rot"):
        payload["rotation"] = round(bounds.get("rot", 0) / 60000, 2)
    if bounds.get("flipH"):
        payload["flipH"] = True
    if bounds.get("flipV"):
        payload["flipV"] = True
    return payload


def build_element_index(elements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}

    def walk(items: list[dict[str, Any]]) -> None:
        for element in items:
            node_id = element.get("node_id")
            if node_id:
                index[str(node_id)] = element
            children = element.get("children") or []
            if children:
                walk(children)

    walk(elements)
    return index


def parse_source_order_path(source_path: str) -> list[int]:
    return [int(match.group(1)) for match in SOURCE_ORDER_RE.finditer(source_path or "")]


def connection_point_px(bounds: dict[str, int] | None, idx: int | None) -> dict[str, float] | None:
    px = emu_bounds_to_px(bounds)
    if not px or idx is None:
        return None
    x = px["x"]
    y = px["y"]
    width = px["width"]
    height = px["height"]
    center_x = x + width / 2
    center_y = y + height / 2
    mapping = {
        0: {"x": center_x, "y": y},
        1: {"x": x, "y": center_y},
        2: {"x": center_x, "y": y + height},
        3: {"x": x + width, "y": center_y},
        4: {"x": x, "y": y},
        5: {"x": x + width, "y": y},
        6: {"x": x, "y": y + height},
        7: {"x": x + width, "y": y + height},
    }
    return mapping.get(idx, {"x": center_x, "y": center_y})


def infer_connector_endpoints(element: dict[str, Any], element_index: dict[str, dict[str, Any]]) -> tuple[dict[str, float] | None, dict[str, float] | None]:
    connector_bounds = emu_bounds_to_px(element.get("bounds"))
    if not connector_bounds:
        return None, None

    horizontal = connector_bounds["width"] >= connector_bounds["height"]
    candidates: list[dict[str, float]] = []
    connector_center_x = connector_bounds["x"] + connector_bounds["width"] / 2
    connector_center_y = connector_bounds["y"] + connector_bounds["height"] / 2

    for other in element_index.values():
        if other is element or other.get("element_type") == "connector":
            continue
        if (other.get("text") or "").strip() and not has_visible_fill(other.get("shape_style")) and not has_visible_line(other.get("shape_style")):
            continue
        other_bounds = emu_bounds_to_px(other.get("bounds"))
        if not other_bounds:
            continue
        other_center_x = other_bounds["x"] + other_bounds["width"] / 2
        other_center_y = other_bounds["y"] + other_bounds["height"] / 2
        if horizontal:
            score = abs(other_center_y - connector_center_y) + abs(other_center_x - connector_center_x)
        else:
            score = abs(other_center_x - connector_center_x) + abs(other_center_y - connector_center_y)
        candidates.append({"score": score, **other_bounds})

    candidates.sort(key=lambda item: item["score"])
    if len(candidates) < 2:
        return None, None

    first, second = candidates[0], candidates[1]
    if horizontal:
        left, right = sorted([first, second], key=lambda item: item["x"])
        return (
            {"x": left["x"] + left["width"], "y": left["y"] + left["height"] / 2},
            {"x": right["x"], "y": right["y"] + right["height"] / 2},
        )

    top, bottom = sorted([first, second], key=lambda item: item["y"])
    return (
        {"x": top["x"] + top["width"] / 2, "y": top["y"] + top["height"]},
        {"x": bottom["x"] + bottom["width"] / 2, "y": bottom["y"]},
    )


def classify_group(element: dict[str, Any]) -> str:
    bounds = element.get("bounds") or {}
    child_count = len(element.get("children", []) or [])
    area = bounds.get("cx", 0) * bounds.get("cy", 0)
    if child_count >= 4 or area >= 10_000_000_000_000:
        return "section_block"
    return "group"


def has_visible_fill(shape_style: dict[str, Any] | None) -> bool:
    if not shape_style:
        return False
    fill = shape_style.get("fill") or {}
    if not fill or fill.get("kind") == "none":
        return False
    alpha = fill.get("alpha")
    return alpha is None or alpha > 0


def has_visible_line(shape_style: dict[str, Any] | None) -> bool:
    if not shape_style:
        return False
    line = shape_style.get("line") or {}
    if not line or line.get("kind") in {"none", "default"}:
        return False
    alpha = line.get("alpha")
    width_px = line.get("width_px")
    if alpha is not None and alpha <= 0:
        return False
    if width_px is not None and width_px <= 0:
        return False
    return True


def classify_shape(element: dict[str, Any]) -> tuple[str, str]:
    text = (element.get("text") or "").strip()
    kind = element.get("shape_kind") or "shape"
    shape_style = element.get("shape_style") or {}
    if element.get("element_type") == "connector":
        return "connector", kind
    if text and kind in {"rect", "roundRect", "ellipse"}:
        if not has_visible_fill(shape_style) and not has_visible_line(shape_style):
            return "text_block", kind
        return "labeled_shape", kind
    if text:
        return "text_block", kind
    return "shape", kind


COMPLEX_VECTOR_SHAPES = {
    "flowChartDecision",
    "flowChartProcess",
    "flowChartDocument",
    "chevron",
    "trapezoid",
    "hexagon",
    "parallelogram",
    "wedgeRoundRectCallout",
    "wedgeRectCallout",
    "leftArrow",
    "rightArrow",
    "upArrow",
    "downArrow",
}


REPLACEMENT_TYPE_BY_SHAPE = {
    "flowChartDecision": "decision_diamond",
    "flowChartProcess": "process_box",
    "flowChartDocument": "document_box",
    "chevron": "chevron_shape",
    "trapezoid": "trapezoid_shape",
    "hexagon": "hexagon_shape",
    "parallelogram": "parallelogram_shape",
    "wedgeRoundRectCallout": "callout_box",
    "wedgeRectCallout": "callout_box",
    "leftArrow": "directional_arrow_shape",
    "rightArrow": "directional_arrow_shape",
    "upArrow": "directional_arrow_shape",
    "downArrow": "directional_arrow_shape",
}


def infer_rendering_metadata(
    *,
    node_type: str,
    subtype: str,
    shape_kind: str,
    text: str,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    rendering = {
        "current_mode": "native",
        "preferred_mode": "native",
        "replacement_candidate": False,
    }
    replacement: dict[str, Any] | None = None

    if subtype == "connector":
        rendering["preferred_mode"] = "vector_fallback"
        rendering["replacement_candidate"] = True
        replacement = {
            "candidate_type": "process_flow_connector",
            "strategy": "vector_then_component_replace",
            "confidence": "high",
            "reason": "connector_fidelity_and_directionality",
        }
    elif subtype in {"section_block", "group"}:
        child_count = (extra or {}).get("child_count", 0)
        if child_count >= 4:
            rendering["replacement_candidate"] = True
            replacement = {
                "candidate_type": "group_container",
                "strategy": "native_then_layout_component_replace",
                "confidence": "medium",
                "reason": "repeated_layout_container",
            }
    elif shape_kind in COMPLEX_VECTOR_SHAPES:
        rendering["preferred_mode"] = "vector_fallback"
        rendering["replacement_candidate"] = True
        replacement = {
            "candidate_type": REPLACEMENT_TYPE_BY_SHAPE.get(shape_kind, "complex_shape"),
            "strategy": "vector_then_component_replace",
            "confidence": "high",
            "reason": "complex_shape_fidelity",
        }
    elif subtype == "labeled_shape" and text:
        rendering["replacement_candidate"] = True
        replacement = {
            "candidate_type": "labeled_ui_box",
            "strategy": "native_then_component_replace",
            "confidence": "medium",
            "reason": "repeated_labeled_box_pattern",
        }
    elif node_type == "asset" and subtype == "image":
        rendering["replacement_candidate"] = True
        replacement = {
            "candidate_type": "image_asset",
            "strategy": "native_asset_replace",
            "confidence": "low",
            "reason": "asset_swap_or_design_asset_upgrade",
        }

    if replacement:
        rendering["replacement"] = replacement

    return rendering


def make_candidate(
    *,
    candidate_id: str,
    parent_candidate_id: str | None,
    slide_no: int,
    node_type: str,
    subtype: str,
    title: str,
    text: str,
    source_path: str,
    source_node_id: str | None,
    bounds_emu: dict[str, int] | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_order_path = parse_source_order_path(source_path)
    raw_source_z_order = (extra or {}).get("source_z_order")
    source_z_order = int(raw_source_z_order) if isinstance(raw_source_z_order, (int, float)) else None
    if source_z_order is None and source_order_path:
        # Backward compatibility for older detail JSON files generated before
        # explicit z_order extraction. element_N is one-based; z-order is zero-based.
        source_z_order = max(int(source_order_path[0]) - 1, 0)
    payload = {
        "candidate_id": candidate_id,
        "parent_candidate_id": parent_candidate_id,
        "slide_no": slide_no,
        "node_type": node_type,
        "subtype": subtype,
        "title": title,
        "text": text,
        "source_path": source_path,
        "source_order_path": source_order_path,
        "source_z_order": source_z_order,
        "source_node_id": source_node_id,
        "bounds_emu": bounds_emu,
        "bounds_px": emu_bounds_to_px(bounds_emu),
    }
    if extra:
        payload["extra"] = extra
    rendering = infer_rendering_metadata(
        node_type=node_type,
        subtype=subtype,
        shape_kind=(extra or {}).get("shape_kind", subtype),
        text=text,
        extra=extra,
    )
    payload["rendering"] = rendering
    return payload


def append_element_candidates(
    *,
    slide_no: int,
    element: dict[str, Any],
    source_path: str,
    parent_candidate_id: str | None,
    candidates: list[dict[str, Any]],
    element_index: dict[str, dict[str, Any]],
) -> None:
    element_type = element.get("element_type")
    candidate_id = f"s{slide_no}:{source_path}"
    title = element.get("name") or element.get("text") or element_type or "element"
    text = (element.get("text") or "").strip()
    source_scope = element.get("source_scope") or "slide"
    placeholder = element.get("placeholder")
    source_z_order = element.get("z_order")

    def source_extra(values: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(values or {})
        payload.setdefault("source_scope", source_scope)
        payload.setdefault("placeholder", placeholder)
        if isinstance(source_z_order, (int, float)):
            payload.setdefault("source_z_order", int(source_z_order))
        return payload

    if element_type == "group":
        subtype = classify_group(element)
        candidates.append(
            make_candidate(
                candidate_id=candidate_id,
                parent_candidate_id=parent_candidate_id,
                slide_no=slide_no,
                node_type="node",
                subtype=subtype,
                title=title,
                text=text,
                source_path=source_path,
                source_node_id=element.get("node_id"),
                bounds_emu=element.get("bounds"),
                extra=source_extra({
                    "child_count": len(element.get("children", []) or []),
                    "shape_style": element.get("shape_style"),
                    "transform": emu_bounds_to_px(element.get("bounds")),
                }),
            )
        )
        for index, child in enumerate(element.get("children", []) or [], start=1):
            append_element_candidates(
                slide_no=slide_no,
                element=child,
                source_path=f"{source_path}/child_{index}",
                parent_candidate_id=candidate_id,
                candidates=candidates,
                element_index=element_index,
            )
        return

    if element_type == "graphic_frame" and element.get("table"):
        table = element["table"]
        candidates.append(
            make_candidate(
                candidate_id=candidate_id,
                parent_candidate_id=parent_candidate_id,
                slide_no=slide_no,
                node_type="node",
                subtype="table",
                title=title,
                text="",
                source_path=source_path,
                source_node_id=element.get("node_id"),
                bounds_emu=element.get("bounds"),
                extra=source_extra({
                    "row_count": table.get("row_count", 0),
                    "column_count": len(table.get("grid_columns", [])),
                    "grid_columns": table.get("grid_columns", []),
                }),
            )
        )
        for row in table.get("rows", []):
            row_id = f"{candidate_id}:row_{row['row_index']}"
            candidates.append(
                make_candidate(
                    candidate_id=row_id,
                    parent_candidate_id=candidate_id,
                    slide_no=slide_no,
                    node_type="node",
                    subtype="table_row",
                    title=f"row {row['row_index']}",
                    text="",
                    source_path=f"{source_path}/row_{row['row_index']}",
                    source_node_id=element.get("node_id"),
                    bounds_emu=None,
                    extra=source_extra({
                        "height": row.get("height"),
                        "row_height_px": row.get("height_px"),
                        "cell_count": len(row.get("cells", [])),
                    }),
                )
            )
            for cell in row.get("cells", []):
                if cell.get("h_merge") or cell.get("v_merge"):
                    continue
                candidates.append(
                    make_candidate(
                        candidate_id=f"{row_id}:cell_{cell['cell_index']}",
                        parent_candidate_id=row_id,
                        slide_no=slide_no,
                        node_type="node",
                        subtype="table_cell",
                        title=f"cell {row['row_index']}-{cell['cell_index']}",
                        text=cell.get("text", ""),
                        source_path=f"{source_path}/row_{row['row_index']}/cell_{cell['cell_index']}",
                        source_node_id=element.get("node_id"),
                        bounds_emu=None,
                        extra=source_extra({
                            "row_height_emu": row.get("height"),
                            "row_height_px": round(int(row["height"]) / EMU_PER_PIXEL, 2) if row.get("height") else None,
                            "grid_span": cell.get("grid_span"),
                            "row_span": cell.get("row_span"),
                            "h_merge": cell.get("h_merge"),
                            "v_merge": cell.get("v_merge"),
                            "start_column_index": cell.get("start_column_index"),
                            "width_px": cell.get("width_px"),
                            "cell_style": cell.get("style"),
                            "text_style": cell.get("text_style"),
                            "text_runs": cell.get("text_runs"),
                            "text_alignment": cell.get("text_alignment"),
                        }),
                    )
                )
        return

    if element_type == "image":
        candidates.append(
            make_candidate(
                candidate_id=candidate_id,
                parent_candidate_id=parent_candidate_id,
                slide_no=slide_no,
                node_type="asset",
                subtype="image",
                title=title,
                text="",
                source_path=source_path,
                source_node_id=element.get("node_id"),
                bounds_emu=element.get("bounds"),
                extra=source_extra({
                    "image_target": element.get("image_target"),
                    "resolved_target": element.get("resolved_target"),
                    "mime_type": element.get("mime_type"),
                    "image_base64": element.get("image_base64"),
                }),
            )
        )
        return

    node_subtype, shape_subtype = classify_shape(element)
    connector_extra: dict[str, Any] = {}
    if node_subtype == "connector":
        start_connection = element.get("start_connection")
        end_connection = element.get("end_connection")
        if start_connection:
            start_target = element_index.get(str(start_connection.get("id")))
            connector_extra["start_connection"] = start_connection
            connector_extra["start_target_bounds_px"] = emu_bounds_to_px(start_target.get("bounds")) if start_target else None
            connector_extra["start_point_px"] = connection_point_px(start_target.get("bounds") if start_target else None, start_connection.get("idx"))
        if end_connection:
            end_target = element_index.get(str(end_connection.get("id")))
            connector_extra["end_connection"] = end_connection
            connector_extra["end_target_bounds_px"] = emu_bounds_to_px(end_target.get("bounds")) if end_target else None
            connector_extra["end_point_px"] = connection_point_px(end_target.get("bounds") if end_target else None, end_connection.get("idx"))
        # Keep connector routing deterministic:
        # use explicit PPT connection facts first, then renderer fallback geometry.
        if element.get("connector_adjusts"):
            connector_extra["connector_adjusts"] = element.get("connector_adjusts")

    candidates.append(
        make_candidate(
            candidate_id=candidate_id,
            parent_candidate_id=parent_candidate_id,
            slide_no=slide_no,
            node_type="node",
            subtype=node_subtype,
            title=title,
            text=text,
            source_path=source_path,
            source_node_id=element.get("node_id"),
            bounds_emu=element.get("bounds"),
            extra=source_extra({
                "shape_kind": shape_subtype,
                "shape_style": element.get("shape_style"),
                "text_style": element.get("text_style"),
                "text_runs": element.get("text_runs"),
                "text_alignment": element.get("text_alignment"),
                **connector_extra,
            }),
        )
    )


def build_intermediate_model(detail_payload: dict[str, Any]) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []

    for slide in detail_payload["slides"]:
        slide_no = slide["slide_no"]
        page_id = f"page:{slide_no}"
        candidates: list[dict[str, Any]] = []
        source_elements = [
            *(slide.get("master_elements") or []),
            *(slide.get("layout_elements") or []),
            *(slide.get("elements") or []),
        ]
        element_index = build_element_index(source_elements)

        scope_counts = {"master": 0, "layout": 0, "slide": 0}
        for element in source_elements:
            scope = element.get("source_scope") or "slide"
            scope_counts.setdefault(scope, 0)
            scope_counts[scope] += 1
            if scope == "master":
                source_path = f"master_{slide_no}/element_{scope_counts[scope]}"
            elif scope == "layout":
                source_path = f"layout_{slide_no}/element_{scope_counts[scope]}"
            else:
                source_path = f"slide_{slide_no}/element_{scope_counts[scope]}"
            append_element_candidates(
                slide_no=slide_no,
                element=element,
                source_path=source_path,
                parent_candidate_id=page_id,
                candidates=candidates,
                element_index=element_index,
            )

        pages.append(
            {
                "page_id": page_id,
                "slide_no": slide_no,
                "title_or_label": slide["title_or_label"],
                "source_path": slide["slide_path"],
                "slide_size": slide.get("slide_size"),
                "theme_colors": slide.get("theme_colors"),
                "summary": slide["summary"],
                "candidates": candidates,
            }
        )

    return {
        "pptxPath": detail_payload["pptxPath"],
        "requestedSlides": detail_payload["requestedSlides"],
        "pages": pages,
    }


def summarize_page(page: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for candidate in page["candidates"]:
        counts[candidate["subtype"]] = counts.get(candidate["subtype"], 0) + 1
    return {
        "slide_no": page["slide_no"],
        "title_or_label": page["title_or_label"],
        "candidate_count": len(page["candidates"]),
        "candidate_subtypes": counts,
    }


def main() -> None:
    input_path = Path("docs/ppt-slide-details-12-19-29.json")
    output_path = Path("docs/ppt-intermediate-candidates-12-19-29.json")
    summary_path = Path("docs/ppt-intermediate-candidates-summary-12-19-29.json")

    detail_payload = json.loads(input_path.read_text(encoding="utf-8"))
    intermediate = build_intermediate_model(detail_payload)
    output_path.write_text(json.dumps(intermediate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = {
        "pptxPath": intermediate["pptxPath"],
        "requestedSlides": intermediate["requestedSlides"],
        "pages": [summarize_page(page) for page in intermediate["pages"]],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Generated intermediate candidates: {output_path}")
    print(f"Generated summary: {summary_path}")


if __name__ == "__main__":
    main()
