#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import sharp from "sharp";

function usage() {
  console.error(
    "usage: node compare_pdf_to_bundle.mjs --reference-image <reference.png> --actual <bundle.json> --out-dir <dir> [--crop x,y,w,h] [--density 600]"
  );
  process.exit(1);
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const value = argv[i + 1];
    if (!value || value.startsWith("--")) {
      args[key] = true;
      continue;
    }
    args[key] = value;
  }
  return args;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function loadJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf-8"));
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;");
}

function rgbaFromFill(fill, fallback = { r: 1, g: 1, b: 1, a: 1 }) {
  if (!fill) return fallback;
  const color = fill.color || {};
  return {
    r: clamp(Math.round((color.r ?? fallback.r) * 255), 0, 255),
    g: clamp(Math.round((color.g ?? fallback.g) * 255), 0, 255),
    b: clamp(Math.round((color.b ?? fallback.b) * 255), 0, 255),
    a: fill.opacity ?? fill.alpha ?? fallback.a ?? 1
  };
}

function cssRgba(fill, fallback) {
  const { r, g, b, a } = rgbaFromFill(fill, fallback);
  return `rgba(${r},${g},${b},${a})`;
}

function toFiniteNumber(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function nodeTransform(node, x, y) {
  const rt = node?.relativeTransform;
  if (!Array.isArray(rt) || rt.length < 2) {
    return `translate(${x},${y})`;
  }
  const r0 = Array.isArray(rt[0]) ? rt[0] : [];
  const r1 = Array.isArray(rt[1]) ? rt[1] : [];
  const a = toFiniteNumber(r0[0], 1);
  const c = toFiniteNumber(r0[1], 0);
  const e = toFiniteNumber(r0[2], 0) + x;
  const b = toFiniteNumber(r1[0], 0);
  const d = toFiniteNumber(r1[1], 1);
  const f = toFiniteNumber(r1[2], 0) + y;
  // Bounds are already absolute. Pure flip/scale matrices (no rotation terms)
  // should not shift rendering in this visual checker.
  if (Math.abs(b) < 1e-6 && Math.abs(c) < 1e-6) {
    return `translate(${e},${f})`;
  }
  const identity =
    Math.abs(a - 1) < 1e-6 &&
    Math.abs(b) < 1e-6 &&
    Math.abs(c) < 1e-6 &&
    Math.abs(d - 1) < 1e-6;
  if (identity) {
    return `translate(${e},${f})`;
  }
  return `matrix(${a} ${b} ${c} ${d} ${e} ${f})`;
}

function parseCrop(value) {
  if (!value) return null;
  const parts = String(value).split(",").map((v) => Number(v.trim()));
  if (parts.length !== 4 || parts.some((v) => Number.isNaN(v))) return null;
  return { x: parts[0], y: parts[1], width: parts[2], height: parts[3] };
}

function parseNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function resolveFontConfig(fontFamily) {
  const family = String(fontFamily || "").trim();
  if (!family) return { family: "Malgun Gothic, sans-serif", scale: 1.0 };
  if (family.includes("LG스마트체")) {
    return { family: "Malgun Gothic, sans-serif", scale: 1.0 };
  }
  return { family, scale: 1.0 };
}

function estimateTextWidth(text, fontSize) {
  let units = 0;
  for (const char of String(text || "")) {
    const code = char.codePointAt(0) || 0;
    if (/\s/.test(char)) {
      units += 0.35;
    } else if (
      (code >= 0x1100 && code <= 0x11ff) ||
      (code >= 0x3130 && code <= 0x318f) ||
      (code >= 0xac00 && code <= 0xd7af) ||
      (code >= 0x4e00 && code <= 0x9fff)
    ) {
      units += 0.95;
    } else {
      units += 0.58;
    }
  }
  return units * fontSize;
}

function wrapTextLines(text, maxWidth, fontSize) {
  const width = Math.max(1, Number(maxWidth || 1));
  const size = Math.max(1, Number(fontSize || 12));
  const wrapped = [];
  for (const rawLine of String(text || "").split("\n")) {
    const tokens = rawLine.split(/(\s+)/).filter((token) => token.length > 0);
    let line = "";
    for (const token of tokens) {
      const candidate = line ? `${line}${token}` : token;
      if (estimateTextWidth(candidate, size) <= width || !line.trim()) {
        line = candidate;
        continue;
      }
      wrapped.push(line.trimEnd());
      line = token.trimStart();
      while (estimateTextWidth(line, size) > width && line.length > 1) {
        let cut = 1;
        while (cut < line.length && estimateTextWidth(line.slice(0, cut + 1), size) <= width) {
          cut += 1;
        }
        wrapped.push(line.slice(0, cut));
        line = line.slice(cut);
      }
    }
    wrapped.push(line.trimEnd());
  }
  return wrapped.length ? wrapped : [""];
}

function shouldSkipBundleNode(node, options = {}) {
  if (!options.visualReferenceOnly) return false;
  return node?.name === "editable/content" || node?.name === "mapping/debug";
}

function renderBundleNode(node, pieces, offset, assets, options = {}) {
  if (shouldSkipBundleNode(node, options)) return;
  const bbox = node.absoluteBoundingBox;
  const type = node.type;
  const x = bbox ? bbox.x - offset.x : 0;
  const y = bbox ? bbox.y - offset.y : 0;
  const w = bbox ? bbox.width : 0;
  const h = bbox ? bbox.height : 0;
  const transform = nodeTransform(node, x, y);

  if (type === "SVG_BLOCK" && node.svgMarkup) {
    pieces.push(`<g transform="${transform}">${node.svgMarkup}</g>`);
  } else if (type === "EDITABLE_TABLE") {
    if (!bbox) return;
    const tableFill = (node.fills || [])[0];
    const tableStroke = (node.strokes || [])[0];
    const tableStrokeWidth = Number(node.strokeWeight || node.stroke_weight || 0);
    if (tableFill || tableStroke) {
      pieces.push(
        `<rect x="0" y="0" width="${w}" height="${h}" transform="${transform}" fill="${
          tableFill ? cssRgba(tableFill) : "none"
        }" stroke="${tableStroke ? cssRgba(tableStroke) : "none"}" stroke-width="${tableStrokeWidth}" />`
      );
    }
    pieces.push(`<g transform="${transform}">`);
    for (const cell of node.cells || []) {
      const cellBox = cell.bounds || {};
      const cellX = Number(cellBox.x || 0);
      const cellY = Number(cellBox.y || 0);
      const cellW = Math.max(1, Number(cellBox.width || 1));
      const cellH = Math.max(1, Number(cellBox.height || 1));
      const cellFill = (cell.fills || [])[0];
      const cellStroke = (cell.strokes || [])[0];
      const cellStrokeWidth = Number(cell.strokeWeight || cell.stroke_weight || 0);
      pieces.push(
        `<rect x="${cellX}" y="${cellY}" width="${cellW}" height="${cellH}" fill="${
          cellFill ? cssRgba(cellFill) : "none"
        }" stroke="${cellStroke ? cssRgba(cellStroke) : "none"}" stroke-width="${cellStrokeWidth}" />`
      );
      const text = cell.text || null;
      const characters = text ? String(text.characters || "") : "";
      if (!characters) continue;
      const style = text.style || {};
      const fill = (text.fills || [])[0];
      const fontSize = Number(style.fontSize || 12);
      const font = resolveFontConfig(style.fontFamily);
      const lineHeight = Number(style.lineHeightPx || fontSize * 1.2);
      const padding = cell.padding || {};
      const padLeft = Number(padding.left ?? 6);
      const padRight = Number(padding.right ?? 6);
      const padTop = Number(padding.top ?? 4);
      const padBottom = Number(padding.bottom ?? 4);
      const lines = wrapTextLines(characters, Math.max(1, cellW - padLeft - padRight), fontSize);
      const totalTextHeight = lines.length * lineHeight;
      const alignH = String(style.textAlignHorizontal || "LEFT").toUpperCase();
      const alignV = String(style.textAlignVertical || "TOP").toUpperCase();
      let textAnchor = "start";
      let textX = cellX + padLeft;
      if (alignH === "CENTER") {
        textAnchor = "middle";
        textX = cellX + cellW / 2;
      } else if (alignH === "RIGHT") {
        textAnchor = "end";
        textX = cellX + cellW - padRight;
      }
      let textY = cellY + padTop + fontSize;
      if (alignV === "CENTER") {
        textY = cellY + Math.max(fontSize, (cellH - totalTextHeight) / 2 + fontSize);
      } else if (alignV === "BOTTOM") {
        textY = cellY + Math.max(fontSize, cellH - padBottom - totalTextHeight + fontSize);
      }
      lines.forEach((line, index) => {
        pieces.push(
          `<text x="${textX}" y="${textY + index * lineHeight}" text-anchor="${textAnchor}" font-family="${esc(
            font.family
          )}" font-size="${fontSize * font.scale}" fill="${cssRgba(fill, {
            r: 0,
            g: 0,
            b: 0,
            a: 1
          })}">${esc(line)}</text>`
        );
      });
    }
    pieces.push(`</g>`);
  } else if (type === "FRAME") {
    if (!bbox) return;
    const fill = (node.fills || [])[0];
    const stroke = (node.strokes || [])[0];
    const strokeWidth = Number(node.strokeWeight || node.stroke_weight || 0);
    const radius = Number(node.cornerRadius || node.corner_radius || 0);
    if (fill || stroke) {
      pieces.push(
        `<rect x="0" y="0" width="${w}" height="${h}" transform="${transform}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
          stroke ? cssRgba(stroke) : "none"
        }" stroke-width="${strokeWidth}" rx="${radius}" />`
      );
    }
  } else if (type === "RECTANGLE") {
    if (!bbox) return;
    const fill = (node.fills || [])[0];
    const stroke = (node.strokes || [])[0];
    const strokeWidth = Number(node.strokeWeight || 0);
    if (fill && fill.type === "IMAGE" && fill.imageRef && assets?.[fill.imageRef]?.base64) {
      const asset = assets[fill.imageRef];
      const mime = asset.mime_type || "image/png";
      pieces.push(
        `<image x="0" y="0" width="${w}" height="${h}" transform="${transform}" preserveAspectRatio="xMidYMid slice" href="data:${mime};base64,${asset.base64}" />`
      );
      if (stroke && strokeWidth > 0) {
        pieces.push(
          `<rect x="0" y="0" width="${w}" height="${h}" transform="${transform}" fill="none" stroke="${cssRgba(
            stroke
          )}" stroke-width="${strokeWidth}" />`
        );
      }
    } else {
      pieces.push(
        `<rect x="0" y="0" width="${w}" height="${h}" transform="${transform}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
          stroke ? cssRgba(stroke) : "none"
        }" stroke-width="${strokeWidth}" />`
      );
    }
  } else if (type === "TEXT") {
    if (!bbox) return;
    const fill = (node.fills || [])[0];
    const style = node.style || {};
    const fontSize = Number(style.fontSize || 12);
    const font = resolveFontConfig(style.fontFamily);
    const fontFamily = font.family;
    const effectiveFontSize = fontSize * font.scale;
    const lines = wrapTextLines(String(node.characters || ""), Math.max(1, w), effectiveFontSize);
    lines.forEach((line, index) => {
      const dy = effectiveFontSize + index * (Number(style.lineHeightPx || effectiveFontSize * 1.2));
      pieces.push(
        `<text x="0" y="${dy}" transform="${transform}" font-family="${esc(fontFamily)}" font-size="${effectiveFontSize}" fill="${cssRgba(
          fill,
          { r: 0, g: 0, b: 0, a: 1 }
        )}">${esc(line)}</text>`
      );
    });
  } else if (type === "VECTOR" || type === "LINE" || type === "POLYGON") {
    const fill = (node.fills || [])[0];
    const stroke = (node.strokes || [])[0];
    const strokeWidth = Number(node.strokeWeight || 0);
    const fillGeometry = Array.isArray(node.fillGeometry) ? node.fillGeometry : [];
    const strokeGeometry = Array.isArray(node.strokeGeometry) ? node.strokeGeometry : [];
    if (fillGeometry.length > 0) {
      for (const part of fillGeometry) {
        const pathData = part?.path;
        if (!pathData) continue;
        pieces.push(
          `<path d="${pathData}" transform="${transform}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
            stroke ? cssRgba(stroke) : "none"
          }" stroke-width="${strokeWidth}" />`
        );
      }
    } else if (strokeGeometry.length > 0) {
      for (const part of strokeGeometry) {
        const pathData = part?.path;
        if (!pathData) continue;
        pieces.push(
          `<path d="${pathData}" transform="${transform}" fill="none" stroke="${
            stroke ? cssRgba(stroke) : "none"
          }" stroke-width="${strokeWidth || 1}" />`
        );
      }
    } else if (bbox) {
      pieces.push(
        `<rect x="0" y="0" width="${w}" height="${h}" transform="${transform}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
          stroke ? cssRgba(stroke) : "none"
        }" stroke-width="${strokeWidth}" />`
      );
    }
  }

  for (const child of node.children || []) {
    renderBundleNode(child, pieces, offset, assets, options);
  }
}

function renderPluginNode(node, pieces, offset = { x: 0, y: 0 }) {
  const bbox = node.bounds_relative_to_scope;
  const type = node.type;
  if (bbox) {
    const x = bbox.x - offset.x;
    const y = bbox.y - offset.y;
    const w = bbox.width;
    const h = bbox.height;
    const imageFill = (node.fills || []).find((item) => item && item.visible !== false && item.type === "IMAGE" && item.imageHash);
    if (type === "RECTANGLE") {
      const fill = (node.fills || []).find((item) => item.visible !== false);
      const stroke = (node.strokes || []).find((item) => item.visible !== false);
      if (imageFill) {
        pieces.push(
          `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="#d8dde4" stroke="${
            stroke ? cssRgba(stroke) : "none"
          }" stroke-width="${Number(node.stroke_weight || 0)}" rx="${Number(node.corner_radius || 0)}" />`
        );
      } else {
        pieces.push(
          `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
            stroke ? cssRgba(stroke) : "none"
          }" stroke-width="${Number(node.stroke_weight || 0)}" rx="${Number(node.corner_radius || 0)}" />`
        );
      }
    } else if (type === "ELLIPSE") {
      const fill = (node.fills || []).find((item) => item.visible !== false);
      const stroke = (node.strokes || []).find((item) => item.visible !== false);
      pieces.push(
        `<ellipse cx="${x + w / 2}" cy="${y + h / 2}" rx="${w / 2}" ry="${h / 2}" fill="${
          fill ? cssRgba(fill) : "none"
        }" stroke="${stroke ? cssRgba(stroke) : "none"}" stroke-width="${Number(node.stroke_weight || 0)}" />`
      );
    } else if (type === "VECTOR" || type === "LINE" || type === "POLYGON") {
      const fill = (node.fills || []).find((item) => item.visible !== false);
      const stroke = (node.strokes || []).find((item) => item.visible !== false);
      if (Array.isArray(node.vector_paths) && node.vector_paths.length > 0) {
        for (const part of node.vector_paths) {
          pieces.push(
            `<path d="${part.data}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
              stroke ? cssRgba(stroke) : "none"
            }" stroke-width="${Number(node.stroke_weight || 0)}" />`
          );
        }
      } else {
        pieces.push(
          `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
            stroke ? cssRgba(stroke) : "none"
          }" stroke-width="${Number(node.stroke_weight || 0)}" />`
        );
      }
    } else if (type === "TEXT") {
      const fill = (node.fills || []).find((item) => item.visible !== false);
      const style = node.style || {};
      const fontSize = Number(style.fontSize || 12);
      const fontFamily = style.fontFamily || "sans-serif";
      const lines = String(node.characters || "").split("\n");
      lines.forEach((line, index) => {
        const dy = y + fontSize + index * (Number(style.lineHeightPx || fontSize * 1.2));
        pieces.push(
          `<text x="${x}" y="${dy}" font-family="${esc(fontFamily)}" font-size="${fontSize}" fill="${cssRgba(
            fill,
            { r: 0, g: 0, b: 0, a: 1 }
          )}">${esc(line)}</text>`
        );
      });
    } else if (type === "FRAME") {
      const fill = (node.fills || []).find((item) => item.visible !== false);
      const stroke = (node.strokes || []).find((item) => item.visible !== false);
      const strokeWidth = Number(node.stroke_weight || node.strokeWeight || 0);
      if (imageFill) {
        pieces.push(
          `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="#d8dde4" stroke="${
            stroke ? cssRgba(stroke) : "none"
          }" stroke-width="${strokeWidth}" rx="${Number(node.corner_radius || node.cornerRadius || 0)}" />`
        );
      } else if (fill || stroke) {
        pieces.push(
          `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
            stroke ? cssRgba(stroke) : "none"
          }" stroke-width="${strokeWidth}" rx="${Number(node.corner_radius || node.cornerRadius || 0)}" />`
        );
      }
    }
  }

  for (const child of node.children || []) {
    renderPluginNode(child, pieces, offset);
  }
}

function bundleToSvg(bundle, crop, options = {}) {
  const doc = bundle.document;
  const pageBox = crop || doc.absoluteBoundingBox;
  const pieces = [];
  pieces.push(`<rect x="0" y="0" width="${pageBox.width}" height="${pageBox.height}" fill="white" />`);
  renderBundleNode(doc, pieces, { x: pageBox.x, y: pageBox.y }, bundle.assets || {}, options);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${pageBox.width}" height="${pageBox.height}" viewBox="0 0 ${pageBox.width} ${pageBox.height}">${pieces.join("")}</svg>`;
}

function pluginToSvg(plugin, crop) {
  const root = (plugin.nodes || [])[0];
  const pageBox = crop || plugin.scope_bounds || root?.bounds_relative_to_scope || { x: 0, y: 0, width: 1280, height: 720 };
  const pieces = [];
  pieces.push(`<rect x="0" y="0" width="${pageBox.width}" height="${pageBox.height}" fill="white" />`);
  for (const node of plugin.nodes || []) {
    renderPluginNode(node, pieces, { x: pageBox.x, y: pageBox.y });
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${pageBox.width}" height="${pageBox.height}" viewBox="0 0 ${pageBox.width} ${pageBox.height}">${pieces.join("")}</svg>`;
}

async function svgToPng(svg, outputPath, density = 600) {
  await sharp(Buffer.from(svg), { density: Math.max(72, Math.round(density)) }).png().toFile(outputPath);
}

function miniSvg(width, height, content) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">${content}</svg>`;
}

async function svgBuffer(width, height, content, density = 600) {
  return sharp(Buffer.from(miniSvg(width, height, content)), { density: Math.max(72, Math.round(density)) }).png().toBuffer();
}

async function rectangleOverlay(node) {
  const bbox = node.absoluteBoundingBox;
  const fill = (node.fills || [])[0];
  const stroke = (node.strokes || [])[0];
  const strokeWidth = Number(node.strokeWeight || 0);
  const content = `<rect x="0" y="0" width="${bbox.width}" height="${bbox.height}" fill="${
    fill ? cssRgba(fill) : "none"
  }" stroke="${stroke ? cssRgba(stroke) : "none"}" stroke-width="${strokeWidth}" />`;
  return svgBuffer(bbox.width, bbox.height, content);
}

async function textOverlay(node, density = 600) {
  const bbox = node.absoluteBoundingBox;
  const fill = (node.fills || [])[0];
  const style = node.style || {};
  const font = resolveFontConfig(style.fontFamily);
  const fontSize = Number(style.fontSize || 12) * font.scale;
  const fontFamily = font.family;
  const lineHeight = Number(style.lineHeightPx || fontSize * 1.2);
  const lines = String(node.characters || "").split("\n");
  const alignH = String(style.textAlignHorizontal || "LEFT").toUpperCase();
  const alignV = String(style.textAlignVertical || "TOP").toUpperCase();
  const totalHeight = lines.length * lineHeight;
  let startY = fontSize;
  if (alignV === "CENTER") {
    startY = Math.max(fontSize, (bbox.height - totalHeight) / 2 + fontSize);
  } else if (alignV === "BOTTOM") {
    startY = Math.max(fontSize, bbox.height - totalHeight + fontSize);
  }
  let textAnchor = "start";
  let textX = 0;
  if (alignH === "CENTER") {
    textAnchor = "middle";
    textX = bbox.width / 2;
  } else if (alignH === "RIGHT") {
    textAnchor = "end";
    textX = bbox.width;
  }
  const content = lines
    .map((line, index) => {
      const dy = startY + index * lineHeight;
      return `<text x="${textX}" y="${dy}" text-anchor="${textAnchor}" font-family="${esc(fontFamily)}" font-size="${fontSize}" fill="${cssRgba(
        fill,
        { r: 0, g: 0, b: 0, a: 1 }
      )}">${esc(line)}</text>`;
    })
    .join("");
  return svgBuffer(Math.max(1, bbox.width), Math.max(1, bbox.height), content, density);
}

async function imageOverlay(node, assets) {
  const fill = (node.fills || [])[0];
  if (!fill?.imageRef || !assets?.[fill.imageRef]?.base64) return null;
  const asset = assets[fill.imageRef];
  return sharp(Buffer.from(asset.base64, "base64"))
    .resize(Math.max(1, Math.round(node.absoluteBoundingBox.width)), Math.max(1, Math.round(node.absoluteBoundingBox.height)), {
      fit: "fill"
    })
    .png()
    .toBuffer();
}

async function svgBlockOverlay(node, density = 600) {
  const bbox = node.absoluteBoundingBox;
  if (!node.svgMarkup) return null;
  return svgBuffer(
    Math.max(1, bbox.width),
    Math.max(1, bbox.height),
    `<g transform="translate(0,0)">${node.svgMarkup}</g>`,
    density
  );
}

function intersectRect(a, b) {
  const left = Math.max(a.left, b.left);
  const top = Math.max(a.top, b.top);
  const right = Math.min(a.left + a.width, b.left + b.width);
  const bottom = Math.min(a.top + a.height, b.top + b.height);
  return {
    left,
    top,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top)
  };
}

