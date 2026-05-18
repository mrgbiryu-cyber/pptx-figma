# PPT to Figma Refactor Plan: Visual Fidelity + Editability

Date: 2026-05-18

## Goal

The product goal is not just to make a PPT look similar in Figma. The goal is to let people who used to work in PowerPoint upload a PPT into Figma and continue working with it comfortably.

This means the converter must produce two different kinds of output:

1. A visual substrate that preserves the slide's appearance: boxes, fills, transparency, shadows, z-order, image crops, and complex decorative shapes.
2. Editable semantic layers that preserve work intent: text, tables, description panels, labels, structured cards, and source mapping.

These two outputs must be separated. A layer should not be forced to be both a perfect visual reproduction and a good editing surface when those goals conflict.

## Current Codebase Findings

The project already extracts more useful PPT structure than it renders.

- `scripts/pptx_inspector.py` extracts table rows, columns, cells, spans, text runs, cell style, text style, alpha, and slide shape z-order.
- `figma-plugin/code.js` replay rendering currently handles `TEXT`, `VECTOR`, `SVG_BLOCK`, `RECTANGLE`, `FRAME`, and `GROUP`; it has no replay-level `EDITABLE_TABLE` or semantic table node.
- The older intermediate renderer has `table`, `table_row`, and `table_cell` handling, but it is absolute-frame oriented and not yet a stable edit-first model.
- `scripts/build_visual_first_replay_bundle.py` still applies overlay heuristics, such as pushing large translucent overlays behind other content. This can help some slides but can also override source z-order.
- `scripts/build_dense_ui_panel_ir_bundle.py` has many slide-29-specific composition rules. This proves the problem is known, but it also means the renderer has become visual-case driven rather than contract driven.

Important files:

- `scripts/pptx_inspector.py`
- `scripts/build_intermediate_candidates.py`
- `scripts/build_visual_first_replay_bundle.py`
- `scripts/build_dense_ui_panel_ir_bundle.py`
- `figma-plugin/code.js`
- `scripts/run_replay_generator_qa_gate.py`

## External Findings

### Figma/PPT import limitations

Figma's own PPTX import still has limitations. In particular, Figma documents that PPT tables are not imported as editable table structures and must be recreated or uploaded as screenshots. This makes editable table reconstruction a valid differentiator for this project.

Reference:
https://help.figma.com/hc/en-us/articles/30601628883607-Import-PowerPoint-files-to-Figma-Slides

### Figma table/editing model

Figma Slides supports table editing concepts such as cell text editing, cell background formatting, alignment, and row/column resizing. The Plugin API also documents `TableNode`, `cellAt`, `resizeRow`, and `resizeColumn`, but `figma.createTable` is documented as FigJam-only. Therefore the first reliable Figma Design implementation should use Auto Layout/frame-based editable table surrogates, not depend on native `TableNode`.

References:
https://help.figma.com/hc/en-us/articles/30600895164439-Add-tables-to-slides
https://developers.figma.com/docs/plugins/api/TableNode/
https://developers.figma.com/docs/plugins/api/figma/

### Z-order and opacity

PowerPoint shape order matters. In `python-pptx`, slide shapes are sequenced back-to-front: first shape is backmost, last shape is topmost. OOXML alpha represents opacity. Figma also has its own render order semantics for layers, groups, effects, fills, strokes, and blurs, and these differences become visible when shapes overlap.

References:
https://python-pptx.readthedocs.io/en/latest/api/shapes.html
https://ooxml.info/docs/20/20.1/20.1.2/20.1.2.3/20.1.2.3.1/
https://help.figma.com/hc/en-us/articles/360041488473-Apply-effects-to-layers

### MCP and AI-friendly Figma structure

Figma MCP guidance strongly favors semantic names, Auto Layout, variables/tokens, components, and smaller logical chunks instead of huge absolute-positioned selections. This maps directly to our target architecture: generate logical editable chunks and use MCP/metadata export as QA, not as the primary converter.

References:
https://developers.figma.com/docs/figma-mcp-server/structure-figma-file/
https://github.com/figma/mcp-server-guide
https://github.com/GLips/Figma-Context-MCP

## Fixed Architecture

### 1. Three-layer output model

