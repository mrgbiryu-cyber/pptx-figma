#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_json_from_git(repo: Path, git_ref: str, rel_path: str) -> dict:
    output = subprocess.check_output(
        ["git", "-C", str(repo), "show", f"{git_ref}:{rel_path}"],
        text=True,
    )
    return json.loads(output)


def summarize_bundle(bundle: dict) -> dict:
    counts = {}
    roles = {}

    def walk(node: dict) -> None:
        node_type = node.get("type", "UNKNOWN")
        counts[node_type] = counts.get(node_type, 0) + 1
        debug = node.get("debug") or {}
        role = debug.get("role")
        if role:
            roles[role] = roles.get(role, 0) + 1
        for child in node.get("children") or []:
            walk(child)

    walk(bundle["document"])
    return {
        "counts": counts,
        "roles": roles,
        "debug": bundle.get("debug") or {},
    }


def diff_counter(before: dict, after: dict) -> dict:
    keys = sorted(set(before) | set(after))
    return {
        key: {
            "before": before.get(key, 0),
            "after": after.get(key, 0),
            "delta": after.get(key, 0) - before.get(key, 0),
        }
        for key in keys
        if before.get(key, 0) != after.get(key, 0)
    }


def build_report(repo: Path, baseline_ref: str, bundle_dir: Path, slides: list[int]) -> dict:
    pages = []
    for slide in slides:
        rel_path = f"docs/visual-first-bundles/visual-slide-{slide}.bundle.json"
        before = load_json_from_git(repo, baseline_ref, rel_path)
        after = load_json(bundle_dir / f"visual-slide-{slide}.bundle.json")
        before_summary = summarize_bundle(before)
        after_summary = summarize_bundle(after)
        pages.append(
            {
                "slide_no": slide,
                "before": before_summary,
                "after": after_summary,
                "count_diff": diff_counter(before_summary["counts"], after_summary["counts"]),
                "role_diff": diff_counter(before_summary["roles"], after_summary["roles"]),
            }
        )
    return {
        "kind": "visual-change-report",
        "baseline_ref": baseline_ref,
        "bundle_dir": str(bundle_dir),
        "pages": pages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare current visual bundles against a git baseline.")
    parser.add_argument("--repo", required=True, help="Repository root")
    parser.add_argument("--baseline-ref", required=True, help="Git ref to compare against")
    parser.add_argument("--bundle-dir", required=True, help="Current bundle directory")
    parser.add_argument("--slides", nargs="+", type=int, required=True, help="Slide numbers to compare")
    parser.add_argument("--output", required=True, help="Output JSON report path")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    bundle_dir = Path(args.bundle_dir).resolve()
    report = build_report(repo, args.baseline_ref, bundle_dir, args.slides)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}")


if __name__ == "__main__":
    main()
