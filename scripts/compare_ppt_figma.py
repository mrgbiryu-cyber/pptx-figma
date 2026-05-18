#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_MAPPING = {
    12: "Page 1",
    19: "Page 2",
    29: "Page 3",
}


def _walk_figma(node: dict[str, Any]):
    yield node
    for child in node.get("children", []) or []:
        yield from _walk_figma(child)


def summarize_figma_page(figma_json: dict[str, Any], page_name: str) -> dict[str, Any]:
    section = next(
        child
        for child in figma_json["page"].get("children", [])
        if child.get("type") == "SECTION"
    )
    page = next(
        child
        for child in section.get("children", [])
        if child.get("type") == "FRAME" and child.get("name") == page_name
    )
    nodes = list(_walk_figma(page))
    type_counts = Counter(node.get("type", "UNKNOWN") for node in nodes)
    text_samples = [
        node.get("characters", "").strip()
        for node in nodes
        if node.get("type") == "TEXT" and node.get("characters", "").strip()
    ][:12]
    image_fill_nodes = sum(
        1
        for node in nodes
        if any(
            fill.get("type") == "IMAGE"
            for fill in (node.get("fills", []) or [])
            if isinstance(fill, dict)
        )
    )
    return {
        "page_name": page_name,
        "total_nodes": len(nodes),
        "type_counts": dict(type_counts),
        "text_count": type_counts.get("TEXT", 0),
        "group_count": type_counts.get("GROUP", 0),
        "frame_count": type_counts.get("FRAME", 0),
        "vector_count": type_counts.get("VECTOR", 0),
        "image_fill_nodes": image_fill_nodes,
        "text_samples": text_samples,
    }


def _walk_ppt_elements(elements: list[dict[str, Any]]):
    for element in elements:
        yield element
        yield from _walk_ppt_elements(element.get("children", []))


def summarize_ppt_slide(slide_payload: dict[str, Any]) -> dict[str, Any]:
    elements = list(_walk_ppt_elements(slide_payload["elements"]))
    element_counts = Counter(element.get("element_type", "unknown") for element in elements)
    text_samples = [
        element.get("text", "").strip()
        for element in elements
        if element.get("text", "").strip()
    ][:12]
    return {
        "slide_no": slide_payload["slide_no"],
        "title_or_label": slide_payload["title_or_label"],
        "total_elements": len(elements),
        "element_counts": dict(element_counts),
        "text_count": sum(1 for element in elements if element.get("text", "").strip()),
        "table_count": sum(1 for element in elements if element.get("table")),
        "image_count": sum(1 for element in elements if element.get("element_type") == "image"),
        "group_count": element_counts.get("group", 0),
        "text_samples": text_samples,
    }


def compare(ppt_details: dict[str, Any], figma_json: dict[str, Any]) -> dict[str, Any]:
    comparisons: list[dict[str, Any]] = []
    for slide_payload in ppt_details["slides"]:
        slide_no = slide_payload["slide_no"]
        page_name = DEFAULT_MAPPING.get(slide_no)
        if not page_name:
            continue
        ppt_summary = summarize_ppt_slide(slide_payload)
        figma_summary = summarize_figma_page(figma_json, page_name)

        observations: list[str] = []
        if ppt_summary["table_count"] > 0 and figma_summary["vector_count"] > figma_summary["text_count"]:
            observations.append("table slide may be heavily vectorized in figma output")
        if ppt_summary["group_count"] > figma_summary["group_count"]:
            observations.append("ppt group hierarchy appears partially flattened")
        if ppt_summary["image_count"] > 0 and figma_summary["image_fill_nodes"] < ppt_summary["image_count"]:
            observations.append("not all ppt images are preserved as explicit image fills")
        if figma_summary["vector_count"] > figma_summary["text_count"]:
            observations.append("figma output is vector-heavy")
        if ppt_summary["text_count"] <= figma_summary["text_count"]:
            observations.append("text granularity is preserved or expanded in figma output")

        comparisons.append(
            {
                "slide_no": slide_no,
                "figma_page": page_name,
                "ppt": ppt_summary,
                "figma": figma_summary,
                "observations": observations,
            }
        )

    return {
        "mapping": DEFAULT_MAPPING,
        "comparisons": comparisons,
    }


def main() -> None:
    ppt_details_path = Path("docs/ppt-slide-details-12-19-29.json")
    figma_json_path = Path("sampling/figma-current-page.json")
    output_path = Path("docs/ppt-vs-figma-comparison-12-19-29.json")

    ppt_details = json.loads(ppt_details_path.read_text(encoding="utf-8"))
    figma_json = json.loads(figma_json_path.read_text(encoding="utf-8"))

    report = compare(ppt_details, figma_json)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated comparison report: {output_path}")


if __name__ == "__main__":
    main()
