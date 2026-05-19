# PPTX to Figma AI Reconstruction Plan

## 1. Purpose

This document defines what the AI layer should do after the PPTX multi-artifact bakeoff.

The other plan, `PPTX_TO_FIGMA_MULTI_ARTIFACT_PLAN.md`, decides which extraction artifacts are trustworthy.

This plan starts after that point:

```text
PPTX artifact pack
-> AI-readable slide context
-> constrained reconstruction plan
-> gate
-> Figma operation bundle
-> Figma execution
-> visual/usability feedback
```

The goal is not to ask AI to freely draw a slide from a screenshot.

The goal is to use AI as a structured planner that decides how to reconstruct native, editable Figma nodes from evidence.

## 2. Product Goal

The product output must be planner-usable Figma structure:

- editable text nodes
- editable table-like groups where possible
- manageable cards, panels, and description areas
- source mapping back to PPTX objects or artifact IDs
- stable z-order and bounds
- fallback only where native reconstruction is unsafe

Full-slide raster backgrounds are allowed only as debug/reference artifacts, not as the product output.

## 3. AI Responsibilities

AI should do these jobs:

1. Classify each slide type.
2. Identify major visual/semantic regions.
3. Decide which regions should be reconstructed as native Figma nodes.
4. Decide which regions need fallback placeholders.
5. Produce a bounded reconstruction plan with source IDs.
6. Explain uncertainty and validation checks.

AI should not:

- create arbitrary source-less nodes
- ignore parser/artifact evidence
- use screenshots as the only source of truth
- output final Figma API calls directly
- keep retrying without a stop condition

## 4. Inputs To AI

Preferred AI input bundle per slide:

```text
reference image or PDF-rendered image
artifact-pack index and score
best available shape/object JSON
SVG XML or SVG summary if available
semantic markdown/JSON if available
raw OOXML/current parser intermediate
current reconstruction JSON if retrying
previous Figma screenshot and feedback if retrying
```

Minimum input when optional tools are unavailable:

```text
current PPTX OOXML parser output
PPTX-rendered reference image if available
slide inventory
page type hints
```

## 5. AI Slide Context

Before asking for a reconstruction plan, convert raw artifacts into an AI-friendly context.

Suggested schema:

```json
{
  "schema": "ai_slide_context_v1",
  "source_pptx": "sampling/current-test.pptx",
  "slide_no": 1,
  "canvas": { "width": 1280, "height": 720, "unit": "px" },
  "artifact_sources": [],
  "page_type_candidates": [],
  "objects": [],
  "text_runs": [],
  "tables": [],
  "images": [],
  "visual_regions": [],
  "source_quality": {},
  "known_risks": []
}
```

This context should be compact enough for model input, but preserve:

- IDs
- z-order
- bounds
- text
- styles
- table/cell structure
- source artifact path

## 6. Reconstruction Plan Output

AI must output JSON, not prose.

Suggested schema:

```json
{
  "schema": "figma_reconstruction_plan_v1",
  "slide_no": 1,
  "page_type": "flow-process | table-heavy | ui-mockup | dense-ui-panel | generic",
  "summary": "",
  "regions": [],
  "operations": [],
  "fallbacks": [],
  "validation": [],
  "confidence": 0.0
}
```

Each operation must include:

```json
{
  "id": "op:region:1",
  "type": "create_frame | create_text | create_table | create_connectors | create_image_placeholder | fallback_region",
  "name": "",
  "bounds": { "x": 0, "y": 0, "width": 100, "height": 50 },
  "source_ids": [],
  "reason": "",
  "confidence": 0.0
}
```

Hard rule:

Every operation should have `source_ids` unless it is an explicit fallback or generated container.

## 7. Gate And Stop Conditions

Do not allow infinite AI loops.

Use this default loop policy:

```text
max normal iterations: 3
max hard-model escalation: 1
stop on PASS
stop on region-level FALLBACK when native reconstruction is unsafe
stop on FAIL when required source evidence is missing
```

Gate decisions:

- `PASS`: plan is structurally valid and execution-ready.
- `HOLD`: needs one more normal-model iteration.
- `FALLBACK`: use controlled fallback for unsafe regions.
- `FAIL`: cannot proceed without better source artifacts.

Gate checks:

- schema is valid
- operation count is bounded
- source IDs are present
- page type is recognized
- bounds are inside canvas
- required visual regions are covered
- fallback regions are explicit
- validation notes are concrete

## 8. Figma Operation Bundle

AI plans should not be executed directly.

Convert reconstruction plans into an execution bundle:

```json
{
  "schema": "figma_operation_bundle_v1",
  "slide_no": 1,
  "page_type": "generic",
  "canvas": { "width": 1280, "height": 720, "unit": "px" },
  "target": {},
  "operations": [],
  "execution_policy": {},
  "gate": {}
}
```

The operation bundle is the contract between the AI/build lane and Figma execution lane.

Possible executors:

- Figma MCP
- Figma plugin
- future backend worker

## 9. Model Policy

Default normal model:

```text
google/gemini-3.1-flash-lite
```

Default hard/escalation model:

```text
anthropic/claude-sonnet-4.6
```

Use the fast model for normal structure planning.

Escalate only when:

- the slide is structurally dense
- gate returns `HOLD` repeatedly
- table/flow/UI region classification fails
- source artifacts conflict

Do not use the hard model as the default for every slide.

## 10. Runtime Split

The build lane can require AI/model access.

The company Figma lane should not require:

- pip install
- Aspose
- OpenRouter key
- LibreOffice
- Docling
- Unstructured
- MarkItDown

Company lane should only need:

- git pull or artifact handoff
- Figma account with edit permission
- Figma MCP or plugin executor
- feedback JSON and screenshots/node URLs

## 11. Development Handoff Loop

Recommended current collaboration loop:

```text
local/build environment:
  generate artifact pack
  generate AI slide context
  generate reconstruction plan
  gate plan
  generate figma operation bundle
  create handoff package

company/Figma environment:
  pull latest repo
  run Figma MCP/plugin execution
  capture screenshots or node URLs
  fill feedback JSON
  push feedback

local/build environment:
  pull feedback
  update parser/artifact scoring/AI plan rules
```

## 12. How This Fits The Multi-Artifact Plan

The multi-artifact plan answers:

```text
Which evidence source should we trust?
```

This AI reconstruction plan answers:

```text
Given trusted evidence, how do we reconstruct native Figma nodes safely?
```

The two plans should be connected like this:

```text
artifact-pack.score.json
-> select source authority per concern
   - z-order authority
   - geometry authority
   - text/table authority
   - visual oracle
   - semantic hints
-> ai_slide_context_v1
-> figma_reconstruction_plan_v1
-> figma_operation_bundle_v1
```

## 13. Immediate Next Step For Codex

Implement only the minimum bridge first:

1. Read `docs/converter-bakeoff/current-test/artifact-pack.score.json`.
2. Build `ai_slide_context_v1` from the best available artifacts.
3. Generate one `figma_reconstruction_plan_v1` for a small slide set.
4. Gate it.
5. Convert it into `figma_operation_bundle_v1`.
6. Do not write to Figma until the bundle passes preflight.

The first goal is not visual perfection.

The first goal is a repeatable contract:

```text
artifact evidence -> AI plan -> gated operation bundle
```
