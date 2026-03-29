"""Keyword prefilter for autofeeder.

Performs a cheap, local pass over fetched items to reduce the number sent to the
LLM triage step.  Counts keyword substring matches in each item's title and
summary, then keeps the most relevant items.

Strategy:
    - If 50 or more items have at least one keyword match, keep the top
      ``keep_top`` items ranked by hit count (ties broken by original order).
    - If fewer than 50 items match, fall back to keeping the first ``keep_top``
      items by recency (i.e. preserve the original order from fetch, which is
      already sorted newest-first).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("autofeeder")

# Minimum number of keyword-matched items required to use the hit-count
# ranking strategy instead of the recency fallback.
_MIN_MATCHES_FOR_RANKING = 50


def _count_hits(text: str, keywords: list[str]) -> int:
    """Count how many *keywords* appear as substrings in *text*.

    Each keyword is checked once (not counted multiple times if it appears
    more than once in the text).  Matching is case-insensitive.
    """
    lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in lower)


def prefilter(
    items: list[dict[str, Any]],
    keywords: list[str],
    keep_top: int = 200,
) -> list[dict[str, Any]]:
    """Filter items by keyword relevance.

    Args:
        items: List of item dicts (must have ``"title"`` and ``"summary"`` keys).
        keywords: Interest keywords from the profile.
        keep_top: Maximum number of items to return.

    Returns:
        A filtered list of items, at most *keep_top* long.
    """
    if not items:
        logger.debug("Prefilter: no items to filter")
        return []

    if not keywords:
        logger.debug("Prefilter: no keywords — returning first %d items by recency", keep_top)
        return items[:keep_top]

    # Score every item
    scored: list[tuple[int, int, dict[str, Any]]] = []
    match_count = 0

    for idx, item in enumerate(items):
        text = (item.get("title", "") + " " + item.get("summary", ""))
        hits = _count_hits(text, keywords)
        scored.append((hits, idx, item))
        if hits > 0:
            match_count += 1

    logger.debug(
        "Prefilter: %d/%d items matched at least one keyword",
        match_count, len(items),
    )

    if match_count >= _MIN_MATCHES_FOR_RANKING:
        # Enough matches — rank by hit count descending, break ties by
        # original order (lower index = more recent = preferred).
        scored.sort(key=lambda t: (-t[0], t[1]))
        result = [entry[2] for entry in scored[:keep_top]]
        logger.info(
            "Prefilter (keyword rank): %d -> %d items (>=%d matched)",
            len(items), len(result), _MIN_MATCHES_FOR_RANKING,
        )
    else:
        # Too few matches — fall back to recency (original order).
        result = items[:keep_top]
        logger.info(
            "Prefilter (recency fallback): %d -> %d items (only %d matched)",
            len(items), len(result), match_count,
        )

    return result