Each slide render should be organized as:

1. `visual/reference`
   - Flattened or semi-flattened visual substrate.
   - Can contain SVG, vectors, images, complex backgrounds, shadows, decorative overlays.
   - May be locked or named clearly as reference/background.

2. `editable/content`
   - Native text, editable table surrogate, labels, cards, key shapes.
   - Uses semantic names and plugin data.
   - Optimized for user editing, not perfect pixel reproduction.

3. `mapping/debug`
   - Source lineage, table/cell IDs, z-order diagnostics, confidence, source paths.
   - Stored in plugin data and exportable manifests.

This replaces the current implicit strategy where every node competes in one visual tree.

### 2. Renderer modes

The pipeline should support explicit render intent per chunk:

- `visual_reference`: preserve appearance, flatten if needed.
- `editable_native`: create editable Figma primitives.
- `editable_table`: create an Auto Layout/frame table surrogate.
- `hybrid_overlay`: keep a visual substrate underneath, add editable content above.
- `discard_rebuild`: discard the original visual asset and rebuild cleanly when source assets fight the product goal.

Asset preservation is not a requirement. If an asset prevents clear editing or causes bad overlap/transparency, it can be discarded, flattened, or rebuilt.

### 3. Visual stacking contract

Introduce an explicit `visual_stack` field in IR:

```json
{
  "source_z_order": 17,
  "render_layer": "editable/content",
  "stack_group": "description_panel",
  "stack_index": 120,
  "stack_policy": "source|background|overlay|content|debug"
}
```

Rules:

- `source_z_order` must come from PPT shape-tree order when available.
- Source order is the default authority.
- Heuristics may not silently override source order.
- Any override must write `stack_policy` and `stack_reason`.
- Visual/reference should generally render below editable/content.
- Debug overlays render above both, but only in debug mode.

### 4. Opacity and paint contract

All paints should normalize to:

```json
{
  "paint_type": "solid|image|gradient|none|unsupported",
  "color": {"r": 0, "g": 0, "b": 0},
  "opacity": 1.0,
  "source_alpha": 1.0,
  "source": "srgbClr|schemeClr|sysClr|fallback",
  "fallback_reason": null
}
```

Rules:

- PPT alpha must map to Figma paint opacity, not node opacity, unless the entire group/layer is transparent.
- `noFill` must become no fill, not a white fill with opacity 0.
- Unsupported gradient/pattern/effect can be flattened into `visual/reference`.
- If color resolution fails, the output must keep a fallback reason for QA.
- Text fallback color must consider background luminance.

### 5. Description/table contract

The right-side description area should not be handled as a visual approximation. It should be an editable semantic table/panel.

Target IR:

```json
{
  "type": "EDITABLE_TABLE",
  "name": "DescriptionTable",
  "columns": [{"index": 1, "width": 84}],
  "rows": [{"index": 1, "height": 28}],
  "cells": [
    {
      "row": 1,
      "column": 1,
      "rowSpan": 1,
      "colSpan": 1,
      "text": "..."
    }
  ]
}
```

Figma Design rendering:

- table frame: semantic name, fixed bounds, Auto Layout vertical when possible.
- row frame: horizontal Auto Layout.
- cell frame: fixed width/height, fill/stroke/padding, semantic name.
- cell text: native TextNode, editable, wrapping enabled.
- merged cells: width/height aggregation first; fallback to absolute cell frame when spans are complex.

Figma Slides/FigJam native table can be explored later behind an adapter, but it is not the first implementation target.

## Gap Closure Decisions

The following design gaps must be treated as fixed decisions before implementation starts.

### Runtime target

Primary runtime is Figma Design through the existing plugin path.

- Do not depend on Figma Slides-only or FigJam-only APIs.
- Do not require native `TableNode` creation for the first implementation.
- Keep a future adapter boundary for native table support if Figma Design exposes stable creation APIs later.

### Sample and source-of-truth policy

When the user uploads a sample file, the review must identify:

- source PPTX path
- generated replay bundle path
- generated Figma/plugin output
- screenshot/reference export path
- reviewed slide numbers

If multiple sample files exist, use the newest file under `sampling/` unless a specific filename is provided.

### Layer naming and locking policy

