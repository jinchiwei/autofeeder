"""Cross-profile aggregation for autofeeder.

Identifies standout papers across multiple profile runs: the single
highest-scoring "paper of the week" and items that scored well in two or
more profiles (crossover papers).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("autofeeder")


# ---------------------------------------------------------------------------
# Shared aggregation helper
# ---------------------------------------------------------------------------

def _aggregate_items(
    all_results: list[tuple[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Deduplicate items by ID, tracking max score and collecting profile names.

    Args:
        all_results: List of ``(profile_name, result_dict)`` tuples. Each
            ``result_dict`` contains a ``"ranked"`` list of item dicts.

    Returns:
        Dict mapping item ID to aggregated item dict with ``"title"``,
        ``"link"``, ``"source"``, ``"score"`` (max), ``"profiles"``
        (list of profile names), and ``"why"`` (from highest-scoring entry).
    """
    items: dict[str, dict[str, Any]] = {}

    for profile_name, result in all_results:
        ranked = result.get("ranked", [])
        if not ranked:
            continue

        for item in ranked:
            item_id = item.get("id", "")
            if not item_id:
                continue

            score = item.get("score", 0.0)
            if not isinstance(score, (int, float)):
                try:
                    score = float(score)
                except (TypeError, ValueError):
                    score = 0.0

            if item_id not in items:
                items[item_id] = {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "source": item.get("source", ""),
                    "score": score,
                    "profiles": [profile_name],
                    "why": item.get("why", ""),
                }
            else:
                existing = items[item_id]
                if profile_name not in existing["profiles"]:
                    existing["profiles"].append(profile_name)
                if score > existing["score"]:
                    existing["score"] = score
                    existing["why"] = item.get("why", existing["why"])

    return items


# ---------------------------------------------------------------------------
# Paper of the Week
# ---------------------------------------------------------------------------

def find_paper_of_the_week(
    all_results: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any] | None:
    """Find the single highest-scoring item across all profiles.

    If the same item appears in multiple profiles, the maximum score is used
    and all profile names are recorded.

    Args:
        all_results: List of ``(profile_name, result_dict)`` tuples. Each
            ``result_dict`` contains a ``"ranked"`` list of item dicts with
            at least ``"id"``, ``"title"``, ``"link"``, ``"source"``,
            ``"score"`` keys, and optionally ``"why"``.

    Returns:
        A dict with ``"title"``, ``"link"``, ``"source"``, ``"score"``,
        ``"profiles"`` (list of profile names), and ``"why"`` (from the
        highest-scoring entry). Returns ``None`` if no items are found.
    """
    if not all_results:
        logger.debug("find_paper_of_the_week: no results provided")
        return None

    items = _aggregate_items(all_results)

    if not items:
        logger.debug("find_paper_of_the_week: no valid items across all profiles")
        return None

    # Find the item with the highest score
    best = max(items.values(), key=lambda x: x["score"])

    logger.info(
        "Paper of the week: '%s' (score=%.2f, profiles=%s)",
        best["title"], best["score"], ", ".join(best["profiles"]),
    )

    return best


# ---------------------------------------------------------------------------
# Crossover Papers
# ---------------------------------------------------------------------------

def find_crossover_papers(
    all_results: list[tuple[str, dict[str, Any]]],
    min_profiles: int = 2,
) -> list[dict[str, Any]]:
    """Find items that appeared in multiple profiles.

    Args:
        all_results: List of ``(profile_name, result_dict)`` tuples (same
            format as :func:`find_paper_of_the_week`).
        min_profiles: Minimum number of profiles an item must appear in to
            qualify as a crossover paper.

    Returns:
        List of crossover dicts sorted by score descending. Each dict has
        ``"title"``, ``"link"``, ``"source"``, ``"score"`` (max across
        profiles), ``"profiles"`` (list of profile names), and ``"why"``.
    """
    if not all_results:
        logger.debug("find_crossover_papers: no results provided")
        return []

    if min_profiles < 2:
        logger.warning(
            "find_crossover_papers: min_profiles=%d is less than 2, "
            "setting to 2", min_profiles,
        )
        min_profiles = 2

    items = _aggregate_items(all_results)

    # Filter to items appearing in min_profiles or more
    crossovers = [
        entry for entry in items.values()
        if len(entry["profiles"]) >= min_profiles
    ]

    # Sort by score descending
    crossovers.sort(key=lambda x: x["score"], reverse=True)

    logger.info(
        "Found %d crossover papers (appearing in %d+ profiles)",
        len(crossovers), min_profiles,
    )

    return crossovers
