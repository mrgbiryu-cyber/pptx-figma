#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import sharp from "sharp";

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = true;
    } else {
      args[key] = next;
      i += 1;
    }
  }
  return args;
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function run(cmd, cmdArgs) {
  const result = spawnSync(cmd, cmdArgs, { stdio: "pipe", encoding: "utf-8" });
  if (result.status !== 0) {
    throw new Error(`${cmd} ${cmdArgs.join(" ")} failed\n${result.stderr || result.stdout}`);
  }
  return result.stdout.trim();
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf-8"));
}

function parseCrop(crop) {
  const parts = String(crop || "")
    .split(",")
    .map((v) => Number(v.trim()));
  if (parts.length !== 4 || parts.some((v) => !Number.isFinite(v))) return null;
  return { x: parts[0], y: parts[1], width: parts[2], height: parts[3] };
}

function clampCrop(cropObj, width, height) {
  if (!cropObj) return null;
  const x = Math.max(0, Math.min(Math.floor(cropObj.x), Math.max(0, width - 1)));
  const y = Math.max(0, Math.min(Math.floor(cropObj.y), Math.max(0, height - 1)));
  const w = Math.max(1, Math.min(Math.floor(cropObj.width), width - x));
  const h = Math.max(1, Math.min(Math.floor(cropObj.height), height - y));
  return `${x},${y},${w},${h}`;
}

function getDocBox(actualJsonPath) {
  try {
    const actual = readJson(actualJsonPath);
    if (actual?.kind === "figma-analysis-export") {
      const box = actual.scope_bounds || actual.nodes?.[0]?.bounds_relative_to_scope;
      if (box?.width > 0 && box?.height > 0) return box;
      return null;
    }
    const box = actual?.document?.absoluteBoundingBox;
    if (box?.width > 0 && box?.height > 0) return box;
  } catch {
    // ignore
  }
  return null;
}

function projectCrop(cropObj, fromW, fromH, toW, toH) {
  if (!cropObj) return null;
  if (!(fromW > 0 && fromH > 0 && toW > 0 && toH > 0)) return cropObj;
  const sx = toW / fromW;
  const sy = toH / fromH;
  return {
    x: cropObj.x * sx,
    y: cropObj.y * sy,
    width: cropObj.width * sx,
    height: cropObj.height * sy,
  };
}

async function detectSlideSize(baseDir, slideNo, referenceImage) {
  if (fs.existsSync(referenceImage)) {
    try {
      const meta = await sharp(referenceImage).metadata();
      const width = Number(meta.width || 0);
      const height = Number(meta.height || 0);
      if (width > 0 && height > 0) {
        return { width, height };
      }
    } catch {
      // fallback to metrics or default
    }
  }
  const metricsPath = path.join(baseDir, `slide${slideNo}-cmp`, "metrics.json");
  if (fs.existsSync(metricsPath)) {
    const metrics = readJson(metricsPath);
    const width = Number(metrics.width || 0);
    const height = Number(metrics.height || 0);
    if (width > 0 && height > 0) {
      return { width, height };
    }
  }
  return { width: 1280, height: 720 };
}

const GOAL_CHECKS = [
  { goal: "arrow", slide_no: 12, crop: "120,100,1020,520", coord_space: "1280x720", note: "flow connectors" },
  { goal: "arrow", slide_no: 19, crop: "140,120,1080,520", coord_space: "1280x720", note: "flow + connector arrows" },
  { goal: "arrow", slide_no: 9, crop: "70,160,1060,460", coord_space: "1280x720", note: "process arrows" },

  { goal: "text", slide_no: 29, crop: "20,10,940,70", coord_space: "1280x720", note: "top meta labels/values" },
  { goal: "text", slide_no: 29, crop: "600,110,340,420", coord_space: "1280x720", note: "right panel text density" },
  { goal: "text", slide_no: 26, crop: "60,70,1180,640", coord_space: "1280x720", note: "table text wrap/size" },

  { goal: "z_order", slide_no: 29, crop: "15,70,320,300", coord_space: "1280x720", note: "left viewer layering" },
  { goal: "z_order", slide_no: 33, crop: "20,50,330,300", coord_space: "1280x720", note: "dense left stack layering" },
  { goal: "z_order", slide_no: 36, crop: "20,50,330,300", coord_space: "1280x720", note: "dense left stack layering" },
  { goal: "z_order", slide_no: 9, crop: "40,240,900,430", coord_space: "1280x720", note: "large bg + foreground layering" },

  { goal: "table", slide_no: 19, crop: "560,230,680,300", coord_space: "1280x720", note: "table/cell fidelity" },
  { goal: "table", slide_no: 26, crop: "40,60,1200,640", coord_space: "1280x720", note: "table-heavy grid/cells" },
  { goal: "table", slide_no: 22, crop: "240,180,1010,500", coord_space: "1280x720", note: "table baseline consistency" },
];