async function clipOverlay(input, left, top, width, height, canvasWidth, canvasHeight) {
  const visible = intersectRect(
    { left, top, width, height },
    { left: 0, top: 0, width: canvasWidth, height: canvasHeight }
  );
  if (visible.width <= 0 || visible.height <= 0) return null;
  const extract = {
    left: Math.max(0, Math.round(visible.left - left)),
    top: Math.max(0, Math.round(visible.top - top)),
    width: Math.max(1, Math.round(visible.width)),
    height: Math.max(1, Math.round(visible.height))
  };
  const clipped = await sharp(input).extract(extract).png().toBuffer();
  return { input: clipped, left: Math.round(visible.left), top: Math.round(visible.top) };
}

async function collectCompositeOps(node, offset, assets, ops, canvasWidth, canvasHeight, density = 600) {
  const bbox = node.absoluteBoundingBox;
  if (!bbox) return;
  const left = Math.round(bbox.x - offset.x);
  const top = Math.round(bbox.y - offset.y);
  const width = Math.max(1, Math.round(bbox.width));
  const height = Math.max(1, Math.round(bbox.height));
  const type = node.type;

  if (type === "SVG_BLOCK" && node.svgMarkup) {
    const input = await svgBlockOverlay(node, density);
    if (input) {
      const clipped = await clipOverlay(input, left, top, width, height, canvasWidth, canvasHeight);
      if (clipped) ops.push(clipped);
    }
  } else if (type === "RECTANGLE") {
    const fill = (node.fills || [])[0];
    if (fill?.type === "IMAGE") {
      const image = await imageOverlay(node, assets);
      if (image) {
        const clipped = await clipOverlay(image, left, top, width, height, canvasWidth, canvasHeight);
        if (clipped) ops.push(clipped);
      }
      const stroke = (node.strokes || [])[0];
      const strokeWidth = Number(node.strokeWeight || 0);
      if (stroke && strokeWidth > 0) {
        const input = await rectangleOverlay({
          ...node,
          fills: [],
          strokes: [stroke],
          strokeWeight
        });
        const clipped = await clipOverlay(input, left, top, width, height, canvasWidth, canvasHeight);
        if (clipped) ops.push(clipped);
      }
    } else {
      const input = await rectangleOverlay(node);
      const clipped = await clipOverlay(input, left, top, width, height, canvasWidth, canvasHeight);
      if (clipped) ops.push(clipped);
    }
  } else if (type === "TEXT") {
    const input = await textOverlay(node, density);
    const clipped = await clipOverlay(input, left, top, width, height, canvasWidth, canvasHeight);
    if (clipped) ops.push(clipped);
  }

  for (const child of node.children || []) {
    await collectCompositeOps(child, offset, assets, ops, canvasWidth, canvasHeight, density);
  }
}

