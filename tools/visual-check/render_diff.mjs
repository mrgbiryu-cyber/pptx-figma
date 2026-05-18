#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import sharp from "sharp";

function usage() {
  console.error(
    "usage: node render_diff.mjs --reference <plugin.json|bundle.json> --actual <bundle.json> --out-dir <dir> [--crop x,y,w,h] [--density 600]"
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
      i -= 1;
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
  // Bounds are absolute in this checker. Ignore pure flip/scale matrices.
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

function renderBundleNode(node, pieces, offset) {
  const bbox = node.absoluteBoundingBox;
  if (!bbox) return;
  const x = bbox.x - offset.x;
  const y = bbox.y - offset.y;
  const w = bbox.width;
  const h = bbox.height;
  const type = node.type;
  const transform = nodeTransform(node, x, y);

  if (type === "SVG_BLOCK" && node.svgMarkup) {
    pieces.push(`<g transform="${transform}">${node.svgMarkup}</g>`);
  } else if (type === "RECTANGLE") {
    const fill = (node.fills || [])[0];
    const stroke = (node.strokes || [])[0];
    const strokeWidth = Number(node.strokeWeight || 0);
    pieces.push(
      `<rect x="0" y="0" width="${w}" height="${h}" transform="${transform}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
        stroke ? cssRgba(stroke) : "none"
      }" stroke-width="${strokeWidth}" />`
    );
  } else if (type === "TEXT") {
    const fill = (node.fills || [])[0];
    const style = node.style || {};
    const fontSize = Number(style.fontSize || 12);
    const fontFamily = style.fontFamily || "sans-serif";
    const lines = String(node.characters || "").split("\n");
    lines.forEach((line, index) => {
      const dy = fontSize + index * (Number(style.lineHeightPx || fontSize * 1.2));
      pieces.push(
        `<text x="0" y="${dy}" transform="${transform}" font-family="${esc(fontFamily)}" font-size="${fontSize}" fill="${cssRgba(
          fill,
          { r: 0, g: 0, b: 0, a: 1 }
        )}">${esc(line)}</text>`
      );
    });
  }

  for (const child of node.children || []) {
    renderBundleNode(child, pieces, offset);
  }
}

function renderPluginNode(node, pieces) {
  const bbox = node.bounds_relative_to_scope;
  const type = node.type;
  if (bbox) {
    const x = bbox.x;
    const y = bbox.y;
    const w = bbox.width;
    const h = bbox.height;
    if (type === "RECTANGLE") {
      const fill = (node.fills || []).find((item) => item.visible !== false);
      const stroke = (node.strokes || []).find((item) => item.visible !== false);
      pieces.push(
        `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill ? cssRgba(fill) : "none"}" stroke="${
          stroke ? cssRgba(stroke) : "none"
        }" stroke-width="${Number(node.stroke_weight || 0)}" rx="${Number(node.corner_radius || 0)}" />`
      );
    } else if (type === "VECTOR") {
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
    }
  }

  for (const child of node.children || []) {
    renderPluginNode(child, pieces);
  }
}

function bundleToSvg(bundle, crop) {
  const doc = bundle.document;
  const pageBox = crop || doc.absoluteBoundingBox;
  const pieces = [];
  pieces.push(`<rect x="0" y="0" width="${pageBox.width}" height="${pageBox.height}" fill="white" />`);
  renderBundleNode(doc, pieces, { x: pageBox.x, y: pageBox.y });
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${pageBox.width}" height="${pageBox.height}" viewBox="0 0 ${pageBox.width} ${pageBox.height}">${pieces.join("")}</svg>`;
}

function pluginToSvg(reference, crop) {
  const root = reference.nodes[0];
  const pageBox = crop || reference.scope_bounds || root.bounds_relative_to_scope;
  const pieces = [];
  pieces.push(`<rect x="0" y="0" width="${pageBox.width}" height="${pageBox.height}" fill="white" />`);
  for (const child of root.children || []) {
    renderPluginNode(child, pieces);
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${pageBox.width}" height="${pageBox.height}" viewBox="0 0 ${pageBox.width} ${pageBox.height}">${pieces.join("")}</svg>`;
}

async function svgToPng(svg, outputPath, density = 600) {
  await sharp(Buffer.from(svg), { density: Math.max(72, Math.round(density)) }).png().toFile(outputPath);
}

async function diffPng(referencePng, actualPng, outPath) {
  const ref = sharp(referencePng);
  const act = sharp(actualPng);
  const refMeta = await ref.metadata();
  const actMeta = await act.metadata();
  const width = Math.max(refMeta.width || 0, actMeta.width || 0);
  const height = Math.max(refMeta.height || 0, actMeta.height || 0);
  const refBuf = await ref.resize(width, height).ensureAlpha().raw().toBuffer();
  const actBuf = await act.resize(width, height).ensureAlpha().raw().toBuffer();

  const diff = Buffer.alloc(refBuf.length);
  let changed = 0;
  for (let i = 0; i < refBuf.length; i += 4) {
    const dr = Math.abs(refBuf[i] - actBuf[i]);
    const dg = Math.abs(refBuf[i + 1] - actBuf[i + 1]);
    const db = Math.abs(refBuf[i + 2] - actBuf[i + 2]);
    const da = Math.abs(refBuf[i + 3] - actBuf[i + 3]);
    const delta = dr + dg + db + da;
    if (delta > 24) {
      changed += 1;
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
  return {
    width,
    height,
    changed_pixels: changed,
    changed_ratio: width * height ? changed / (width * height) : 0
  };
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.reference || !args.actual || !args["out-dir"]) usage();
  const density = Number.isFinite(Number(args.density)) ? Number(args.density) : 600;
  const outDir = path.resolve(args["out-dir"]);
  ensureDir(outDir);
  const crop = parseCrop(args.crop);

  const reference = loadJson(path.resolve(args.reference));
  const actual = loadJson(path.resolve(args.actual));

  const referenceSvg =
    reference.kind === "figma-analysis-export" ? pluginToSvg(reference, crop) : bundleToSvg(reference, crop);
  const actualSvg = bundleToSvg(actual, crop);

  const referenceSvgPath = path.join(outDir, "reference.svg");
  const actualSvgPath = path.join(outDir, "actual.svg");
  const referencePngPath = path.join(outDir, "reference.png");
  const actualPngPath = path.join(outDir, "actual.png");
  const diffPngPath = path.join(outDir, "diff.png");
  const metricsPath = path.join(outDir, "metrics.json");

  fs.writeFileSync(referenceSvgPath, referenceSvg, "utf-8");
  fs.writeFileSync(actualSvgPath, actualSvg, "utf-8");
  await svgToPng(referenceSvg, referencePngPath, density);
  await svgToPng(actualSvg, actualPngPath, density);
  const metrics = await diffPng(referencePngPath, actualPngPath, diffPngPath);
  fs.writeFileSync(metricsPath, JSON.stringify({ crop, density, ...metrics }, null, 2), "utf-8");
  console.log(JSON.stringify({ outDir, metrics }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
