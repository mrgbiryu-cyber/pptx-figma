const DEFAULT_FONT = { family: "Inter", style: "Regular" };
const FORCE_SYSTEM_FONT = true;
const SYSTEM_FONT_FAMILY = "Malgun Gothic";
const PLUGIN_BUILD_TAG = "V54";
const SLIDE_GAP = 120;
const MIN_PAGE_WIDTH = 960;
const MIN_PAGE_HEIGHT = 540;
const REPLAY_PLUGIN_PREFIX = "cnsatlas.replay.";
const ARROW_ROTATION_FLIP_IDS = new Set([
  "s12:slide_12/element_4",
  "s12:slide_12/element_7",
  "s12:slide_12/element_56",
  "s19:slide_19/element_7",
  "s19:slide_19/element_11",
]);
const FONT_FALLBACKS = {
  "LG스마트체": [{ family: "LG스마트체", style: "Regular" }, { family: "Malgun Gothic", style: "Regular" }, DEFAULT_FONT],
  "LG스마트체2.0": [{ family: "LG스마트체2.0", style: "Regular" }, { family: "Malgun Gothic", style: "Regular" }, DEFAULT_FONT],
  "LG Smart_H": [{ family: "LG Smart_H", style: "Regular" }, { family: "Malgun Gothic", style: "Regular" }, DEFAULT_FONT],
  "+mn-ea": [{ family: "Malgun Gothic", style: "Regular" }, DEFAULT_FONT],
  "+mj-ea": [{ family: "Malgun Gothic", style: "Regular" }, DEFAULT_FONT],
};

let fontLoaded = false;
const fontAvailability = new Map();
let activeRenderMode = "read-first";
let replayDebugState = {
  skipped_nodes: [],
};

figma.showUI(__html__, {
  width: 420,
  height: 360,
});

figma.ui.onmessage = async (message) => {
  if (message.type === "render-intermediate-json") {
    try {
      const payload = JSON.parse(message.jsonText);
      activeRenderMode = message.renderMode === "vector-heavy" ? "vector-heavy" : "read-first";
      let renderedCount = 0;
      if (payload && payload.kind === "slide-review-manifest" && payload.entry_bundle && payload.entry_bundle.document) {
        await renderFigmaReplayBundle(payload.entry_bundle);
      } else if (payload && payload.kind === "figma-replay-collection" && Array.isArray(payload.pages)) {
        renderedCount = await renderFigmaReplayCollection(payload);
      } else if (payload && payload.kind === "figma-replay-bundle" && payload.document) {
        await renderFigmaReplayBundle(payload);
      } else {
        renderedCount = await renderIntermediatePayload(payload);
      }
        figma.ui.postMessage({
          type: "render-success",
          message: payload && payload.kind === "slide-review-manifest"
          ? `${PLUGIN_BUILD_TAG} | kind=slide-review-manifest | Rendered review manifest (${payload.title || payload.review_id || "unknown review"})`
          : payload && payload.kind === "figma-replay-collection"
          ? `${PLUGIN_BUILD_TAG} | kind=figma-replay-collection | Rendered replay collection (${payload.pages.length} pages) / frames: ${renderedCount}`
          : payload && payload.kind === "figma-replay-bundle"
          ? `${PLUGIN_BUILD_TAG} | kind=figma-replay-bundle | Rendered figma replay bundle (${payload.page_name || "unknown page"})`
          : `${PLUGIN_BUILD_TAG} | kind=intermediate | Rendered ${payload.pages.length} slide previews (${activeRenderMode}) / frames: ${renderedCount}`,
      });
    } catch (error) {
      figma.ui.postMessage({
        type: "render-error",
        message: error instanceof Error ? `${error.name}: ${error.message}\n${error.stack}` : String(error),
      });
    }
  } else if (message.type === "export-actual-manifest") {
    try {
      const manifest = exportActualManifest();
      figma.ui.postMessage({
        type: "actual-manifest-exported",
        filename: `actual-manifest-${manifest.page_id || "unknown"}.json`,
        jsonText: JSON.stringify(manifest, null, 2),
      });
    } catch (error) {
      figma.ui.postMessage({
        type: "render-error",
        message: error instanceof Error ? `${error.name}: ${error.message}\n${error.stack}` : String(error),
      });
    }
  } else if (message.type === "export-figma-analysis-json") {
    try {
      const payload = exportFigmaAnalysisJson(message.scope === "selection" ? "selection" : "page");
      figma.ui.postMessage({
        type: "figma-analysis-exported",
        filename: payload.filename,
        jsonText: JSON.stringify(payload.document, null, 2),
      });
    } catch (error) {
      figma.ui.postMessage({
        type: "render-error",
        message: error instanceof Error ? `${error.name}: ${error.message}\n${error.stack}` : String(error),
      });
    }
  }
};

function clearPreviousVisualTests() {
  for (const child of [...figma.currentPage.children]) {
    if (child.name && (child.name.startsWith("CNS Atlas Visual Test") || child.name.startsWith("CNS Atlas Replay"))) {
      child.remove();
    }
  }
}

function resetReplayDebugState() {
  replayDebugState = {
    skipped_nodes: [],
  };
}

function pushSkippedReplayNode(node, origin, reason) {
  if (!node) {
    return;
  }
  const bounds = getReplayBounds(node) || { x: 0, y: 0, width: 0, height: 0 };
  const fills = node.fills || [];
  let fillType = "";
  let fillOpacity = "";
  let fillColor = "";
  if (fills.length > 0 && fills[0]) {
    const fill = fills[0];
    fillType = fill.type || "";
    const color = fill.color || {};
    const opacity = typeof fill.opacity === "number"
      ? fill.opacity
      : (typeof color.a === "number" ? color.a : "");
    fillOpacity = opacity === "" ? "" : String(opacity);
    if (fill.type === "SOLID") {
      fillColor = `${color.r || 0},${color.g || 0},${color.b || 0}`;
    }
  }
  replayDebugState.skipped_nodes.push({
    reference_node_id: node.id || "",
    reference_parent_id: origin && origin.referenceParentId ? origin.referenceParentId : "",
    node_type: node.type || "",
    node_name: node.name || "",
    reason,
    source_is_clip_like: Boolean(origin && origin.sourceIsClipLike),
    bbox_absolute: bounds,
    page_bounds_hint: origin ? (origin.pageBounds || { x: origin.x, y: origin.y, width: origin.width, height: origin.height }) : null,
    fill_type: fillType,
    fill_opacity: fillOpacity,
    fill_color: fillColor,
  });
}

function isVectorHeavyMode() {
  return activeRenderMode === "vector-heavy";
}

async function ensureFontLoaded() {
  if (!fontLoaded) {
    await figma.loadFontAsync(DEFAULT_FONT);
    fontAvailability.set(`${DEFAULT_FONT.family}::${DEFAULT_FONT.style}`, DEFAULT_FONT);
    fontLoaded = true;
  }
}

function normalizeFontCandidate(textStyle) {
  if (FORCE_SYSTEM_FONT) {
    const rawStyle = String(textStyle && textStyle.font_style ? textStyle.font_style : "Regular");
    const requested = String(rawStyle).toLowerCase();
    const style = requested.includes("bold") ? "Bold" : "Regular";
    return {
      family: SYSTEM_FONT_FAMILY,
      style,
      fallbacks: [{ family: SYSTEM_FONT_FAMILY, style }, DEFAULT_FONT],
    };
  }
  const rawFamily = textStyle && textStyle.font_family ? textStyle.font_family : "";
  if (!rawFamily) {
    return { family: DEFAULT_FONT.family, style: DEFAULT_FONT.style, fallbacks: [DEFAULT_FONT] };
  }

  let family = rawFamily;
  let style = "Regular";
  if (rawFamily.endsWith(" Bold")) {
    family = rawFamily.slice(0, -" Bold".length);
    style = "Bold";
  } else if (rawFamily.endsWith(" SemiBold")) {
    family = rawFamily.slice(0, -" SemiBold".length);
    style = "SemiBold";
  } else if (rawFamily.endsWith(" Light")) {
    family = rawFamily.slice(0, -" Light".length);
    style = "Light";
  } else if (rawFamily.endsWith(" Regular")) {
    family = rawFamily.slice(0, -" Regular".length);
    style = "Regular";
  }

  const chain = [{ family, style }];
  const fallbackFamily = FONT_FALLBACKS[family] || FONT_FALLBACKS[rawFamily];
  if (fallbackFamily) {
    chain.push(...fallbackFamily.map((item) => ({ family: item.family, style: style === "Bold" && item.family === "Malgun Gothic" ? "Bold" : item.style })));
  } else {
    chain.push({ family: "Malgun Gothic", style: style === "Bold" ? "Bold" : "Regular" });
    chain.push(DEFAULT_FONT);
  }

  return { family, style, fallbacks: chain };
}

async function resolveFontName(textStyle) {
  const candidate = normalizeFontCandidate(textStyle || {});
  for (const font of candidate.fallbacks) {
    const key = `${font.family}::${font.style}`;
    if (fontAvailability.has(key)) {
      const cached = fontAvailability.get(key);
      if (cached) return cached;
      continue;
    }
    try {
      await figma.loadFontAsync(font);
      fontAvailability.set(key, font);
      return font;
    } catch (error) {
      fontAvailability.set(key, null);
    }
  }
  return DEFAULT_FONT;
}

async function resolveFigmaFontName(style) {
  if (FORCE_SYSTEM_FONT) {
    const requestedStyle = String((style && style.fontStyle) || "Regular").toLowerCase();
    const styleName = requestedStyle.includes("bold") ? "Bold" : "Regular";
    const candidates = [
      { family: SYSTEM_FONT_FAMILY, style: styleName },
      { family: SYSTEM_FONT_FAMILY, style: "Regular" },
      DEFAULT_FONT,
    ];
    for (const font of candidates) {
      const key = `${font.family}::${font.style}`;
      if (fontAvailability.has(key)) {
        const cached = fontAvailability.get(key);
        if (cached) {
          return cached;
        }
        continue;
      }
      try {
        await figma.loadFontAsync(font);
        fontAvailability.set(key, font);
        return font;
      } catch (error) {
        fontAvailability.set(key, null);
      }
    }
    return DEFAULT_FONT;
  }
  const family = style && style.fontFamily ? style.fontFamily : DEFAULT_FONT.family;
  const fontStyle = style && style.fontStyle ? style.fontStyle : DEFAULT_FONT.style;
  const postScript = style && style.fontPostScriptName ? style.fontPostScriptName : null;
  const candidates = [];
  candidates.push({ family, style: fontStyle });
  if (postScript && postScript.toLowerCase().includes("bold")) {
    candidates.push({ family, style: "Bold" });
  }
  candidates.push(...(FONT_FALLBACKS[family] || []));
  candidates.push(DEFAULT_FONT);

  for (const font of candidates) {
    const key = `${font.family}::${font.style}`;
    if (fontAvailability.has(key)) {
      const cached = fontAvailability.get(key);
      if (cached) {
        return cached;
      }
      continue;
    }
    try {
      await figma.loadFontAsync(font);
      fontAvailability.set(key, font);
      return font;
    } catch (error) {
      fontAvailability.set(key, null);
    }
  }
  return DEFAULT_FONT;
}

function computePageBounds(candidates) {
  let maxRight = 0;
  let maxBottom = 0;

  for (const candidate of candidates) {
    const bounds = candidate.bounds_px;
    if (!bounds) {
      continue;
    }
    maxRight = Math.max(maxRight, bounds.x + bounds.width);
    maxBottom = Math.max(maxBottom, bounds.y + bounds.height);
  }

  return {
    width: Math.max(MIN_PAGE_WIDTH, Math.ceil(maxRight + 40)),
    height: Math.max(MIN_PAGE_HEIGHT, Math.ceil(maxBottom + 40)),
  };
}

function colorFromHex(value, fallback) {
  if (!value || value.length !== 6) {
    return fallback;
  }
  return {
    r: parseInt(value.slice(0, 2), 16) / 255,
    g: parseInt(value.slice(2, 4), 16) / 255,
    b: parseInt(value.slice(4, 6), 16) / 255,
  };
}

function makeSolidPaint(styleColor, fallback, defaultOpacity) {
  const resolvedHex = styleColor && (styleColor.resolved_value || styleColor.value);
  const paint = {
    type: "SOLID",
    color: resolvedHex ? colorFromHex(resolvedHex, fallback) : fallback,
  };
  const opacity = styleColor && typeof styleColor.alpha === "number" ? styleColor.alpha : defaultOpacity;
  if (typeof opacity === "number") {
    paint.opacity = opacity;
  }
  return paint;
}

function getShapeStyle(candidate) {
  return candidate.extra && candidate.extra.shape_style ? candidate.extra.shape_style : {};
}

function getTextStyle(candidate) {
  return candidate.extra && candidate.extra.text_style ? candidate.extra.text_style : {};
}

function getRendering(candidate) {
  return candidate && candidate.rendering ? candidate.rendering : {};
}

function getReplacement(candidate) {
  const rendering = getRendering(candidate);
  return rendering && rendering.replacement ? rendering.replacement : null;
}

function prefixedNodeName(candidate) {
  const replacement = getReplacement(candidate);
  if (!replacement) {
    return candidate.title || candidate.subtype;
  }
  return `VF/${replacement.candidate_type}/${candidate.title || candidate.subtype}`;
}

