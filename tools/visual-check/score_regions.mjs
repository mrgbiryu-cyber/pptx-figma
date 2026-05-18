#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

function usage() {
  console.error(
    "usage: node score_regions.mjs (--reference <plugin.json> | --reference-image <reference.png>) --actual <bundle.json> --out-dir <dir> [--profile slide29]"
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

const REGION_PROFILES = {
  slide29: [
    { key: "full", crop: null },
    { key: "top_meta", crop: "20,10,940,70" },
    { key: "left_static", crop: "25,455,250,80" },
    { key: "left_option", crop: "30,395,245,62" },
    { key: "left_viewer", crop: "15,70,300,260" },
    { key: "center_controls", crop: "360,320,260,170" },
    { key: "right_panel", crop: "575,15,370,510" }
  ]
};

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function runRenderDiff(scriptPath, reference, actual, outDir, crop) {
  const blurSigma = process.env.VISUAL_DIFF_BLUR_SIGMA || "1.2";
  const deltaThreshold = process.env.VISUAL_DIFF_DELTA_THRESHOLD || "40";
  const args = [scriptPath];
  if (reference.image) {
    args.push("--reference-image", reference.image);
  } else {
    args.push("--reference", reference.json);
  }
  args.push("--actual", actual, "--out-dir", outDir);
  if (crop) {
    args.push("--crop", crop);
  }
  if (reference.image) {
    args.push("--blur-sigma", blurSigma, "--delta-threshold", deltaThreshold);
  }
  const result = spawnSync("node", args, {
    stdio: "pipe",
    encoding: "utf-8"
  });
  if (result.status !== 0) {
    throw new Error(result.stderr || result.stdout || `render_diff failed for ${outDir}`);
  }
  const metricsPath = path.join(outDir, "metrics.json");
  return JSON.parse(fs.readFileSync(metricsPath, "utf-8"));
}

function scoreFromMetrics(metrics) {
  const changedRatio = Number(metrics.changed_ratio || 0);
  return {
    changed_ratio: changedRatio,
    match_score: Number((1 - changedRatio).toFixed(4))
  };
}

function aggregateScore(regionRows) {
  const weighted = regionRows
    .filter((row) => row.key !== "full")
    .map((row) => {
      const crop = row.cropObj;
      const area = crop ? crop.width * crop.height : row.metrics.width * row.metrics.height;
      return { area, score: row.match_score };
    });
  const totalArea = weighted.reduce((sum, row) => sum + row.area, 0);
  if (!totalArea) return 0;
  const sum = weighted.reduce((acc, row) => acc + row.area * row.score, 0);
  return Number((sum / totalArea).toFixed(4));
}

function parseCrop(crop) {
  if (!crop) return null;
  const [x, y, width, height] = crop.split(",").map(Number);
  return { x, y, width, height };
}

function buildGateRows(regionRows) {
  const byKey = Object.fromEntries(regionRows.map((row) => [row.key, row]));
  const checks = [
    {
      key: "right_panel_min",
      passed: (byKey.right_panel?.match_score || 0) >= 0.55,
      threshold: 0.55,
      actual: byKey.right_panel?.match_score || 0
    },
    {
      key: "left_static_min",
      passed: (byKey.left_static?.match_score || 0) >= 0.25,
      threshold: 0.25,
      actual: byKey.left_static?.match_score || 0
    },
    {
      key: "top_meta_min",
      passed: (byKey.top_meta?.match_score || 0) >= 0.5,
      threshold: 0.5,
      actual: byKey.top_meta?.match_score || 0
    }
  ];
  return checks;
}

function main() {
  const args = parseArgs(process.argv);
  if ((!args.reference && !args["reference-image"]) || !args.actual || !args["out-dir"]) usage();

  const profileName = args.profile || "slide29";
  const regions = REGION_PROFILES[profileName];
  if (!regions) {
    throw new Error(`Unknown profile: ${profileName}`);
  }

  const repoRoot = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "..");
  const scriptPath = path.resolve(
    path.dirname(new URL(import.meta.url).pathname),
    args["reference-image"] ? "compare_pdf_to_bundle.mjs" : "render_diff.mjs"
  );
  const outDir = path.resolve(args["out-dir"]);
  ensureDir(outDir);
  const reference = args["reference-image"]
    ? { image: path.resolve(args["reference-image"]) }
    : { json: path.resolve(args.reference) };

  const rows = [];
  for (const region of regions) {
    const regionOutDir = path.join(outDir, region.key);
    ensureDir(regionOutDir);
    const metrics = runRenderDiff(
      scriptPath,
      reference,
      path.resolve(args.actual),
      regionOutDir,
      region.crop
    );
    const score = scoreFromMetrics(metrics);
    rows.push({
      key: region.key,
      crop: region.crop,
      cropObj: parseCrop(region.crop),
      metrics,
      ...score,
      out_dir: path.relative(repoRoot, regionOutDir)
    });
  }

  const report = {
    kind: "visual-region-score-report",
    profile: profileName,
    reference: reference.image || reference.json,
    actual: path.resolve(args.actual),
    generated_at: new Date().toISOString(),
    global_match_score: aggregateScore(rows),
    full_match_score: rows.find((row) => row.key === "full")?.match_score || 0,
    regions: rows.map(({ cropObj, ...rest }) => rest),
    gates: buildGateRows(rows)
  };

  const reportPath = path.join(outDir, "report.json");
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf-8");
  console.log(JSON.stringify(report, null, 2));
}

main();
