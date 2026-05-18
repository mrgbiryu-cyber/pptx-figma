#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
_orig_get = requests.get


def _patched_get(*args, **kwargs):
    kwargs["verify"] = False
    return _orig_get(*args, **kwargs)


requests.get = _patched_get
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


FIGMA_TOKEN = "figd_"
FILE_KEY = "VdhL71dZBwFoqFeuPCuG1l"
PAGE_1_NODE_ID = "4:12282"
PAGE_2_NODE_ID = "4:11879"
PAGE_3_NODE_ID = "4:11477"

HEADERS = {"X-Figma-Token": FIGMA_TOKEN}


def save_json(filename: str, data: dict) -> None:
    with open(filename, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
    print(f"saved: {filename}")


def download_image(url: str, filename: str) -> None:
    response = requests.get(url)
    response.raise_for_status()
    Path(filename).write_bytes(response.content)
    print(f"downloaded: {filename}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Figma JSON and PNG snapshots.")
    parser.add_argument(
        "--scale",
        type=float,
        default=float(os.environ.get("FIGMA_IMAGE_SCALE", "4.0")),
        help="Figma images API scale (0.01~4.0).",
    )
    args = parser.parse_args()
    image_scale = max(0.01, min(4.0, float(args.scale)))

    print("start figma export")

    print("1) fetching full file json")
    res_full = requests.get(f"https://api.figma.com/v1/files/{FILE_KEY}", headers=HEADERS)
    res_full.raise_for_status()
    save_json("figma-full-file.json", res_full.json())

    fonts_used = res_full.json().get("document", {}).get("usedFonts", [])
    if fonts_used:
        save_json("figma-used-fonts.json", fonts_used)

    page_nodes = {
        "generated-visual-page-1": PAGE_1_NODE_ID,
        "generated-visual-page-2": PAGE_2_NODE_ID,
        "generated-visual-page-3": PAGE_3_NODE_ID,
    }

    print("2) fetching page node json")
    for filename, node_id in page_nodes.items():
        if not node_id:
            continue
        res_node = requests.get(
            f"https://api.figma.com/v1/files/{FILE_KEY}/nodes?ids={node_id}&geometry=paths",
            headers=HEADERS,
        )
        res_node.raise_for_status()
        save_json(filename, res_node.json())

    print(f"3) fetching page png urls (scale={image_scale})")
    node_ids = ",".join([node_id for node_id in page_nodes.values() if node_id])
    res_images = requests.get(
        f"https://api.figma.com/v1/images/{FILE_KEY}?ids={node_ids}&format=png&scale={image_scale}&use_absolute_bounds=true",
        headers=HEADERS,
    )
    res_images.raise_for_status()
    image_urls = res_images.json().get("images", {})

    if image_urls.get(PAGE_1_NODE_ID):
        download_image(image_urls[PAGE_1_NODE_ID], "figma-page-1.png")
    if image_urls.get(PAGE_2_NODE_ID):
        download_image(image_urls[PAGE_2_NODE_ID], "figma-page-2.png")
    if image_urls.get(PAGE_3_NODE_ID):
        download_image(image_urls[PAGE_3_NODE_ID], "figma-page-3.png")

    print("4) fetching image assets")
    Path("assets").mkdir(exist_ok=True)
    res_assets = requests.get(f"https://api.figma.com/v1/files/{FILE_KEY}/images", headers=HEADERS)
    res_assets.raise_for_status()
    assets_meta = res_assets.json().get("meta", {}).get("images", {})
    for image_ref, url in assets_meta.items():
        download_image(url, f"assets/{image_ref}.png")

    print("done")


if __name__ == "__main__":
    main()
