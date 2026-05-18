#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";

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

function run(cmd, cmdArgs) {
  const result = spawnSync(cmd, cmdArgs, { stdio: "pipe", encoding: "utf-8" });
  if (result.status !== 0) {
    throw new Error(`${cmd} ${cmdArgs.join(" ")} failed\n${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

function runWithResult(cmd, cmdArgs, options = {}) {
  return spawnSync(cmd, cmdArgs, {
    stdio: "pipe",
    encoding: "utf-8",
    timeout: options.timeoutMs,
  });
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf-8"));
}

function firstExisting(paths) {
  for (const candidate of paths) {
    const resolved = path.resolve(candidate);
    if (fs.existsSync(resolved)) return resolved;
  }
  return path.resolve(paths[0]);
}

function inferEndSlide(args, intermediate) {
  if (args.end) return Number(args.end);
  if (intermediate && fs.existsSync(intermediate)) {
    const payload = readJson(intermediate);
    const slideNos = (payload.pages || [])
      .map((page) => Number(page.slide_no))
      .filter((slideNo) => Number.isFinite(slideNo));
    if (slideNos.length) return Math.max(...slideNos);
  }
  return 39;
}

function resolveReferenceImage(referenceDir, slideNo) {
  if (!referenceDir) return null;
  const candidates = [
    `slide${slideNo}.png`,
    `${slideNo}.png`,
    `page-${slideNo}.png`,
    `slide-${slideNo}.png`,
    `p${slideNo}.png`,
  ];
  for (const name of candidates) {
    const file = path.join(referenceDir, name);
    if (fs.existsSync(file)) return file;
  }
  return null;
}

function parseScaleFallback(raw) {
  const tokens = String(raw || "")
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);
  if (!tokens.length) return [4, 3, 2, 1.5, 1];
  return [...new Set(tokens)];
}

function buildEditableCoverageReport(pythonBin, base, outPath) {
  const reportOut = outPath ? path.resolve(outPath) : path.join(base, "editable-coverage-report.json");
  run(pythonBin, [
    "scripts/build_editable_coverage_report.py",
    "--bundle-dir", base,
    "--out", reportOut,
  ]);
  return reportOut;
}

function upsertResult(results, row) {
  const idx = results.findIndex((r) => Number(r.slide_no) === Number(row.slide_no));
  if (idx >= 0) {
    results[idx] = row;
  } else {
    results.push(row);
  }
  results.sort((a, b) => Number(a.slide_no) - Number(b.slide_no));
}

function resolvedRange(results, fallbackStart, fallbackEnd) {
  if (!results.length) return { start: fallbackStart, end: fallbackEnd };
  const ordered = [...results].sort((a, b) => Number(a.slide_no) - Number(b.slide_no));
  return {
    start: Number(ordered[0].slide_no),
    end: Number(ordered[ordered.length - 1].slide_no),
  };
}

async function main() {
  const args = parseArgs(process.argv);
  const pptx = args.pptx
    ? path.resolve(args.pptx)
    : firstExisting(["sampling/current-test.pptx", "sampling/pptsample.pptx"]);
  const pdf = args.pdf
    ? path.resolve(args.pdf)
    : firstExisting(["sampling/fulling/current-test.reference.pdf", "sampling/fulltest/figma-page-pdf.pdf"]);
  const referenceDir = args["reference-dir"] ? path.resolve(args["reference-dir"]) : "";
  const base = path.resolve(args["base-dir"] || "docs/render-diff/current-fulltest-pages-all");
  const start = Number(args.start || 1);
  const inferredIntermediate =
    args.intermediate
      ? path.resolve(args.intermediate)
      : path.basename(pptx).toLowerCase() === "current-test.pptx" && fs.existsSync(path.resolve("sampling/current-test.intermediate.json"))
      ? path.resolve("sampling/current-test.intermediate.json")
      : "";
  const end = inferEndSlide(args, inferredIntermediate);
  const scale = String(args.scale || "4");
  const density = String(args.density || "600");
  const renderMode = String(args["render-mode"] || "default");
  const renderFallbackMode = String(args["render-fallback-mode"] || "");
  const tileSize = Number(args["tile-size"] || 0);
  const maxImageSize = Number(args["max-image-size"] || -1);
  const renderTimeoutMs = Number(args["render-timeout-ms"] || 120000);
  const scaleFallbacks = parseScaleFallback(args["scale-fallback"] || `${scale},3,2,1.5,1`);
  const pythonBin = String(args.python || process.env.PYTHON || "python");
  const intermediate = inferredIntermediate;
  const resume = Boolean(args.resume);
  const sleepMs = Number(args["sleep-ms"] || 150);
  const visualReferenceOnly = Boolean(args["visual-reference-only"]);
  const editableReport = !Boolean(args["skip-editable-report"]);
  const editableReportOut = args["editable-report-out"] ? path.resolve(args["editable-report-out"]) : path.join(base, "editable-coverage-report.json");

  if (args["dry-run"]) {
    process.stdout.write(
      JSON.stringify(
        {
          pptx,
          pdf,
          intermediate: intermediate || null,
          base,
          start,
          end,
          scale,
          density,
          python: pythonBin,
          visual_reference_only: visualReferenceOnly,
          editable_report: editableReport,
          editable_report_out: editableReportOut,
        },
        null,
        2
      ) + "\n"
    );
    return;
  }

  ensureDir(base);

  const partialPath = path.join(base, "report.partial.json");
  const results = [];
  if (resume && fs.existsSync(partialPath)) {
    const partial = readJson(partialPath);
    for (const row of partial.results || []) {
      upsertResult(results, row);
    }
  }

  for (let slideNo = start; slideNo <= end; slideNo += 1) {
    const referencePng = path.join(base, `slide${slideNo}.png`);
    const actualJson = path.join(base, `slide${slideNo}.json`);
    const cmpDir = path.join(base, `slide${slideNo}-cmp`);
    const metricsPath = path.join(cmpDir, "metrics.json");
    ensureDir(cmpDir);

    if (resume && fs.existsSync(metricsPath) && fs.existsSync(actualJson) && fs.existsSync(referencePng)) {
      const metrics = readJson(metricsPath);
      upsertResult(results, {
        slide_no: slideNo,
        match_score: metrics.match_score,
        changed_ratio: metrics.changed_ratio,
      });
      process.stdout.write(`skip ${slideNo} ${Number(metrics.match_score || 0).toFixed(4)}\n`);
      continue;
    }

    let usedScale = null;
    let usedRenderMode = null;
    const externalReference = resolveReferenceImage(referenceDir, slideNo);
    let referenceImageForCompare = referencePng;
    if (externalReference) {
      referenceImageForCompare = externalReference;
      process.stdout.write(`slide ${slideNo}: using external reference ${path.basename(externalReference)}\n`);
    } else {
      let renderError = null;
      for (const candidateScale of scaleFallbacks) {
        const modes = [renderMode];
        if (renderFallbackMode && renderFallbackMode !== renderMode) {
          modes.push(renderFallbackMode);
        }
        for (const mode of modes) {
          const renderScript =
            mode === "split4"
              ? "tools/visual-check/render_pdf_page_split4.mjs"
              : "tools/visual-check/render_pdf_page.mjs";
          const renderArgs = [
            renderScript,
            "--pdf", pdf,
            "--page", String(slideNo),
            "--out", referencePng,
            "--scale", String(candidateScale),
          ];
          if (mode !== "split4" && tileSize > 0) {
            renderArgs.push("--tile-size", String(tileSize));
          }
          if (maxImageSize > 0) {
            renderArgs.push("--max-image-size", String(maxImageSize));
          }
          const render = runWithResult("node", renderArgs, { timeoutMs: renderTimeoutMs });
          if (render.status === 0) {
            usedScale = candidateScale;
            usedRenderMode = mode;
            if (candidateScale !== Number(scale)) {
              process.stdout.write(`slide ${slideNo}: pdf_scale_fallback ${scale} -> ${candidateScale}\n`);
            }
            if (mode !== renderMode) {
              process.stdout.write(`slide ${slideNo}: render_mode_fallback ${renderMode} -> ${mode}\n`);
            }
            break;
          }
          const timedOut = Boolean(render.signal) && String(render.signal).toUpperCase().includes("SIGTERM");
          renderError = `${timedOut ? "timeout" : "failed"} @scale=${candidateScale} @mode=${mode} :: ${render.stderr || render.stdout}`;
          process.stdout.write(
            `slide ${slideNo}: render retry after ${timedOut ? "timeout" : "failure"} scale=${candidateScale} mode=${mode}\n`
          );
        }
        if (usedScale !== null) break;
      }
      if (usedScale === null) {
        throw new Error(`render_pdf_page.mjs failed for slide ${slideNo}\n${renderError || "unknown error"}`);
      }
    }

    const exportArgs = [
      "scripts/export_current_replay_bundle.py",
      "--pptx", pptx,
      "--slide", String(slideNo),
      "--out", actualJson,
      "--reference-pdf", pdf,
      "--reference-image", referenceImageForCompare,
    ];
    if (intermediate) {
      exportArgs.push("--intermediate", intermediate);
    }
    run(pythonBin, exportArgs);

    const compareArgs = [
      "tools/visual-check/compare_pdf_to_bundle.mjs",
      "--reference-image", referenceImageForCompare,
      "--actual", actualJson,
      "--out-dir", cmpDir,
      "--density", density,
    ];
    if (visualReferenceOnly) {
      compareArgs.push("--visual-reference-only");
    }
    run("node", compareArgs);

    const metrics = readJson(metricsPath);
    upsertResult(results, {
      slide_no: slideNo,
      match_score: metrics.match_score,
      changed_ratio: metrics.changed_ratio,
      reference_scale: usedScale,
      render_mode_used: usedRenderMode || renderMode,
      reference_source: externalReference ? "external_png" : "pdf_render",
    });

    fs.writeFileSync(
      partialPath,
      JSON.stringify(
        {
          kind: "current-fulltest-scan-partial",
          completed: results.length,
          ...resolvedRange(results, start, end),
          requested_start: start,
          requested_end: end,
          scale: Number(scale),
          density: Number(density),
          tile_size: tileSize > 0 ? tileSize : null,
          max_image_size: maxImageSize > 0 ? maxImageSize : null,
          render_mode: renderMode,
          render_fallback_mode: renderFallbackMode || null,
          reference_dir: referenceDir || null,
          intermediate: intermediate || null,
          python: pythonBin,
          visual_reference_only: visualReferenceOnly,
          render_timeout_ms: renderTimeoutMs,
          scale_fallback: scaleFallbacks,
          results,
        },
        null,
        2
      ),
      "utf-8"
    );

    process.stdout.write(`done ${slideNo} ${Number(metrics.match_score || 0).toFixed(4)}\n`);

    if (sleepMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, sleepMs));
    }
  }

  let editableCoverageReport = null;
  if (editableReport) {
    editableCoverageReport = buildEditableCoverageReport(pythonBin, base, editableReportOut);
  }

  fs.writeFileSync(
    path.join(base, "report.json"),
    JSON.stringify(
      {
        kind: "current-fulltest-scan-report",
        completed: results.length,
        ...resolvedRange(results, start, end),
        requested_start: start,
        requested_end: end,
        scale: Number(scale),
        density: Number(density),
        tile_size: tileSize > 0 ? tileSize : null,
        max_image_size: maxImageSize > 0 ? maxImageSize : null,
        render_mode: renderMode,
        render_fallback_mode: renderFallbackMode || null,
        reference_dir: referenceDir || null,
        intermediate: intermediate || null,
        python: pythonBin,
        visual_reference_only: visualReferenceOnly,
        editable_coverage_report: editableCoverageReport,
        render_timeout_ms: renderTimeoutMs,
        scale_fallback: scaleFallbacks,
        results,
      },
      null,
      2
    ),
    "utf-8"
  );
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
