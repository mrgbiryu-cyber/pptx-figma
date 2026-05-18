#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


RIGHT_PANEL_X = 660.0


def load_json(path: str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def union_bounds(bounds_list: list[dict[str, Any]]) -> dict[str, float]:
    if not bounds_list:
        return {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0}
    min_x = min(float(b["x"]) for b in bounds_list)
    min_y = min(float(b["y"]) for b in bounds_list)
    max_x = max(float(b["x"]) + float(b["width"]) for b in bounds_list)
    max_y = max(float(b["y"]) + float(b["height"]) for b in bounds_list)
    return {
        "x": round(min_x, 2),
        "y": round(min_y, 2),
        "width": round(max_x - min_x, 2),
        "height": round(max_y - min_y, 2),
    }


def collect_reference_hits(node: Any, hits: list[dict[str, Any]], path: list[str]) -> None:
    if isinstance(node, dict):
        name = str(node.get("name") or "")
        node_type = str(node.get("type") or "")
        bounds = node.get("absoluteBoundingBox") or {}
        if node_type in {"FRAME", "GROUP", "TEXT", "RECTANGLE"}:
            lower = name.lower()
            if any(
                token in lower
                for token in (
                    "clip path group",
                    "issue",
                    "fold",
                    "screen",
                    "page",
                    "sticky",
                    "key visual",
                    "video",
                    "badge",
                    "v 1.",
                    "v1.",
                    "v2.",
                    "v 2.",
                    "v5.0",
                )
            ):
                hits.append(
                    {
                        "path": "/".join(path[-8:]),
                        "name": name,
                        "type": node_type,
                        "bounds": bounds,
                    }
                )
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                collect_reference_hits(value, hits, path + [str(key)])
    elif isinstance(node, list):
        for index, value in enumerate(node):
            collect_reference_hits(value, hits, path + [str(index)])


def summarize_page29(ir_payload: dict[str, Any]) -> dict[str, Any]:
    page = next(pg for pg in ir_payload["pages"] if pg.get("slide_no") == 29)
    atoms = page["atoms"]
    atoms_by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    atoms_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    atoms_by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    group_by_owner: dict[str, str] = {}

    for group in page["group_buckets"]:
        for owner_id in group["owner_ids"]:
            group_by_owner[owner_id] = group["group_id"]

    for atom in atoms:
        owner_id = str(atom.get("owner_id") or "")
        group_id = group_by_owner.get(owner_id, owner_id)
        chunk_id = str(atom.get("chunk_id") or "")
        atoms_by_owner[owner_id].append(atom)
        atoms_by_group[group_id].append(atom)
        atoms_by_chunk[chunk_id].append(atom)

    owner_summary = []
    for owner_id, owner_atoms in sorted(atoms_by_owner.items()):
        roles = Counter(str(atom.get("layer_role") or "") for atom in owner_atoms)
        bounds = union_bounds([atom.get("visual_bounds_px") or atom.get("source_bounds_px") for atom in owner_atoms])
        owner_summary.append(
            {
                "owner_id": owner_id,
                "atom_count": len(owner_atoms),
                "roles": dict(roles),
                "bounds": bounds,
            }
        )

    group_summary = []
    for group_id, group_atoms in sorted(atoms_by_group.items()):
        roles = Counter(str(atom.get("layer_role") or "") for atom in group_atoms)
        owners = Counter(str(atom.get("owner_id") or "") for atom in group_atoms)
        bounds = union_bounds([atom.get("visual_bounds_px") or atom.get("source_bounds_px") for atom in group_atoms])
        group_summary.append(
            {
                "group_id": group_id,
                "atom_count": len(group_atoms),
                "owner_count": len(owners),
                "owners": dict(owners),
                "roles": dict(roles),
                "bounds": bounds,
            }
        )

    chunk_summary = []
    for chunk_id, chunk_atoms in sorted(atoms_by_chunk.items()):
        roles = Counter(str(atom.get("layer_role") or "") for atom in chunk_atoms)
        owners = Counter(str(atom.get("owner_id") or "") for atom in chunk_atoms)
        bounds = union_bounds([atom.get("visual_bounds_px") or atom.get("source_bounds_px") for atom in chunk_atoms])
        chunk_summary.append(
            {
                "chunk_id": chunk_id,
                "atom_count": len(chunk_atoms),
                "owner_count": len(owners),
                "owners": dict(owners),
                "roles": dict(roles),
                "bounds": bounds,
            }
        )

    panel_small_assets = []
    global_small_assets = []
    for owner_id in ("dense_ui_panel:panel_small_assets", "dense_ui_panel:panel_overlay_notes"):
        for atom in atoms_by_owner.get(owner_id, []):
            panel_small_assets.append(atom["id"])
    for atom in atoms_by_owner.get("dense_ui_panel:global_ui_assets", []):
        global_small_assets.append(atom["id"])

    recommendations = [
        {
            "problem": "description_body_chunk should replace semantic-heavy text grouping",
            "evidence": {
                "owners": next(
                    (g["owners"] for g in chunk_summary if g["chunk_id"] == "dense_ui_panel:description_body_chunk"),
                    {},
                ),
                "roles": next(
                    (g["roles"] for g in chunk_summary if g["chunk_id"] == "dense_ui_panel:description_body_chunk"),
                    {},
                ),
            },
            "suggestion": "Use `description_body_chunk` as the visual chunk, but keep semantic support layers out of final composition by default.",
        },
        {
            "problem": "small assets mix panel and global UI atoms",
            "evidence": {
                "panel_small_assets": len(panel_small_assets),
                "global_small_assets": len(global_small_assets),
            },
            "suggestion": "Split into `panel_small_assets` and `global_ui_assets` before rendering.",
        },
        {
            "problem": "version_stack is already a coherent visual chunk",
            "evidence": {
                "owner": next(
                    (o for o in owner_summary if o["owner_id"] == "dense_ui_panel:version_stack"),
                    {},
                )
            },
            "suggestion": "Keep as a single visual chunk and avoid partial additive hybrid overlays.",
        },
    ]

    return {
        "page_title": page["title"],
        "page_type": page["page_type"],
        "owner_summary": owner_summary,
        "group_summary": group_summary,
        "chunk_summary": chunk_summary,
        "small_asset_split_preview": {
            "panel_small_asset_count": len(panel_small_assets),
            "global_ui_asset_count": len(global_small_assets),
            "panel_examples": panel_small_assets[:12],
            "global_examples": global_small_assets[:12],
        },
        "recommendations": recommendations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build dense_ui_panel chunk classification report")
    parser.add_argument(
        "--reference",
        default=str(Path(__file__).resolve().parent / "reference-page-3.json"),
        help="Reference plugin JSON path",
    )
    parser.add_argument(
        "--ir",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "resolved-ppt-ir-12-19-29.json"),
        help="Resolved PPT IR path",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "dense-ui-panel-chunk-report.json"),
        help="Output report path",
    )
    args = parser.parse_args()

    reference = load_json(args.reference)
    ir_payload = load_json(args.ir)

    reference_hits: list[dict[str, Any]] = []
    collect_reference_hits(reference, reference_hits, [])

    report = {
        "report_version": "dense-ui-panel-chunk-report-v1",
        "reference_summary": {
            "interesting_node_count": len(reference_hits),
            "sample_nodes": reference_hits[:40],
        },
        "page29_summary": summarize_page29(ir_payload),
    }

    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
