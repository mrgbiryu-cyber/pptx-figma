#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split()).strip()


def append_projection(rows: list[dict[str, Any]], row: dict[str, Any]) -> None:
    searchable = normalize_text(row["searchable_text"])
    if not searchable:
        return
    row["searchable_text"] = searchable
    rows.append(row)


def build_search_projection(seed: dict[str, Any]) -> dict[str, Any]:
    updated_at = now_iso()
    rows: list[dict[str, Any]] = []

    for document in seed["atlas_documents"]:
        append_projection(
            rows,
            {
                "entity_type": "document",
                "entity_id": document["id"],
                "document_id": document["id"],
                "page_id": None,
                "searchable_text": " ".join(
                    part for part in [document["title"], document.get("description"), document["subtype"]] if part
                ),
                "metadata_json": json.dumps(
                    {
                        "title": document["title"],
                        "subtype": document["subtype"],
                        "primary_source_type": document["primary_source_type"],
                    },
                    ensure_ascii=False,
                ),
                "updated_at": updated_at,
            },
        )

    page_by_id = {page["id"]: page for page in seed["atlas_pages"]}

    for page in seed["atlas_pages"]:
        append_projection(
            rows,
            {
                "entity_type": "page",
                "entity_id": page["id"],
                "document_id": page["document_id"],
                "page_id": page["id"],
                "searchable_text": " ".join(
                    part for part in [page["title"], page["subtype"], page["source_ref_id"]] if part
                ),
                "metadata_json": json.dumps(
                    {
                        "title": page["title"],
                        "order_index": page["order_index"],
                        "page_type": page["page_type"],
                    },
                    ensure_ascii=False,
                ),
                "updated_at": updated_at,
            },
        )

    source_mapping_by_entity = {
        (row["internal_entity_type"], row["internal_entity_id"]): row for row in seed["atlas_source_mappings"]
    }

    for node in seed["atlas_nodes"]:
        page = page_by_id[node["page_id"]]
        source_mapping = source_mapping_by_entity.get(("node", node["id"]), {})
        append_projection(
            rows,
            {
                "entity_type": "node",
                "entity_id": node["id"],
                "document_id": node["document_id"],
                "page_id": node["page_id"],
                "searchable_text": " ".join(
                    part
                    for part in [
                        node["title"],
                        node.get("raw_text"),
                        node.get("normalized_text"),
                        node["subtype"],
                    ]
                    if part
                ),
                "metadata_json": json.dumps(
                    {
                        "title": node["title"],
                        "subtype": node["subtype"],
                        "node_type": node["node_type"],
                        "page_title": page["title"],
                        "source_path": source_mapping.get("source_path"),
                        "authoritative_source": node["authoritative_source"],
                    },
                    ensure_ascii=False,
                ),
                "updated_at": updated_at,
            },
        )

    for asset in seed["atlas_assets"]:
        page = page_by_id[asset["page_id"]]
        source_mapping = source_mapping_by_entity.get(("asset", asset["id"]), {})
        append_projection(
            rows,
            {
                "entity_type": "asset",
                "entity_id": asset["id"],
                "document_id": asset["document_id"],
                "page_id": asset["page_id"],
                "searchable_text": " ".join(
                    part
                    for part in [
                        asset["asset_type"],
                        asset.get("storage_url"),
                    ]
                    if part
                ),
                "metadata_json": json.dumps(
                    {
                        "asset_type": asset["asset_type"],
                        "storage_url": asset.get("storage_url"),
                        "mime_type": asset.get("mime_type"),
                        "source_path": source_mapping.get("source_path"),
                    },
                    ensure_ascii=False,
                ),
                "updated_at": updated_at,
            },
        )

    return {
        "generated_at": updated_at,
        "row_count": len(rows),
        "rows": rows,
    }


def main() -> None:
    input_path = REPO_ROOT / "docs" / "canonical-seed-12-19-29.json"
    output_path = REPO_ROOT / "docs" / "phase2-search-index-12-19-29.json"
    seed = json.loads(input_path.read_text(encoding="utf-8"))
    projection = build_search_projection(seed)
    output_path.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated search projection: {output_path}")


if __name__ == "__main__":
    main()
