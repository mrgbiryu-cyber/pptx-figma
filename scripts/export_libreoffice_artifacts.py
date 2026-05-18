#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from pptx_artifact_common import find_executable, run_command, status_manifest, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Export LibreOffice comparison artifacts.")
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    soffice = find_executable(["soffice", "libreoffice"])
    if not soffice:
        manifest = status_manifest(
            kind="libreoffice-artifacts",
            source=args.pptx,
            out_dir=args.out_dir,
            status="missing_tool",
            details={"hint": "Install LibreOffice and ensure soffice/libreoffice is on PATH."},
        )
        write_json(args.out_dir / "libreoffice.manifest.json", manifest)
        print(args.out_dir / "libreoffice.manifest.json")
        return 1

    pdf_result = run_command(
        [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(args.out_dir),
            str(args.pptx),
        ],
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    manifest = status_manifest(
        kind="libreoffice-artifacts",
        source=args.pptx,
        out_dir=args.out_dir,
        status="ok" if pdf_result.get("status") in {"ok", "dry_run"} else "failed",
        details={
            "tool": soffice,
            "intended_outputs": ["*.pdf"],
            "pdf_command_result": pdf_result,
            "notes": [
                "LibreOffice is a comparison renderer, not the primary authority.",
                "PPTX fidelity may differ from Microsoft PowerPoint, especially fonts, effects, and embedded vector formats.",
            ],
        },
    )
    write_json(args.out_dir / "libreoffice.manifest.json", manifest)
    print(args.out_dir / "libreoffice.manifest.json")
    return 0 if manifest["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
