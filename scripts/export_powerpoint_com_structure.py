#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pptx_artifact_common import run_command, status_manifest, write_json


POWERSHELL_STRUCTURE_EXPORT = r"""
$ErrorActionPreference = 'Stop'
$pptx = Resolve-Path -LiteralPath $env:PPTX_INPUT
$outDir = $env:PPTX_OUT
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$outJson = Join-Path $outDir 'powerpoint-com-structure.json'

function Get-ColorValue($colorFormat) {
  try {
    $rgb = [int]$colorFormat.RGB
    return @{
      rgb = $rgb
      hex = ('{0:X6}' -f ($rgb -band 0xFFFFFF))
      transparency = $colorFormat.Transparency
    }
  } catch {
    return $null
  }
}

function Get-FillValue($shape) {
  try {
    return @{
      visible = [int]$shape.Fill.Visible
      type = [int]$shape.Fill.Type
      fore_color = Get-ColorValue $shape.Fill.ForeColor
      back_color = Get-ColorValue $shape.Fill.BackColor
      transparency = $shape.Fill.Transparency
    }
  } catch {
    return $null
  }
}

function Get-LineValue($shape) {
  try {
    return @{
      visible = [int]$shape.Line.Visible
      weight = $shape.Line.Weight
      dash_style = [int]$shape.Line.DashStyle
      fore_color = Get-ColorValue $shape.Line.ForeColor
      transparency = $shape.Line.Transparency
    }
  } catch {
    return $null
  }
}

function Get-TextValue($shape) {
  try {
    if (-not $shape.HasTextFrame) { return $null }
    if (-not $shape.TextFrame.HasText) { return $null }
    $range = $shape.TextFrame.TextRange
    $runs = @()
    for ($i = 1; $i -le $range.Runs().Count; $i++) {
      $run = $range.Runs($i)
      $runs += @{
        text = $run.Text
        start = $run.Start
        length = $run.Length
        font_name = $run.Font.Name
        font_size = $run.Font.Size
        bold = [int]$run.Font.Bold
        italic = [int]$run.Font.Italic
        color = Get-ColorValue $run.Font.Color
      }
    }
    return @{
      text = $range.Text
      orientation = [int]$shape.TextFrame.Orientation
      margin_left = $shape.TextFrame.MarginLeft
      margin_right = $shape.TextFrame.MarginRight
      margin_top = $shape.TextFrame.MarginTop
      margin_bottom = $shape.TextFrame.MarginBottom
      auto_size = [int]$shape.TextFrame.AutoSize
      word_wrap = [int]$shape.TextFrame.WordWrap
      runs = $runs
    }
  } catch {
    return @{ error = $_.Exception.Message }
  }
}

function Get-TableValue($shape) {
  try {
    if (-not $shape.HasTable) { return $null }
    $table = $shape.Table
    $rows = @()
    for ($r = 1; $r -le $table.Rows.Count; $r++) {
      $cells = @()
      for ($c = 1; $c -le $table.Columns.Count; $c++) {
        $cell = $table.Cell($r, $c)
        $cellShape = $cell.Shape
        $cells += @{
          row = $r
          column = $c
          selected = $false
          text = Get-TextValue $cellShape
          fill = Get-FillValue $cellShape
          line = Get-LineValue $cellShape
        }
      }
      $rows += @{ index = $r; cells = $cells }
    }
    return @{
      row_count = $table.Rows.Count
      column_count = $table.Columns.Count
      rows = $rows
    }
  } catch {
    return @{ error = $_.Exception.Message }
  }
}

function Get-ShapeRows($shapes, $slideNo, $parentPath) {
  $rows = @()
  for ($i = 1; $i -le $shapes.Count; $i++) {
    $shape = $shapes.Item($i)
    $path = if ($parentPath) { "$parentPath/$i" } else { "$i" }
    $autoShapeType = $null
    $hasTextFrame = $false
    $hasTable = $false
    try { $autoShapeType = [int]$shape.AutoShapeType } catch {}
    try { $hasTextFrame = [bool]$shape.HasTextFrame } catch {}
    try { $hasTable = [bool]$shape.HasTable } catch {}
    $row = @{
      slide_no = $slideNo
      path = $path
      z_index = $i - 1
      id = $shape.Id
      name = $shape.Name
      type = [int]$shape.Type
      auto_shape_type = $autoShapeType
      left = $shape.Left
      top = $shape.Top
      width = $shape.Width
      height = $shape.Height
      rotation = $shape.Rotation
      visible = [int]$shape.Visible
      lock_aspect_ratio = [int]$shape.LockAspectRatio
      has_text_frame = $hasTextFrame
      has_table = $hasTable
      fill = Get-FillValue $shape
      line = Get-LineValue $shape
      text = Get-TextValue $shape
      table = Get-TableValue $shape
    }
    $rows += $row
    try {
      if ($shape.Type -eq 6) {
        $rows += Get-ShapeRows $shape.GroupItems $slideNo $path
      }
    } catch {}
  }
  return $rows
}

$app = New-Object -ComObject PowerPoint.Application
$presentation = $null
try {
  $presentation = $app.Presentations.Open($pptx.Path, $true, $false, $true)
  $slides = @()
  $allShapes = @()
  for ($s = 1; $s -le $presentation.Slides.Count; $s++) {
    $slide = $presentation.Slides.Item($s)
    $shapeRows = Get-ShapeRows $slide.Shapes $s ''
    $slides += @{
      slide_no = $s
      slide_id = $slide.SlideID
      name = $slide.Name
      width = $presentation.PageSetup.SlideWidth
      height = $presentation.PageSetup.SlideHeight
      shape_count = $slide.Shapes.Count
    }
    $allShapes += $shapeRows
  }
  $payload = @{
    kind = 'powerpoint-com-structure'
    source = $pptx.Path
    slide_count = $presentation.Slides.Count
    slide_width = $presentation.PageSetup.SlideWidth
    slide_height = $presentation.PageSetup.SlideHeight
    slides = $slides
    shapes = $allShapes
  }
  $payload | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $outJson -Encoding UTF8
}
finally {
  try {
    if ($presentation -ne $null) {
      $presentation.Saved = $true
      $presentation.Close()
    }
  } catch {
    Write-Warning ("PowerPoint close warning: " + $_.Exception.Message)
  }
  try {
    $app.Quit()
  } catch {
    Write-Warning ("PowerPoint quit warning: " + $_.Exception.Message)
  }
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Export PowerPoint COM structure JSON without Python package installs.")
    parser.add_argument("--pptx", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
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
            env_prefix + POWERSHELL_STRUCTURE_EXPORT,
        ],
        timeout=args.timeout,
        dry_run=args.dry_run,
    )
    status = "ok" if result.get("status") in {"ok", "dry_run"} else "failed"
    manifest = status_manifest(
        kind="powerpoint-com-structure-artifacts",
        source=args.pptx,
        out_dir=args.out_dir,
        status=status,
        details={
            "tool": "Microsoft PowerPoint COM",
            "intended_outputs": ["powerpoint-com-structure.json"],
            "command_result": result,
            "notes": [
                "No pip package install required.",
                "PowerPoint is opened with a visible window because hidden COM opens are unreliable in locked-down desktops.",
                "Best used as a corporate-workstation bakeoff extractor, not as unattended server automation.",
            ],
        },
    )
    write_json(args.out_dir / "powerpoint-com-structure.manifest.json", manifest)
    print(args.out_dir / "powerpoint-com-structure.manifest.json")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