function applyRenderingMetadata(node, candidate) {
  if (!node || !candidate || typeof node.setPluginData !== "function") {
    return;
  }
  const rendering = getRendering(candidate);
  const replacement = getReplacement(candidate);
  node.setPluginData("candidate_id", candidate.candidate_id || "");
  node.setPluginData("source_path", candidate.source_path || "");
  node.setPluginData("current_mode", rendering.current_mode || "native");
  node.setPluginData("preferred_mode", rendering.preferred_mode || "native");
  node.setPluginData("replacement_candidate", rendering.replacement_candidate ? "true" : "false");
  if (replacement) {
    node.setPluginData("replacement_candidate_type", replacement.candidate_type || "");
    node.setPluginData("replacement_strategy", replacement.strategy || "");
    node.setPluginData("replacement_confidence", replacement.confidence || "");
    node.name = prefixedNodeName(candidate);
  }
}

function pageCanvasSize(page) {
  const slideSize = page.slide_size || {};
  const width = slideSize.width_px ? Math.ceil(slideSize.width_px) : null;
  const height = slideSize.height_px ? Math.ceil(slideSize.height_px) : null;
  if (width && height) {
    return { width, height };
  }
  return computePageBounds(page.candidates);
}

function alignTextNode(node, bounds, textStyle, horizontalFallback, verticalFallback) {
  const horizontal = textStyle.horizontal_align || horizontalFallback || "l";
  const vertical = textStyle.vertical_align || verticalFallback || "ctr";

  const leftInset = typeof textStyle.lIns === "number" ? textStyle.lIns : 6;
  const rightInset = typeof textStyle.rIns === "number" ? textStyle.rIns : 6;
  const topInset = typeof textStyle.tIns === "number" ? textStyle.tIns : 4;
  const bottomInset = typeof textStyle.bIns === "number" ? textStyle.bIns : 4;

  if (horizontal === "ctr" || horizontal === "center") {
    node.x = Math.max((bounds.width - node.width) / 2, 4);
  } else if (horizontal === "r" || horizontal === "right") {
    node.x = Math.max(bounds.width - node.width - rightInset, 4);
  } else {
    node.x = leftInset;
  }

  if (vertical === "ctr" || vertical === "mid" || vertical === "center") {
    node.y = Math.max((bounds.height - node.height) / 2, 4);
  } else if (vertical === "b" || vertical === "bottom") {
    node.y = Math.max(bounds.height - node.height - bottomInset, 4);
  } else {
    node.y = topInset;
  }
  node.x = Math.round(node.x);
  node.y = Math.round(node.y);
}

function mapHorizontalAlign(value, fallback) {
  const align = value || fallback || "l";
  if (align === "ctr" || align === "center") {
    return "CENTER";
  }
  if (align === "r" || align === "right") {
    return "RIGHT";
  }
  if (align === "just" || align === "justify") {
    return "JUSTIFIED";
  }
  return "LEFT";
}

function mapVerticalAlign(value, fallback) {
  const align = value || fallback || "t";
  if (align === "ctr" || align === "mid" || align === "center") {
    return "CENTER";
  }
  if (align === "b" || align === "bottom") {
    return "BOTTOM";
  }
  return "TOP";
}

function deriveWrapMode(textValue, textStyle, bounds, options) {
  const text = typeof textValue === "string" ? textValue : "";
  const explicitLineBreak = text.includes("\n");
  if (explicitLineBreak) {
    return "square";
  }

  const wrap = textStyle && textStyle.wrap ? textStyle.wrap : null;
  const explicitFontSize = textStyle && (textStyle.font_size_max || textStyle.font_size_avg);
  const fontSize = clampFontSize(
    explicitFontSize ||
    Math.min(bounds ? Math.min(bounds.width, bounds.height) * 0.6 : 12, 24)
  );
  const boxWidth = bounds ? bounds.width : 120;
  const boxHeight = bounds ? bounds.height : fontSize * 1.4;
  const roughCharCapacity = Math.max(Math.floor((boxWidth - 12) / Math.max(fontSize * 0.55, 4)), 4);
  const needsWrapByLength = text.length > roughCharCapacity;
  const canVisuallyHoldMultipleLines = boxHeight >= fontSize * 1.9;

  if (options && options.forceWrap) {
    return "square";
  }
  if (wrap === "none") {
    // PPT's wrap="none" means the shape doesn't auto-wrap, but in Figma a
    // WIDTH_AND_HEIGHT single line can overflow sideways and get clipped by a
    // parent frame.  When text clearly exceeds what one line can hold
    // (>= 1.5× the estimated single-line capacity) AND the box is tall enough
    // to show at least two lines, fall back to HEIGHT wrapping so all content
    // remains visible — this matches the pre-regression behaviour on text-heavy
    // panels such as slide 29's right description column.
    if (canVisuallyHoldMultipleLines && text.length >= roughCharCapacity * 1.5) {
      return "square";
    }
    return "none";
  }
  if (wrap && wrap !== "none" && canVisuallyHoldMultipleLines && needsWrapByLength) {
    return "square";
  }
  if (canVisuallyHoldMultipleLines && needsWrapByLength) {
    return "square";
  }
  return "none";
}

function createTransparentFrame(bounds, name) {
  const frame = figma.createFrame();
  frame.name = name;
  const snappedX = Math.round(bounds.x);
  const snappedY = Math.round(bounds.y);
  const snappedWidth = Math.max(Math.round(bounds.width), 1);
  const snappedHeight = Math.max(Math.round(bounds.height), 1);
  frame.x = snappedX;
  frame.y = snappedY;
  frame.resize(snappedWidth, snappedHeight);
  frame.fills = [];
  frame.strokes = [];
  frame.clipsContent = false;
  if (bounds.rotation) {
    frame.rotation = bounds.rotation;
  }
  return frame;
}

function shouldFlattenVisual(candidate) {
  if (!isVectorHeavyMode()) {
    return false;
  }
  const replacement = getReplacement(candidate);
  if (candidate.subtype === "connector") {
    return true;
  }
  if (replacement && (replacement.candidate_type === "decision_diamond" || replacement.candidate_type === "complex_shape")) {
    return true;
  }
  if (candidate.subtype === "shape") {
    return true;
  }
  return false;
}

function finalizeVectorHeavyVisual(frame, candidate) {
  if (!shouldFlattenVisual(candidate)) {
    return frame;
  }
  const flattenTargets = frame.children.filter((child) => child.type !== "TEXT");
  if (flattenTargets.length === 0) {
    return frame;
  }
  try {
    const flattened = figma.flatten(flattenTargets, frame);
    flattened.name = `${candidate.title || candidate.subtype} vector`;
    if (typeof flattened.setPluginData === "function") {
      flattened.setPluginData("vector_heavy", "true");
      flattened.setPluginData("candidate_id", candidate.candidate_id || "");
    }
  } catch (error) {
    console.warn(`vector-heavy flatten skipped for ${candidate.candidate_id}:`, error);
  }
  return frame;
}

async function appendTextIntoContainer(container, candidate, textValue, textStyle, bounds, horizontalFallback, verticalFallback) {
  const text = figma.createText();
  text.name = `${container.name} text`;
  text.fontName = await resolveFontName(textStyle);
  text.characters = textValue || candidate.title || "";
  text.fills = [makeSolidPaint(textStyle.fill, { r: 0.12, g: 0.12, b: 0.12 }, 1)];
  // When no explicit font size in data, use 0.6× shorter dimension but cap at 24px
  // to prevent large containers from producing oversized fallback text.
  const fontSizeFallback = Math.min(Math.min(bounds.width, bounds.height) * 0.6, 24);
  text.fontSize = Math.max(1, Math.round(clampFontSize(textStyle.font_size_max || textStyle.font_size_avg || fontSizeFallback)));
  text.textAlignHorizontal = mapHorizontalAlign(textStyle.horizontal_align, horizontalFallback);
  text.textAlignVertical = mapVerticalAlign(textStyle.vertical_align, verticalFallback);
  const wrapMode = deriveWrapMode(text.characters, textStyle, bounds, { forceWrap: false });
  text.textAutoResize = wrapMode === "none" ? "WIDTH_AND_HEIGHT" : "HEIGHT";

  const leftInset = typeof textStyle.lIns === "number" ? textStyle.lIns : 6;
  const rightInset = typeof textStyle.rIns === "number" ? textStyle.rIns : 6;
  const contentWidth = Math.max(Math.round(bounds.width - leftInset - rightInset), 12);
  if (wrapMode !== "none") {
    text.resize(contentWidth, Math.max(Math.round(bounds.height), 16));
  }

  container.appendChild(text);
  alignTextNode(
    text,
    {
      width: bounds.width,
      height: Math.max(bounds.height, text.height),
    },
    textStyle,
    horizontalFallback,
    verticalFallback
  );
  text.x = Math.round(text.x);
  text.y = Math.round(text.y);
  text.opacity = 1;
  text.effects = [];
  return text;
}

function base64ToBytes(base64) {
  if (typeof figma !== "undefined" && typeof figma.base64Decode === "function") {
    return figma.base64Decode(base64);
  }
  if (typeof atob === "function") {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }
  throw new Error("Base64 decoder is unavailable in this Figma runtime.");
}

function addArrowHeadIfNeeded(candidate, parentNode, bounds, lineColor, direction, tipPoint) {
  const shapeStyle = getShapeStyle(candidate);
  const line = shapeStyle.line || {};
  const tailEnd = line.tail_end || {};
  if (tailEnd.type !== "triangle") {
    return;
  }

  // Scale arrowhead with the connector's stroke weight (clamped 8–20 px)
  // so thin lines get a small head and thick lines get a proportional one.
  const strokeWeight = line.width_px ? Math.max(line.width_px, 1) : 1;
  const arrowSize = Math.max(8, Math.min(Math.round(strokeWeight * 5), 20));
  const half = arrowSize / 2;

  const arrow = figma.createPolygon();
  arrow.pointCount = 3;
  arrow.resize(arrowSize, arrowSize);
  arrow.fills = [{ type: "SOLID", color: lineColor }];
  arrow.strokes = [];

  if (direction && (direction.dx !== 0 || direction.dy !== 0)) {
    const angle = Math.atan2(direction.dy, direction.dx) * (180 / Math.PI);
    arrow.rotation = angle + 90 + (ARROW_ROTATION_FLIP_IDS.has(candidate.candidate_id) ? 180 : 0);
    const radians = Math.atan2(direction.dy, direction.dx);
    if (tipPoint) {
      const centerX = tipPoint.x - Math.cos(radians) * half;
      const centerY = tipPoint.y - Math.sin(radians) * half;
      arrow.x = centerX - half;
      arrow.y = centerY - half;
    } else {
      const centerX = bounds.x + Math.cos(radians) * half;
      const centerY = bounds.y + Math.sin(radians) * half;
      arrow.x = centerX - half;
      arrow.y = centerY - half;
    }
  } else {
    const rotation = ((bounds.rotation || 0) % 360 + 360) % 360;
    const horizontalLike = bounds.width >= bounds.height;
    if (rotation >= 45 && rotation < 135) {
      arrow.rotation = 180;
      arrow.x = bounds.x - half + 1;
      arrow.y = bounds.y + Math.max(bounds.height - half, 0);
    } else if (rotation >= 225 && rotation < 315) {
      arrow.rotation = 0;
      arrow.x = bounds.x - half + 1;
      arrow.y = bounds.y - half + 1;
    } else if (horizontalLike) {
      arrow.rotation = 90;
      arrow.x = bounds.x + Math.max(bounds.width - half, 0);
      arrow.y = bounds.y - half + 1;
    } else {
      arrow.rotation = 180;
      arrow.x = bounds.x - half + 1;
      arrow.y = bounds.y + Math.max(bounds.height - half, 0);
    }
  }

  parentNode.appendChild(arrow);
}

function buildChildrenMap(candidates) {
  const byParent = new Map();
  for (const candidate of candidates) {
    const parentId = candidate.parent_candidate_id;
    if (!byParent.has(parentId)) {
      byParent.set(parentId, []);
    }
    byParent.get(parentId).push(candidate);
  }
  return byParent;
}

async function renderIntermediatePayload(payload) {
  await ensureFontLoaded();

  clearPreviousVisualTests();

  let cursorX = 0;
  let maxHeight = MIN_PAGE_HEIGHT;
  const renderedFrames = [];

  for (const page of payload.pages) {
    const pageFrame = figma.createFrame();
    const pageBounds = pageCanvasSize(page);
    pageFrame.name = `Slide ${page.slide_no} - ${page.title_or_label}`;
    pageFrame.resize(pageBounds.width, pageBounds.height);
    pageFrame.x = cursorX;
    pageFrame.y = 0;
    pageFrame.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
    pageFrame.strokes = [{ type: "SOLID", color: { r: 0.82, g: 0.82, b: 0.82 } }];
    pageFrame.strokeWeight = 1;
    figma.currentPage.appendChild(pageFrame);
    renderedFrames.push(pageFrame);

    const childrenMap = buildChildrenMap(page.candidates);
    const roots = [...(childrenMap.get(page.page_id) || [])].sort(sortByPosition);
    for (const candidate of roots) {
      await renderCandidateTree(candidate, childrenMap, pageFrame, { x: 0, y: 0 }, 0);
    }

    cursorX += pageBounds.width + SLIDE_GAP;
    maxHeight = Math.max(maxHeight, pageBounds.height);
  }

  if (renderedFrames.length > 0) {
    figma.viewport.scrollAndZoomIntoView(renderedFrames);
    return renderedFrames.length;
  }

  const emptyFrame = figma.createFrame();
  emptyFrame.name = `CNS Atlas Visual Test (${activeRenderMode})`;
  emptyFrame.resize(Math.max(cursorX - SLIDE_GAP, 1), maxHeight);
  emptyFrame.fills = [];
  emptyFrame.strokes = [];
  figma.currentPage.appendChild(emptyFrame);
  figma.viewport.scrollAndZoomIntoView([emptyFrame]);
  return 0;
}