const GOAL_WEIGHTS = {
  arrow: 0.35,
  text: 0.25,
  z_order: 0.25,
  table: 0.15,
};

function aggregateGoal(rows, goal) {
  const picked = rows.filter((row) => row.goal === goal);
  if (!picked.length) {
    return { goal, count: 0, avg: 0, min: 0, max: 0 };
  }
  const scores = picked.map((row) => Number(row.match_score || 0));
  const sum = scores.reduce((acc, value) => acc + value, 0);
  return {
    goal,
    count: picked.length,
    avg: sum / picked.length,
    min: Math.min(...scores),
    max: Math.max(...scores),
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const baseDir = path.resolve(args["base-dir"] || "docs/render-diff/current-fulltest-pages-all");
  const outDir = path.resolve(args["out-dir"] || "docs/render-diff/goal-score");
  ensureDir(outDir);

  const rows = [];
  for (const check of GOAL_CHECKS) {
    const referenceImage = path.join(baseDir, `slide${check.slide_no}.png`);
    const actualJson = path.join(baseDir, `slide${check.slide_no}.json`);
    if (!fs.existsSync(referenceImage) || !fs.existsSync(actualJson)) {
      rows.push({
        ...check,
        status: "missing_input",
        match_score: null,
        changed_ratio: null,
      });
      continue;
    }

    const checkOutDir = path.join(
      outDir,
      `${check.goal}-s${check.slide_no}-${check.crop.replaceAll(",", "_")}`
    );
    ensureDir(checkOutDir);
    const docBox = getDocBox(actualJson);
    let cropObj = parseCrop(check.crop);
    if (cropObj && check.coord_space === "1280x720" && docBox?.width && docBox?.height) {
      cropObj = projectCrop(cropObj, 1280, 720, docBox.width, docBox.height);
    }
    const cropWidth = Number(docBox?.width || 0);
    const cropHeight = Number(docBox?.height || 0);
    let safeCrop = null;
    if (cropObj && cropWidth > 0 && cropHeight > 0) {
      safeCrop = clampCrop(cropObj, cropWidth, cropHeight);
    } else {
      const size = await detectSlideSize(baseDir, check.slide_no, referenceImage);
      safeCrop = clampCrop(cropObj, size.width, size.height);
    }
    run("node", [
      "tools/visual-check/compare_pdf_to_bundle.mjs",
      "--reference-image", referenceImage,
      "--actual", actualJson,
      "--out-dir", checkOutDir,
      "--crop", safeCrop || check.crop,
    ]);
    const metrics = readJson(path.join(checkOutDir, "metrics.json"));
    rows.push({
      ...check,
      status: "ok",
      applied_crop: safeCrop || check.crop,
      match_score: Number(metrics.match_score || 0),
      changed_ratio: Number(metrics.changed_ratio || 0),
      out_dir: checkOutDir,
    });
  }

  const goals = ["arrow", "text", "z_order", "table"].map((goal) => aggregateGoal(rows, goal));
  const weighted = goals.reduce(
    (acc, item) => acc + Number(item.avg || 0) * Number(GOAL_WEIGHTS[item.goal] || 0),
    0
  );

  const report = {
    kind: "goal-track-score-report",
    generated_at: new Date().toISOString(),
    base_dir: baseDir,
    goal_weights: GOAL_WEIGHTS,
    goals,
    weighted_goal_score: Number(weighted.toFixed(6)),
    rows,
  };

  const outPath = path.join(outDir, "report.json");
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2), "utf-8");

  console.log(`saved ${outPath}`);
  for (const item of goals) {
    console.log(
      `${item.goal}: avg=${item.avg.toFixed(4)} min=${item.min.toFixed(4)} max=${item.max.toFixed(4)} count=${item.count}`
    );
  }
  console.log(`weighted_goal_score=${report.weighted_goal_score.toFixed(4)}`);
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
