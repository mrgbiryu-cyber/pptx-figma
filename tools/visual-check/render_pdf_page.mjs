import fs from 'fs/promises';
import path from 'path';
import { getDocument } from 'pdfjs-dist/legacy/build/pdf.mjs';
import { createCanvas } from '@napi-rs/canvas';

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key.startsWith('--')) {
      args[key.slice(2)] = value;
      i += 1;
    }
  }
  return args;
}

function parseCrop(value) {
  if (!value) return null;
  const parts = String(value).split(',').map((v) => Number(v.trim()));
  if (parts.length !== 4 || parts.some((v) => !Number.isFinite(v))) return null;
  return {
    x: Math.max(0, Math.floor(parts[0])),
    y: Math.max(0, Math.floor(parts[1])),
    width: Math.max(1, Math.floor(parts[2])),
    height: Math.max(1, Math.floor(parts[3])),
  };
}

class NodeCanvasFactory {
  create(width, height) {
    const canvas = createCanvas(width, height);
    const context = canvas.getContext('2d');
    return { canvas, context };
  }
  reset(canvasAndContext, width, height) {
    canvasAndContext.canvas.width = width;
    canvasAndContext.canvas.height = height;
  }
  destroy(canvasAndContext) {
    canvasAndContext.canvas.width = 0;
    canvasAndContext.canvas.height = 0;
    canvasAndContext.canvas = null;
    canvasAndContext.context = null;
  }
}

async function main() {
  const args = parseArgs(process.argv);
  const pdfPath = args.pdf;
  const out = args.out;
  const pageNum = Number(args.page || 1);
  const scale = Number(args.scale || 2);
  const tileSize = Number(args['tile-size'] || 0);
  const maxImageSize = Number(args['max-image-size'] || -1);
  const crop = parseCrop(args.crop);
  if (!pdfPath || !out) throw new Error('Usage: --pdf path --page N --out file [--scale 2]');

  const data = await fs.readFile(pdfPath);
  const loadingTask = getDocument({
    data: new Uint8Array(data),
    ...(Number.isFinite(maxImageSize) && maxImageSize > 0 ? { maxImageSize } : {}),
  });
  const pdf = await loadingTask.promise;
  const page = await pdf.getPage(pageNum);
  const viewport = page.getViewport({ scale });
  const fullWidth = Math.ceil(viewport.width);
  const fullHeight = Math.ceil(viewport.height);
  const width = crop ? Math.min(crop.width, Math.max(1, fullWidth - crop.x)) : fullWidth;
  const height = crop ? Math.min(crop.height, Math.max(1, fullHeight - crop.y)) : fullHeight;
  const offsetX = crop ? crop.x : 0;
  const offsetY = crop ? crop.y : 0;
  const factory = new NodeCanvasFactory();
  let png;

  if (!crop && tileSize > 0 && (width > tileSize || height > tileSize)) {
    const { canvas: finalCanvas, context: finalContext } = factory.create(width, height);
    finalContext.fillStyle = '#ffffff';
    finalContext.fillRect(0, 0, width, height);
    for (let ty = 0; ty < height; ty += tileSize) {
      for (let tx = 0; tx < width; tx += tileSize) {
        const tw = Math.min(tileSize, width - tx);
        const th = Math.min(tileSize, height - ty);
        const { canvas, context } = factory.create(tw, th);
        await page.render({
          canvasContext: context,
          viewport,
          canvasFactory: factory,
          transform: [1, 0, 0, 1, -tx, -ty],
        }).promise;
        finalContext.drawImage(canvas, tx, ty);
        factory.destroy({ canvas, context });
      }
    }
    png = finalCanvas.toBuffer('image/png');
    factory.destroy({ canvas: finalCanvas, context: finalContext });
  } else {
    const { canvas, context } = factory.create(width, height);
    const renderOptions = { canvasContext: context, viewport, canvasFactory: factory };
    if (offsetX !== 0 || offsetY !== 0) {
      renderOptions.transform = [1, 0, 0, 1, -offsetX, -offsetY];
    }
    await page.render(renderOptions).promise;
    png = canvas.toBuffer('image/png');
    factory.destroy({ canvas, context });
  }

  page.cleanup();
  await pdf.destroy();
  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, png);
  console.log(
    JSON.stringify(
      {
        page: pageNum,
        width,
        height,
        full_width: fullWidth,
        full_height: fullHeight,
        out,
        tile_size: tileSize || null,
        max_image_size: maxImageSize > 0 ? maxImageSize : null,
        crop: crop ? { x: offsetX, y: offsetY, width, height } : null,
      },
      null,
      2
    )
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
