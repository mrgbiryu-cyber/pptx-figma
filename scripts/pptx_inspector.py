#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from zipfile import ZipFile
import xml.etree.ElementTree as ET


P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS = {"p": P_NS, "r": R_NS, "a": A_NS}
CORE_SLIDES = {12, 26, 29, 34}
EMU_PER_PIXEL = 9525


@dataclass
class SlideInspection:
    slide_no: int
    slide_path: str
    rel_path: str | None
    title_or_label: str
    shape_count: int
    group_count: int
    image_count: int
    graphic_frame_count: int
    table_count: int
    text_node_count: int
    has_notes: bool
    hidden: bool


def _local_name(tag: str) -> str:
    return tag.split("}")[-1]


def _normalize_text(value: str | None, slide_no: int) -> str:
    if value is None:
        return f"Slide {slide_no}"
    normalized = " ".join(value.split()).strip()
    return normalized or f"Slide {slide_no}"


def _first_text(shape: ET.Element) -> str | None:
    texts: list[str] = []
    for node in shape.findall(".//a:t", NS):
        if node.text and node.text.strip():
            texts.append(node.text.strip())
    if texts:
        return " ".join(texts)
    return None


def _infer_title(shapes: Iterable[ET.Element], slide_no: int) -> str:
    for shape in shapes:
        text = _first_text(shape)
        if text:
            return _normalize_text(text, slide_no)
    return f"Slide {slide_no}"


def _extract_xfrm(node: ET.Element | None) -> dict[str, int] | None:
    if node is None:
        return None
    off = node.find("a:off", NS)
    ext = node.find("a:ext", NS)
    if off is None and ext is None:
        return None
    return {
        "x": int(off.attrib.get("x", "0")) if off is not None else 0,
        "y": int(off.attrib.get("y", "0")) if off is not None else 0,
        "cx": int(ext.attrib.get("cx", "0")) if ext is not None else 0,
        "cy": int(ext.attrib.get("cy", "0")) if ext is not None else 0,
        "rot": int(node.attrib.get("rot", "0")) if node.attrib.get("rot") else 0,
        "flipH": node.attrib.get("flipH") == "1",
        "flipV": node.attrib.get("flipV") == "1",
    }


def _extract_group_context(node: ET.Element | None) -> dict[str, int] | None:
    if node is None:
        return None
    off = node.find("a:off", NS)
    ext = node.find("a:ext", NS)
    ch_off = node.find("a:chOff", NS)
    ch_ext = node.find("a:chExt", NS)
    if off is None and ext is None:
        return None
    return {
        "x": int(off.attrib.get("x", "0")) if off is not None else 0,
        "y": int(off.attrib.get("y", "0")) if off is not None else 0,
        "cx": int(ext.attrib.get("cx", "0")) if ext is not None else 0,
        "cy": int(ext.attrib.get("cy", "0")) if ext is not None else 0,
        "chOffX": int(ch_off.attrib.get("x", "0")) if ch_off is not None else 0,
        "chOffY": int(ch_off.attrib.get("y", "0")) if ch_off is not None else 0,
        "chExtCx": int(ch_ext.attrib.get("cx", ext.attrib.get("cx", "0"))) if ch_ext is not None and ext is not None else (int(ext.attrib.get("cx", "0")) if ext is not None else 0),
        "chExtCy": int(ch_ext.attrib.get("cy", ext.attrib.get("cy", "0"))) if ch_ext is not None and ext is not None else (int(ext.attrib.get("cy", "0")) if ext is not None else 0),
        "rot": int(node.attrib.get("rot", "0")) if node.attrib.get("rot") else 0,
        "flipH": node.attrib.get("flipH") == "1",
        "flipV": node.attrib.get("flipV") == "1",
    }


def _apply_group_transform(bounds: dict[str, int] | None, group_context: dict[str, int] | None) -> dict[str, int] | None:
    if bounds is None or group_context is None:
        return bounds
    scale_x = group_context["cx"] / group_context["chExtCx"] if group_context.get("chExtCx") else 1
    scale_y = group_context["cy"] / group_context["chExtCy"] if group_context.get("chExtCy") else 1
    return {
        "x": int(round(group_context["x"] + (bounds.get("x", 0) - group_context.get("chOffX", 0)) * scale_x)),
        "y": int(round(group_context["y"] + (bounds.get("y", 0) - group_context.get("chOffY", 0)) * scale_y)),
        "cx": int(round(bounds.get("cx", 0) * scale_x)),
        "cy": int(round(bounds.get("cy", 0) * scale_y)),
        "rot": bounds.get("rot", 0),
        "flipH": bounds.get("flipH", False),
        "flipV": bounds.get("flipV", False),
    }


