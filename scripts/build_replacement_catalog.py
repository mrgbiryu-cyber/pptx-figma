#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    input_path = Path("docs/ppt-intermediate-candidates-12-19-29.json")
    output_path = Path("docs/vector-replacement-catalog-12-19-29.json")

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    by_type: Counter[str] = Counter()
    by_slide: dict[str, list[dict[str, object]]] = defaultdict(list)

    for page in payload.get("pages", []):
        slide_no = page["slide_no"]
        for candidate in page.get("candidates", []):
            rendering = candidate.get("rendering") or {}
            replacement = rendering.get("replacement") or {}
            if not rendering.get("replacement_candidate"):
                continue
            candidate_type = replacement.get("candidate_type", "unknown")
            by_type[str(candidate_type)] += 1
            by_slide[str(slide_no)].append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "subtype": candidate["subtype"],
                    "title": candidate["title"],
                    "preferred_mode": rendering.get("preferred_mode"),
                    "candidate_type": candidate_type,
                    "strategy": replacement.get("strategy"),
                    "confidence": replacement.get("confidence"),
                }
            )

    catalog = {
        "pptxPath": payload.get("pptxPath"),
        "requestedSlides": payload.get("requestedSlides"),
        "summary": {
            "replacement_candidate_count": sum(by_type.values()),
            "candidate_types": dict(sorted(by_type.items())),
        },
        "slides": {
            slide_no: {
                "count": len(items),
                "items": items,
            }
            for slide_no, items in sorted(by_slide.items(), key=lambda item: int(item[0]))
        },
    }
    output_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated replacement catalog: {output_path}")


if __name__ == "__main__":
    main()
