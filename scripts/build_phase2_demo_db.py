#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "sql" / "atlas_phase2_schema.sql"
SEED_PATH = REPO_ROOT / "docs" / "canonical-seed-12-19-29.json"
SEARCH_PATH = REPO_ROOT / "docs" / "phase2-search-index-12-19-29.json"
DB_PATH = REPO_ROOT / "docs" / "phase2-demo.sqlite"


def executemany_dict(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join(f":{column}" for column in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, rows)


def main() -> None:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    search_projection = json.loads(SEARCH_PATH.read_text(encoding="utf-8"))

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        executemany_dict(conn, "atlas_documents", seed["atlas_documents"])
        executemany_dict(conn, "atlas_pages", seed["atlas_pages"])
        executemany_dict(conn, "atlas_nodes", seed["atlas_nodes"])
        executemany_dict(conn, "atlas_assets", seed["atlas_assets"])
        executemany_dict(conn, "atlas_source_mappings", seed["atlas_source_mappings"])
        executemany_dict(conn, "atlas_relations", seed["atlas_relations"])
        executemany_dict(conn, "atlas_search_index", search_projection["rows"])
        conn.commit()
    finally:
        conn.close()

    print(f"Built demo DB: {DB_PATH}")


if __name__ == "__main__":
    main()