def _emu_to_px(value: int | str | None) -> float | None:
    if value is None:
        return None
    return round(int(value) / EMU_PER_PIXEL, 2)


def _extract_alpha(node: ET.Element | None) -> float | None:
    if node is None:
        return None
    alpha = node.find("a:alpha", NS)
    if alpha is None:
        return None
    value = alpha.attrib.get("val")
    if value is None:
        return None
    return round(int(value) / 100000, 4)


def _extract_color_payload(node: ET.Element | None, theme_colors: dict[str, str] | None = None) -> dict[str, Any] | None:
    if node is None:
        return None

    srgb = node.find("a:srgbClr", NS)
    if srgb is not None:
        return {
            "type": "srgb",
            "value": srgb.attrib.get("val"),
            "alpha": _extract_alpha(srgb),
        }

    scheme = node.find("a:schemeClr", NS)
    if scheme is not None:
        return {
            "type": "scheme",
            "value": scheme.attrib.get("val"),
            "resolved_value": theme_colors.get(scheme.attrib.get("val")) if theme_colors and scheme.attrib.get("val") else None,
            "alpha": _extract_alpha(scheme),
        }

    sys_clr = node.find("a:sysClr", NS)
    if sys_clr is not None:
        return {
            "type": "system",
            "value": sys_clr.attrib.get("val"),
            "resolved_value": sys_clr.attrib.get("lastClr"),
            "alpha": _extract_alpha(sys_clr),
        }

    return None


def _extract_shape_style(node: ET.Element, theme_colors: dict[str, str] | None = None) -> dict[str, Any]:
    sp_pr = node.find("p:spPr", NS)
    if sp_pr is None:
        return {}

    fill_payload: dict[str, Any] | None = None
    solid_fill = sp_pr.find("a:solidFill", NS)
    if solid_fill is not None:
        fill_payload = _extract_color_payload(solid_fill, theme_colors)
        if fill_payload:
            fill_payload["kind"] = "solid"
    elif sp_pr.find("a:noFill", NS) is not None:
        fill_payload = {"kind": "none"}

    line_payload: dict[str, Any] | None = None
    line = sp_pr.find("a:ln", NS)
    if line is not None:
        line_payload = _extract_color_payload(line.find("a:solidFill", NS), theme_colors) or {"kind": "default"}
        line_payload["width_emu"] = int(line.attrib.get("w", "0")) if line.attrib.get("w") else None
        line_payload["width_px"] = _emu_to_px(line_payload["width_emu"]) if line_payload.get("width_emu") else None
        head_end = line.find("a:headEnd", NS)
        tail_end = line.find("a:tailEnd", NS)
        if head_end is not None:
            line_payload["head_end"] = dict(head_end.attrib)
        if tail_end is not None:
            line_payload["tail_end"] = dict(tail_end.attrib)

    return {
        "fill": fill_payload,
        "line": line_payload,
    }


def _extract_text_alignment(node: ET.Element) -> dict[str, Any]:
    body_pr = node.find(".//p:txBody/a:bodyPr", NS) or node.find(".//a:txBody/a:bodyPr", NS)
    first_paragraph = node.find(".//p:txBody/a:p", NS) or node.find(".//a:txBody/a:p", NS)
    p_pr = first_paragraph.find("a:pPr", NS) if first_paragraph is not None else None
    payload: dict[str, Any] = {}
    # PPT default horizontal alignment is left ("l") when algn is absent.
    # Always emit the value so renderers don't need to guess.
    raw_algn = p_pr.attrib.get("algn") if p_pr is not None else None
    payload["horizontal_align"] = raw_algn if raw_algn else "l"
    if body_pr is not None:
        # PPT default vertical anchor is top ("t") when anchor is absent.
        raw_anchor = body_pr.attrib.get("anchor")
        payload["vertical_align"] = raw_anchor if raw_anchor else "t"
        for attr in ("lIns", "rIns", "tIns", "bIns"):
            if body_pr.attrib.get(attr):
                payload[attr] = _emu_to_px(body_pr.attrib.get(attr))
        if body_pr.attrib.get("wrap"):
            payload["wrap"] = body_pr.attrib.get("wrap")
    return payload


def _extract_cnvpr(container: ET.Element | None) -> dict[str, Any]:
    if container is None:
        return {"id": None, "name": None}
    c_nv_pr = container.find(".//p:cNvPr", NS)
    if c_nv_pr is None:
        return {"id": None, "name": None}
    return {
        "id": c_nv_pr.attrib.get("id"),
        "name": c_nv_pr.attrib.get("name"),
        "descr": c_nv_pr.attrib.get("descr"),
    }