async function renderBundleToPng(bundle, crop, outputPath, density = 600) {
  const doc = bundle.document;
  const pageBox = crop || doc.absoluteBoundingBox;
  const width = Math.max(1, Math.round(pageBox.width));
  const height = Math.max(1, Math.round(pageBox.height));
  const ops = [];
  await collectCompositeOps(doc, { x: pageBox.x, y: pageBox.y }, bundle.assets || {}, ops, width, height, density);
  await sharp({
    create: {
      width,
      height,
      channels: 4,
      background: { r: 255, g: 255, b: 255, alpha: 1 }
    }
  })
    .composite(ops)
    .png()
    .toFile(outputPath);
}

async function cropOrCopyReference(referenceImagePath, outPath, crop, referenceBaseBox = null) {
  let image = sharp(referenceImagePath);
  if (crop) {
    const meta = await image.metadata();
    const imageWidth = Math.max(1, Math.round(meta.width || crop.width || 1));
    const imageHeight = Math.max(1, Math.round(meta.height || crop.height || 1));
    const baseWidth = Math.max(1, Math.round(referenceBaseBox?.width || imageWidth));
    const baseHeight = Math.max(1, Math.round(referenceBaseBox?.height || imageHeight));
    const scaleX = imageWidth / baseWidth;
    const scaleY = imageHeight / baseHeight;
    const rawLeft = Math.max(0, Math.round(crop.x * scaleX));
    const rawTop = Math.max(0, Math.round(crop.y * scaleY));
    const left = Math.min(rawLeft, Math.max(0, imageWidth - 1));
    const top = Math.min(rawTop, Math.max(0, imageHeight - 1));
    const rawWidth = Math.max(1, Math.round(crop.width * scaleX));
    const rawHeight = Math.max(1, Math.round(crop.height * scaleY));
    const width = Math.max(1, Math.min(rawWidth, imageWidth - left));
    const height = Math.max(1, Math.min(rawHeight, imageHeight - top));
    image = image.extract({ left, top, width, height });
  }
  await image.png().toFile(outPath);
}