function colorToSvg(color, opacity) {
  const r = Math.round((color.r || 0) * 255);
  const g = Math.round((color.g || 0) * 255);
  const b = Math.round((color.b || 0) * 255);
  const a = typeof opacity === "number" ? opacity : (typeof color.a === "number" ? color.a : 1);
  return { rgb: `rgb(${r}, ${g}, ${b})`, opacity: a };
}

function replayPluginKey(key) {
  return `${REPLAY_PLUGIN_PREFIX}${key}`;
}

function setReplayPluginData(node, key, value) {
  if (!node || typeof node.setPluginData !== "function") {
    return;
  }
  node.setPluginData(replayPluginKey(key), value == null ? "" : String(value));
}

function getReplayPluginData(node, key) {
  if (!node || typeof node.getPluginData !== "function") {
    return "";
  }
  return node.getPluginData(replayPluginKey(key));
}

function inferReplayComparisonLevel(node) {
  if (!node || typeof node !== "object") {
    return "ignore";
  }
  const bounds = getReplayBounds(node);
  const width = bounds ? bounds.width || 0 : 0;
  const height = bounds ? bounds.height || 0 : 0;
  if (node.type === "TEXT" || node.type === "VECTOR") {
    return "L2";
  }
  if (node.type === "RECTANGLE") {
    if ((node.fills || []).some((fill) => fill && fill.type === "IMAGE")) {
      return "L2";
    }
    if (width >= 180 || height >= 80) {
      return "L2";
    }
    return width < 24 && height < 24 ? "L3" : "L2";
  }
  if (node.type === "FRAME") {
    if (width >= 180 || height >= 80) {
      return "L1";
    }
    return "L2";
  }
  return "ignore";
}

function annotateReplayNode(renderedNode, sourceNode, context, role) {
  if (!renderedNode || !sourceNode) {
    return;
  }
  const pageId = context && context.pageId ? context.pageId : "";
  const referenceParentId = context && context.referenceParentId ? context.referenceParentId : "";
  setReplayPluginData(renderedNode, "reference_node_id", sourceNode.id || "");
  setReplayPluginData(renderedNode, "reference_parent_id", referenceParentId);
  setReplayPluginData(renderedNode, "reference_type", sourceNode.type || "");
  setReplayPluginData(renderedNode, "reference_name", sourceNode.name || "");
  setReplayPluginData(renderedNode, "replay_page_id", pageId || "");
  setReplayPluginData(renderedNode, "replay_role", role || "render-node");
  setReplayPluginData(renderedNode, "comparison_level", inferReplayComparisonLevel(sourceNode));
  const debug = sourceNode.debug || {};
  if (debug.render_layer) {
    setReplayPluginData(renderedNode, "render_layer", debug.render_layer);
  }
  if (debug.render_intent) {
    setReplayPluginData(renderedNode, "render_intent", debug.render_intent);
  }
  if (debug.stack_policy) {
    setReplayPluginData(renderedNode, "stack_policy", debug.stack_policy);
  }
  if (debug.stack_reason) {
    setReplayPluginData(renderedNode, "stack_reason", debug.stack_reason);
  }
  if (debug.source_z_order !== undefined && debug.source_z_order !== null) {
    setReplayPluginData(renderedNode, "source_z_order", String(debug.source_z_order));
  }
  if (context && context.renderFlipX !== undefined) {
    setReplayPluginData(renderedNode, "render_flip_x", context.renderFlipX ? "true" : "false");
  }
  if (context && context.renderFlipY !== undefined) {
    setReplayPluginData(renderedNode, "render_flip_y", context.renderFlipY ? "true" : "false");
  }
  if (context && context.renderRotationHint !== undefined) {
    setReplayPluginData(renderedNode, "render_rotation_hint", String(context.renderRotationHint));
  }
  if (context && context.sourceTransform) {
    setReplayPluginData(renderedNode, "render_transform_signature", transformSignatureFromMatrix(context.sourceTransform));
  }
  if (context && context.sourceIsClipLike !== undefined) {
    setReplayPluginData(renderedNode, "source_is_clip_like", context.sourceIsClipLike ? "true" : "false");
  }
}

function getReplayBounds(node) {
  return node.absoluteBoundingBox || node.absoluteRenderBounds || null;
}

function shouldSkipReplayNode(node) {
  const name = node && node.name ? String(node.name) : "";
  return false;
}

function isClipLikeReplayNode(node) {
  const name = node && node.name ? String(node.name).toLowerCase() : "";
  return name.includes("clip path") || name.includes("mask");
}

function hasVisibleSolidPaint(node) {
  const fills = node && node.fills ? node.fills : [];
  for (const fill of fills) {
    if (!fill || fill.visible === false) {
      continue;
    }
    if (fill.type === "SOLID") {
      const opacity = typeof fill.opacity === "number"
        ? fill.opacity
        : (fill.color && typeof fill.color.a === "number" ? fill.color.a : 1);
      if (opacity > 0) {
        return true;
      }
    }
    if (fill.type === "IMAGE") {
      return true;
    }
  }
  return false;
}

function hasVisibleStroke(node) {
  const strokes = node && node.strokes ? node.strokes : [];
  for (const stroke of strokes) {
    if (!stroke || stroke.visible === false) {
      continue;
    }
    const opacity = typeof stroke.opacity === "number"
      ? stroke.opacity
      : (stroke.color && typeof stroke.color.a === "number" ? stroke.color.a : 1);
    if (opacity > 0) {
      return true;
    }
  }
  return false;
}

function isNear(value, target, tolerance) {
  return Math.abs(value - target) <= tolerance;
}

function isFullPageBlackOverlayVector(node, origin) {
  if (!node || node.type !== "VECTOR" || !origin) {
    return false;
  }
  const fills = node.fills || [];
  if (fills.length !== 1) {
    return false;
  }
  const fill = fills[0];
  if (!fill || fill.type !== "SOLID" || fill.visible === false) {
    return false;
  }
  const color = fill.color || {};
  const opacity = typeof fill.opacity === "number"
    ? fill.opacity
    : (typeof color.a === "number" ? color.a : 1);
  if (opacity < 0.95) {
    return false;
  }
  if ((color.r || 0) > 0.02 || (color.g || 0) > 0.02 || (color.b || 0) > 0.02) {
    return false;
  }
  const bounds = getReplayBounds(node);
  if (!bounds) {
    return false;
  }
  const pageBounds = origin.pageBounds || origin;
  return (
    isNear(bounds.x, pageBounds.x, 1) &&
    isNear(bounds.y, pageBounds.y, 1) &&
    isNear(bounds.width, pageBounds.width, 1) &&
    isNear(bounds.height, pageBounds.height, 1)
  );
}

function boundsRelativeToOrigin(bounds, origin) {
  return {
    x: bounds.x - origin.x,
    y: bounds.y - origin.y,
    width: Math.max(bounds.width || 1, 1),
    height: Math.max(bounds.height || 1, 1),
  };
}

function computeReplayRootBounds(node) {
  const fallback = getReplayBounds(node) || { x: 0, y: 0, width: MIN_PAGE_WIDTH, height: MIN_PAGE_HEIGHT };
  let minX = fallback.x;
  let minY = fallback.y;
  let maxX = fallback.x + fallback.width;
  let maxY = fallback.y + fallback.height;

  function walk(current) {
    if (!current || typeof current !== "object") {
      return;
    }
    const bounds = getReplayBounds(current);
    if (bounds) {
      minX = Math.min(minX, bounds.x);
      minY = Math.min(minY, bounds.y);
      maxX = Math.max(maxX, bounds.x + bounds.width);
      maxY = Math.max(maxY, bounds.y + bounds.height);
    }
    for (const child of current.children || []) {
      walk(child);
    }
  }

  walk(node);
  return { x: minX, y: minY, width: Math.max(maxX - minX, 1), height: Math.max(maxY - minY, 1) };
}

function multiplyAffine(parent, child) {
  const pa = parent[0][0];
  const pc = parent[0][1];
  const pe = parent[0][2];
  const pb = parent[1][0];
  const pd = parent[1][1];
  const pf = parent[1][2];
  const ca = child[0][0];
  const cc = child[0][1];
  const ce = child[0][2];
  const cb = child[1][0];
  const cd = child[1][1];
  const cf = child[1][2];
  return [
    [pa * ca + pc * cb, pa * cc + pc * cd, pa * ce + pc * cf + pe],
    [pb * ca + pd * cb, pb * cc + pd * cd, pb * ce + pd * cf + pf],
  ];
}

function identityAffine() {
  return [[1, 0, 0], [0, 1, 0]];
}

function getNodeRelativeTransform(node) {
  return node && node.relativeTransform ? node.relativeTransform : identityAffine();
}

function getTransformSigns(matrix) {
  const a = matrix[0][0];
  const d = matrix[1][1];
  return {
    flipX: a < 0,
    flipY: d < 0,
    rotation: transformRotationDegrees(matrix),
  };
}

function transformRotationDegrees(matrix) {
  const a = matrix[0][0];
  const b = matrix[1][0];
  return Math.round((Math.atan2(b, a) * 180) / Math.PI);
}

function transformSignatureFromMatrix(matrix) {
  const signs = getTransformSigns(matrix);
  return `${signs.flipX ? "-" : "+"}:${signs.flipY ? "-" : "+"}:R${signs.rotation}`;
}

function computeNodeComposedTransform(node) {
  if (!node || typeof node !== "object") {
    return identityAffine();
  }
  if (node.absoluteTransform) {
    return node.absoluteTransform;
  }
  const parentTransform = node.parent ? computeNodeComposedTransform(node.parent) : identityAffine();
  return multiplyAffine(parentTransform, getNodeRelativeTransform(node));
}

function computePluginNodeBounds(node) {
  const absolute = node.absoluteRenderBounds || node.absoluteBoundingBox;
  if (absolute) {
    return {
      x: absolute.x,
      y: absolute.y,
      width: absolute.width,
      height: absolute.height,
    };
  }
  const transform = computeNodeComposedTransform(node);
  const width = typeof node.width === "number" ? node.width : 0;
  const height = typeof node.height === "number" ? node.height : 0;
  return {
    x: transform[0][2],
    y: transform[1][2],
    width,
    height,
  };
}

function unionBounds(boundsList) {
  if (!boundsList.length) {
    return null;
  }
  let minX = boundsList[0].x;
  let minY = boundsList[0].y;
  let maxX = boundsList[0].x + boundsList[0].width;
  let maxY = boundsList[0].y + boundsList[0].height;
  for (let i = 1; i < boundsList.length; i += 1) {
    const bounds = boundsList[i];
    minX = Math.min(minX, bounds.x);
    minY = Math.min(minY, bounds.y);
    maxX = Math.max(maxX, bounds.x + bounds.width);
    maxY = Math.max(maxY, bounds.y + bounds.height);
  }
  return {
    x: minX,
    y: minY,
    width: maxX - minX,
    height: maxY - minY,
  };
}

function computeRenderableBounds(node) {
  if (!node || typeof node !== "object") {
    return computePluginNodeBounds(node);
  }
  const referenceType = getReplayPluginData(node, "reference_type");
  const replayRole = getReplayPluginData(node, "replay_role");
  if (referenceType === "VECTOR" && replayRole === "render-node" && "children" in node && Array.isArray(node.children) && node.children.length > 0) {
    const childBounds = [];
    for (const child of node.children) {
      const bounds = computePluginNodeBounds(child);
      if (bounds && bounds.width > 0 && bounds.height > 0) {
        childBounds.push(bounds);
      }
    }
    const union = unionBounds(childBounds);
    if (union) {
      return union;
    }
  }
  return computePluginNodeBounds(node);
}

