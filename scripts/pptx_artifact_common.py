#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def find_executable(candidates: list[str]) -> str | None:
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def run_command(
    command: list[str],
    cwd: Path | None = None,
    timeout: int = 300,
    dry_run: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "command": command,
        "cwd": str(cwd) if cwd else None,
        "timeout_seconds": timeout,
        "dry_run": dry_run,
    }
    if dry_run:
        row.update({"status": "dry_run", "returncode": None, "stdout": "", "stderr": ""})
        return row
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        row.update(
            {
                "status": "ok" if result.returncode == 0 else "failed",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
    except Exception as exc:  # pragma: no cover - diagnostic path
        row.update({"status": "error", "returncode": None, "stdout": "", "stderr": str(exc)})
    return row


def parse_slide_range(value: str | None) -> list[int] | None:
    if not value:
        return None
    slides: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_raw, end_raw = token.split("-", 1)
            start = int(start_raw)
            end = int(end_raw)
            slides.extend(range(start, end + 1))
        else:
            slides.append(int(token))
    return sorted(set(slides))


def selected_slide_indices(total: int, slides: list[int] | None) -> list[int]:
    if slides is None:
        return list(range(total))
    return [slide_no - 1 for slide_no in slides if 1 <= slide_no <= total]


def status_manifest(
    *,
    kind: str,
    source: Path,
    out_dir: Path,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "generated_at": utc_now(),
        "source": str(source),
        "out_dir": str(out_dir),
        "status": status,
        "details": details or {},
    }