function getActualBaseBox(actual) {
  if (actual?.kind === "figma-analysis-export") {
    return actual.scope_bounds || actual.nodes?.[0]?.bounds_relative_to_scope || null;
  }
  return actual?.document?.absoluteBoundingBox || null;
}

function findPdfReferenceBackground(node) {
  if (!node || typeof node !== "object") return null;
  const imageFill = (node.fills || []).find((fill) => fill && fill.type === "IMAGE" && fill.imageRef);
  const debug = node.debug || {};
  if (imageFill && debug.render_intent === "pdf_reference_background") {
    return { node, imageRef: imageFill.imageRef };
  }
  for (const child of node.children || []) {
    const found = findPdfReferenceBackground(child);
    if (found) return found;
  }
  return null;
}

async function renderActualToPng(actual, crop, outputPath, density = 600, options = {}) {
  if (actual?.kind === "figma-analysis-export") {
    const svg = pluginToSvg(actual, crop);
    await sharp(Buffer.from(svg), { density: Math.max(72, Math.round(density)) }).png().toFile(outputPath);
    return svg;
  }
  if (options.visualReferenceOnly) {
    const background = findPdfReferenceBackground(actual?.document);
    const asset = background ? actual?.assets?.[background.imageRef] : null;
    if (asset?.base64) {
      let image = sharp(Buffer.from(asset.base64, "base64"));
      if (crop) {
        const meta = await image.metadata();
        const baseBox = actual?.document?.absoluteBoundingBox || { width: meta.width, height: meta.height };
        const scaleX = Math.max(1, Number(meta.width || 1)) / Math.max(1, Number(baseBox.width || 1));
        const scaleY = Math.max(1, Number(meta.height || 1)) / Math.max(1, Number(baseBox.height || 1));
        image = image.extract({
          left: Math.max(0, Math.round(crop.x * scaleX)),
          top: Math.max(0, Math.round(crop.y * scaleY)),
          width: Math.max(1, Math.round(crop.width * scaleX)),
          height: Math.max(1, Math.round(crop.height * scaleY))
        });
      }
      await image.png().toFile(outputPath);
      return `<svg xmlns="http://www.w3.org/2000/svg"><!-- direct pdf_reference_background asset --></svg>`;
    }
  }
  const svg = bundleToSvg(actual, crop, options);
  await sharp(Buffer.from(svg), { density: Math.max(72, Math.round(density)) }).png().toFile(outputPath);
  return svg;
}