function normalizeWhitespace(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function widthBucket(width) {
  if (width < 80) return "XS";
  if (width < 160) return "S";
  if (width < 320) return "M";
  if (width < 640) return "L";
  return "XL";
}

function buildTextLineBreakSignature(text, width) {
  const raw = String(text || "");
  const explicitNewlines = (raw.match(/\n/g) || []).length;
  const renderedLines = Math.max(raw.split("\n").length, 1);
  return `NL${explicitNewlines}-L${renderedLines}-W${widthBucket(width || 0)}`;
}

function getActualParentReferenceId(node) {
  if (!node || !node.parent) {
    return "";
  }
  return getReplayPluginData(node.parent, "reference_node_id");
}

function collectActualManifestNodes(current, rows) {
  if (!current || typeof current !== "object") {
    return;
  }
  const referenceNodeId = getReplayPluginData(current, "reference_node_id");
  if (referenceNodeId) {
    const bounds = computeRenderableBounds(current);
    const parentBounds = current.parent ? computeRenderableBounds(current.parent) : { x: 0, y: 0 };
    const relativeTransform = current.relativeTransform || identityAffine();
    const composedTransform = computeNodeComposedTransform(current);
    const fills = "fills" in current && Array.isArray(current.fills) ? current.fills : [];
    const strokes = "strokes" in current && Array.isArray(current.strokes) ? current.strokes : [];
    const renderFlipX = getReplayPluginData(current, "render_flip_x");
    const renderFlipY = getReplayPluginData(current, "render_flip_y");
    const renderRotationHint = getReplayPluginData(current, "render_rotation_hint");
    const renderTransformSignature = getReplayPluginData(current, "render_transform_signature");
    const sourceIsClipLike = getReplayPluginData(current, "source_is_clip_like");
    const effectiveFlipX = renderFlipX ? renderFlipX === "true" : composedTransform[0][0] < 0;
    const effectiveFlipY = renderFlipY ? renderFlipY === "true" : composedTransform[1][1] < 0;
    const effectiveRotationHint = renderRotationHint ? Number(renderRotationHint || "0") : transformRotationDegrees(composedTransform);
    rows.push({
      actual_node_id: current.id,
      actual_parent_id: current.parent ? current.parent.id : "",
      page_id: getReplayPluginData(current, "replay_page_id"),
      reference_node_id: referenceNodeId,
      reference_parent_id: getReplayPluginData(current, "reference_parent_id") || getActualParentReferenceId(current),
      reference_type: getReplayPluginData(current, "reference_type"),
      reference_name: getReplayPluginData(current, "reference_name"),
      replay_role: getReplayPluginData(current, "replay_role"),
      comparison_level: getReplayPluginData(current, "comparison_level") || "ignore",
      node_type: current.type,
      node_name: current.name || "",
      bbox_absolute: bounds,
      bbox_parent_relative: {
        x: bounds.x - parentBounds.x,
        y: bounds.y - parentBounds.y,
        width: bounds.width,
        height: bounds.height,
      },
      relative_transform: relativeTransform,
      composed_transform: composedTransform,
      flip_x: effectiveFlipX,
      flip_y: effectiveFlipY,
      rotation_hint: effectiveRotationHint,
      has_fill: fills.length > 0,
      has_stroke: strokes.length > 0,
      has_image_fill: fills.some((fill) => fill && fill.type === "IMAGE"),
      has_vector_geometry: current.type === "VECTOR",
      text_characters: current.type === "TEXT" ? current.characters || "" : "",
      text_line_break_signature: current.type === "TEXT"
        ? buildTextLineBreakSignature(current.characters || "", bounds.width)
        : "",
      structure_key: `${getReplayPluginData(current, "reference_parent_id") || ""}|${current.type}|${getReplayPluginData(current, "comparison_level") || ""}`,
      debug_render_flip_x: renderFlipX || "",
      debug_render_flip_y: renderFlipY || "",
      debug_render_rotation_hint: renderRotationHint || "",
      debug_render_transform_signature: renderTransformSignature || "",
      debug_source_is_clip_like: sourceIsClipLike === "true",
      debug_child_count: "children" in current && Array.isArray(current.children) ? current.children.length : 0,
    });
  }
  if ("children" in current && Array.isArray(current.children)) {
    for (const child of current.children) {
      collectActualManifestNodes(child, rows);
    }
  }
}

function pickReplayRootFromSelection() {
  const selected = figma.currentPage.selection || [];
  for (const node of selected) {
    if (node && node.name && node.name.startsWith("CNS Atlas Replay")) {
      return node;
    }
  }
  const pageChildren = [...figma.currentPage.children].reverse();
  for (const node of pageChildren) {
    if (node && node.name && node.name.startsWith("CNS Atlas Replay")) {
      return node;
    }
  }
  return null;
}

function exportActualManifest() {
  const root = pickReplayRootFromSelection();
  if (!root) {
    throw new Error("내보낼 replay root를 찾지 못했습니다. replay 결과를 먼저 생성하거나 root frame을 선택하세요.");
  }
  const rows = [];
  collectActualManifestNodes(root, rows);
  const pageId = rows[0] ? rows[0].page_id : "";
  const summary = {
    node_count: rows.length,
    by_reference_type: {},
    by_node_type: {},
    by_replay_role: {},
    render_flip_y_true: 0,
    source_clip_like_true: 0,
    skipped_node_count: replayDebugState.skipped_nodes.length,
  };
  for (const row of rows) {
    const referenceType = row.reference_type || "";
    const nodeType = row.node_type || "";
    const replayRole = row.replay_role || "";
    summary.by_reference_type[referenceType] = (summary.by_reference_type[referenceType] || 0) + 1;
    summary.by_node_type[nodeType] = (summary.by_node_type[nodeType] || 0) + 1;
    summary.by_replay_role[replayRole] = (summary.by_replay_role[replayRole] || 0) + 1;
    if (row.flip_y) {
      summary.render_flip_y_true += 1;
    }
    if (row.debug_source_is_clip_like) {
      summary.source_clip_like_true += 1;
    }
  }
  return {
    kind: "actual-manifest",
    page_id: pageId,
    generated_at: new Date().toISOString(),
    root_actual_node_id: root.id,
    summary,
    debug: {
      skipped_nodes: replayDebugState.skipped_nodes,
    },
    nodes: rows,
  };
}

function serializePaint(paint) {
  if (!paint) return null;
  const result = {
    type: paint.type || "",
    visible: paint.visible !== false,
  };
  if (typeof paint.opacity === "number") {
    result.opacity = paint.opacity;
  }
  if (paint.color) {
    result.color = {
      r: paint.color.r || 0,
      g: paint.color.g || 0,
      b: paint.color.b || 0,
      a: typeof paint.color.a === "number" ? paint.color.a : undefined,
    };
  }
  if (paint.imageHash) result.imageHash = paint.imageHash;
  if (paint.scaleMode) result.scaleMode = paint.scaleMode;
  if (paint.blendMode) result.blendMode = paint.blendMode;
  return result;
}

function pluginDataSnapshot(node) {
  if (!node || typeof node.getPluginDataKeys !== "function") {
    return {};
  }
  const data = {};
  for (const key of node.getPluginDataKeys()) {
    data[key] = node.getPluginData(key);
  }
  return data;
}

function sharedPluginDataSnapshot(node) {
  return {};
}

function serializeNodeForAnalysis(node, originBounds) {
  const bounds = computeRenderableBounds(node);
  const rel = bounds && originBounds ? {
    x: bounds.x - originBounds.x,
    y: bounds.y - originBounds.y,
    width: bounds.width,
    height: bounds.height,
  } : null;
  const serialized = {
    id: node.id,
    name: node.name || "",
    type: node.type,
    visible: node.visible !== false,
    locked: node.locked === true,
    bounds_absolute: bounds,
    bounds_relative_to_scope: rel,
    width: typeof node.width === "number" ? node.width : undefined,
    height: typeof node.height === "number" ? node.height : undefined,
    relative_transform: node.relativeTransform || identityAffine(),
    composed_transform: computeNodeComposedTransform(node),
    clips_content: "clipsContent" in node ? Boolean(node.clipsContent) : undefined,
    opacity: typeof node.opacity === "number" ? node.opacity : undefined,
    fills: Array.isArray(node.fills) ? node.fills.map(serializePaint).filter(Boolean) : [],
    strokes: Array.isArray(node.strokes) ? node.strokes.map(serializePaint).filter(Boolean) : [],
    stroke_weight: typeof node.strokeWeight === "number" ? node.strokeWeight : undefined,
    corner_radius: typeof node.cornerRadius === "number" ? node.cornerRadius : undefined,
    plugin_data: pluginDataSnapshot(node),
    shared_plugin_data: sharedPluginDataSnapshot(node),
  };
  if (node.type === "TEXT") {
    serialized.characters = node.characters || "";
    serialized.text_style = {
      fontName: node.fontName === figma.mixed ? "mixed" : node.fontName,
      fontSize: node.fontSize === figma.mixed ? "mixed" : node.fontSize,
      textAlignHorizontal: node.textAlignHorizontal,
      textAlignVertical: node.textAlignVertical,
      textAutoResize: node.textAutoResize,
      lineHeight: node.lineHeight === figma.mixed ? "mixed" : node.lineHeight,
      letterSpacing: node.letterSpacing === figma.mixed ? "mixed" : node.letterSpacing,
    };
  }
  if ("children" in node && Array.isArray(node.children) && node.children.length > 0) {
    serialized.children = node.children.map((child) => serializeNodeForAnalysis(child, originBounds));
  } else {
    serialized.children = [];
  }
  return serialized;
}

function exportFigmaAnalysisJson(scope) {
  const selected = figma.currentPage.selection || [];
  const useSelection = scope === "selection" && selected.length > 0;
  const scopeNodes = useSelection ? [...selected] : [...figma.currentPage.children];
  if (!scopeNodes.length) {
    throw new Error(useSelection ? "선택된 노드가 없습니다." : "현재 페이지에 노드가 없습니다.");
  }
  const scopeBounds = unionBounds(scopeNodes.map((node) => computeRenderableBounds(node)).filter(Boolean))
    || { x: 0, y: 0, width: MIN_PAGE_WIDTH, height: MIN_PAGE_HEIGHT };
  const document = {
    kind: "figma-analysis-export",
    export_scope: useSelection ? "selection" : "page",
    page: {
      id: figma.currentPage.id,
      name: figma.currentPage.name || "",
    },
    selection_ids: selected.map((node) => node.id),
    exported_at: new Date().toISOString(),
    scope_bounds: scopeBounds,
    nodes: scopeNodes.map((node) => serializeNodeForAnalysis(node, scopeBounds)),
  };
  return {
    filename: useSelection
      ? `figma-selection-${figma.currentPage.id}.json`
      : `figma-page-${figma.currentPage.id}.json`,
    document,
  };
}

function mapReplayHorizontalAlign(value) {
  if (value === "CENTER") return "CENTER";
  if (value === "RIGHT") return "RIGHT";
  if (value === "JUSTIFIED") return "JUSTIFIED";
  return "LEFT";
}

function mapReplayVerticalAlign(value) {
  if (value === "CENTER") return "CENTER";
  if (value === "BOTTOM") return "BOTTOM";
  return "TOP";
}

function buildVectorSvg(node, bounds) {
  const fillGeometry = node.fillGeometry || [];
  const strokeGeometry = node.strokeGeometry || [];
  const solidFill = (node.fills || []).find((fill) => fill && fill.type === "SOLID");
  const solidStroke = (node.strokes || []).find((stroke) => stroke && stroke.type === "SOLID");
  const fillInfo = solidFill ? colorToSvg(solidFill.color || {}, solidFill.opacity) : null;
  const strokeInfo = solidStroke ? colorToSvg(solidStroke.color || {}, solidStroke.opacity) : null;
  const strokeWidth = node.strokeWeight || 1;
  const fillRule = fillGeometry[0] && fillGeometry[0].windingRule === "NONZERO" ? "nonzero" : "evenodd";

  const parts = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${bounds.width}" height="${bounds.height}" viewBox="0 0 ${bounds.width} ${bounds.height}">`,
  ];

  const renderTransform = node.renderTransform || node.relativeTransform || [[1, 0, 0], [0, 1, 0]];
  const scaleX = renderTransform[0] && typeof renderTransform[0][0] === "number" ? renderTransform[0][0] : 1;
  const scaleY = renderTransform[1] && typeof renderTransform[1][1] === "number" ? renderTransform[1][1] : 1;
  let transformParts = [];
  if (scaleX < 0) {
    transformParts.push(`translate(${bounds.width} 0) scale(-1 1)`);
  }
  if (scaleY < 0) {
    transformParts.push(`translate(0 ${bounds.height}) scale(1 -1)`);
  }
  if (transformParts.length > 0) {
    parts.push(`<g transform="${transformParts.join(" ")}">`);
  }

  for (const geometry of fillGeometry) {
    if (!geometry.path) continue;
    const fillAttrs = fillInfo
      ? `fill="${fillInfo.rgb}" fill-opacity="${fillInfo.opacity}"`
      : 'fill="none"';
    parts.push(`<path d="${geometry.path}" ${fillAttrs} fill-rule="${fillRule}" />`);
  }

  for (const geometry of strokeGeometry) {
    if (!geometry.path) continue;
    const strokeAttrs = strokeInfo
      ? `stroke="${strokeInfo.rgb}" stroke-opacity="${strokeInfo.opacity}" stroke-width="${strokeWidth}"`
      : `stroke="rgb(0,0,0)" stroke-width="${strokeWidth}"`;
    parts.push(`<path d="${geometry.path}" fill="none" ${strokeAttrs} />`);
  }

  if (transformParts.length > 0) {
    parts.push("</g>");
  }

  parts.push("</svg>");
  return parts.join("");
}

function findAssetBytes(bundle, imageRef) {
  if (!bundle || !bundle.assets || !bundle.assets[imageRef]) {
    return null;
  }
  return base64ToBytes(bundle.assets[imageRef].base64);
}

function createReplayContainer(node, parentNode, origin) {
  const bounds = getReplayBounds(node);
  if (!bounds) {
    return parentNode;
  }
  const local = boundsRelativeToOrigin(bounds, origin);
  const snappedX = Math.round(local.x);
  const snappedY = Math.round(local.y);
  const snappedWidth = Math.max(Math.round(local.width), 1);
  const snappedHeight = Math.max(Math.round(local.height), 1);
  const frame = figma.createFrame();
  frame.name = node.name || node.type || "container";
  frame.x = snappedX;
  frame.y = snappedY;
  frame.resize(snappedWidth, snappedHeight);
  frame.fills = [];
  frame.strokes = [];
  frame.clipsContent = false;
  parentNode.appendChild(frame);
  annotateReplayNode(frame, node, origin, "frame-container");
  return frame;
}

function createReplayFrameShell(node, parentNode, origin) {
  const bounds = getReplayBounds(node);
  if (!bounds) {
    return null;
  }
  const local = boundsRelativeToOrigin(bounds, origin);
  const snappedX = Math.round(local.x);
  const snappedY = Math.round(local.y);
  const snappedWidth = Math.max(Math.round(local.width), 1);
  const snappedHeight = Math.max(Math.round(local.height), 1);
  const shell = figma.createRectangle();
  shell.name = node.name || node.type || "frame-shell";
  shell.x = snappedX;
  shell.y = snappedY;
  shell.resize(snappedWidth, snappedHeight);
  shell.fills = (node.fills || []).filter((fill) => fill && (fill.type === "SOLID" || fill.type === "IMAGE")).map((fill) => {
    if (fill.type === "SOLID") {
      return {
        type: "SOLID",
        color: fill.color,
        opacity: typeof fill.opacity === "number" ? fill.opacity : (fill.color && typeof fill.color.a === "number" ? fill.color.a : 1),
      };
    }
    if (fill.type === "IMAGE") {
      return fill;
    }
    return null;
  }).filter(Boolean);
  shell.strokes = (node.strokes || []).filter((stroke) => stroke && stroke.type === "SOLID").map((stroke) => ({
    type: "SOLID",
    color: stroke.color,
    opacity: typeof stroke.opacity === "number" ? stroke.opacity : (stroke.color && typeof stroke.color.a === "number" ? stroke.color.a : 1),
  }));
  shell.strokeWeight = node.strokeWeight || 1;
  const rotation = transformRotationDegrees(origin.sourceTransform || identityAffine());
  if (rotation) {
    shell.rotation = rotation;
  }
  parentNode.appendChild(shell);
  annotateReplayNode(shell, node, origin, "frame-shell");
  return shell;
}