def _extract_placeholder(node: ET.Element) -> dict[str, Any] | None:
    placeholder = node.find(".//p:nvPr/p:ph", NS)
    if placeholder is None:
        return None
    payload: dict[str, Any] = {}
    for key in ("type", "idx", "sz", "orient"):
        if placeholder.attrib.get(key) is not None:
            payload[key] = placeholder.attrib.get(key)
    return payload or None


def _extract_text_runs(node: ET.Element, theme_colors: dict[str, str] | None = None) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    paragraphs = node.findall(".//a:p", NS)
    for paragraph_index, paragraph in enumerate(paragraphs):
        paragraph_runs: list[dict[str, Any]] = []
        for child in list(paragraph):
            tag = _local_name(child.tag)
            if tag not in {"r", "fld", "br"}:
                continue
            if tag == "br":
                paragraph_runs.append({"type": "line_break", "text": "\n"})
                continue
            text_node = child.find("a:t", NS)
            if text_node is None or not text_node.text:
                continue
            r_pr = child.find("a:rPr", NS)
            paragraph_runs.append(
                {
                    "type": "text",
                    "text": text_node.text,
                    "font_size": int(r_pr.attrib.get("sz", "0")) / 100 if r_pr is not None and r_pr.attrib.get("sz") else None,
                    "bold": r_pr.attrib.get("b") == "1" if r_pr is not None else False,
                    "italic": r_pr.attrib.get("i") == "1" if r_pr is not None else False,
                    "font_family": (
                        (r_pr.find("a:latin", NS).attrib.get("typeface") if r_pr is not None and r_pr.find("a:latin", NS) is not None else None)
                        or (r_pr.find("a:ea", NS).attrib.get("typeface") if r_pr is not None and r_pr.find("a:ea", NS) is not None else None)
                    ),
                    "fill": _extract_color_payload(r_pr.find("a:solidFill", NS), theme_colors) if r_pr is not None else None,
                }
            )
        if paragraph_runs:
            runs.extend(paragraph_runs)
            if paragraph_index < len(paragraphs) - 1:
                runs.append({"type": "paragraph_break", "text": "\n"})
    return runs


