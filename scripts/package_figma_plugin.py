#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dist" / "cnsatlas-figma-plugin-current.zip"

PACKAGE_FILES = [
    "figma-plugin/manifest.json",
    "figma-plugin/code.js",
    "figma-plugin/ui.html",
    "docs/figma-plugin-usage.md",
    "docs/figma-plugin-current-package.md",
    "scripts/build_intermediate_candidates.py",
    "scripts/build_ppt_replay_bundle.py",
    "scripts/build_visual_first_replay_bundle.py",
    "scripts/build_resolved_ppt_ir.py",
    "scripts/build_dense_ui_panel_ir_bundle.py",
    "scripts/export_current_replay_bundle.py",
    "scripts/figma_plugin_local_server.py",
    "scripts/ppt_source_extractor.py",
    "scripts/pptx_inspector.py",
    "docs/block-bundles/ir-dense-ui-panel-29.bundle.json",
    "docs/block-bundles/ir-dense-ui-panel-29-left-product-price-only.bundle.json",
]


def package_plugin(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as zip_file:
        for relative in PACKAGE_FILES:
            path = ROOT / relative
            if not path.exists():
                raise FileNotFoundError(f"Missing package file: {relative}")
            zip_file.write(path, arcname=relative)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package the current Figma plugin and replay pipeline files.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output zip path")
    args = parser.parse_args()

    output_path = Path(args.output).resolve()
    package_plugin(output_path)
    print(f"packaged {output_path}")


if __name__ == "__main__":
    main()