async function renderReplayText(node, parentNode, origin) {
  const bounds = getReplayBounds(node);
  if (!bounds) {
    return;
  }
  const local = boundsRelativeToOrigin(bounds, origin);
  const snappedX = Math.round(local.x);
  const snappedY = Math.round(local.y);
  const snappedWidth = Math.max(Math.round(local.width), 12);
  const snappedHeight = Math.max(Math.round(local.height), 16);
  const text = figma.createText();
  text.name = node.name || "Text";
  text.fontName = await resolveFigmaFontName(node.style || {});
  text.characters = node.characters || "";
  const sourceFontSize = node.style && typeof node.style.fontSize === "number" ? node.style.fontSize : 12;
  text.fontSize = Math.max(1, Math.round(sourceFontSize));
  text.textAlignHorizontal = mapReplayHorizontalAlign(node.style && node.style.textAlignHorizontal);
  text.textAlignVertical = mapReplayVerticalAlign(node.style && node.style.textAlignVertical);
  const textAutoResize = (node.style && node.style.textAutoResize) || "NONE";
  if (textAutoResize === "WIDTH_AND_HEIGHT") {
    text.textAutoResize = "WIDTH_AND_HEIGHT";
  } else {
    // Upgrade NONE → HEIGHT so text is never silently clipped.
    // Source Figma nodes often have NONE because they were authored at a fixed size,
    // but regenerated slides may have different content lengths that need to wrap.
    text.textAutoResize = "HEIGHT";
    text.resize(snappedWidth, snappedHeight);
  }
  if (node.style && typeof node.style.letterSpacing === "number") {
    text.letterSpacing = { value: Math.round(node.style.letterSpacing * 100) / 100, unit: "PIXELS" };
  }
  if (node.style && typeof node.style.lineHeightPx === "number") {
    text.lineHeight = { unit: "PIXELS", value: Math.max(text.fontSize + 1, Math.round(node.style.lineHeightPx)) };
  }
  text.fills = (node.fills || []).filter((fill) => fill && fill.type === "SOLID").map((fill) => ({
    type: "SOLID",
    color: { r: fill.color.r || 0, g: fill.color.g || 0, b: fill.color.b || 0 },
    opacity: typeof fill.opacity === "number" ? fill.opacity : (fill.color && typeof fill.color.a === "number" ? fill.color.a : 1),
  }));
  if (text.fills.length === 0) {
    text.fills = [{ type: "SOLID", color: { r: 0, g: 0, b: 0 } }];
  }
  await applyReplayTextRuns(text, node.textRuns || []);
  text.x = snappedX;
  text.y = snappedY;
  text.opacity = 1;
  text.effects = [];
  const rotation = transformRotationDegrees(origin.sourceTransform || identityAffine());
  if (rotation) {
    text.rotation = rotation;
  }
  parentNode.appendChild(text);
  annotateReplayNode(text, node, origin, "render-node");
}

function mapReplaySolidPaints(paints) {
  return (paints || []).filter((paint) => paint && paint.type === "SOLID").map((paint) => ({
    type: "SOLID",
    color: {
      r: paint.color && typeof paint.color.r === "number" ? paint.color.r : 0,
      g: paint.color && typeof paint.color.g === "number" ? paint.color.g : 0,
      b: paint.color && typeof paint.color.b === "number" ? paint.color.b : 0,
    },
    opacity: typeof paint.opacity === "number" ? paint.opacity : (paint.color && typeof paint.color.a === "number" ? paint.color.a : 1),
  }));
}

async function applyReplayTextRuns(textNode, textRuns) {
  const characters = textNode.characters || "";
  for (const run of textRuns || []) {
    const start = Math.max(0, Math.min(Number(run.start || 0), characters.length));
    const end = Math.max(start, Math.min(Number(run.end || 0), characters.length));
    if (start >= end) {
      continue;
    }
    const style = run.style || {};
    if (style.fontFamily || style.fontStyle) {
      try {
        const fontName = await resolveFigmaFontName(style);
        textNode.setRangeFontName(start, end, fontName);
      } catch (error) {
        // Keep the base font when a source range font is unavailable in Figma.
      }
    }
    if (typeof style.fontSize === "number") {
      textNode.setRangeFontSize(start, end, Math.max(1, Math.round(style.fontSize)));
    }
    const fills = mapReplaySolidPaints(style.fills || []);
    if (fills.length > 0) {
      textNode.setRangeFills(start, end, fills);
    }
  }
}

async function renderReplayEditableTable(node, parentNode, origin) {
  const bounds = getReplayBounds(node);
  if (!bounds) {
    return;
  }
  const local = boundsRelativeToOrigin(bounds, origin);
  const tableFrame = figma.createFrame();
  tableFrame.name = node.name || "Editable Table";
  tableFrame.x = Math.round(local.x);
  tableFrame.y = Math.round(local.y);
  tableFrame.resize(Math.max(Math.round(local.width), 1), Math.max(Math.round(local.height), 1));
  tableFrame.fills = [];
  tableFrame.strokes = [];
  tableFrame.clipsContent = false;
  tableFrame.layoutMode = "VERTICAL";
  tableFrame.primaryAxisSizingMode = "FIXED";
  tableFrame.counterAxisSizingMode = "FIXED";
  tableFrame.itemSpacing = 0;
  tableFrame.paddingLeft = 0;
  tableFrame.paddingRight = 0;
  tableFrame.paddingTop = 0;
  tableFrame.paddingBottom = 0;
  parentNode.appendChild(tableFrame);
  annotateReplayNode(tableFrame, node, origin, "editable-table");

  const rows = [...(node.rows || [])].sort((a, b) => Number(a.index || 0) - Number(b.index || 0));
  const cellsByRow = new Map();
  for (const cell of node.cells || []) {
    const key = String(cell.row || 0);
    if (!cellsByRow.has(key)) {
      cellsByRow.set(key, []);
    }
    cellsByRow.get(key).push(cell);
  }

  for (const rowInfo of rows) {
    const rowFrame = figma.createFrame();
    rowFrame.name = `row ${rowInfo.index || ""}`.trim();
    rowFrame.resize(
      Math.max(Math.round(tableFrame.width), 1),
      Math.max(Math.round(rowInfo.height || 24), 1)
    );
    rowFrame.fills = [];
    rowFrame.strokes = [];
    rowFrame.clipsContent = false;
    rowFrame.layoutMode = "HORIZONTAL";
    rowFrame.primaryAxisSizingMode = "FIXED";
    rowFrame.counterAxisSizingMode = "FIXED";
    rowFrame.itemSpacing = 0;
    rowFrame.paddingLeft = 0;
    rowFrame.paddingRight = 0;
    rowFrame.paddingTop = 0;
    rowFrame.paddingBottom = 0;
    tableFrame.appendChild(rowFrame);
    annotateReplayNode(rowFrame, Object.assign({ type: "FRAME", name: rowFrame.name, id: rowInfo.id || "" }, rowInfo), origin, "editable-table-row");

    const rowCells = [...(cellsByRow.get(String(rowInfo.index || 0)) || [])].sort((a, b) => Number(a.column || 0) - Number(b.column || 0));
    for (const cellInfo of rowCells) {
      const cellBounds = cellInfo.bounds || {};
      const cellFrame = figma.createFrame();
      cellFrame.name = cellInfo.name || `cell ${cellInfo.row || ""}-${cellInfo.column || ""}`;
      cellFrame.resize(
        Math.max(Math.round(cellBounds.width || 1), 1),
        Math.max(Math.round(cellBounds.height || rowInfo.height || 1), 1)
      );
      const mappedFills = mapReplaySolidPaints(cellInfo.fills || []);
      cellFrame.fills = mappedFills.length > 0 ? mappedFills : [];
      cellFrame.strokes = mapReplaySolidPaints(cellInfo.strokes || []);
      cellFrame.strokeWeight = typeof cellInfo.strokeWeight === "number" ? cellInfo.strokeWeight : 1;
      cellFrame.clipsContent = false;
      rowFrame.appendChild(cellFrame);
      annotateReplayNode(cellFrame, Object.assign({ type: "FRAME", name: cellFrame.name, id: cellInfo.id || "" }, cellInfo), origin, "editable-table-cell");

      if (cellInfo.text && String(cellInfo.text.characters || "").length > 0) {
        const padding = cellInfo.padding || {};
        const left = typeof padding.left === "number" ? padding.left : 6;
        const right = typeof padding.right === "number" ? padding.right : 6;
        const top = typeof padding.top === "number" ? padding.top : 4;
        const bottom = typeof padding.bottom === "number" ? padding.bottom : 4;
        const textNode = figma.createText();
        const textStyle = cellInfo.text.style || {};
        textNode.name = `${cellFrame.name} text`;
        textNode.fontName = await resolveFigmaFontName(textStyle);
        textNode.characters = String(cellInfo.text.characters || "");
        textNode.fontSize = Math.max(1, Math.round(typeof textStyle.fontSize === "number" ? textStyle.fontSize : 12));
        textNode.textAlignHorizontal = mapReplayHorizontalAlign(textStyle.textAlignHorizontal);
        textNode.textAlignVertical = mapReplayVerticalAlign(textStyle.textAlignVertical);
        if (typeof textStyle.lineHeightPx === "number") {
          textNode.lineHeight = { unit: "PIXELS", value: Math.max(textNode.fontSize + 1, Math.round(textStyle.lineHeightPx)) };
        }
        const textFills = mapReplaySolidPaints(cellInfo.text.fills || []);
        textNode.fills = textFills.length > 0 ? textFills : [{ type: "SOLID", color: { r: 0, g: 0, b: 0 } }];
        await applyReplayTextRuns(textNode, cellInfo.text.textRuns || []);
        textNode.textAutoResize = "HEIGHT";
        textNode.resize(
          Math.max(Math.round(cellFrame.width - left - right), 12),
          Math.max(Math.round(cellFrame.height - top - bottom), 12)
        );
        textNode.x = Math.round(left);
        textNode.y = Math.round(top);
        textNode.opacity = 1;
        textNode.effects = [];
        cellFrame.appendChild(textNode);
        annotateReplayNode(
          textNode,
          Object.assign({ type: "TEXT", name: textNode.name, id: `${cellInfo.id || cellFrame.name}:text`, debug: cellInfo.text.debug || cellInfo.debug || {} }, cellInfo.text),
          origin,
          "editable-table-text"
        );
      }
    }
  }
}

function renderReplayRectangle(node, parentNode, origin, bundle) {
  const bounds = getReplayBounds(node);
  if (!bounds) {
    return;
  }
  const local = boundsRelativeToOrigin(bounds, origin);
  const snappedX = Math.round(local.x);
  const snappedY = Math.round(local.y);
  const snappedWidth = Math.max(Math.round(local.width), 1);
  const snappedHeight = Math.max(Math.round(local.height), 1);
  const rect = figma.createRectangle();
  rect.name = node.name || "Rectangle";
  rect.x = snappedX;
  rect.y = snappedY;
  rect.resize(snappedWidth, snappedHeight);
  if (typeof node.cornerRadius === "number") {
    rect.cornerRadius = node.cornerRadius;
  }
  const imageFill = (node.fills || []).find((fill) => fill && fill.type === "IMAGE" && fill.imageRef);
  if (imageFill) {
    const bytes = findAssetBytes(bundle, imageFill.imageRef);
    if (bytes) {
      const image = figma.createImage(bytes);
      rect.fills = [{
        type: "IMAGE",
        scaleMode: imageFill.scaleMode || "FIT",
        imageHash: image.hash,
      }];
    }
  }
  if (!rect.fills || rect.fills.length === 0) {
    const solidFills = (node.fills || []).filter((fill) => fill && fill.type === "SOLID");
    rect.fills = solidFills.length > 0 ? solidFills.map((fill) => ({
      type: "SOLID",
      color: { r: fill.color.r || 0, g: fill.color.g || 0, b: fill.color.b || 0 },
      opacity: typeof fill.opacity === "number" ? fill.opacity : (fill.color && typeof fill.color.a === "number" ? fill.color.a : 1),
    })) : [];
  }
  rect.strokes = (node.strokes || []).filter((stroke) => stroke && stroke.type === "SOLID").map((stroke) => ({
    type: "SOLID",
    color: { r: stroke.color.r || 0, g: stroke.color.g || 0, b: stroke.color.b || 0 },
    opacity: typeof stroke.opacity === "number" ? stroke.opacity : (stroke.color && typeof stroke.color.a === "number" ? stroke.color.a : 1),
  }));
  rect.strokeWeight = node.strokeWeight || 1;
  const rotation = transformRotationDegrees(origin.sourceTransform || identityAffine());
  if (rotation) {
    rect.rotation = rotation;
  }
  parentNode.appendChild(rect);
  annotateReplayNode(rect, node, origin, "render-node");
}

