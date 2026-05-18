#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "docs" / "phase2-demo.sqlite"
OUTPUT_PATH = REPO_ROOT / "docs" / "phase2-search-checks.json"

QUERIES = [
    "케어십",
    "리뷰",
    "제품 카테고리별 평가 항목",
    "최대할인가",
    "옵션 선택",
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        results = []
        for query in QUERIES:
            rows = conn.execute(
                """
                SELECT
                  idx.entity_type,
                  idx.entity_id,
                  idx.document_id,
                  idx.page_id,
                  idx.searchable_text,
                  idx.metadata_json
                FROM atlas_search_index AS idx
                WHERE idx.searchable_text LIKE ?
                ORDER BY
                  CASE idx.entity_type
                    WHEN 'node' THEN 1
                    WHEN 'page' THEN 2
                    WHEN 'document' THEN 3
                    ELSE 4
                  END,
                  idx.page_id,
                  idx.entity_id
                LIMIT 15
                """,
                (f"%{query}%",),
            ).fetchall()

            parsed_rows = []
            for row in rows:
                metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
                parsed_rows.append(
                    {
                        "entity_type": row["entity_type"],
                        "entity_id": row["entity_id"],
                        "document_id": row["document_id"],
                        "page_id": row["page_id"],
                        "title": metadata.get("title"),
                        "subtype": metadata.get("subtype") or metadata.get("asset_type"),
                        "source_path": metadata.get("source_path"),
                        "searchable_text_excerpt": row["searchable_text"][:160],
                    }
                )

            results.append(
                {
                    "query": query,
                    "match_count": len(parsed_rows),
                    "matches": parsed_rows,
                }
            )
    finally:
        conn.close()

    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "db_path": str(DB_PATH),
                "queries": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote search checks: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