async function diffPng(referencePng, actualPng, outPath, options = {}) {
  const blurSigma = parseNumber(options.blurSigma, 1.2);
  const deltaThreshold = parseNumber(options.deltaThreshold, 40);
  const hotspotMinPixels = Math.max(1, Math.round(parseNumber(options.hotspotMinPixels, 80)));
  const hotspotLimit = Math.max(1, Math.round(parseNumber(options.hotspotLimit, 12)));
  let ref = sharp(referencePng);
  let act = sharp(actualPng);
  const refMeta = await ref.metadata();
  const actMeta = await act.metadata();
  const width = Math.max(refMeta.width || 0, actMeta.width || 0);
  const height = Math.max(refMeta.height || 0, actMeta.height || 0);
  if (blurSigma > 0) {
    ref = ref.clone().blur(blurSigma);
    act = act.clone().blur(blurSigma);
  }
  const refBuf = await ref.resize(width, height).ensureAlpha().raw().toBuffer();
  const actBuf = await act.resize(width, height).ensureAlpha().raw().toBuffer();

  const diff = Buffer.alloc(refBuf.length);
  const changedMask = new Uint8Array(width * height);
  let changed = 0;
  for (let i = 0; i < refBuf.length; i += 4) {
    const dr = Math.abs(refBuf[i] - actBuf[i]);
    const dg = Math.abs(refBuf[i + 1] - actBuf[i + 1]);
    const db = Math.abs(refBuf[i + 2] - actBuf[i + 2]);
    const da = Math.abs(refBuf[i + 3] - actBuf[i + 3]);
    const delta = dr + dg + db + da;
    if (delta > deltaThreshold) {
      changed += 1;
      changedMask[i / 4] = 1;
      diff[i] = 255;
      diff[i + 1] = 0;
      diff[i + 2] = 0;
      diff[i + 3] = 180;
    } else {
      diff[i] = refBuf[i];
      diff[i + 1] = refBuf[i + 1];
      diff[i + 2] = refBuf[i + 2];
      diff[i + 3] = 70;
    }
  }
  await sharp(diff, { raw: { width, height, channels: 4 } }).png().toFile(outPath);
  const hotspots = extractHotspots(changedMask, width, height, hotspotMinPixels, hotspotLimit);
  return {
    width,
    height,
    blur_sigma: blurSigma,
    delta_threshold: deltaThreshold,
    changed_pixels: changed,
    changed_ratio: width * height ? changed / (width * height) : 0,
    match_score: width * height ? 1 - changed / (width * height) : 0,
    hotspots
  };
}