Generated Figma output must use predictable top-level names:

- `visual/reference`
- `editable/content`
- `mapping/debug`

Rules:

- `visual/reference` may be locked after rendering if the plugin/runtime supports it safely.
- `editable/content` must remain selectable and editable.
- `mapping/debug` should be hidden or omitted outside debug mode.
- Editable text and table cells must not be nested inside flattened SVG nodes.

### Selection/editability contract

A converted slide is not acceptable if the user has to ungroup many decorative layers to edit core content.

Required editable units:

- title and major labels
- description/table cell text
- table rows/cells in the right description area
- important card labels and values

Allowed non-editable units:

- complex backgrounds
- decorative patterns
- screenshots/images
- unsupported charts/SmartArt until a later phase

### Effects, masks, and unsupported visuals

The visual substrate builder must classify unsupported visual features instead of silently approximating them.

Feature policy:

- simple solid fill/stroke/opacity: native rectangle/frame
- simple image: native rectangle with image fill
- mask/crop/effect that is important visually: flatten into `visual/reference`
- gradient/pattern/shadow/blur that cannot be represented cleanly: flatten or rebuild
- conflicting overlay that damages editability: discard or rebuild

Every fallback must write `fallback_reason`.

### Font and text policy

Font fidelity is secondary to editable, readable text.

Rules:

- Preserve source font metadata in plugin data even if rendering uses fallback fonts.
- Text fallback color must consider local background luminance.
- Text clipping is a failure for editable/content.
- Dense description text should be split into paragraph/cell text nodes, not one giant text group.

### Coordinate and scaling policy

All generated layers must use one coordinate model per slide.

Rules:

- Preserve native slide size for edit-first output unless a specific export mode requests scaling.
- Store source bounds and rendered bounds separately when scaling occurs.
- Do not mix scaled and native coordinates in the same IR subtree.

### Browser review prerequisites

Browser-driven Figma review requires user-granted access at review time.

Required from the user when we reach Phase 6:

- Figma file URL or clear instructions for opening the target file
- permission to use the logged-in browser/Figma session
- permission to run the local development plugin or import `figma-plugin/manifest.json`
- target sample filename if more than one sample is present

Until those are available, implementation and JSON/static QA can continue locally.

## Refactor Phases

### Phase 0. Baseline and metrics

Create a locked baseline for slides 12, 19, 29, and 34.

Add QA metrics:

- visual diff score
- source z-order preservation score
- transparency preservation score
- overlap anomaly count
- editable text coverage
- editable table cell coverage
- semantic mapping coverage
- flattened text count
- fallback paint count

Completion:

- Metrics can be generated from current output.
- Slide 29 right description area has a separate score.

### Phase 1. Normalize source model

Work:

- Make `source_z_order` a first-class candidate field, not only an incidental `source_order_path`.
- Preserve z-order through group children, table children, and replay bundle nodes.
- Normalize paint objects for fill, stroke, text fill, and table cell fill.
- Add color modifier handling for `schemeClr` where needed.
- Record fallback reasons.

Files:

- `scripts/pptx_inspector.py`
- `scripts/build_intermediate_candidates.py`
- `scripts/ppt_source_extractor.py`

Completion:

- Every rendered node has source lineage and stack metadata.
- Alpha/noFill cases are visible in manifest output.

### Phase 2. Split visual/reference from editable/content

Work:

- Add `render_layer` and `render_intent` to bundle IR.
- Build slide frames with separate top-level children:
  - `visual/reference`
  - `editable/content`
  - `mapping/debug`
- Replace implicit overlay sorting with explicit stack policies.
- Remove or quarantine slide-specific z-order hacks unless they are expressed as chunk policy.

Files:

- `scripts/build_visual_first_replay_bundle.py`
- `scripts/build_dense_ui_panel_ir_bundle.py`
- `figma-plugin/code.js`

Completion:

- A visual substrate can be regenerated independently from editable content.
- Editable content no longer fights background overlays.

### Phase 3. Editable description table renderer

Work:

- Add `EDITABLE_TABLE` IR.
- Generate it from PPT table payload and dense description-panel classification.
- Add `renderReplayEditableTable()` to the Figma plugin.
- Prefer Auto Layout row/cell frames.
- Store `ppt_table_id`, `row_index`, `column_index`, `source_path`, and confidence as plugin data.