function renderReplayVector(node, parentNode, origin) {
  const bounds = getReplayBounds(node);
  if (!bounds) {
    return;
  }
  const local = boundsRelativeToOrigin(bounds, origin);
  const signs = getTransformSigns(origin.sourceTransform || identityAffine());
  const vectorNode = Object.assign({}, node, {
    renderTransform: [
      [signs.flipX ? -1 : 1, 0, 0],
      [0, signs.flipY ? -1 : 1, 0],
    ],
  });
  const svg = buildVectorSvg(vectorNode, local);
  const svgNode = figma.createNodeFromSvg(svg);
  svgNode.name = node.name || "Vector";
  svgNode.x = Math.round(local.x);
  svgNode.y = Math.round(local.y);
  if (signs.rotation) {
    svgNode.rotation = signs.rotation;
  }
  parentNode.appendChild(svgNode);
  annotateReplayNode(svgNode, node, Object.assign({}, origin, {
    renderFlipX: signs.flipX,
    renderFlipY: signs.flipY,
    renderRotationHint: signs.rotation,
  }), "render-node");
}

function renderReplaySvgBlock(node, parentNode, origin) {
  const bounds = getReplayBounds(node);
  if (!bounds || !node.svgMarkup) {
    return;
  }
  const local = boundsRelativeToOrigin(bounds, origin);
  const svgNode = figma.createNodeFromSvg(node.svgMarkup);
  svgNode.name = node.name || "SvgBlock";
  svgNode.x = Math.round(local.x);
  svgNode.y = Math.round(local.y);
  const rotation = transformRotationDegrees(origin.sourceTransform || identityAffine());
  if (rotation) {
    svgNode.rotation = rotation;
  }
  parentNode.appendChild(svgNode);
  annotateReplayNode(svgNode, node, origin, "render-node");
}

async function renderReplayNode(node, parentNode, origin, bundle) {
  if (!node || typeof node !== "object") {
    return;
  }
  if (shouldSkipReplayNode(node)) {
    return;
  }

  const currentSourceTransform = multiplyAffine(origin.sourceTransform || identityAffine(), getNodeRelativeTransform(node));
  const sourceIsClipLike = Boolean(origin.sourceIsClipLike || isClipLikeReplayNode(node));
  if (node.type === "VECTOR" && sourceIsClipLike && isFullPageBlackOverlayVector(node, origin)) {
    pushSkippedReplayNode(node, origin, "skip_full_page_clip_overlay_vector");
    return;
  }
  const currentNodeOrigin = Object.assign({}, origin, {
    sourceTransform: currentSourceTransform,
    sourceIsClipLike,
  });
  const nextOriginBase = Object.assign({}, origin, {
    referenceParentId: node.id || origin.referenceParentId || "",
    sourceTransform: currentSourceTransform,
    sourceIsClipLike,
  });

  switch (node.type) {
    case "EDITABLE_TABLE":
      await renderReplayEditableTable(node, parentNode, currentNodeOrigin);
      return;
    case "TEXT":
      await renderReplayText(node, parentNode, currentNodeOrigin);
      return;
    case "VECTOR":
      renderReplayVector(node, parentNode, currentNodeOrigin);
      return;
    case "SVG_BLOCK":
      renderReplaySvgBlock(node, parentNode, currentNodeOrigin);
      return;
    case "RECTANGLE":
      renderReplayRectangle(node, parentNode, currentNodeOrigin, bundle);
      return;
    case "FRAME":
      if (node.clipsContent) {
        const clipFrame = createReplayContainer(node, parentNode, currentNodeOrigin);
        clipFrame.clipsContent = true;
        const childBounds = getReplayBounds(node) || origin;
        const clipOrigin = Object.assign({}, nextOriginBase, {
          x: childBounds.x,
          y: childBounds.y,
          width: childBounds.width,
          height: childBounds.height,
        });
        for (const child of node.children || []) {
          await renderReplayNode(child, clipFrame, clipOrigin, bundle);
        }
        return;
      }
      {
        const frameBounds = getReplayBounds(node) || origin;
        const frameContainer = createReplayContainer(node, parentNode, currentNodeOrigin);
        const frameLocalOrigin = Object.assign({}, nextOriginBase, {
          x: frameBounds.x,
          y: frameBounds.y,
          width: frameBounds.width,
          height: frameBounds.height,
        });
        if (!isClipLikeReplayNode(node) && (hasVisibleSolidPaint(node) || hasVisibleStroke(node))) {
          createReplayFrameShell(node, frameContainer, frameLocalOrigin);
        }
        for (const child of node.children || []) {
          await renderReplayNode(child, frameContainer, frameLocalOrigin, bundle);
        }
      }
      return;
    case "GROUP":
      {
        const groupBounds = getReplayBounds(node) || origin;
        const groupContainer = createReplayContainer(node, parentNode, currentNodeOrigin);
        const groupLocalOrigin = Object.assign({}, nextOriginBase, {
          x: groupBounds.x,
          y: groupBounds.y,
          width: groupBounds.width,
          height: groupBounds.height,
        });
        for (const child of node.children || []) {
          await renderReplayNode(child, groupContainer, groupLocalOrigin, bundle);
        }
      }
      return;
    default:
      for (const child of node.children || []) {
        await renderReplayNode(child, parentNode, nextOriginBase, bundle);
      }
  }
}

async function renderFigmaReplayBundle(bundle) {
  await ensureFontLoaded();
  clearPreviousVisualTests();
  resetReplayDebugState();

  const rootFrame = await buildReplayRootFrame(bundle, 0);
  figma.currentPage.appendChild(rootFrame);
  figma.viewport.scrollAndZoomIntoView([rootFrame]);
}

async function renderFigmaReplayCollection(collection) {
  await ensureFontLoaded();
  clearPreviousVisualTests();
  resetReplayDebugState();

  let cursorX = 0;
  const renderedFrames = [];
  for (const bundle of collection.pages || []) {
    const rootFrame = await buildReplayRootFrame(bundle, cursorX);
    figma.currentPage.appendChild(rootFrame);
    renderedFrames.push(rootFrame);
    cursorX += rootFrame.width + SLIDE_GAP;
  }

  if (renderedFrames.length > 0) {
    figma.viewport.scrollAndZoomIntoView(renderedFrames);
  }
  return renderedFrames.length;
}

async function buildReplayRootFrame(bundle, xOffset) {
  const documentNode = bundle.document;
  const documentBounds = getReplayBounds(documentNode);
  const rootBounds = documentBounds || computeReplayRootBounds(documentNode);
  const rootFrame = figma.createFrame();
  rootFrame.name = `CNS Atlas Replay (${bundle.page_name || bundle.node_id || "page"})`;
  rootFrame.x = xOffset;
  rootFrame.y = 0;
  rootFrame.resize(rootBounds.width, rootBounds.height);
  rootFrame.fills = (documentNode && documentNode.fills ? documentNode.fills : []).filter((fill) => fill && (fill.type === "SOLID" || fill.type === "IMAGE")).map((fill) => {
    if (fill.type === "SOLID") {
      return {
        type: "SOLID",
        color: fill.color,
        opacity: typeof fill.opacity === "number" ? fill.opacity : (fill.color && typeof fill.color.a === "number" ? fill.color.a : 1),
      };
    }
    return fill;
  });
  rootFrame.strokes = (documentNode && documentNode.strokes ? documentNode.strokes : []).filter((stroke) => stroke && stroke.type === "SOLID").map((stroke) => ({
    type: "SOLID",
    color: stroke.color,
    opacity: typeof stroke.opacity === "number" ? stroke.opacity : (stroke.color && typeof stroke.color.a === "number" ? stroke.color.a : 1),
  }));
  rootFrame.strokeWeight = documentNode && documentNode.strokeWeight ? documentNode.strokeWeight : 1;
  if (rootFrame.fills.length === 0) {
    rootFrame.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
  }
  if (rootFrame.strokes.length === 0) {
    rootFrame.strokes = [{ type: "SOLID", color: { r: 0.82, g: 0.82, b: 0.82 } }];
    rootFrame.strokeWeight = 1;
  }

  const replayOrigin = Object.assign({}, rootBounds, {
    pageId: bundle.node_id || "",
    referenceParentId: documentNode && documentNode.id ? documentNode.id : "",
    sourceTransform: identityAffine(),
    pageBounds: documentBounds || rootBounds,
  });
  if (documentNode && documentNode.type === "FRAME") {
    for (const child of documentNode.children || []) {
      await renderReplayNode(child, rootFrame, replayOrigin, bundle);
    }
  } else {
    await renderReplayNode(documentNode, rootFrame, replayOrigin, bundle);
  }
  return rootFrame;
}

function sortByPosition(a, b) {
  // z_order from the PPT source is the authoritative stacking order.
  // Use it first so overlapping shapes render in the correct front-to-back
  // sequence.  Fall back to Y→X position for elements that have no z_order.
  const az = typeof a.z_order === "number" ? a.z_order : Number.MAX_SAFE_INTEGER;
  const bz = typeof b.z_order === "number" ? b.z_order : Number.MAX_SAFE_INTEGER;
  if (az !== bz) {
    return az - bz;
  }
  const ay = a.bounds_px ? a.bounds_px.y : Number.MAX_SAFE_INTEGER;
  const by = b.bounds_px ? b.bounds_px.y : Number.MAX_SAFE_INTEGER;
  if (ay !== by) {
    return ay - by;
  }
  const ax = a.bounds_px ? a.bounds_px.x : Number.MAX_SAFE_INTEGER;
  const bx = b.bounds_px ? b.bounds_px.x : Number.MAX_SAFE_INTEGER;
  return ax - bx;
}

function relativeBounds(candidate, origin) {
  const bounds = candidate.bounds_px;
  if (!bounds) {
    return null;
  }
  return {
    x: bounds.x - origin.x,
    y: bounds.y - origin.y,
    width: Math.max(bounds.width || 1, 1),
    height: Math.max(bounds.height || 1, 1),
    rotation: bounds.rotation || 0,
    flipH: Boolean(bounds.flipH),
    flipV: Boolean(bounds.flipV),
  };
}

async function renderCandidateTree(candidate, childrenMap, parentNode, origin, fallbackIndex) {
  const node = await createNodeForCandidate(candidate, parentNode, origin, fallbackIndex);
  const children = [...(childrenMap.get(candidate.candidate_id) || [])].sort(sortByPosition);

  if (children.length === 0) {
    return node;
  }

  const nextOrigin = candidate.bounds_px || origin;
  let childFallbackIndex = 0;
  for (const child of children) {
    try {
      await renderCandidateTree(child, childrenMap, node, nextOrigin, childFallbackIndex);
    } catch (err) {
      console.error(`Error rendering child ${child.candidate_id}`, err);
      throw err;
    }
    childFallbackIndex += 1;
  }
  return node;
}

async function createNodeForCandidate(candidate, parentNode, origin, fallbackIndex) {
  switch (candidate.subtype) {
    case "text_block":
      return createTextBlock(candidate, parentNode, origin, fallbackIndex);
    case "labeled_shape":
      return createLabeledShape(candidate, parentNode, origin, fallbackIndex);
    case "shape":
      return createShape(candidate, parentNode, origin, fallbackIndex);
    case "connector":
      return createConnector(candidate, parentNode, origin, fallbackIndex);
    case "group":
    case "section_block":
      return createGroupFrame(candidate, parentNode, origin, fallbackIndex);
    case "table":
      return createTableFrame(candidate, parentNode, origin, fallbackIndex);
    case "table_row":
      return createTableRow(candidate, parentNode);
    case "table_cell":
      return createTableCell(candidate, parentNode);
    case "image":
      return createImagePlaceholder(candidate, parentNode, origin, fallbackIndex);
    default:
      return createShape(candidate, parentNode, origin, fallbackIndex);
  }
}

async function createTextBlock(candidate, parentNode, origin, fallbackIndex) {
  const textStyle = getTextStyle(candidate);
  const bounds = relativeBounds(candidate, origin);
  if (bounds) {
    const frame = createTransparentFrame(bounds, candidate.title || candidate.subtype);
    applyRenderingMetadata(frame, candidate);
    parentNode.appendChild(frame);
    await appendTextIntoContainer(
      frame,
      candidate,
      candidate.text || candidate.title || "",
      Object.assign({}, textStyle, { wrap: bounds ? textStyle.wrap : "none" }),
      bounds,
      "l",
      "t"
    );
    return frame;
  }

  const fallbackBounds = {
    x: 20,
    y: 20 + fallbackIndex * 20,
    width: 720,
    height: 28,
  };
  const frame = createTransparentFrame(fallbackBounds, candidate.title || candidate.subtype);
  applyRenderingMetadata(frame, candidate);
  parentNode.appendChild(frame);
  await appendTextIntoContainer(
    frame,
    candidate,
    candidate.text || candidate.title || "",
    Object.assign({}, textStyle, { wrap: "none" }),
    fallbackBounds,
    "l",
    "t"
  );
  return frame;
}

