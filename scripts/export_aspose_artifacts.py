#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pptx_artifact_common import parse_slide_range, selected_slide_indices, status_manifest, write_json


def color_to_dict(color: Any) -> dict[str, Any] | None:
    if color is None:
        return None
    try:
        return {
            "r": getattr(color, "r", None),
            "g": getattr(color, "g", None),
            "b": getattr(color, "b", None),
            "a": getattr(color, "a", None),
        }
    except Exception:
        return {"repr": repr(color)}


def fill_to_dict(shape: Any) -> dict[str, Any]:
    fill = getattr(shape, "fill_format", None)
    if fill is None:
        return {}
    result: dict[str, Any] = {"fill_type": str(getattr(fill, "fill_type", ""))}
    solid = getattr(fill, "solid_fill_color", None)
    if solid is not None:
        result["solid_color"] = color_to_dict(getattr(solid, "color", None))
    return result


def line_to_dict(shape: Any) -> dict[str, Any]:
    line = getattr(shape, "line_format", None)
    if line is None:
        return {}
    result: dict[str, Any] = {
        "width": getattr(line, "width", None),
        "dash_style": str(getattr(line, "dash_style", "")),
    }
    fill = getattr(line, "fill_format", None)
    if fill is not None:
        solid = getattr(fill, "solid_fill_color", None)
        if solid is not None:
            result["solid_color"] = color_to_dict(getattr(solid, "color", None))
    return result


def text_to_dict(shape: Any) -> dict[str, Any]:
    text_frame = getattr(shape, "text_frame", None)
    if text_frame is None:
        return {}
    paragraphs_payload = []
    texts = []
    for paragraph in getattr(text_frame, "paragraphs", []) or []:
        portions_payload = []
        for portion in getattr(paragraph, "portions", []) or []:
            text = str(getattr(portion, "text", "") or "")
            texts.append(text)
            portion_format = getattr(portion, "portion_format", None)
            portions_payload.append(
                {
                    "text": text,
                    "font_height": getattr(portion_format, "font_height", None),
                    "latin_font": str(getattr(getattr(portion_format, "latin_font", None), "font_name", "") or ""),
                    "fill": fill_to_dict(portion_format) if portion_format is not None else {},
                }
            )
        paragraphs_payload.append({"portions": portions_payload})
    return {
        "text": "".join(texts),
        "paragraphs": paragraphs_payload,
    }


def shape_to_dict(shape: Any, z_index: int, slide_no: int) -> dict[str, Any]:
    frame = getattr(shape, "frame", None)
    return {
        "slide_no": slide_no,
        "z_index": z_index,
        "name": str(getattr(shape, "name", "") or ""),
        "shape_type": str(getattr(shape, "shape_type", "") or ""),
        "office_interop_shape_id": getattr(shape, "office_interop_shape_id", None),
        "alternative_text": str(getattr(shape, "alternative_text", "") or ""),
        "hidden": bool(getattr(shape, "hidden", False)),
        "x": getattr(shape, "x", None),
        "y": getattr(shape, "y", None),
        "width": getattr(shape, "width", None),
        "height": getattr(shape, "height", None),
        "rotation": getattr(frame, "rotation", None) if frame is not None else None,
        "fill": fill_to_dict(shape),
        "line": line_to_dict(shape),
        "text": text_to_dict(shape),
    }


def export_aspose(pptx: Path, out_dir: Path, slides_filter: list[int] | None, vectorize_text: bool) -> dict[str, Any]:
    import aspose.slides as slides  # type: ignore

    svg_dir = out_dir / "svg"
    shape_svg_dir = out_dir / "shape-svg"
    svg_dir.mkdir(parents=True, exist_ok=True)
    shape_svg_dir.mkdir(parents=True, exist_ok=True)
    slide_rows: list[dict[str, Any]] = []
    shape_rows: list[dict[str, Any]] = []

    with slides.Presentation(str(pptx)) as presentation:
        try:
            slide_count = int(presentation.slides.length)
        except Exception:
            slide_count = len(presentation.slides)
        selected = selected_slide_indices(slide_count, slides_filter)
        for slide_index in selected:
            slide = presentation.slides[slide_index]
            slide_no = slide_index + 1
            svg_path = svg_dir / f"slide{slide_no}.svg"
            try:
                options = slides.export.SVGOptions()
                options.vectorize_text = vectorize_text
                with svg_path.open("wb") as handle:
                    slide.write_as_svg(handle, options)
            except Exception:
                with svg_path.open("wb") as handle:
                    slide.write_as_svg(handle)
            slide_rows.append(
                {
                    "slide_no": slide_no,
                    "slide_id": getattr(slide, "slide_id", None),
                    "name": str(getattr(slide, "name", "") or ""),
                    "shape_count": len(slide.shapes),
                    "svg": str(svg_path),
                }
            )
            for z_index, shape in enumerate(slide.shapes):
                shape_row = shape_to_dict(shape, z_index, slide_no)
                shape_svg_path = shape_svg_dir / f"slide{slide_no}-z{z_index}-{shape_row.get('office_interop_shape_id') or 'shape'}.svg"
                try:
                    with shape_svg_path.open("wb") as handle:
                        shape.write_as_svg(handle)
                    shape_row["shape_svg"] = str(shape_svg_path)
                except Exception as exc:
                    shape_row["shape_svg_error"] = str(exc)
                shape_rows.append(shape_row)

    return {
        "slides": slide_rows,
        "shapes": shape_rows,
        "shape_count": len(shape_rows),
        "shape_text_count": sum(1 for row in shape_rows if (row.get("text") or {}).get("text")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Aspose.Slides SVG and object JSON artifacts.")
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--slides", help="Slide range, e.g. 1-8 or 1,3,5")
    parser.add_argument("--vectorize-text", action="store_true", help="Save SVG text as vector paths.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    details: dict[str, Any]
    status = "ok"
    if args.dry_run:
        details = {"dry_run": True, "intended_outputs": ["svg/*.svg", "shape-svg/*.svg", "aspose-shapes.json"]}
    else:
        try:
            details = export_aspose(args.pptx, args.out_dir, parse_slide_range(args.slides), args.vectorize_text)
            write_json(args.out_dir / "aspose-shapes.json", {"kind": "aspose-shape-manifest", **details})
        except Exception as exc:
            status = "failed"
            details = {
                "error": str(exc),
                "hint": "Install/enable aspose.slides to run this exporter.",
            }

    manifest = status_manifest(
        kind="aspose-artifacts",
        source=args.pptx,
        out_dir=args.out_dir,
        status=status,
        details=details,
    )
    write_json(args.out_dir / "aspose.manifest.json", manifest)
    print(args.out_dir / "aspose.manifest.json")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