Files:

- `scripts/build_dense_ui_panel_ir_bundle.py`
- `scripts/build_visual_first_replay_bundle.py`
- `figma-plugin/code.js`

Completion:

- Slide 29 right description area is editable as rows/cells/text.
- Cell text coverage is at least 95% for the target slide.
- No description text is emitted as SVG text.

### Phase 4. Box/transparency visual substrate rewrite

Work:

- Create a `VisualSubstrateBuilder`.
- Inputs: normalized shapes, images, fills, strokes, effects, z-order.
- Output: stable background/reference layer.
- For complex overlapping boxes, decide per chunk:
  - preserve native rectangles if simple
  - flatten to SVG/image if complex effects or overlapping opacity cannot be represented cleanly
  - discard/rebuild if the source asset is harmful
- Keep editable text/table above substrate.

Completion:

- Box overlap artifacts decrease.
- Transparent overlays no longer cover editable content unexpectedly.
- Fallback decisions are visible in debug manifest.

### Phase 5. QA gate and MCP-assisted review

Work:

- Extend `run_replay_generator_qa_gate.py` with:
  - z-order checks
  - paint opacity checks
  - overlap anomaly checks
  - editable table checks
- Use Figma analysis export and, where available, Figma MCP metadata on the selected description chunk.
- MCP is used to inspect generated Figma structure, not to perform the conversion.

Completion:

- QA fails if description table is flattened.
- QA fails if source opacity is lost without a fallback reason.
- QA fails if content layer is visually below reference layer.

### Phase 6. Browser-driven Figma accuracy review

Work:

- After the user places the sample PPT/Figma files in the sampling area, run the actual Figma/plugin workflow through a browser-controlled review loop where possible.
- Open the generated or imported Figma result in the browser/Figma surface and inspect the real rendered screen, not only exported JSON.
- Capture screenshots for the original/reference state and the generated Figma state.
- Check the following visually and structurally:
  - right description/table area cell count, row grouping, text wrapping, and editability
  - box fill color, stroke, corner radius, and opacity
  - transparent overlays and whether they incorrectly cover editable content
  - z-order of overlapping boxes, labels, icons, and panels
  - text clipping, line height, and readable wrapping
  - whether editable/content layers are selectable independently from visual/reference layers
- Record browser-review findings as a small report under `docs/` or `docs/review-manifests/`.
- Feed any confirmed findings back into the IR/rendering contract instead of making one-off visual patches.

Completion:

- A screenshot-based review exists for the uploaded sample.
- Each mismatch is classified as one of:
  - `visual_substrate`
  - `editable_content`
  - `stacking`
  - `opacity_paint`
  - `text_layout`
  - `source_mapping`
- The review identifies whether to preserve, flatten, discard, or rebuild the problematic source asset/chunk.
- The result is good enough for a PPT user to continue editing in Figma, not merely close in a static screenshot.

## Implementation Order

1. Add source/paint/stack metadata to IR.
2. Add separate top-level layer groups in Figma output.
3. Implement `EDITABLE_TABLE` IR and renderer for slide 29 description area.
4. Replace dense panel visual hacks with chunk render intents.
5. Add visual substrate builder for boxes/transparency.
6. Add QA gates for editability, opacity, z-order, and overlap.
7. Run browser-driven Figma accuracy review on the uploaded sample file.
8. Re-run benchmark slides and decide which old assets/hacks to delete.

## Non-goals

- Do not preserve every original asset at all costs.
- Do not make every PPT object fully editable in the first pass.
- Do not depend on Figma native `TableNode` for Figma Design until runtime support is verified.
- Do not use MCP as a converter.
- Do not keep slide-specific hacks unless they are expressed as reusable chunk policy.

## Decision

This plan is the fixed direction for the next refactor:

1. Separate visual fidelity and editability into different layers.
2. Make z-order, opacity, and fallback decisions explicit in IR.
3. Treat the right description/table area as editable semantic content.
4. Use visual substrate rendering for boxes, transparency, and complex overlap.
5. Use QA, MCP-style metadata inspection, and browser-driven visual review to enforce the structure.
