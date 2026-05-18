#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import sharp from "sharp";

function usage() {
  console.error(
    "usage: node build_review_board.mjs --dir <cmp-dir> [--out <board.png>] [--title <text>]"
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

function esc(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function readMetrics(metricsPath) {
  if (!fs.existsSync(metricsPath)) return null;
  return JSON.parse(fs.readFileSync(metricsPath, "utf-8"));
}

async function makeLabel(text, width, height = 42, fill = "#111827") {
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">
      <rect x="0" y="0" width="${width}" height="${height}" fill="#f3f4f6" />
      <text x="16" y="28" font-family="Malgun Gothic, sans-serif" font-size="20" fill="${fill}">${esc(text)}</text>
    </svg>
  `;
  return sharp(Buffer.from(svg)).png().toBuffer();
}

async function makeHeader(title, subtitle, width, height = 76) {
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">
      <rect x="0" y="0" width="${width}" height="${height}" fill="#ffffff" />
      <text x="20" y="30" font-family="Malgun Gothic, sans-serif" font-size="24" font-weight="700" fill="#111827">${esc(title)}</text>
      <text x="20" y="58" font-family="Malgun Gothic, sans-serif" font-size="16" fill="#4b5563">${esc(subtitle)}</text>
    </svg>
  `;
  return sharp(Buffer.from(svg)).png().toBuffer();
}

async function main() {
  const args = parseArgs(process.argv);
  const dir = args.dir;
  if (!dir) usage();

  const referencePath = path.join(dir, "reference.png");
  const actualPath = path.join(dir, "actual.png");
  const diffPath = path.join(dir, "diff.png");
  const metricsPath = path.join(dir, "metrics.json");
  if (!fs.existsSync(referencePath) || !fs.existsSync(actualPath) || !fs.existsSync(diffPath)) {
    throw new Error(`missing comparison images in ${dir}`);
  }

  const metrics = await readMetrics(metricsPath);
  const title = args.title || path.basename(dir);
  const subtitle = metrics
    ? `match=${Number(metrics.match_score || 0).toFixed(4)} changed=${Number(metrics.changed_ratio || 0).toFixed(4)}`
    : "comparison board";

  const reference = sharp(referencePath);
  const actual = sharp(actualPath);
  const diff = sharp(diffPath);
  const meta = await reference.metadata();
  const width = meta.width || 960;
  const height = meta.height || 540;
  const gap = 16;
  const panelWidth = width;
  const boardWidth = panelWidth * 3 + gap * 4;
  const boardHeight = 76 + 42 + height + gap * 4;

  const header = await makeHeader(title, subtitle, boardWidth, 76);
  const labelRef = await makeLabel("REFERENCE", panelWidth);
  const labelActual = await makeLabel("ACTUAL", panelWidth);
  const labelDiff = await makeLabel("DIFF", panelWidth, 42, "#7f1d1d");

  const canvas = sharp({
    create: {
      width: boardWidth,
      height: boardHeight,
      channels: 4,
      background: "#e5e7eb",
    },
  });

  const topY = gap;
  const labelY = topY + 76 + gap;
  const imageY = labelY + 42;

  const x1 = gap;
  const x2 = x1 + panelWidth + gap;
  const x3 = x2 + panelWidth + gap;

  await canvas
    .composite([
      { input: header, left: 0, top: topY },
      { input: labelRef, left: x1, top: labelY },
      { input: labelActual, left: x2, top: labelY },
      { input: labelDiff, left: x3, top: labelY },
      { input: await reference.png().toBuffer(), left: x1, top: imageY },
      { input: await actual.png().toBuffer(), left: x2, top: imageY },
      { input: await diff.png().toBuffer(), left: x3, top: imageY },
    ])
    .png()
    .toFile(args.out || path.join(dir, "board.png"));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
