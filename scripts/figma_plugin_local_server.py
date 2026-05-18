#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from build_intermediate_candidates import build_intermediate_model
from build_dense_ui_panel_ir_bundle import build_bundle as build_dense_ui_panel_bundle
from build_resolved_ppt_ir import build_page_ir
from build_visual_first_replay_bundle import build_bundle_from_page
from pptx_inspector import extract_slide_details

SERVER_BUILD_TAG = "V54"


def parse_slides(raw: Any) -> list[int]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [int(item) for item in raw]
    if isinstance(raw, str):
        values = [item.strip() for item in raw.split(",")]
        return [int(item) for item in values if item]
    raise ValueError("slides must be a list or comma-separated string")


class LocalHandler(BaseHTTPRequestHandler):
    server_version = f"CNSAtlasLocalPlugin/{SERVER_BUILD_TAG}"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/render-pptx":
            self._send_json(404, {"error": "not_found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            filename = str(payload.get("filename") or "upload.pptx")
            pptx_bytes = base64.b64decode(str(payload.get("fileBase64") or ""))
            slides = parse_slides(payload.get("slides"))

            with tempfile.TemporaryDirectory(prefix="cnsatlas-pptx-") as temp_dir:
                pptx_path = Path(temp_dir) / filename
                pptx_path.write_bytes(pptx_bytes)

                detail_payload = extract_slide_details(pptx_path, slides)
                intermediate = build_intermediate_model(detail_payload)
                bundles = []
                for page in intermediate.get("pages") or []:
                    resolved_page = build_page_ir(page, preserve_native_size=True)
                    if str(resolved_page.get("page_type") or "") == "dense_ui_panel":
                        bundles.append(build_dense_ui_panel_bundle(resolved_page, str(pptx_path)))
                    else:
                        bundles.append(
                            build_bundle_from_page(
                                page,
                                str(pptx_path),
                                preserve_native_size=True,
                            )
                        )
                collection = {
                    "kind": "figma-replay-collection",
                    "source_kind": "pptx-upload-visual-first",
                    "source_file": filename,
                    "pages": bundles,
                }
                page_sizes = []
                for bundle in bundles:
                    doc = bundle.get("document") or {}
                    bounds = doc.get("absoluteBoundingBox") or {}
                    slide_no = ((doc.get("debug") or {}).get("source_slide_no"))
                    if not isinstance(slide_no, int):
                        slide_no = None
                    page_sizes.append(
                        {
                            "slide": slide_no,
                            "width": bounds.get("width"),
                            "height": bounds.get("height"),
                            "pageName": bundle.get("page_name"),
                        }
                    )

            self._send_json(
                200,
                {
                    "ok": True,
                    "kind": "figma-replay-collection",
                    "serverVersion": self.server_version,
                    "requestedSlides": slides,
                    "pageCount": len(intermediate.get("pages") or []),
                    "payload": collection,
                    "slides": [page.get("slide_no") for page in intermediate.get("pages") or []],
                    "pageSizes": page_sizes,
                    "nativeSizeEnabled": True,
                },
            )
        except Exception as error:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": f"{type(error).__name__}: {error}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="Local helper server for Figma plugin PPTX upload.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=27184)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), LocalHandler)
    print(f"listening on http://{args.host}:{args.port} ({LocalHandler.server_version})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
