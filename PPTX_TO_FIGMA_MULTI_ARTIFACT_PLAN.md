# PPTX to Figma Multi-Artifact Plan

## 1. Context

This project is trying to convert PPTX files into Figma outputs that can be used for continued planning, editing, and management.

The important product requirement is not just visual similarity. The output must preserve usable Figma structure:

- editable text,
- editable tables/cells where possible,
- manageable card/panel/description areas,
- stable source mapping,
- reliable z-order,
- colors and typography close enough for practical continuation,
- no dependency on full-slide raster backgrounds as the product result.

Previous attempts spent significant time improving a rule/parser-driven PPTX to Figma path. The recurring failures were:

- z-index and layer order remained unreliable,
- colors and theme resolution were inconsistent,
- right-side description/common areas were not detected or reconstructed consistently,
- screenshot/PDF feedback loops did not reliably fix the structural problems,
- image/PDF fallback made visual scores look good but did not solve editability or management.

Because of this, continuing the same loop is high risk.

## 2. Current Conclusion

The main direction should not be:

```text
PPTX -> PDF image -> Figma image/reference layer -> editable overlay
```

That path is useful for visual QA, but it does not satisfy the product requirement by itself.

The revised direction is:

```text
PPTX
  -> multiple extraction/rendering artifacts
  -> compare which artifact best preserves structure
  -> choose or combine sources
  -> reconstruct native Figma structure
```

PDF/PNG remains useful only as a visual oracle, not as the product output.

## 3. Why Multi-Artifact

No single conversion artifact is enough:

- PDF/PNG preserves appearance but loses editable structure.
- Markdown is AI-friendly but loses precise layout, z-order, shapes, and colors.
- SVG is more machine-readable than PDF and preserves draw order/paths, but importing SVG directly into Figma does not guarantee editable native text or table structure.
- OOXML/PPTX has the source truth but is complex and hard to resolve correctly.
- Figma native PPTX import has documented limits, especially fonts, tables, and diagrams.
- PowerPoint itself has the strongest knowledge of how the deck should render, but COM/server automation is fragile in corporate environments.
- Aspose.Slides may provide a useful commercial extraction/rendering layer, but it requires installation/licensing approval.

Therefore the planned input should be an AI-readable artifact pack:

```text
PowerPoint or Graph PDF/PNG reference
Aspose SVG/object JSON if available
PowerPoint COM structure JSON if available
LibreOffice comparison output if available
Docling/Unstructured/MarkItDown semantic output if available
Raw OOXML/current parser intermediate
```

## 4. External Evidence Summary

Research points:

- Figma PPTX import supports importing PPTX into Figma Slides, but Figma documents that fonts are changed to Inter and unsupported objects include tables and diagrams. Those must be recreated or uploaded as screenshots.
- Microsoft documents PowerPoint save/export formats such as PDF and PNG, but Microsoft also warns that unattended Office automation is risky and unsupported in server-side scenarios.
- Microsoft Graph can convert Office files such as PPTX to PDF, which is useful for a visual reference, but it does not provide editable Figma-ready structure.
- Aspose.Slides documentation shows support for accessing shapes, changing/retrieving shape order, getting interop shape IDs, exporting slides/shapes to SVG, and controlling SVG options such as text vectorization.
- LibreOffice can open PPTX and export PDF/graphics, but PPTX fidelity can differ from Microsoft PowerPoint, so it should be a comparison renderer, not the authority.
- Docling, Unstructured, and MarkItDown are useful for AI ingestion/semantic extraction, but they should not be geometry or z-order authorities.

## 5. Product Direction

The product should be framed as:

```text
PPTX -> native Figma reconstruction
```

not:

```text
PPTX -> visual clone image in Figma
```

Native Figma reconstruction means:

- text becomes Figma TextNode,
- tables become frame/cell/text structures or future table-compatible adapters,
- recurring UI/description regions become managed frame/component-like structures,
- images remain images only when they are true source images,
- full-slide raster fallback is debug/reference only,
- every node should preserve source lineage where possible.

## 6. Bakeoff Goal

Before further converter work, run a converter bakeoff to answer:

1. Which source exposes the best z-order?
2. Which source exposes stable IDs?
3. Which source exposes resolved colors?
4. Which source exposes text runs and tables?
5. Which source provides the best AI-readable visual artifact?
6. Which source is practical in the corporate environment without blocked installs?

The bakeoff should prevent repeating weeks of work on a weak input layer.

## 7. Implemented Tracking Files

The plan is split into separate code files so each path can be tracked independently.

```text
scripts/pptx_artifact_common.py
scripts/export_powerpoint_artifacts.py
scripts/export_powerpoint_com_structure.py
scripts/export_aspose_artifacts.py
scripts/export_libreoffice_artifacts.py
scripts/export_ai_semantic_artifacts.py
scripts/build_multi_artifact_pack.py
scripts/score_multi_artifact_pack.py
docs/pptx-multi-artifact-bakeoff-plan.md
```

Purpose:

- `pptx_artifact_common.py`: shared JSON/manifest/command helpers.
- `export_powerpoint_artifacts.py`: PowerPoint PDF/PNG reference export.
- `export_powerpoint_com_structure.py`: no-pip PowerPoint COM structure extraction.
- `export_aspose_artifacts.py`: Aspose SVG/object JSON export.
- `export_libreoffice_artifacts.py`: LibreOffice comparison export.
- `export_ai_semantic_artifacts.py`: Docling/Unstructured/MarkItDown semantic export.
- `build_multi_artifact_pack.py`: orchestrates selected exporters.
- `score_multi_artifact_pack.py`: scores available artifacts and records recommendations.

## 8. Current Environment Findings