def _build_text_from_runs(text_runs: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for run in text_runs:
        if run.get("type") in {"text", "line_break", "paragraph_break"}:
            parts.append(run.get("text", ""))
    return "".join(parts).strip()


def _extract_shape_kind(node: ET.Element) -> str | None:
    if _local_name(node.tag) == "cxnSp":
        geom = node.find("p:spPr/a:prstGeom", NS)
        return geom.attrib.get("prst") if geom is not None else "connector"
    geom = node.find("p:spPr/a:prstGeom", NS)
    if geom is not None:
        return geom.attrib.get("prst")
    return None


def _extract_connector_adjusts(node: ET.Element) -> dict[str, int]:
    geom = node.find("p:spPr/a:prstGeom", NS)
    av_lst = geom.find("a:avLst", NS) if geom is not None else None
    payload: dict[str, int] = {}
    if av_lst is None:
        return payload
    for gd in av_lst.findall("a:gd", NS):
        name = gd.attrib.get("name")
        fmla = gd.attrib.get("fmla", "")
        if not name or not fmla.startswith("val "):
            continue
        try:
            payload[name] = int(fmla.split(" ", 1)[1])
        except ValueError:
            continue
    return payload


def _extract_connector_links(node: ET.Element) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    cnv = node.find("p:nvCxnSpPr/p:cNvCxnSpPr", NS)
    if cnv is None:
        return payload
    start = cnv.find("a:stCxn", NS)
    end = cnv.find("a:endCxn", NS)
    if start is not None:
        payload["start_connection"] = {
            "id": start.attrib.get("id"),
            "idx": int(start.attrib.get("idx", "0")) if start.attrib.get("idx") else None,
        }
    if end is not None:
        payload["end_connection"] = {
            "id": end.attrib.get("id"),
            "idx": int(end.attrib.get("idx", "0")) if end.attrib.get("idx") else None,
        }
    return payload


def _extract_table(frame: ET.Element, theme_colors: dict[str, str] | None = None) -> dict[str, Any] | None:
    table = frame.find(".//a:tbl", NS)
    if table is None:
        return None
    grid = table.find("a:tblGrid", NS)
    grid_columns: list[dict[str, Any]] = []
    if grid is not None:
        for index, column in enumerate(grid.findall("a:gridCol", NS), start=1):
            width_emu = int(column.attrib.get("w", "0")) if column.attrib.get("w") else 0
            grid_columns.append(
                {
                    "column_index": index,
                    "width_emu": width_emu,
                    "width_px": _emu_to_px(width_emu),
                }
            )
    rows_payload: list[dict[str, Any]] = []
    for row_index, row in enumerate(table.findall("a:tr", NS), start=1):
        cells_payload: list[dict[str, Any]] = []
        running_col_index = 0
        for cell_index, cell in enumerate(row.findall("a:tc", NS), start=1):
            tc_pr = cell.find("a:tcPr", NS)
            text_runs = _extract_text_runs(cell, theme_colors)
            text_alignment = _extract_text_alignment(cell)
            grid_span = int(cell.attrib.get("gridSpan", "1")) if cell.attrib.get("gridSpan") else 1
            row_span = int(cell.attrib.get("rowSpan", "1")) if cell.attrib.get("rowSpan") else 1
            spanned_columns = grid_columns[running_col_index: running_col_index + grid_span]
            width_emu = sum(column["width_emu"] for column in spanned_columns)
            cells_payload.append(
                {
                    "cell_index": cell_index,
                    "text": _build_text_from_runs(text_runs),
                    "grid_span": grid_span,
                    "row_span": row_span,
                    "h_merge": cell.attrib.get("hMerge"),
                    "v_merge": cell.attrib.get("vMerge"),
                    "start_column_index": running_col_index + 1,
                    "width_emu": width_emu,
                    "width_px": _emu_to_px(width_emu),
                    "style": {
                        "fill": _extract_color_payload(tc_pr.find("a:solidFill", NS), theme_colors) if tc_pr is not None else None,
                        "anchor": tc_pr.attrib.get("anchor") if tc_pr is not None else None,
                        "marL": _emu_to_px(tc_pr.attrib.get("marL")) if tc_pr is not None and tc_pr.attrib.get("marL") else None,
                        "marR": _emu_to_px(tc_pr.attrib.get("marR")) if tc_pr is not None and tc_pr.attrib.get("marR") else None,
                        "marT": _emu_to_px(tc_pr.attrib.get("marT")) if tc_pr is not None and tc_pr.attrib.get("marT") else None,
                        "marB": _emu_to_px(tc_pr.attrib.get("marB")) if tc_pr is not None and tc_pr.attrib.get("marB") else None,
                    },
                    "text_runs": text_runs,
                    "text_alignment": text_alignment,
                    "text_style": _summarize_text_style(text_runs, text_alignment),
                }
            )
            running_col_index += grid_span
        rows_payload.append(
            {
                "row_index": row_index,
                "height": row.attrib.get("h"),
                "height_px": _emu_to_px(row.attrib.get("h")) if row.attrib.get("h") else None,
                "cells": cells_payload,
            }
        )
    return {
        "row_count": len(rows_payload),
        "grid_columns": grid_columns,
        "rows": rows_payload,
    }


def _resolve_part_path(base_path: str, target: str) -> str:
    base = PurePosixPath(base_path).parent
    resolved = (base / target).as_posix()
    stack: list[str] = []
    for part in resolved.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if stack:
                stack.pop()
            continue
        stack.append(part)
    return "/".join(stack)


def _extract_picture(node: ET.Element, rel_targets: dict[str, str], archive: ZipFile, slide_path: str) -> dict[str, Any]:
    blip = node.find(".//a:blip", NS)
    embed = blip.attrib.get(f"{{{R_NS}}}embed") if blip is not None else None
    image_target = rel_targets.get(embed) if embed else None
    resolved_target = _resolve_part_path(slide_path, image_target) if image_target else None
    mime_type = None
    image_base64 = None
    if resolved_target and resolved_target in archive.namelist():
        suffix = Path(resolved_target).suffix.lower()
        mime_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
        }.get(suffix)
        if mime_type:
            image_base64 = base64.b64encode(archive.read(resolved_target)).decode("ascii")
    return {
        "image_rel_id": embed,
        "image_target": image_target,
        "resolved_target": resolved_target,
        "mime_type": mime_type,
        "image_base64": image_base64,
    }


def _summarize_text_style(text_runs: list[dict[str, Any]], alignment: dict[str, Any]) -> dict[str, Any]:
    font_sizes = [run["font_size"] for run in text_runs if run.get("type") == "text" and run.get("font_size")]
    first_fill = next((run.get("fill") for run in text_runs if run.get("type") == "text" and run.get("fill")), None)
    first_family = next((run.get("font_family") for run in text_runs if run.get("type") == "text" and run.get("font_family")), None)
    return {
        "font_size_max": max(font_sizes) if font_sizes else None,
        "font_size_min": min(font_sizes) if font_sizes else None,
        "font_size_avg": round(sum(font_sizes) / len(font_sizes), 2) if font_sizes else None,
        "font_family": first_family,
        "fill": first_fill,
        **alignment,
    }


