#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from ppt_source_extractor import make_bounds, placeholder_key, scale_bounds, scale_value


def candidate_abs_bounds(candidate: dict[str, Any], context: dict[str, Any]) -> dict[str, float]:
    bounds = candidate.get("bounds_px")
    if bounds:
        return make_bounds(
            scale_value(bounds.get("x", 0), context["scale_x"]),
            scale_value(bounds.get("y", 0), context["scale_y"]),
            scale_value(bounds.get("width", 0), context["scale_x"]),
            scale_value(bounds.get("height", 0), context["scale_y"]),
        )
    extra = candidate.get("extra") or {}
    placeholder = extra.get("placeholder") or {}
    anchor = context.get("placeholder_anchor_map", {}).get(placeholder_key(placeholder))
    if anchor and anchor.get("bounds_px"):
        return scale_bounds(anchor["bounds_px"], context["scale_x"], context["scale_y"])
    return make_bounds(
        0,
        0,
        1,
        1,
    )


def area(bounds: dict[str, Any]) -> float:
    return max(float(bounds.get("width", 0)), 0.0) * max(float(bounds.get("height", 0)), 0.0)


def overlap_area(bounds_a: dict[str, Any], bounds_b: dict[str, Any]) -> float:
    left = max(float(bounds_a.get("x", 0)), float(bounds_b.get("x", 0)))
    top = max(float(bounds_a.get("y", 0)), float(bounds_b.get("y", 0)))
    right = min(float(bounds_a.get("x", 0)) + float(bounds_a.get("width", 0)), float(bounds_b.get("x", 0)) + float(bounds_b.get("width", 0)))
    bottom = min(float(bounds_a.get("y", 0)) + float(bounds_a.get("height", 0)), float(bounds_b.get("y", 0)) + float(bounds_b.get("height", 0)))
    return max(right - left, 0.0) * max(bottom - top, 0.0)


def containment_ratio(inner: dict[str, Any], outer: dict[str, Any]) -> float:
    return overlap_area(inner, outer) / max(area(inner), 1.0)


def owner_rank(candidate: dict[str, Any]) -> int:
    subtype = str(candidate.get("subtype") or "")
    return {
        "table_cell": 0,
        "table": 1,
        "labeled_shape": 2,
        "shape": 3,
        "group": 4,
        "section_block": 4,
    }.get(subtype, 9)


