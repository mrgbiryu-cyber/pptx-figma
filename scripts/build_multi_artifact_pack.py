#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from pptx_artifact_common import read_json, run_command, status_manifest, write_json


EXPORTERS = {
    "powerpoint": {
        "script": "scripts/export_powerpoint_artifacts.py",
        "out": "powerpoint",
        "manifest": "powerpoint.manifest.json",
    },
    "powerpoint-com-structure": {
        "script": "scripts/export_powerpoint_com_structure.py",
        "out": "powerpoint-com-structure",
        "manifest": "powerpoint-com-structure.manifest.json",
    },
    "aspose": {
        "script": "scripts/export_aspose_artifacts.py",
        "out": "aspose",
        "manifest": "aspose.manifest.json",
    },
    "libreoffice": {
        "script": "scripts/export_libreoffice_artifacts.py",
        "out": "libreoffice",
        "manifest": "libreoffice.manifest.json",
    },
    "ai-semantic": {
        "script": "scripts/export_ai_semantic_artifacts.py",
        "out": "ai-semantic",
        "manifest": "ai-semantic.manifest.json",
    },
}


def run_exporter(
    name: str,
    config: dict[str, str],
    pptx: Path,
    base_dir: Path,
    slides: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    out_dir = base_dir / config["out"]
    command = [sys.executable, config["script"], "--pptx", str(pptx), "--out-dir", str(out_dir)]
    if dry_run:
        command.append("--dry-run")
    if slides and name == "aspose":
        command.extend(["--slides", slides])
    result = run_command(command, timeout=900)
    manifest_path = out_dir / config["manifest"]
    manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            manifest = read_json(manifest_path)
        except Exception as exc:
            manifest = {"status": "manifest_read_error", "error": str(exc)}
    return {
        "name": name,
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "manifest_status": (manifest or {}).get("status"),
        "command_status": result.get("status"),
        "returncode": result.get("returncode"),
        "command": result.get("command"),
        "stderr": result.get("stderr"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a PPTX multi-artifact pack for converter bakeoff.")
    parser.add_argument("--pptx", type=Path, default=Path("sampling/current-test.pptx"))
    parser.add_argument("--out-dir", type=Path, default=Path("docs/converter-bakeoff/current-test"))
    parser.add_argument("--slides", help="Optional slide range passed to exporters that support it.")
    parser.add_argument("--only", help="Comma-separated exporters: powerpoint,aspose,libreoffice,ai-semantic")
    parser.add_argument("--skip", help="Comma-separated exporters to skip.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    only = {item.strip() for item in str(args.only or "").split(",") if item.strip()}
    skip = {item.strip() for item in str(args.skip or "").split(",") if item.strip()}
    selected = {
        name: config
        for name, config in EXPORTERS.items()
        if (not only or name in only) and name not in skip
    }

    rows = [
        run_exporter(name, config, args.pptx.resolve(), args.out_dir.resolve(), args.slides, args.dry_run)
        for name, config in selected.items()
    ]
    status = "ok" if any(row.get("manifest_status") == "ok" for row in rows) else "needs_tools"
    pack = status_manifest(
        kind="pptx-multi-artifact-pack",
        source=args.pptx,
        out_dir=args.out_dir,
        status=status,
        details={
            "slides": args.slides,
            "dry_run": args.dry_run,
            "exporters": rows,
            "next_step": "Run score_multi_artifact_pack.py to compare structure availability before choosing the primary converter.",
        },
    )
    write_json(args.out_dir / "artifact-pack.index.json", pack)
    print(args.out_dir / "artifact-pack.index.json")
    return 0 if status == "ok" or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