In the current corporate machine:

- `pip install` is blocked by company policy.
- `aspose.slides` is not installed.
- LibreOffice is not available on PATH.
- MarkItDown, Docling, and Unstructured are not installed.
- PowerPoint COM path was added as a no-install fallback.
- PowerPoint COM script can execute, but the first run returned:

```text
slide_count: 0
shape_count: 0
```

This likely means PowerPoint did not fully open the file through COM, or a corporate/PowerPoint prompt blocked normal access.

Recommended local check:

1. Close all PowerPoint windows.
2. Open `sampling/current-test.pptx` manually in PowerPoint.
3. Clear any Protected View, Enable Editing, repair, trust, login, or security prompt.
4. Close the file.
5. Rerun the PowerPoint COM structure extraction.

## 9. Current Commands

From the project root:

```powershell
cd C:\Users\A85378\Desktop\project\cnsatlas
```

No-install PowerPoint COM structure extraction:

```powershell
python scripts\build_multi_artifact_pack.py --pptx sampling\current-test.pptx --out-dir docs\converter-bakeoff\current-test --only powerpoint-com-structure
python scripts\score_multi_artifact_pack.py --pack-dir docs\converter-bakeoff\current-test
```

If a machine allows package installation and Aspose is available:

```powershell
python scripts\build_multi_artifact_pack.py --pptx sampling\current-test.pptx --out-dir docs\converter-bakeoff\current-test --slides 1-8 --skip powerpoint
python scripts\score_multi_artifact_pack.py --pack-dir docs\converter-bakeoff\current-test
```

Full bakeoff, only if all tools are available:

```powershell
python scripts\build_multi_artifact_pack.py --pptx sampling\current-test.pptx --out-dir docs\converter-bakeoff\current-test --slides 1-8
python scripts\score_multi_artifact_pack.py --pack-dir docs\converter-bakeoff\current-test
```

## 10. Expected Output Files

Main index:

```text
docs/converter-bakeoff/current-test/artifact-pack.index.json
```

Score:

```text
docs/converter-bakeoff/current-test/artifact-pack.score.json
```

PowerPoint COM structure:

```text
docs/converter-bakeoff/current-test/powerpoint-com-structure/powerpoint-com-structure.json
docs/converter-bakeoff/current-test/powerpoint-com-structure/powerpoint-com-structure.manifest.json
```

Aspose, if available:

```text
docs/converter-bakeoff/current-test/aspose/aspose-shapes.json
docs/converter-bakeoff/current-test/aspose/svg/
docs/converter-bakeoff/current-test/aspose/shape-svg/
docs/converter-bakeoff/current-test/aspose/aspose.manifest.json
```

## 11. Decision Gates

### Gate A. Structure Source

Candidate extractor must expose:

- slide count,
- shape count,
- shape IDs,
- z-order,
- bounds,
- rotation,
- fill/stroke colors,
- text content,
- text runs if possible,
- table/cell structure if possible.

If `shape_count` is zero, that source is not usable yet.

### Gate B. Visual Oracle

PowerPoint or Graph-rendered PDF/PNG is preferred as the visual oracle.

LibreOffice can be used only as a comparison renderer unless it proves closer than PowerPoint for the target deck, which is unlikely.

### Gate C. AI Role

AI should not generate arbitrary final Figma nodes from screenshots.

AI should receive:

```text
reference image
SVG XML
shape/object JSON
semantic JSON/Markdown
current reconstruction JSON
```

AI should then classify structure and propose constrained reconstruction decisions.

### Gate D. Product Acceptance

Reject outputs that depend on:

- full-slide raster background as product output,
- source-less generated shapes,
- loss of editable text/table structures,
- loss of mapping back to the PPTX object.

Accept only if native Figma structure is good enough for planning continuation.

## 12. Possible Outcomes

### Outcome 1. PowerPoint COM Works

If PowerPoint COM returns non-zero shapes and reliable z-order/color/text/table data, use it as the corporate no-install bakeoff source.

This is not ideal for server automation, but it is useful for deciding what the correct structure should be.

### Outcome 2. Aspose Works on Another Machine

If Aspose returns strong object JSON and SVG, it becomes the leading candidate for a production extraction layer, subject to licensing/IT approval.

### Outcome 3. Only OOXML Is Available

If no external converter is allowed, the project must continue with raw PPTX/OOXML parsing, but the strategy should change:

- stop relying on screenshot correction,
- improve resolved model and source mapping,
- use PowerPoint-rendered reference only for QA,
- prioritize semantic reconstruction over exact visual cloning.

### Outcome 4. Exact PPT Clone Remains Unstable

If z-order/color/detail cannot be stabilized even with better artifacts, the product should pivot from exact clone to semantic reconstruction:

```text
PPTX -> planning-friendly Figma structure
```

rather than:

```text
PPTX -> pixel-identical editable Figma clone
```

## 13. Immediate Next Step

Run the no-install PowerPoint COM extraction after manually opening and trusting the PPTX in PowerPoint:

```powershell
python scripts\build_multi_artifact_pack.py --pptx sampling\current-test.pptx --out-dir docs\converter-bakeoff\current-test --only powerpoint-com-structure
python scripts\score_multi_artifact_pack.py --pack-dir docs\converter-bakeoff\current-test
```

Then inspect:

```text
docs/converter-bakeoff/current-test/artifact-pack.score.json
```

The important values are:

```text
powerpoint_com_structure.shape_count
powerpoint_com_structure.text_shape_count
powerpoint_com_structure.z_order_shape_count
powerpoint_com_structure.id_shape_count
```

If those remain zero, external continuation should focus on an environment where Aspose or another approved converter can be installed and tested.