def _extract_element(
    node: ET.Element,
    rel_targets: dict[str, str],
    archive: ZipFile,
    slide_path: str,
    theme_colors: dict[str, str],
    group_context: dict[str, int] | None = None,
    source_scope: str = "slide",
) -> dict[str, Any]:
    tag = _local_name(node.tag)
    meta = _extract_cnvpr(node)

    if tag == "grpSp":
        raw_group_xfrm = _extract_group_context(node.find("p:grpSpPr/a:xfrm", NS))
        absolute_group_bounds = _apply_group_transform(raw_group_xfrm, group_context)
        child_group_context = {
            **(absolute_group_bounds or {}),
            "chOffX": raw_group_xfrm.get("chOffX", 0) if raw_group_xfrm else 0,
            "chOffY": raw_group_xfrm.get("chOffY", 0) if raw_group_xfrm else 0,
            "chExtCx": raw_group_xfrm.get("chExtCx", raw_group_xfrm.get("cx", 0)) if raw_group_xfrm else 0,
            "chExtCy": raw_group_xfrm.get("chExtCy", raw_group_xfrm.get("cy", 0)) if raw_group_xfrm else 0,
        } if raw_group_xfrm else None
        children = [
            _extract_element(child, rel_targets, archive, slide_path, theme_colors, child_group_context, source_scope)
            for child in list(node)
            if _local_name(child.tag) not in {"nvGrpSpPr", "grpSpPr"}
        ]
        return {
            "element_type": "group",
            "node_tag": tag,
            "node_id": meta.get("id"),
            "name": meta.get("name"),
            "descr": meta.get("descr"),
            "bounds": absolute_group_bounds,
            "children": children,
            "source_scope": source_scope,
        }

    payload: dict[str, Any] = {
        "element_type": {
            "sp": "shape",
            "cxnSp": "connector",
            "graphicFrame": "graphic_frame",
            "pic": "image",
        }.get(tag, tag),
        "node_tag": tag,
        "node_id": meta.get("id"),
        "name": meta.get("name"),
        "descr": meta.get("descr"),
        "children": [],
        "source_scope": source_scope,
        "placeholder": _extract_placeholder(node),
    }

    if tag in {"sp", "cxnSp"}:
        payload["bounds"] = _apply_group_transform(_extract_xfrm(node.find("p:spPr/a:xfrm", NS)), group_context)
        payload["shape_kind"] = _extract_shape_kind(node)
        payload["text_runs"] = _extract_text_runs(node, theme_colors)
        payload["shape_style"] = _extract_shape_style(node, theme_colors)
        payload["text_alignment"] = _extract_text_alignment(node)
        payload["text_style"] = _summarize_text_style(payload["text_runs"], payload["text_alignment"])
        payload["text"] = _build_text_from_runs(payload["text_runs"])
        if tag == "cxnSp":
            payload["connector_adjusts"] = _extract_connector_adjusts(node)
            payload.update(_extract_connector_links(node))
    elif tag == "graphicFrame":
        payload["bounds"] = _apply_group_transform(_extract_xfrm(node.find("p:xfrm", NS)), group_context)
        table_payload = _extract_table(node, theme_colors)
        payload["table"] = table_payload
        payload["frame_kind"] = "table" if table_payload else "graphic_frame"
    elif tag == "pic":
        payload["bounds"] = _apply_group_transform(_extract_xfrm(node.find("p:spPr/a:xfrm", NS)), group_context)
        payload.update(_extract_picture(node, rel_targets, archive, slide_path))
    else:
        payload["bounds"] = None

    return payload


def _slide_rel_targets(archive: ZipFile, slide_path: str) -> dict[str, str]:
    part = PurePosixPath(slide_path)
    rel_path = str(part.parent / "_rels" / (part.name + ".rels"))
    if rel_path not in archive.namelist():
        return {}
    rel_root = ET.fromstring(archive.read(rel_path))
    mapping: dict[str, str] = {}
    for rel in rel_root.findall("{*}Relationship"):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            mapping[rel_id] = target
    return mapping


def _part_relationships(archive: ZipFile, part_path: str) -> list[dict[str, str]]:
    rel_path = str(PurePosixPath(part_path).parent / "_rels" / (PurePosixPath(part_path).name + ".rels"))
    if rel_path not in archive.namelist():
        return []
    rel_root = ET.fromstring(archive.read(rel_path))
    relationships: list[dict[str, str]] = []
    for rel in rel_root.findall("{*}Relationship"):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        rel_type = rel.attrib.get("Type")
        if rel_id and target and rel_type:
            relationships.append({"id": rel_id, "target": target, "type": rel_type})
    return relationships


def _resolve_related_part(archive: ZipFile, part_path: str, relationship_suffix: str) -> str | None:
    for rel in _part_relationships(archive, part_path):
        if rel["type"].endswith(relationship_suffix):
            resolved = _resolve_part_path(part_path, rel["target"])
            if resolved in archive.namelist():
                return resolved
    return None