async function createLabeledShape(candidate, parentNode, origin, fallbackIndex) {
  const bounds = relativeBounds(candidate, origin) || {
    x: 20,
    y: 20 + fallbackIndex * 24,
    width: 120,
    height: 32,
  };
  const shapeStyle = getShapeStyle(candidate);
  const textStyle = getTextStyle(candidate);
  const shapeKind = candidate.extra && candidate.extra.shape_kind ? candidate.extra.shape_kind : "";
  const frame = createTransparentFrame(bounds, candidate.title || candidate.subtype);
  applyRenderingMetadata(frame, candidate);
  parentNode.appendChild(frame);

  let visualShape;
  if (shapeKind === "ellipse") {
    visualShape = figma.createEllipse();
  } else if (shapeKind === "flowChartDecision") {
    visualShape = figma.createPolygon();
    visualShape.pointCount = 4;
    const shapeWidth = Math.max(bounds.width * 0.88, 24);
    const shapeHeight = Math.max(bounds.height * 0.88, 24);
    visualShape.resize(shapeWidth, shapeHeight);
    visualShape.x = (bounds.width - shapeWidth) / 2;
    visualShape.y = (bounds.height - shapeHeight) / 2;
  } else {
    visualShape = figma.createRectangle();
    visualShape.resize(bounds.width, bounds.height);
    visualShape.x = 0;
    visualShape.y = 0;
    if (shapeKind === "roundRect") {
      visualShape.cornerRadius = 8;
    } else if (shapeKind === "rightBracket") {
      visualShape.fills = [];
      visualShape.strokes = [makeSolidPaint(shapeStyle.line, { r: 0.2, g: 0.2, b: 0.2 }, 1)];
      visualShape.strokeWeight = 2;
    }
  }
  if (shapeKind === "ellipse") {
    visualShape.resize(bounds.width, bounds.height);
    visualShape.x = 0;
    visualShape.y = 0;
  }
  if (shapeKind !== "rightBracket") {
    visualShape.fills = [makeSolidPaint(shapeStyle.fill, { r: 1, g: 1, b: 1 }, shapeStyle.fill && shapeStyle.fill.kind === "none" ? 0 : 1)];
    if (shapeStyle.fill && shapeStyle.fill.kind === "none") {
      visualShape.fills = [];
    }
    visualShape.strokes = [makeSolidPaint(shapeStyle.line, { r: 0.28, g: 0.28, b: 0.28 }, 1)];
    visualShape.strokeWeight = shapeStyle.line && shapeStyle.line.width_px ? Math.max(shapeStyle.line.width_px, 1) : 1;
  }
  frame.appendChild(visualShape);

  // Use explicit alignment from the data when available.
  // PPT's default for shapes is left-horizontal, center-vertical ("l"/"ctr").
  // Only fall back to full-center for small shapes that look like buttons.
  const text = candidate.text || candidate.title || "";
  const isSmallShape = bounds.width < 120 && bounds.height < 48 && !text.includes("\n");
  const hFallback = isSmallShape ? "ctr" : "l";
  const vFallback = "ctr";
  await appendTextIntoContainer(frame, candidate, text, textStyle, bounds, hFallback, vFallback);
  finalizeVectorHeavyVisual(frame, candidate);
  return frame;
}

function createShape(candidate, parentNode, origin, fallbackIndex) {
  const bounds = relativeBounds(candidate, origin) || {
    x: 20,
    y: 20 + fallbackIndex * 20,
    width: 120,
    height: 24,
  };
  const shapeStyle = getShapeStyle(candidate);
  const shapeKind = candidate.extra && candidate.extra.shape_kind ? candidate.extra.shape_kind : "";
  let node;
  if (shapeKind === "ellipse") {
    node = figma.createEllipse();
  } else if (shapeKind === "flowChartDecision") {
    node = figma.createPolygon();
    node.pointCount = 4;
  } else {
    node = figma.createRectangle();
  }
  node.name = candidate.title || candidate.subtype;
  node.x = bounds.x;
  node.y = bounds.y;
  node.resize(bounds.width, bounds.height);
  if (shapeKind === "roundRect") {
    node.cornerRadius = 8;
  }
  if (shapeKind === "rightBracket") {
    node.fills = [];
    node.strokes = [makeSolidPaint(shapeStyle.line, { r: 0.2, g: 0.2, b: 0.2 }, 1)];
    node.strokeWeight = 2;
  } else {
    node.fills = [makeSolidPaint(shapeStyle.fill, { r: 0.94, g: 0.95, b: 0.97 }, shapeStyle.fill && shapeStyle.fill.kind === "none" ? 0 : 1)];
    if (shapeStyle.fill && shapeStyle.fill.kind === "none") {
      node.fills = [];
    }
    node.strokes = [makeSolidPaint(shapeStyle.line, { r: 0.75, g: 0.78, b: 0.82 }, 1)];
    node.strokeWeight = shapeStyle.line && shapeStyle.line.width_px ? Math.max(shapeStyle.line.width_px, 1) : 1;
  }
  if (bounds.rotation && shapeKind !== "flowChartDecision") {
    node.rotation = bounds.rotation;
  }
  parentNode.appendChild(node);
  applyRenderingMetadata(node, candidate);
  if (shouldFlattenVisual(candidate)) {
    const wrapper = createTransparentFrame(bounds, candidate.title || candidate.subtype);
    applyRenderingMetadata(wrapper, candidate);
    node.x = 0;
    node.y = 0;
    wrapper.appendChild(node);
    parentNode.appendChild(wrapper);
    finalizeVectorHeavyVisual(wrapper, candidate);
    return wrapper;
  }
  return node;
}

function createConnector(candidate, parentNode, origin, fallbackIndex) {
  const fallbackBounds = relativeBounds(candidate, origin) || {
    x: 20,
    y: 20 + fallbackIndex * 20,
    width: 80,
    height: 2,
  };
  const shapeStyle = getShapeStyle(candidate);
  const linePaint = makeSolidPaint(shapeStyle.line, { r: 0.35, g: 0.35, b: 0.35 }, 1);
  const strokeWeight = shapeStyle.line && shapeStyle.line.width_px ? Math.max(shapeStyle.line.width_px, 1) : 1;
  const kind = candidate.extra && candidate.extra.shape_kind ? candidate.extra.shape_kind : "connector";
  const localWidth = Math.max(fallbackBounds.width, 6);
  const localHeight = Math.max(fallbackBounds.height, 6);
  const flipH = Boolean(fallbackBounds.flipH);
  const flipV = Boolean(fallbackBounds.flipV);
  const startPointPx = candidate.extra && candidate.extra.start_point_px ? candidate.extra.start_point_px : null;
  const endPointPx = candidate.extra && candidate.extra.end_point_px ? candidate.extra.end_point_px : null;
  const connectorAdjusts = candidate.extra && candidate.extra.connector_adjusts ? candidate.extra.connector_adjusts : {};
  const startIdx = candidate.extra && candidate.extra.start_connection ? candidate.extra.start_connection.idx : null;
  const endIdx = candidate.extra && candidate.extra.end_connection ? candidate.extra.end_connection.idx : null;

  function mapPoint(x, y) {
    return {
      x: flipH ? localWidth - x : x,
      y: flipV ? localHeight - y : y,
    };
  }

  function localPointFromIdx(idx) {
    const centerX = localWidth / 2;
    const centerY = localHeight / 2;
    const mapping = {
      0: { x: centerX, y: 0 },
      1: { x: 0, y: centerY },
      2: { x: centerX, y: localHeight },
      3: { x: localWidth, y: centerY },
      4: { x: 0, y: 0 },
      5: { x: localWidth, y: 0 },
      6: { x: 0, y: localHeight },
      7: { x: localWidth, y: localHeight },
    };
    const point = mapping[idx];
    if (!point) {
      return null;
    }
    return mapPoint(point.x, point.y);
  }

  function pointFromAbsolute(point) {
    return {
      x: point.x - fallbackBounds.x,
      y: point.y - fallbackBounds.y,
    };
  }

  function sideFromIdx(idx) {
    // PPT OOXML standard for rect geometry: 0=top, 1=right, 2=bottom, 3=left.
    // Previous mapping had 1/3 swapped, causing elbow routes to go the wrong way.
    if (idx === 0) return "top";
    if (idx === 1) return "right";
    if (idx === 2) return "bottom";
    if (idx === 3) return "left";
    if (idx === 4) return "top-left";
    if (idx === 5) return "top-right";
    if (idx === 6) return "bottom-left";
    if (idx === 7) return "bottom-right";
    return "unknown";
  }

  function inferSideFromDelta(dx, dy, role) {
    if (Math.abs(dx) >= Math.abs(dy)) {
      if (role === "start") {
        return dx >= 0 ? "right" : "left";
      }
      return dx >= 0 ? "left" : "right";
    }
    if (role === "start") {
      return dy >= 0 ? "bottom" : "top";
    }
    return dy >= 0 ? "top" : "bottom";
  }

  function chooseConnectorSide(rawSide, inferredSide) {
    if (rawSide === "unknown") {
      return inferredSide;
    }
    if (rawSide.includes("-")) {
      return inferredSide;
    }
    return rawSide;
  }

  function offsetFromSide(point, side, margin) {
    if (side === "left" || side === "top-left" || side === "bottom-left") {
      return { x: point.x - margin, y: point.y };
    }
    if (side === "right" || side === "top-right" || side === "bottom-right") {
      return { x: point.x + margin, y: point.y };
    }
    if (side === "top") {
      return { x: point.x, y: point.y - margin };
    }
    if (side === "bottom") {
      return { x: point.x, y: point.y + margin };
    }
    return { x: point.x, y: point.y };
  }

  function pathUsingReadableElbow(start, end, startSide, endSide, kindName, adjusts) {
    // Reduced from 16 → 8 px so connector endpoints sit closer to shapes,
    // matching the PPT visual gap more accurately.
    const leadMargin = 8;
    const startLead = offsetFromSide(start, startSide, leadMargin);
    const endLead = offsetFromSide(end, endSide, leadMargin);
    const startOrientation = (startSide === "left" || startSide === "right") ? "horizontal" : "vertical";
    const endOrientation = (endSide === "left" || endSide === "right") ? "horizontal" : "vertical";
    const adj1 = typeof adjusts.adj1 === "number" ? adjusts.adj1 / 100000 : 0.5;
    const adj2 = typeof adjusts.adj2 === "number" ? adjusts.adj2 / 100000 : 0.5;

    if (kindName === "straightConnector1") {
      if (Math.abs(start.y - end.y) <= 3 || Math.abs(start.x - end.x) <= 3) {
        return [start, end];
      }
      if (Math.abs(end.x - start.x) >= Math.abs(end.y - start.y)) {
        return [start, { x: end.x, y: start.y }, end];
      }
      return [start, { x: start.x, y: end.y }, end];
    }

    if (startOrientation === "horizontal" && endOrientation === "horizontal") {
      const routeRight = Math.max(startLead.x, endLead.x) + 18;
      const routeLeft = Math.min(startLead.x, endLead.x) - 18;
      const preferRight = startSide === "right" || endSide === "left";
      const routeX = preferRight ? routeRight : routeLeft;
      return [start, startLead, { x: routeX, y: startLead.y }, { x: routeX, y: endLead.y }, endLead, end];
    }

    if (startOrientation === "vertical" && endOrientation === "vertical") {
      const routeBottom = Math.max(startLead.y, endLead.y) + 18;
      const routeTop = Math.min(startLead.y, endLead.y) - 18;
      const preferBottom = startSide === "bottom" || endSide === "top";
      const routeY = preferBottom ? routeBottom : routeTop;
      return [start, startLead, { x: startLead.x, y: routeY }, { x: endLead.x, y: routeY }, endLead, end];
    }

    if (kindName === "bentConnector4") {
      const midX = startLead.x + (endLead.x - startLead.x) * adj1;
      const midY = startLead.y + (endLead.y - startLead.y) * adj2;
      if (startOrientation === "horizontal") {
        return [start, startLead, { x: midX, y: startLead.y }, { x: midX, y: midY }, { x: endLead.x, y: midY }, endLead, end];
      }
      return [start, startLead, { x: startLead.x, y: midY }, { x: midX, y: midY }, { x: midX, y: endLead.y }, endLead, end];
    }

    if (startOrientation === "horizontal" && endOrientation === "vertical") {
      return [start, startLead, { x: endLead.x, y: startLead.y }, endLead, end];
    }
    if (startOrientation === "vertical" && endOrientation === "horizontal") {
      return [start, startLead, { x: startLead.x, y: endLead.y }, endLead, end];
    }

    if (startOrientation === "horizontal") {
      const midX = startLead.x + (endLead.x - startLead.x) * adj1;
      return [start, startLead, { x: midX, y: startLead.y }, { x: midX, y: endLead.y }, endLead, end];
    }
    const midY = startLead.y + (endLead.y - startLead.y) * adj1;
    return [start, startLead, { x: startLead.x, y: midY }, { x: endLead.x, y: midY }, endLead, end];
  }

  function appendSegment(frame, start, end) {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const isHorizontal = Math.abs(dy) <= 0.5;
    const isVertical = Math.abs(dx) <= 0.5;
    let segment;
    if (isHorizontal || isVertical) {
      segment = figma.createRectangle();
      segment.fills = [linePaint];
      segment.strokes = [];
      if (isHorizontal) {
        segment.x = Math.min(start.x, end.x);
        segment.y = start.y - strokeWeight / 2;
        segment.resize(Math.max(Math.abs(dx), 1), strokeWeight);
      } else {
        segment.x = start.x - strokeWeight / 2;
        segment.y = Math.min(start.y, end.y);
        segment.resize(strokeWeight, Math.max(Math.abs(dy), 1));
      }
    } else {
      segment = figma.createLine();
      segment.x = start.x;
      segment.y = start.y;
      segment.strokes = [linePaint];
      segment.strokeWeight = strokeWeight;
      segment.resize(Math.max(Math.abs(dx), 1), Math.max(Math.abs(dy), 1));
      segment.rotation = Math.atan2(dy, dx) * (180 / Math.PI);
    }
    frame.appendChild(segment);
  }

  let points;
  const localStart = localPointFromIdx(startIdx);
  const localEnd = localPointFromIdx(endIdx);
  if (localStart && localEnd) {
    const start = localStart;
    const end = localEnd;
    const deltaX = end.x - start.x;
    const deltaY = end.y - start.y;
    const startSide = chooseConnectorSide(sideFromIdx(startIdx), inferSideFromDelta(deltaX, deltaY, "start"));
    const endSide = chooseConnectorSide(sideFromIdx(endIdx), inferSideFromDelta(deltaX, deltaY, "end"));
    points = pathUsingReadableElbow(start, end, startSide, endSide, kind, connectorAdjusts);
  } else if (startPointPx && endPointPx) {
    const start = pointFromAbsolute(startPointPx);
    const end = pointFromAbsolute(endPointPx);
    const deltaX = end.x - start.x;
    const deltaY = end.y - start.y;
    const startSide = chooseConnectorSide(sideFromIdx(startIdx), inferSideFromDelta(deltaX, deltaY, "start"));
    const endSide = chooseConnectorSide(sideFromIdx(endIdx), inferSideFromDelta(deltaX, deltaY, "end"));
    points = pathUsingReadableElbow(start, end, startSide, endSide, kind, connectorAdjusts);
  } else if (kind === "straightConnector1") {
    points = [mapPoint(0, localHeight / 2), mapPoint(localWidth, localHeight / 2)];
  } else if (kind === "bentConnector2") {
    points = [mapPoint(0, 0), mapPoint(0, localHeight), mapPoint(localWidth, localHeight)];
  } else if (kind === "bentConnector4") {
    points = [
      mapPoint(0, 0),
      mapPoint(0, localHeight * 0.35),
      mapPoint(localWidth * 0.5, localHeight * 0.35),
      mapPoint(localWidth * 0.5, localHeight),
      mapPoint(localWidth, localHeight),
    ];
  } else {
    points = [
      mapPoint(0, 0),
      mapPoint(0, localHeight * 0.5),
      mapPoint(localWidth, localHeight * 0.5),
      mapPoint(localWidth, localHeight),
    ];
  }

  const filteredPoints = [];
  for (const point of points) {
    const previous = filteredPoints[filteredPoints.length - 1];
    if (!previous || Math.abs(previous.x - point.x) > 0.1 || Math.abs(previous.y - point.y) > 0.1) {
      filteredPoints.push({ x: point.x, y: point.y });
    }
  }
  const originalTipPoint = filteredPoints[filteredPoints.length - 1];
  const adjustedPoints = filteredPoints.map((point) => ({ x: point.x, y: point.y }));
  if (adjustedPoints.length >= 2) {
    const arrowInset = 8;
    const lastPoint = adjustedPoints[adjustedPoints.length - 1];
    const prevPoint = adjustedPoints[adjustedPoints.length - 2];
    const dx = lastPoint.x - prevPoint.x;
    const dy = lastPoint.y - prevPoint.y;
    if (Math.abs(dx) >= Math.abs(dy) && Math.abs(dx) > arrowInset) {
      lastPoint.x += dx > 0 ? -arrowInset : arrowInset;
    } else if (Math.abs(dy) > arrowInset) {
      lastPoint.y += dy > 0 ? -arrowInset : arrowInset;
    }
  }

  const minX = Math.min(...adjustedPoints.map((point) => point.x));
  const minY = Math.min(...adjustedPoints.map((point) => point.y));
  const maxX = Math.max(...adjustedPoints.map((point) => point.x));
  const maxY = Math.max(...adjustedPoints.map((point) => point.y));
  const frame = createTransparentFrame(
    {
      x: fallbackBounds.x + minX,
      y: fallbackBounds.y + minY,
      width: Math.max(maxX - minX, strokeWeight + 2, 6),
      height: Math.max(maxY - minY, strokeWeight + 2, 6),
      rotation: 0,
      flipH: false,
      flipV: false,
    },
    candidate.title || "connector"
  );
  applyRenderingMetadata(frame, candidate);
  parentNode.appendChild(frame);

  const localizedPoints = adjustedPoints.map((point) => ({
    x: point.x - minX,
    y: point.y - minY,
  }));

  for (let index = 0; index < localizedPoints.length - 1; index += 1) {
    appendSegment(frame, localizedPoints[index], localizedPoints[index + 1]);
  }

  const endPoint = localizedPoints[localizedPoints.length - 1];
  const prevPoint = localizedPoints[localizedPoints.length - 2] || localizedPoints[0];
  addArrowHeadIfNeeded(
    candidate,
    frame,
    { x: endPoint.x, y: endPoint.y, width: 1, height: 1, rotation: 0 },
    linePaint.color,
    { dx: endPoint.x - prevPoint.x, dy: endPoint.y - prevPoint.y },
    originalTipPoint
      ? {
        x: originalTipPoint.x - minX,
        y: originalTipPoint.y - minY,
      }
      : null
  );
  finalizeVectorHeavyVisual(frame, candidate);
  return frame;
}