function extractHotspots(mask, width, height, minPixels, limit) {
  const visited = new Uint8Array(mask.length);
  const qx = new Int32Array(mask.length);
  const qy = new Int32Array(mask.length);
  const regions = [];

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const start = y * width + x;
      if (!mask[start] || visited[start]) continue;

      let head = 0;
      let tail = 0;
      qx[tail] = x;
      qy[tail] = y;
      tail += 1;
      visited[start] = 1;

      let count = 0;
      let minX = x;
      let maxX = x;
      let minY = y;
      let maxY = y;

      while (head < tail) {
        const cx = qx[head];
        const cy = qy[head];
        head += 1;
        count += 1;

        if (cx < minX) minX = cx;
        if (cx > maxX) maxX = cx;
        if (cy < minY) minY = cy;
        if (cy > maxY) maxY = cy;

        const n1 = cy * width + (cx + 1);
        const n2 = cy * width + (cx - 1);
        const n3 = (cy + 1) * width + cx;
        const n4 = (cy - 1) * width + cx;

        if (cx + 1 < width && mask[n1] && !visited[n1]) {
          visited[n1] = 1;
          qx[tail] = cx + 1;
          qy[tail] = cy;
          tail += 1;
        }
        if (cx - 1 >= 0 && mask[n2] && !visited[n2]) {
          visited[n2] = 1;
          qx[tail] = cx - 1;
          qy[tail] = cy;
          tail += 1;
        }
        if (cy + 1 < height && mask[n3] && !visited[n3]) {
          visited[n3] = 1;
          qx[tail] = cx;
          qy[tail] = cy + 1;
          tail += 1;
        }
        if (cy - 1 >= 0 && mask[n4] && !visited[n4]) {
          visited[n4] = 1;
          qx[tail] = cx;
          qy[tail] = cy - 1;
          tail += 1;
        }
      }

      if (count >= minPixels) {
        regions.push({
          pixels: count,
          x: minX,
          y: minY,
          width: maxX - minX + 1,
          height: maxY - minY + 1
        });
      }
    }
  }

  regions.sort((a, b) => b.pixels - a.pixels);
  return regions.slice(0, limit);
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args["reference-image"] || !args.actual || !args["out-dir"]) usage();

  const outDir = path.resolve(args["out-dir"]);
  ensureDir(outDir);
  const crop = parseCrop(args.crop);
  const density = parseNumber(args.density, 600);
  const actual = loadJson(path.resolve(args.actual));
  const actualDocBox = getActualBaseBox(actual);
  const referencePngPath = path.join(outDir, "reference.png");
  const actualSvgPath = path.join(outDir, "actual.svg");
  const actualPngPath = path.join(outDir, "actual.png");
  const diffPngPath = path.join(outDir, "diff.png");
  const metricsPath = path.join(outDir, "metrics.json");

  await cropOrCopyReference(path.resolve(args["reference-image"]), referencePngPath, crop, actualDocBox);
  const renderOptions = {
    visualReferenceOnly: Boolean(args["visual-reference-only"])
  };
  const actualSvg =
    renderOptions.visualReferenceOnly && actual?.kind === "figma-replay-bundle" && findPdfReferenceBackground(actual?.document)
      ? `<svg xmlns="http://www.w3.org/2000/svg"><!-- direct pdf_reference_background asset --></svg>`
      : actual?.kind === "figma-analysis-export"
      ? pluginToSvg(actual, crop)
      : bundleToSvg(actual, crop, renderOptions);
  fs.writeFileSync(actualSvgPath, actualSvg, "utf-8");
  await renderActualToPng(actual, crop, actualPngPath, density, renderOptions);
  const metrics = await diffPng(referencePngPath, actualPngPath, diffPngPath, {
    blurSigma: args["blur-sigma"],
    deltaThreshold: args["delta-threshold"]
  });
  fs.writeFileSync(metricsPath, JSON.stringify({ crop, density, ...metrics }, null, 2), "utf-8");
  console.log(JSON.stringify({ outDir, metrics }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
