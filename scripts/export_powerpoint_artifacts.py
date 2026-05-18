#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pptx_artifact_common import run_command, status_manifest, write_json


POWERSHELL_EXPORT = r"""
$ErrorActionPreference = 'Stop'
$pptx = Resolve-Path -LiteralPath $env:PPTX_INPUT
$outDir = $env:PPTX_OUT
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$app = New-Object -ComObject PowerPoint.Application
$presentation = $null
try {
  $presentation = $app.Presentations.Open($pptx.Path, $true, $true, $false)
  $pdf = Join-Path $outDir 'reference.powerpoint.pdf'
  $presentation.SaveAs($pdf, 32)
  $pngDir = Join-Path $outDir 'png'
  New-Item -ItemType Directory -Force -Path $pngDir | Out-Null
  $presentation.Export($pngDir, 'PNG', 0, 0)
}
finally {
  if ($presentation -ne $null) { $presentation.Close() }
  $app.Quit()
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Export PowerPoint-native reference PDF/PNG artifacts.")
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    env_prefix = (
        f"$env:PPTX_INPUT={json.dumps(str(args.pptx.resolve()))}; "
        f"$env:PPTX_OUT={json.dumps(str(args.out_dir.resolve()))}; "
    )
    result = run_command(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            env_prefix + POWERSHELL_EXPORT,
        ],
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    status = "ok" if result.get("status") in {"ok", "dry_run"} else "failed"
    manifest = status_manifest(
        kind="powerpoint-artifacts",
        source=args.pptx,
        out_dir=args.out_dir,
        status=status,
        details={
            "tool": "Microsoft PowerPoint COM",
            "intended_outputs": ["reference.powerpoint.pdf", "png/*.PNG"],
            "command_result": result,
            "notes": [
                "Best local visual baseline when PowerPoint is installed.",
                "Not recommended as unattended server-side product automation.",
            ],
        },
    )
    write_json(args.out_dir / "powerpoint.manifest.json", manifest)
    print(args.out_dir / "powerpoint.manifest.json")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