def _presentation_slide_size(archive: ZipFile) -> dict[str, Any]:
    presentation_root = ET.fromstring(archive.read("ppt/presentation.xml"))
    slide_size = presentation_root.find("p:sldSz", NS)
    if slide_size is None:
        return {}
    cx = int(slide_size.attrib.get("cx", "0"))
    cy = int(slide_size.attrib.get("cy", "0"))
    return {
        "cx": cx,
        "cy": cy,
        "width_px": _emu_to_px(cx),
        "height_px": _emu_to_px(cy),
    }


def _read_layout_font_defaults(archive: ZipFile, layout_path: str | None) -> dict[tuple, float]:
    """Read default font sizes (px) from a layout XML's placeholder lstStyle.

    Returns a dict keyed by (ph_type, ph_idx) → font_size_px.
    PPT OOXML stores the default run font size in:
        p:sp/p:txBody/a:lstStyle/a:lvl1pPr/a:defRPr@sz
    The sz attribute is in hundredths of a point (e.g. sz=1100 → 11pt → 14.67px).
    """
    if not layout_path or layout_path not in archive.namelist():
        return {}
    layout_root = ET.fromstring(archive.read(layout_path))
    defaults: dict[tuple, float] = {}
    for sp in layout_root.iter(f"{{{P_NS}}}sp"):
        ph = sp.find(f".//{{{P_NS}}}ph")
        if ph is None:
            continue
        ph_type = ph.attrib.get("type", "body")
        ph_idx_raw = ph.attrib.get("idx", "0")
        try:
            ph_idx = int(ph_idx_raw)
        except (ValueError, TypeError):
            ph_idx = 0
        # Walk lstStyle levels looking for the first explicit sz
        tx_body = sp.find(f".//{{{P_NS}}}txBody") or sp.find(f".//{{{A_NS}}}txBody")
        if tx_body is None:
            continue
        lst_style = tx_body.find(f"{{{A_NS}}}lstStyle")
        if lst_style is None:
            continue
        for level in range(1, 10):
            lvl_el = lst_style.find(f"{{{A_NS}}}lvl{level}pPr")
            if lvl_el is None:
                continue
            def_rpr = lvl_el.find(f"{{{A_NS}}}defRPr")
            if def_rpr is None:
                continue
            sz_raw = def_rpr.attrib.get("sz")
            if not sz_raw:
                continue
            try:
                pt = int(sz_raw) / 100.0
            except (ValueError, TypeError):
                continue
            if pt > 0:
                px = round(pt * 96.0 / 72.0, 2)
                defaults[(ph_type, ph_idx)] = px
                break  # use level-1 default only
    return defaults


def _extract_theme_colors(archive: ZipFile) -> dict[str, str]:
    theme_path = "ppt/theme/theme1.xml"
    if theme_path not in archive.namelist():
        return {}
    theme_root = ET.fromstring(archive.read(theme_path))
    scheme = theme_root.find(".//a:clrScheme", NS)
    if scheme is None:
        return {}
    colors: dict[str, str] = {}
    for child in list(scheme):
        name = _local_name(child.tag)
        first = list(child)[0] if list(child) else child
        if _local_name(first.tag) == "srgbClr":
            colors[name] = first.attrib.get("val", "")
        elif _local_name(first.tag) == "sysClr":
            colors[name] = first.attrib.get("lastClr", "")
    alias_map = {
        "bg1": colors.get("lt1") or colors.get("dk1", ""),
        "tx1": colors.get("dk1") or colors.get("lt1", ""),
        "bg2": colors.get("lt2") or colors.get("dk2", ""),
        "tx2": colors.get("dk2") or colors.get("lt2", ""),
    }
    for alias, value in alias_map.items():
        if value:
            colors[alias] = value
    return colors


def _extract_part_elements(
    archive: ZipFile,
    part_path: str | None,
    theme_colors: dict[str, str],
    *,
    source_scope: str,
) -> list[dict[str, Any]]:
    if not part_path or part_path not in archive.namelist():
        return []
    part_root = ET.fromstring(archive.read(part_path))
    sp_tree = part_root.find("p:cSld/p:spTree", NS)
    if sp_tree is None:
        return []
    rel_targets = _slide_rel_targets(archive, part_path)
    visible = [c for c in list(sp_tree) if _local_name(c.tag) not in {"nvGrpSpPr", "grpSpPr"}]
    return [
        {**_extract_element(child, rel_targets, archive, part_path, theme_colors, None, source_scope), "z_order": idx}
        for idx, child in enumerate(visible)
    ]


