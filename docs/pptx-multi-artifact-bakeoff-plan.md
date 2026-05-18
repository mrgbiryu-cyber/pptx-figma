# PPTX Multi-Artifact Bakeoff Plan

## Goal

Decide whether the current PPTX parser should remain the primary source, or whether a converter-backed artifact pack should become the new input layer for Figma reconstruction.

The product goal is not full-slide raster fidelity. The product goal is editable and manageable Figma structure with reliable source mapping.

## Principle

Use several artifacts for different jobs:

- PowerPoint/Graph PDF or PNG: visual reference only.
- Aspose object JSON: candidate source for z-order, shape IDs, resolved geometry, text, colors, and tables.
- Aspose SVG: AI-readable visual/layout artifact and shape-level debug artifact.
- LibreOffice PDF/SVG: open-source comparison renderer, not authority.
- Docling/Unstructured/MarkItDown: semantic section hints, not geometry authority.
- Raw OOXML/current intermediate: lineage fallback and cross-check source.

## Code Files

- `scripts/pptx_artifact_common.py`: shared manifest, JSON, command, and slide-range helpers.
- `scripts/export_powerpoint_artifacts.py`: local PowerPoint PDF/PNG reference export.
- `scripts/export_aspose_artifacts.py`: Aspose slide SVG, shape SVG, and object JSON export.
- `scripts/export_libreoffice_artifacts.py`: LibreOffice PDF comparison export.
- `scripts/export_ai_semantic_artifacts.py`: Docling, Unstructured, and MarkItDown semantic exports.
- `scripts/build_multi_artifact_pack.py`: orchestrates all exporters into one tracked artifact pack.
- `scripts/score_multi_artifact_pack.py`: scores artifact availability and recommends which source to trust.

## First Run

```powershell
cd C:\Users\A85378\Desktop\project\cnsatlas
python scripts\build_multi_artifact_pack.py --pptx sampling\current-test.pptx --out-dir docs\converter-bakeoff\current-test --slides 1-8
python scripts\score_multi_artifact_pack.py --pack-dir docs\converter-bakeoff\current-test
```

If optional tools are not installed, the exporter writes a manifest with `missing_tool` or `failed`. That is intentional; the first pass is a tool readiness map.

## Decision Gates

### Gate 1. Structure Source

Pass only if a candidate exposes:

- stable slide/shape IDs,
- z-order for most shapes,
- shape bounds and rotation,
- resolved fills/strokes,
- text and run-level formatting,
- table/cell structure.

### Gate 2. Visual Oracle

PowerPoint or Graph render is preferred as the visual oracle. LibreOffice can be a comparison renderer but not the authority.

### Gate 3. AI Input

AI should receive:

- reference image,
- SVG XML,
- shape JSON,
- semantic JSON/Markdown,
- current reconstruction JSON.

AI should not generate arbitrary final Figma nodes. It should classify patterns and propose constrained reconstruction decisions.

### Gate 4. Product Acceptance

Reject if the output depends on full-slide raster backgrounds. Accept only if native Figma nodes preserve text, tables, cards, source mapping, and operational editability.

## Expected Outcome

The bakeoff should tell us whether to:

1. replace the current extractor with Aspose-backed extraction,
2. keep current OOXML extraction and use Aspose only as a validator,
3. use SVG as a visual/AI artifact only,
4. stop pursuing exact PPT clone fidelity and shift to semantic reconstruction.
