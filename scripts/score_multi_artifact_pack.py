#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from pptx_artifact_common import read_json, status_manifest, write_json


def safe_read(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def aspose_score(base_dir: Path) -> dict[str, Any]:
    manifest = safe_read(base_dir / "aspose" / "aspose.manifest.json")
    shapes = safe_read(base_dir / "aspose" / "aspose-shapes.json")
    if not manifest:
        return {"status": "missing"}
    if (manifest.get("details") or {}).get("dry_run"):
        return {"status": "dry_run", "note": "Aspose exporter was not executed; no structure metrics available."}
    if manifest.get("status") != "ok" or not shapes:
        return {"status": manifest.get("status"), "error": (manifest.get("details") or {}).get("error")}
    rows = shapes.get("shapes") or []
    shape_count = len(rows)
    text_count = sum(1 for row in rows if ((row.get("text") or {}).get("text") or "").strip())
    fill_count = sum(1 for row in rows if (row.get("fill") or {}).get("solid_color"))
    line_count = sum(1 for row in rows if (row.get("line") or {}).get("solid_color"))
    z_order_count = sum(1 for row in rows if isinstance(row.get("z_index"), int))
    interop_id_count = sum(1 for row in rows if row.get("office_interop_shape_id") is not None)
    shape_svg_count = sum(1 for row in rows if row.get("shape_svg"))
    return {
        "status": "ok",
        "shape_count": shape_count,
        "text_shape_count": text_count,
        "fill_color_shape_count": fill_count,
        "line_color_shape_count": line_count,
        "z_order_shape_count": z_order_count,
        "interop_id_shape_count": interop_id_count,
        "shape_svg_count": shape_svg_count,
        "text_shape_ratio": ratio(text_count, shape_count),
        "fill_color_ratio": ratio(fill_count, shape_count),
        "z_order_ratio": ratio(z_order_count, shape_count),
        "interop_id_ratio": ratio(interop_id_count, shape_count),
        "shape_svg_ratio": ratio(shape_svg_count, shape_count),
    }


def ai_semantic_score(base_dir: Path) -> dict[str, Any]:
    manifest = safe_read(base_dir / "ai-semantic" / "ai-semantic.manifest.json")
    if not manifest:
        return {"status": "missing"}
    if any((row or {}).get("status") == "dry_run" for row in (manifest.get("details") or {}).values()):
        return {"status": "dry_run", "note": "Semantic exporters were not executed."}
    details = manifest.get("details") or {}
    return {
        "status": manifest.get("status"),
        "tools_ok": [name for name, row in details.items() if row.get("status") == "ok"],
        "tools_missing": [name for name, row in details.items() if row.get("status") == "missing_tool"],
        "tools_failed": [name for name, row in details.items() if row.get("status") == "failed"],
    }


def renderer_score(base_dir: Path, name: str) -> dict[str, Any]:
    manifest = safe_read(base_dir / name / f"{name}.manifest.json")
    if not manifest:
        return {"status": "missing"}
    return {
        "status": manifest.get("status"),
        "details": manifest.get("details") or {},
    }


def powerpoint_com_structure_score(base_dir: Path) -> dict[str, Any]:
    manifest = safe_read(base_dir / "powerpoint-com-structure" / "powerpoint-com-structure.manifest.json")
    structure = safe_read(base_dir / "powerpoint-com-structure" / "powerpoint-com-structure.json")
    if not manifest:
        return {"status": "missing"}
    if (manifest.get("details") or {}).get("command_result", {}).get("dry_run"):
        return {"status": "dry_run", "note": "PowerPoint COM structure exporter was not executed."}
    if manifest.get("status") != "ok" or not structure:
        return {"status": manifest.get("status"), "error": (manifest.get("details") or {}).get("command_result", {}).get("stderr")}
    rows = structure.get("shapes") or []
    shape_count = len(rows)
    text_count = sum(1 for row in rows if ((row.get("text") or {}).get("text") or "").strip())
    table_count = sum(1 for row in rows if row.get("has_table"))
    fill_count = sum(1 for row in rows if (row.get("fill") or {}).get("fore_color"))
    line_count = sum(1 for row in rows if (row.get("line") or {}).get("fore_color"))
    z_order_count = sum(1 for row in rows if isinstance(row.get("z_index"), int))
    id_count = sum(1 for row in rows if row.get("id") is not None)
    return {
        "status": "ok",
        "shape_count": shape_count,
        "text_shape_count": text_count,
        "table_shape_count": table_count,
        "fill_color_shape_count": fill_count,
        "line_color_shape_count": line_count,
        "z_order_shape_count": z_order_count,
        "id_shape_count": id_count,
        "text_shape_ratio": ratio(text_count, shape_count),
        "fill_color_ratio": ratio(fill_count, shape_count),
        "z_order_ratio": ratio(z_order_count, shape_count),
        "id_ratio": ratio(id_count, shape_count),
    }


def selected_exporters(base_dir: Path) -> set[str] | None:
    index = safe_read(base_dir / "artifact-pack.index.json")
    if not index:
        return None
    rows = ((index.get("details") or {}).get("exporters") or [])
    names = {str(row.get("name")) for row in rows if row.get("name")}
    return names or None


def ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 6)


