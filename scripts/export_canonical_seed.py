#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE_ID = "ws:cnsatlas"
PROJECT_ID = "proj:cnsatlas-ax"
DOCUMENT_ID = "doc:ppt-benchmark-pptsample"
REPO_ROOT = Path(__file__).resolve().parent.parent

NODE_SUBTYPE_TO_TYPE = {
    "text_block": "text",
    "labeled_shape": "shape",
    "shape": "shape",
    "connector": "connector",
    "group": "group",
    "section_block": "frame",
    "table": "table",
    "table_row": "row",
    "table_cell": "cell",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


def short_summary(value: str, limit: int = 180) -> str | None:
    normalized = normalize_text(value)
    if not normalized:
        return None
    return normalized[:limit]


def infer_mime_type(path: str | None) -> str | None:
    if not path:
        return None
    lowered = path.lower()
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
        return "image/jpeg"
    if lowered.endswith(".svg"):
        return "image/svg+xml"
    if lowered.endswith(".gif"):
        return "image/gif"
    return None


def candidate_entity_id(candidate: dict[str, Any]) -> str:
    prefix = "asset" if candidate["node_type"] == "asset" else "node"
    return f"{prefix}:{candidate['candidate_id']}"


def page_entity_id(slide_no: int) -> str:
    return f"page:{slide_no}"


def make_source_mapping(
    *,
    mapping_id: str,
    entity_type: str,
    entity_id: str,
    external_ref_id: str | None,
    source_path: str | None,
    raw_payload: dict[str, Any],
    pptx_path: str,
    timestamp: str,
) -> dict[str, Any]:
    return {
        "id": mapping_id,
        "internal_entity_type": entity_type,
        "internal_entity_id": entity_id,
        "source_type": "ppt",
        "external_container_id": pptx_path,
        "external_ref_id": external_ref_id,
        "source_path": source_path,
        "source_hash": None,
        "source_version": "phase2-v1",
        "is_primary": 1,
        "raw_payload_json": json.dumps(raw_payload, ensure_ascii=False),
        "fetched_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def export_canonical_seed(input_path: Path, output_path: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    timestamp = now_iso()
    pptx_path = payload["pptxPath"]
    document_title = Path(pptx_path).name

    seed: dict[str, Any] = {
        "source_pptx_path": pptx_path,
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "atlas_documents": [],
        "atlas_pages": [],
        "atlas_nodes": [],
        "atlas_assets": [],
        "atlas_source_mappings": [],
        "atlas_relations": [],
    }

    document_row = {
        "id": DOCUMENT_ID,
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "document_type": "planning_doc",
        "subtype": "ppt_file",
        "title": document_title,
        "description": "Benchmark PPT imported for phase 2 canonical and search validation",
        "status": "active",
        "primary_source_type": "ppt",
        "created_at": timestamp,
        "updated_at": timestamp,
        "deleted_at": None,
    }
    seed["atlas_documents"].append(document_row)
    seed["atlas_source_mappings"].append(
        make_source_mapping(
            mapping_id="map:document:ppt-benchmark-pptsample",
            entity_type="document",
            entity_id=DOCUMENT_ID,
            external_ref_id=document_title,
            source_path=pptx_path,
            raw_payload=document_row,
            pptx_path=pptx_path,
            timestamp=timestamp,
        )
    )

    for page in payload["pages"]:
        page_id = page_entity_id(page["slide_no"])
        page_row = {
            "id": page_id,
            "document_id": DOCUMENT_ID,
            "page_type": "ppt_slide",
            "subtype": "ppt_slide",
            "title": page["title_or_label"],
            "order_index": page["slide_no"],
            "source_ref_id": f"slide:{page['slide_no']}",
            "status": "active",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        seed["atlas_pages"].append(page_row)
        seed["atlas_source_mappings"].append(
            make_source_mapping(
                mapping_id=f"map:page:{page['slide_no']}",
                entity_type="page",
                entity_id=page_id,
                external_ref_id=str(page["slide_no"]),
                source_path=page["source_path"],
                raw_payload=page,
                pptx_path=pptx_path,
                timestamp=timestamp,
            )
        )

        candidate_id_to_entity_id = {
            candidate["candidate_id"]: candidate_entity_id(candidate)
            for candidate in page["candidates"]
        }

        for candidate in page["candidates"]:
            entity_id = candidate_id_to_entity_id[candidate["candidate_id"]]
            parent_candidate_id = candidate.get("parent_candidate_id")
            parent_entity_id = candidate_id_to_entity_id.get(parent_candidate_id)
            bounds_px = candidate.get("bounds_px")
            extra = candidate.get("extra") or {}
            source_mapping = make_source_mapping(
                mapping_id=f"map:{entity_id}",
                entity_type="asset" if candidate["node_type"] == "asset" else "node",
                entity_id=entity_id,
                external_ref_id=candidate.get("source_node_id"),
                source_path=candidate.get("source_path"),
                raw_payload=candidate,
                pptx_path=pptx_path,
                timestamp=timestamp,
            )
            seed["atlas_source_mappings"].append(source_mapping)

            if candidate["node_type"] == "asset":
                asset_row = {
                    "id": entity_id,
                    "document_id": DOCUMENT_ID,
                    "page_id": page_id,
                    "node_id": parent_entity_id if parent_entity_id and parent_entity_id.startswith("node:") else None,
                    "asset_type": candidate["subtype"],
                    "storage_url": extra.get("image_target"),
                    "mime_type": infer_mime_type(extra.get("image_target")),
                    "width": bounds_px["width"] if bounds_px else None,
                    "height": bounds_px["height"] if bounds_px else None,
                    "checksum": None,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                seed["atlas_assets"].append(asset_row)
                continue

            raw_text = candidate.get("text") or ""
            node_row = {
                "id": entity_id,
                "document_id": DOCUMENT_ID,
                "page_id": page_id,
                "parent_node_id": parent_entity_id if parent_entity_id and parent_entity_id.startswith("node:") else None,
                "node_type": NODE_SUBTYPE_TO_TYPE.get(candidate["subtype"], "shape"),
                "subtype": candidate["subtype"],
                "title": candidate.get("title") or candidate["subtype"],
                "raw_text": raw_text,
                "normalized_text": normalize_text(raw_text),
                "semantic_summary": short_summary(raw_text or candidate.get("title") or ""),
                "geometry_json": json.dumps(bounds_px, ensure_ascii=False) if bounds_px else None,
                "style_json": json.dumps(extra, ensure_ascii=False) if extra else None,
                "status": "active",
                "authoritative_source": "ppt",
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            seed["atlas_nodes"].append(node_row)

            if node_row["parent_node_id"]:
                seed["atlas_relations"].append(
                    {
                        "id": f"rel:parent:{node_row['parent_node_id']}->{node_row['id']}",
                        "from_entity_type": "node",
                        "from_entity_id": node_row["parent_node_id"],
                        "to_entity_type": "node",
                        "to_entity_id": node_row["id"],
                        "relation_type": "parent_child",
                        "subtype": candidate["subtype"],
                        "metadata_json": json.dumps(
                            {
                                "page_id": page_id,
                                "source_path": candidate.get("source_path"),
                            },
                            ensure_ascii=False,
                        ),
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return seed


def main() -> None:
    input_path = REPO_ROOT / "docs" / "ppt-intermediate-candidates-12-19-29.json"
    output_path = REPO_ROOT / "docs" / "canonical-seed-12-19-29.json"
    export_canonical_seed(input_path, output_path)
    print(f"Generated canonical seed: {output_path}")


if __name__ == "__main__":
    main()