def extract_slide_details(pptx_path: Path, slide_numbers: list[int]) -> dict[str, Any]:
    inspections = inspect_pptx(pptx_path)
    inspection_by_no = {item.slide_no: item for item in inspections}
    requested_slide_numbers = slide_numbers or [item.slide_no for item in inspections]

    with ZipFile(pptx_path) as archive:
        slide_size = _presentation_slide_size(archive)
        theme_colors = _extract_theme_colors(archive)
        slides_payload: list[dict[str, Any]] = []
        for slide_no in requested_slide_numbers:
            inspection = inspection_by_no.get(slide_no)
            if inspection is None:
                raise ValueError(f"Slide {slide_no} not found in {pptx_path}")

            slide_root = ET.fromstring(archive.read(inspection.slide_path))
            sp_tree = slide_root.find("p:cSld/p:spTree", NS)
            if sp_tree is None:
                raise ValueError(f"Missing spTree in {inspection.slide_path}")

            rel_targets = _slide_rel_targets(archive, inspection.slide_path)
            layout_path = _resolve_related_part(archive, inspection.slide_path, "/slideLayout")
            master_path = _resolve_related_part(archive, layout_path, "/slideMaster") if layout_path else None
            visible_children = [c for c in list(sp_tree) if _local_name(c.tag) not in {"nvGrpSpPr", "grpSpPr"}]
            elements = [
                {**_extract_element(child, rel_targets, archive, inspection.slide_path, theme_colors, None, "slide"), "z_order": idx}
                for idx, child in enumerate(visible_children)
            ]
            layout_elements = _extract_part_elements(archive, layout_path, theme_colors, source_scope="layout")
            master_elements = _extract_part_elements(archive, master_path, theme_colors, source_scope="master")

            # Fill in font sizes inherited from the layout for placeholder shapes.
            # PPT runs often have no explicit rPr.sz — the size comes from the
            # layout's lstStyle/lvl1pPr/defRPr.sz.  Without this, the bundle
            # builder falls back to a height-based heuristic that is far too large.
            layout_font_defaults = _read_layout_font_defaults(archive, layout_path)
            if layout_font_defaults:
                for elem in elements:
                    ph = elem.get("placeholder") or {}
                    if not ph:
                        continue
                    text_style = elem.get("text_style") or {}
                    if text_style.get("font_size_max") is not None:
                        continue  # explicit size already present
                    ph_type = str(ph.get("type") or "body")
                    try:
                        ph_idx = int(ph.get("idx") or 0)
                    except (ValueError, TypeError):
                        ph_idx = 0
                    default_px = (
                        layout_font_defaults.get((ph_type, ph_idx))
                        or layout_font_defaults.get((ph_type, 0))
                        or layout_font_defaults.get(("body", 0))
                    )
                    if default_px:
                        merged = dict(text_style)
                        merged["font_size_max"] = default_px
                        if merged.get("font_size_min") is None:
                            merged["font_size_min"] = default_px
                        if merged.get("font_size_avg") is None:
                            merged["font_size_avg"] = default_px
                        elem["text_style"] = merged

            slides_payload.append(
                {
                    "slide_no": slide_no,
                    "title_or_label": inspection.title_or_label,
                    "slide_path": inspection.slide_path,
                    "layout_path": layout_path,
                    "master_path": master_path,
                    "slide_size": slide_size,
                    "theme_colors": theme_colors,
                    "summary": asdict(inspection),
                    "elements": elements,
                    "layout_elements": layout_elements,
                    "master_elements": master_elements,
                }
            )

        return {
            "pptxPath": str(pptx_path),
            "requestedSlides": requested_slide_numbers,
            "slides": slides_payload,
        }