def recommendation(scores: dict[str, Any]) -> list[str]:
    items: list[str] = []
    aspose = scores.get("aspose") or {}
    power_com = scores.get("powerpoint_com_structure") or {}
    if power_com.get("status") == "ok" and power_com.get("shape_count", 0) > 0:
        items.append("Use PowerPoint COM structure as the no-install corporate bakeoff source candidate.")
    elif power_com.get("status") == "ok":
        items.append("PowerPoint COM ran but returned zero shapes; open the PPTX manually in PowerPoint, clear Protected View/prompts, close it, and rerun.")
    if aspose.get("status") == "ok":
        if aspose.get("z_order_ratio", 0) >= 0.95 and aspose.get("interop_id_ratio", 0) >= 0.9:
            items.append("Use Aspose object JSON as the primary structure source candidate.")
        else:
            items.append("Aspose is present, but z-order or stable IDs need deeper validation.")
        if aspose.get("shape_svg_ratio", 0) >= 0.8:
            items.append("Use Aspose slide/shape SVG as the AI-readable visual source, not as direct Figma import.")
    elif aspose.get("status") == "dry_run":
        items.append("Run the Aspose exporter for real before deciding whether to replace the current extractor.")
    else:
        items.append("Aspose exporter is not ready; install/license it before deciding on the new primary extractor.")
    if scores.get("ai_semantic", {}).get("tools_ok"):
        items.append("Use semantic exports only for section classification and description-area detection.")
    else:
        items.append("Semantic exporters are unavailable; treat this as optional after object/SVG validation.")
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Score availability and usefulness of a PPTX multi-artifact pack.")
    parser.add_argument("--pack-dir", type=Path, default=Path("docs/converter-bakeoff/current-test"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    selected = selected_exporters(args.pack_dir)
    scores = {
        "powerpoint": renderer_score(args.pack_dir, "powerpoint") if selected is None or "powerpoint" in selected else {"status": "skipped"},
        "powerpoint_com_structure": powerpoint_com_structure_score(args.pack_dir)
        if selected is None or "powerpoint-com-structure" in selected
        else {"status": "skipped"},
        "aspose": aspose_score(args.pack_dir) if selected is None or "aspose" in selected else {"status": "skipped"},
        "libreoffice": renderer_score(args.pack_dir, "libreoffice") if selected is None or "libreoffice" in selected else {"status": "skipped"},
        "ai_semantic": ai_semantic_score(args.pack_dir) if selected is None or "ai-semantic" in selected else {"status": "skipped"},
    }
    report = status_manifest(
        kind="pptx-multi-artifact-pack-score",
        source=args.pack_dir,
        out_dir=args.pack_dir,
        status="ok",
        details={
            "scores": scores,
            "recommendation": recommendation(scores),
            "gate_notes": [
                "Do not accept full-slide raster backgrounds as product success.",
                "Prefer converters that expose z-order, stable IDs, resolved colors, text runs, and table/cell structure.",
                "SVG is an analysis artifact unless text and object identity can be mapped back to native Figma nodes.",
            ],
        },
    )
    out = args.out or (args.pack_dir / "artifact-pack.score.json")
    write_json(out, report)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