def detect_text_owner(text_candidate: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    bounds = text_candidate.get("bounds_px") or {}
    if not bounds:
        return None
    owners: list[tuple[int, float, dict[str, Any]]] = []
    for candidate in candidates:
        if candidate.get("candidate_id") == text_candidate.get("candidate_id"):
            continue
        subtype = str(candidate.get("subtype") or "")
        if subtype not in {"table_cell", "table", "labeled_shape", "shape", "group", "section_block"}:
            continue
        owner_bounds = candidate.get("bounds_px") or {}
        if not owner_bounds:
            continue
        ratio = containment_ratio(bounds, owner_bounds)
        if ratio < 0.8:
            continue
        owners.append((owner_rank(candidate), -ratio, candidate))
    if not owners:
        return None
    owners.sort(key=lambda row: (row[0], row[1]))
    chosen = owners[0][2]
    return {
        "owner_candidate_id": chosen.get("candidate_id"),
        "owner_subtype": chosen.get("subtype"),
        "owner_title": chosen.get("title"),
        "containment_ratio": round(-owners[0][1], 3),
    }


def build_text_owner_map(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if candidate.get("subtype") != "text_block":
            continue
        owner = detect_text_owner(candidate, candidates)
        if owner:
            mapping[str(candidate.get("candidate_id") or "")] = owner
    return mapping


def detect_candidate_owner(
    target_candidate: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    owner_subtypes: set[str] | None = None,
    containment_threshold: float = 0.8,
) -> dict[str, Any] | None:
    bounds = target_candidate.get("bounds_px") or {}
    if not bounds:
        return None
    owner_subtypes = owner_subtypes or {"table", "table_cell", "labeled_shape", "shape", "group", "section_block"}
    owners: list[tuple[int, float, dict[str, Any]]] = []
    for candidate in candidates:
        if candidate.get("candidate_id") == target_candidate.get("candidate_id"):
            continue
        subtype = str(candidate.get("subtype") or "")
        if subtype not in owner_subtypes:
            continue
        owner_bounds = candidate.get("bounds_px") or {}
        if not owner_bounds:
            continue
        ratio = containment_ratio(bounds, owner_bounds)
        if ratio < containment_threshold:
            continue
        owners.append((owner_rank(candidate), -ratio, candidate))
    if not owners:
        return None
    owners.sort(key=lambda row: (row[0], row[1]))
    chosen = owners[0][2]
    return {
        "owner_candidate_id": chosen.get("candidate_id"),
        "owner_subtype": chosen.get("subtype"),
        "owner_title": chosen.get("title"),
        "containment_ratio": round(-owners[0][1], 3),
    }


def build_candidate_owner_map(
    candidates: list[dict[str, Any]],
    *,
    target_subtypes: set[str] | None = None,
    owner_subtypes: set[str] | None = None,
    containment_threshold: float = 0.8,
) -> dict[str, dict[str, Any]]:
    target_subtypes = target_subtypes or {"text_block", "shape", "labeled_shape"}
    mapping: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        if str(candidate.get("subtype") or "") not in target_subtypes:
            continue
        owner = detect_candidate_owner(
            candidate,
            candidates,
            owner_subtypes=owner_subtypes,
            containment_threshold=containment_threshold,
        )
        if owner:
            mapping[str(candidate.get("candidate_id") or "")] = owner
    return mapping


def build_children_lookup(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    lookup: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        parent_id = str(candidate.get("parent_candidate_id") or "")
        if not parent_id:
            continue
        lookup.setdefault(parent_id, []).append(candidate)
    return lookup


def build_candidate_lookup(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(candidate.get("candidate_id") or ""): candidate for candidate in candidates}


def descendant_candidates(
    container_id: str,
    children_lookup: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    queue = list(children_lookup.get(container_id, []))
    while queue:
        candidate = queue.pop(0)
        output.append(candidate)
        queue.extend(children_lookup.get(str(candidate.get("candidate_id") or ""), []))
    return output


def build_rich_containers(
    candidates: list[dict[str, Any]],
    *,
    container_subtypes: set[str] | None = None,
    min_descendants: int = 3,
    min_text_descendants: int = 1,
) -> list[dict[str, Any]]:
    container_subtypes = container_subtypes or {"group", "section_block"}
    children_lookup = build_children_lookup(candidates)
    containers: list[dict[str, Any]] = []
    for candidate in candidates:
        if str(candidate.get("subtype") or "") not in container_subtypes:
            continue
        container_id = str(candidate.get("candidate_id") or "")
        descendants = descendant_candidates(container_id, children_lookup)
        if len(descendants) < min_descendants:
            continue
        text_descendants = [item for item in descendants if item.get("subtype") == "text_block"]
        if len(text_descendants) < min_text_descendants:
            continue
        containers.append(
            {
                "candidate": candidate,
                "descendants": descendants,
                "descendant_ids": {str(item.get("candidate_id") or "") for item in descendants},
            }
        )
    return containers


def dominant_candidate(
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    allowed_subtypes: set[str],
) -> tuple[dict[str, Any] | None, dict[str, float] | None]:
    eligible = [candidate for candidate in candidates if str(candidate.get("subtype") or "") in allowed_subtypes]
    if not eligible:
        return None, None
    chosen = max(
        eligible,
        key=lambda row: area(candidate_abs_bounds(row, context)),
    )
    return chosen, candidate_abs_bounds(chosen, context)


def should_skip_candidate_inside_owner(
    candidate: dict[str, Any],
    context: dict[str, Any],
    owner_candidate: dict[str, Any] | None,
    owner_bounds: dict[str, float] | None,
    *,
    overlap_threshold: float = 0.75,
    duplicate_subtypes: set[str] | None = None,
) -> bool:
    if not owner_candidate or not owner_bounds:
        return False
    if candidate.get("candidate_id") == owner_candidate.get("candidate_id"):
        return False
    subtype = str(candidate.get("subtype") or "")
    duplicate_subtypes = duplicate_subtypes or {"labeled_shape", "text_block", "shape", "group", "section_block"}
    if subtype not in duplicate_subtypes:
        return False
    bounds = candidate_abs_bounds(candidate, context)
    overlap_ratio = containment_ratio(bounds, owner_bounds)
    if overlap_ratio < overlap_threshold:
        return False
    if subtype == "text_block":
        return True
    if subtype == "labeled_shape":
        return True
    text_value = str(candidate.get("text") or candidate.get("title") or "")
    if subtype in {"group", "section_block", "shape"} and text_value:
        return True
    return False


def should_skip_text_by_owner(
    candidate: dict[str, Any],
    text_owner_map: dict[str, dict[str, Any]],
    *,
    owner_subtypes: set[str] | None = None,
) -> bool:
    if candidate.get("subtype") != "text_block":
        return False
    owner = text_owner_map.get(str(candidate.get("candidate_id") or ""))
    if not owner:
        return False
    if owner_subtypes is None:
        owner_subtypes = {"table", "table_cell", "labeled_shape"}
    return str(owner.get("owner_subtype") or "") in owner_subtypes


def should_skip_candidate_by_owner(
    candidate: dict[str, Any],
    candidate_owner_map: dict[str, dict[str, Any]],
    *,
    owner_subtypes: set[str] | None = None,
) -> bool:
    owner = candidate_owner_map.get(str(candidate.get("candidate_id") or ""))
    if not owner:
        return False
    if owner_subtypes is None:
        owner_subtypes = {"table", "table_cell"}
    return str(owner.get("owner_subtype") or "") in owner_subtypes


def should_skip_candidate_by_rich_container(
    candidate: dict[str, Any],
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
    rich_containers: list[dict[str, Any]],
    *,
    overlap_threshold: float = 0.85,
    candidate_subtypes: set[str] | None = None,
) -> bool:
    candidate_id = str(candidate.get("candidate_id") or "")
    subtype = str(candidate.get("subtype") or "")
    candidate_subtypes = candidate_subtypes or {"shape", "labeled_shape", "text_block", "group"}
    if subtype not in candidate_subtypes:
        return False
    parent_id = str(candidate.get("parent_candidate_id") or "")
    if not parent_id.startswith("page:"):
        return False
    candidate_bounds = candidate_abs_bounds(candidate, context)
    for container in rich_containers:
        container_candidate = container["candidate"]
        container_id = str(container_candidate.get("candidate_id") or "")
        if candidate_id == container_id or candidate_id in container["descendant_ids"]:
            continue
        container_bounds = candidate_abs_bounds(container_candidate, context)
        ratio = containment_ratio(candidate_bounds, container_bounds)
        if ratio < overlap_threshold:
            continue
        return True
    return False


def has_ancestor_in_set(
    candidate: dict[str, Any],
    candidate_lookup: dict[str, dict[str, Any]],
    blocked_ids: set[str],
) -> bool:
    parent_id = str(candidate.get("parent_candidate_id") or "")
    while parent_id:
        if parent_id in blocked_ids:
            return True
        parent = candidate_lookup.get(parent_id)
        if not parent:
            break
        parent_id = str(parent.get("parent_candidate_id") or "")
    return False


def filter_block_candidates(
    candidates: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    skip_subtypes: set[str] | None = None,
    dominant_owner_subtypes: set[str] | None = None,
    text_owner_subtypes: set[str] | None = None,
    candidate_owner_subtypes: set[str] | None = None,
    duplicate_subtypes: set[str] | None = None,
    overlap_threshold: float = 0.75,
) -> dict[str, Any]:
    if skip_subtypes is None:
        skip_subtypes = {"group", "section_block", "table_row", "table_cell"}
    if text_owner_subtypes is None:
        text_owner_subtypes = {"table", "table_cell", "labeled_shape"}
    if duplicate_subtypes is None:
        duplicate_subtypes = {"labeled_shape", "text_block", "shape", "group", "section_block"}
    candidate_lookup = build_candidate_lookup(candidates)
    rich_containers = build_rich_containers(candidates)
    suppressed_cluster_ids: set[str] = set()
    for candidate in candidates:
        subtype = str(candidate.get("subtype") or "")
        if subtype not in {"group", "section_block", "shape", "labeled_shape"}:
            continue
        if should_skip_candidate_by_rich_container(candidate, candidates, context, rich_containers):
            suppressed_cluster_ids.add(str(candidate.get("candidate_id") or ""))
    dominant_owner: dict[str, Any] | None = None
    dominant_owner_bounds: dict[str, float] | None = None
    if dominant_owner_subtypes:
        dominant_owner, dominant_owner_bounds = dominant_candidate(
            candidates,
            context,
            allowed_subtypes=dominant_owner_subtypes,
        )
    text_owner_map = build_text_owner_map(candidates)
    candidate_owner_map = build_candidate_owner_map(candidates)
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        subtype = str(candidate.get("subtype") or "")
        if subtype in skip_subtypes:
            continue
        if has_ancestor_in_set(candidate, candidate_lookup, suppressed_cluster_ids):
            continue
        if should_skip_text_by_owner(candidate, text_owner_map, owner_subtypes=text_owner_subtypes):
            continue
        if should_skip_candidate_by_owner(candidate, candidate_owner_map, owner_subtypes=candidate_owner_subtypes):
            continue
        if should_skip_candidate_by_rich_container(candidate, candidates, context, rich_containers):
            continue
        if should_skip_candidate_inside_owner(
            candidate,
            context,
            dominant_owner,
            dominant_owner_bounds,
            overlap_threshold=overlap_threshold,
            duplicate_subtypes=duplicate_subtypes,
        ):
            continue
        filtered.append(candidate)
    return {
        "dominant_owner": dominant_owner,
        "dominant_owner_bounds": dominant_owner_bounds,
        "text_owner_map": text_owner_map,
        "candidate_owner_map": candidate_owner_map,
        "filtered_candidates": filtered,
    }
