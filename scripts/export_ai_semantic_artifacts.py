#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from typing import Any

from pptx_artifact_common import run_command, status_manifest, write_json


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def export_markitdown(pptx: Path, out_dir: Path, dry_run: bool) -> dict[str, Any]:
    md_path = out_dir / "markitdown.md"
    if dry_run:
        return {"status": "dry_run", "output": str(md_path)}
    if not module_available("markitdown"):
        return {"status": "missing_tool", "tool": "markitdown"}
    try:
        from markitdown import MarkItDown  # type: ignore

        result = MarkItDown().convert(str(pptx))
        md_path.write_text(str(result.text_content or ""), encoding="utf-8")
        return {"status": "ok", "output": str(md_path), "characters": len(result.text_content or "")}
    except Exception as exc:
        return {"status": "failed", "tool": "markitdown", "error": str(exc)}


def export_docling(pptx: Path, out_dir: Path, dry_run: bool) -> dict[str, Any]:
    md_path = out_dir / "docling.md"
    json_path = out_dir / "docling.json"
    if dry_run:
        return {"status": "dry_run", "outputs": [str(md_path), str(json_path)]}
    if not module_available("docling"):
        return {"status": "missing_tool", "tool": "docling"}
    try:
        from docling.document_converter import DocumentConverter  # type: ignore

        result = DocumentConverter().convert(str(pptx))
        document = result.document
        md_path.write_text(document.export_to_markdown(), encoding="utf-8")
        if hasattr(document, "export_to_dict"):
            write_json(json_path, {"kind": "docling-document", "document": document.export_to_dict()})
        return {"status": "ok", "outputs": [str(md_path), str(json_path)]}
    except Exception as exc:
        return {"status": "failed", "tool": "docling", "error": str(exc)}


def export_unstructured(pptx: Path, out_dir: Path, dry_run: bool) -> dict[str, Any]:
    json_path = out_dir / "unstructured.json"
    if dry_run:
        return {"status": "dry_run", "output": str(json_path)}
    if not module_available("unstructured"):
        return {"status": "missing_tool", "tool": "unstructured"}
    try:
        from unstructured.partition.pptx import partition_pptx  # type: ignore

        elements = partition_pptx(filename=str(pptx), include_page_breaks=True)
        rows: list[dict[str, Any]] = []
        for element in elements:
            metadata = getattr(element, "metadata", None)
            rows.append(
                {
                    "category": getattr(element, "category", type(element).__name__),
                    "text": str(element),
                    "metadata": metadata.to_dict() if hasattr(metadata, "to_dict") else {},
                }
            )
        write_json(json_path, {"kind": "unstructured-elements", "elements": rows})
        return {"status": "ok", "output": str(json_path), "element_count": len(rows)}
    except Exception as exc:
        return {"status": "failed", "tool": "unstructured", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export AI-ingestion semantic artifacts from PPTX.")
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    details = {
        "markitdown": export_markitdown(args.pptx, args.out_dir, args.dry_run),
        "docling": export_docling(args.pptx, args.out_dir, args.dry_run),
        "unstructured": export_unstructured(args.pptx, args.out_dir, args.dry_run),
    }
    ok_count = sum(1 for row in details.values() if row.get("status") in {"ok", "dry_run"})
    status = "ok" if ok_count > 0 else "missing_tool"
    manifest = status_manifest(
        kind="ai-semantic-artifacts",
        source=args.pptx,
        out_dir=args.out_dir,
        status=status,
        details=details,
    )
    write_json(args.out_dir / "ai-semantic.manifest.json", manifest)
    print(args.out_dir / "ai-semantic.manifest.json")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