function createGroupFrame(candidate, parentNode, origin, fallbackIndex) {
  const bounds = relativeBounds(candidate, origin) || {
    x: 20,
    y: 20 + fallbackIndex * 24,
    width: 160,
    height: 60,
  };
  const frame = createTransparentFrame(bounds, candidate.title || candidate.subtype);
  applyRenderingMetadata(frame, candidate);
  parentNode.appendChild(frame);
  return frame;
}

function createTableFrame(candidate, parentNode, origin, fallbackIndex) {
  const bounds = relativeBounds(candidate, origin) || {
    x: 20,
    y: 20 + fallbackIndex * 24,
    width: 400,
    height: 240,
  };
  const frame = figma.createFrame();
  const shapeStyle = getShapeStyle(candidate);
  frame.name = candidate.title || "table";
  frame.x = bounds.x;
  frame.y = bounds.y;
  frame.resize(bounds.width, bounds.height);
  frame.fills = [makeSolidPaint(shapeStyle.fill, { r: 1, g: 1, b: 1 }, 1)];
  frame.strokes = [makeSolidPaint(shapeStyle.line, { r: 0.45, g: 0.45, b: 0.45 }, 1)];
  frame.strokeWeight = shapeStyle.line && shapeStyle.line.width_px ? Math.max(shapeStyle.line.width_px, 1) : 1;
  frame.clipsContent = false;
  const rowCount = candidate.extra && candidate.extra.row_count ? candidate.extra.row_count : 1;
  const gridColumns = candidate.extra && candidate.extra.grid_columns ? candidate.extra.grid_columns : [];
  frame.setPluginData("rowCount", String(rowCount));
  frame.setPluginData("gridColumns", JSON.stringify(gridColumns));
  parentNode.appendChild(frame);
  return frame;
}

function createTableRow(candidate, parentNode) {
  const row = figma.createFrame();
  row.name = candidate.title || candidate.subtype;
  row.fills = [];
  row.strokes = [];
  row.clipsContent = false;

  const extra = candidate.extra || {};
  const cellCount = extra.cell_count || 1;
  const siblings = parentNode.children.filter((child) => child.type === "FRAME");
  const rowY = siblings.reduce((sum, child) => sum + child.height, 0);
  const rowCount = Number(parentNode.getPluginData("rowCount") || "1");
  const rowHeight = Math.max(extra.row_height_px || (parentNode.height / Math.max(rowCount, 1)), 24);
  row.x = 0;
  row.y = rowY;
  row.resize(parentNode.width, rowHeight);
  parentNode.appendChild(row);
  row.setPluginData("cellCount", String(cellCount));
  return row;
}

async function createTableCell(candidate, parentNode) {
  const extra = candidate.extra || {};
  if (extra.h_merge || extra.v_merge) {
    const placeholder = figma.createFrame();
    placeholder.name = `${candidate.title || candidate.subtype} merged-skip`;
    placeholder.resize(0.01, 0.01);
    placeholder.fills = [];
    placeholder.strokes = [];
    parentNode.appendChild(placeholder);
    return placeholder;
  }

  const cell = figma.createFrame();
  const textStyle = getTextStyle(candidate);
  cell.name = candidate.title || candidate.subtype;
  const cellCount = Number(parentNode.getPluginData("cellCount") || "1");
  const tableFrame = parentNode.parent;
  const gridColumns = tableFrame && tableFrame.type === "FRAME"
    ? JSON.parse(tableFrame.getPluginData("gridColumns") || "[]")
    : [];
  const startColumnIndex = Number(extra.start_column_index || 1);
  const width = extra.width_px || (parentNode.width / Math.max(cellCount, 1));
  const cellX = gridColumns.length
    ? gridColumns
      .filter((column) => column.column_index < startColumnIndex)
      .reduce((sum, column) => sum + (column.width_px || 0), 0)
    : parentNode.children.filter((child) => child.type === "FRAME").reduce((sum, child) => sum + child.width, 0);
  cell.x = Math.round(cellX);
  cell.y = 0;
  cell.resize(Math.max(Math.round(width), 1), Math.max(Math.round(parentNode.height), 1));
  const cellStyle = extra.cell_style || {};
  const fill = cellStyle.fill ? makeSolidPaint(cellStyle.fill, { r: 1, g: 1, b: 1 }, 1) : { type: "SOLID", color: { r: 1, g: 1, b: 1 } };
  cell.fills = [fill];
  cell.strokes = [{ type: "SOLID", color: { r: 0.75, g: 0.75, b: 0.75 } }];
  cell.strokeWeight = 1;
  parentNode.appendChild(cell);

  const text = figma.createText();
  text.name = `${cell.name} text`;
  text.fontName = await resolveFontName(textStyle);
  text.characters = candidate.text || "";
  text.fontSize = Math.max(1, Math.round(deriveTableCellFontSize(textStyle, cellStyle, parentNode.height, width)));
  text.fills = [makeSolidPaint(textStyle.fill, { r: 0.15, g: 0.15, b: 0.15 }, 1)];
  text.textAlignHorizontal = mapHorizontalAlign(textStyle.horizontal_align, "l");
  text.textAlignVertical = mapVerticalAlign(cellStyle.anchor, "ctr");
  text.lineHeight = {
    unit: "PIXELS",
    value: Math.max(Math.round(text.fontSize * 1.22), text.fontSize + 2),
  };
  const leftInset = typeof cellStyle.marL === "number" ? cellStyle.marL : 6;
  const rightInset = typeof cellStyle.marR === "number" ? cellStyle.marR : 6;
  const availableWidth = Math.max(Math.round(width - leftInset - rightInset), 12);
  const wrapMode = deriveWrapMode(text.characters, Object.assign({}, textStyle, cellStyle), { width, height: parentNode.height }, { forceWrap: true });
  text.textAutoResize = wrapMode === "none" ? "WIDTH_AND_HEIGHT" : "HEIGHT";
  text.resize(availableWidth, Math.max(Math.round(parentNode.height), 16));
  cell.appendChild(text);
  alignTextNode(text, { width, height: parentNode.height }, Object.assign({}, textStyle, cellStyle), "l", cellStyle.anchor || "ctr");
  text.x = Math.round(text.x);
  text.y = Math.round(text.y);
  text.opacity = 1;
  text.effects = [];
  return cell;
}

async function createImagePlaceholder(candidate, parentNode, origin, fallbackIndex) {
  const bounds = relativeBounds(candidate, origin) || {
    x: 20,
    y: 20 + fallbackIndex * 24,
    width: 80,
    height: 80,
  };
  const frame = figma.createFrame();
  const extra = candidate.extra || {};
  frame.name = candidate.title || candidate.subtype;
  frame.x = bounds.x;
  frame.y = bounds.y;
  frame.resize(bounds.width, bounds.height);
  if (extra.image_base64 && extra.mime_type) {
    try {
      const image = figma.createImage(base64ToBytes(extra.image_base64));
      frame.fills = [{
        type: "IMAGE",
        scaleMode: "FILL",
        imageHash: image.hash,
      }];
    } catch (error) {
      frame.fills = [{ type: "SOLID", color: { r: 0.93, g: 0.94, b: 0.96 } }];
    }
  } else {
    frame.fills = [{ type: "SOLID", color: { r: 0.93, g: 0.94, b: 0.96 } }];
  }
  frame.strokes = [{ type: "SOLID", color: { r: 0.64, g: 0.68, b: 0.74 } }];
  frame.strokeWeight = 1;
  parentNode.appendChild(frame);

  if (!extra.image_base64 || !extra.mime_type) {
    const text = figma.createText();
    text.name = `${frame.name} placeholder`;
    text.fontName = await resolveFontName({});
    text.characters = extra.resolved_target && extra.resolved_target.endsWith(".emf") ? "EMF IMAGE" : "IMAGE";
    text.fontSize = clampFontSize(bounds.height * 0.22);
    text.fills = [{ type: "SOLID", color: { r: 0.32, g: 0.36, b: 0.42 } }];
    frame.appendChild(text);
    text.x = Math.max((bounds.width - text.width) / 2, 6);
    text.y = Math.max((bounds.height - text.height) / 2, 4);
  }
  return frame;
}

function clampFontSize(value) {
  // Allow 7–72 px so PPT large titles (32–48pt) and small captions (8–9pt)
  // are not forced into the old 10–28 band that caused visible size mismatch.
  return Math.max(7, Math.min(Math.round(value), 72));
}

function deriveTableCellFontSize(textStyle, cellStyle, rowHeight, width) {
  const hinted = Number(
    textStyle.font_size_max
    || textStyle.font_size_avg
    || textStyle.fontSize
    || cellStyle.fontSize
    || 0
  );
  const height = Number(rowHeight || 0);
  const cellWidth = Number(width || 0);
  let minimum = 10;
  if (height >= 36) {
    minimum = 12;
  } else if (height >= 28) {
    minimum = 11;
  } else if (height < 20) {
    minimum = 9;
  }
  if (cellWidth > 0 && cellWidth < 72) {
    minimum = Math.max(9, minimum - 1);
  }
  return Math.max(minimum, clampFontSize(hinted || minimum));
}