def inspect_pptx(pptx_path: Path) -> list[SlideInspection]:
    with ZipFile(pptx_path) as archive:
        presentation_root = ET.fromstring(archive.read("ppt/presentation.xml"))
        presentation_rels_root = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))

        rel_by_id: dict[str, str] = {}
        for rel in presentation_rels_root.findall("{*}Relationship"):
            rel_id = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rel_id and target:
                rel_by_id[rel_id] = target

        slide_refs = presentation_root.findall("p:sldIdLst/p:sldId", NS)
        inspections: list[SlideInspection] = []

        for index, slide_ref in enumerate(slide_refs, start=1):
            rel_id = slide_ref.attrib.get(f"{{{R_NS}}}id")
            if not rel_id or rel_id not in rel_by_id:
                raise ValueError(f"Missing relationship target for slide {index}")

            target = rel_by_id[rel_id].lstrip("/")
            slide_path = f"ppt/{target}"
            rel_path = slide_path.replace("/slides/", "/slides/_rels/") + ".rels"
            slide_root = ET.fromstring(archive.read(slide_path))
            sp_tree = slide_root.find("p:cSld/p:spTree", NS)
            if sp_tree is None:
                raise ValueError(f"Missing spTree in {slide_path}")

            shapes = sp_tree.findall("p:sp", NS)
            groups = sp_tree.findall("p:grpSp", NS)
            pictures = sp_tree.findall("p:pic", NS)
            graphic_frames = sp_tree.findall("p:graphicFrame", NS)
            tables = [frame for frame in graphic_frames if frame.find(".//a:tbl", NS) is not None]
            text_node_count = sum(1 for shape in shapes if _first_text(shape))
            title_or_label = _infer_title(shapes, index)
            notes_path = f"ppt/notesSlides/notesSlide{index}.xml"

            inspections.append(
                SlideInspection(
                    slide_no=index,
                    slide_path=slide_path,
                    rel_path=rel_path if rel_path in archive.namelist() else None,
                    title_or_label=title_or_label,
                    shape_count=len(shapes),
                    group_count=len(groups),
                    image_count=len(pictures),
                    graphic_frame_count=len(graphic_frames),
                    table_count=len(tables),
                    text_node_count=text_node_count,
                    has_notes=notes_path in archive.namelist(),
                    hidden=slide_root.attrib.get("show") == "0",
                )
            )

        return inspections


def _infer_structure_tags(slide: SlideInspection) -> list[str]:
    tags: list[str] = []
    if slide.table_count > 0:
        tags.extend(["table", "cell"])
    if slide.group_count > 0:
        tags.extend(["group", "section"])
    if slide.image_count > 0:
        tags.append("image")
    if slide.text_node_count > 1:
        tags.append("mixed_text")
    if slide.shape_count >= 10:
        tags.append("complex_layout")
    return sorted(set(tags))


def _infer_difficulty(slide: SlideInspection) -> str:
    complexity = slide.table_count * 3 + slide.group_count * 2 + slide.image_count + slide.shape_count // 8
    if complexity >= 5:
        return "high"
    if complexity >= 2:
        return "medium"
    return "low"


def _infer_risk_notes(slide: SlideInspection) -> list[str]:
    notes: list[str] = []
    if slide.table_count > 0:
        notes.append("table/cell preservation risk")
    if slide.group_count > 0:
        notes.append("group/section hierarchy flattening risk")
    if slide.image_count > 0:
        notes.append("image asset positioning risk")
    if slide.text_node_count > 3:
        notes.append("mixed text and font handling risk")
    if slide.hidden:
        notes.append("hidden slide state detected")
    return notes


def build_benchmark_metadata(slides: list[SlideInspection], pptx_path: Path) -> dict:
    return {
        "benchmarkFile": str(pptx_path),
        "detectedSlideCount": len(slides),
        "coreSlides": sorted(CORE_SLIDES),
        "slides": [
            {
                "slide_no": slide.slide_no,
                "title_or_label": slide.title_or_label,
                "difficulty": _infer_difficulty(slide),
                "structure_tags": _infer_structure_tags(slide),
                "risk_notes": _infer_risk_notes(slide),
                "must_pass": "yes" if slide.slide_no in CORE_SLIDES else "no",
                "human_review_required": "yes" if slide.slide_no in CORE_SLIDES else "no",
                "auto_result": "",
                "human_result": "",
                "notes": "core benchmark slide" if slide.slide_no in CORE_SLIDES else "",
            }
            for slide in slides
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a PPTX file and optionally generate benchmark metadata.")
    parser.add_argument("pptx_path", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, help="Write benchmark metadata JSON to this path.")
    parser.add_argument("--detail-slides", dest="detail_slides", help="Comma-separated slide numbers for detailed extraction.")
    parser.add_argument("--detail-json", dest="detail_json_path", type=Path, help="Write detailed slide extraction JSON to this path.")
    args = parser.parse_args()

    slides = inspect_pptx(args.pptx_path)
    payload = {
        "pptxPath": str(args.pptx_path),
        "slideCount": len(slides),
        "slides": [asdict(slide) for slide in slides],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if args.json_path:
        metadata = build_benchmark_metadata(slides, args.pptx_path)
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nGenerated benchmark metadata: {args.json_path}")

    if args.detail_slides:
        slide_numbers = [int(part.strip()) for part in args.detail_slides.split(",") if part.strip()]
        detail_payload = extract_slide_details(args.pptx_path, slide_numbers)
        serialized = json.dumps(detail_payload, ensure_ascii=False, indent=2)
        if args.detail_json_path:
            args.detail_json_path.parent.mkdir(parents=True, exist_ok=True)
            args.detail_json_path.write_text(serialized + "\n", encoding="utf-8")
            print(f"\nGenerated detailed extraction: {args.detail_json_path}")
        else:
            print(f"\n{serialized}")


if __name__ == "__main__":
    main()
