"""RSS feed fetcher for autofeeder.

Fetches all feeds defined in a profile, extracts item metadata, deduplicates,
filters by recency, and returns a sorted list of items conforming to the shared
item schema.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import feedparser
from dateutil import parser as dateutil_parser

logger = logging.getLogger("autofeeder")

# Maximum length for the summary field (characters).
MAX_SUMMARY_CHARS = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_id(source: str, title: str, link: str) -> str:
    """Generate a deterministic item ID as sha1(source|title|link)."""
    raw = f"{source}|{title}|{link}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _parse_date(entry: Any) -> str | None:
    """Extract a UTC ISO datetime string from a feedparser entry.

    Tries, in order:
        1. ``published_parsed`` (struct_time)
        2. ``updated_parsed``   (struct_time)
        3. ``published`` string  (via dateutil)
        4. ``updated`` string    (via dateutil)

    Returns:
        ISO-formatted UTC datetime string, or ``None`` if unparseable.
    """
    # Try struct_time fields first
    for attr in ("published_parsed", "updated_parsed"):
        st = getattr(entry, attr, None)
        if st is not None:
            try:
                dt = datetime(*st[:6], tzinfo=timezone.utc)
                return dt.isoformat()
            except (TypeError, ValueError):
                continue

    # Fall back to string parsing via dateutil
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = dateutil_parser.parse(raw)
                # Normalise to UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt.isoformat()
            except (ValueError, OverflowError):
                continue

    return None


def _strip_html(text: str) -> str:
    """Crude HTML tag removal for RSS summaries."""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def _resolve_source_name(
    profile_feed_name: str,
    parsed_feed: Any,
    feed_url: str,
) -> str:
    """Determine the display name for a feed source.

    Priority:
        1. Profile-supplied feed name (e.g. ``"Nature Neuroscience"``)
        2. RSS feed title from the feed itself
        3. The feed URL as a last resort
    """
    if profile_feed_name:
        return profile_feed_name
    feed_title = getattr(parsed_feed.feed, "title", "")
    if feed_title:
        return feed_title.strip()
    return feed_url


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_items(
    feeds: list[dict[str, str]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch RSS items from all *feeds* and return a deduplicated, sorted list.

    Args:
        feeds: List of ``{"name": str, "url": str}`` dicts from the profile.
        config: Global configuration dict (uses ``config["fetch"]``).

    Returns:
        List of item dicts conforming to the shared item schema, sorted by
        ``published_utc`` descending (newest first), capped at
        ``max_total_items``.
    """
    fetch_cfg = config.get("fetch", {})
    lookback_days: int = fetch_cfg.get("lookback_days", 7)
    max_per_feed: int = fetch_cfg.get("max_items_per_feed", 50)
    max_total: int = fetch_cfg.get("max_total_items", 400)

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cutoff_iso = cutoff.isoformat()

    seen_ids: set[str] = set()
    all_items: list[dict[str, Any]] = []

    logger.info(
        "Fetching %d feeds (lookback=%d days, max_per_feed=%d)",
        len(feeds), lookback_days, max_per_feed,
    )

    for feed_info in feeds:
        url = feed_info["url"]
        profile_name = feed_info.get("name", "")

        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            logger.warning("Failed to fetch feed %s: %s", url, exc)
            continue

        if parsed.bozo and not parsed.entries:
            logger.warning(
                "Feed %s returned bozo error with no entries: %s",
                url, getattr(parsed, "bozo_exception", "unknown"),
            )
            continue

        source = _resolve_source_name(profile_name, parsed, url)
        count = 0

        for entry in parsed.entries:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()

            # Skip entries missing essential fields
            if not title or not link:
                continue

            # Published date
            published_utc = _parse_date(entry)

            # Filter by lookback window
            if published_utc is not None and published_utc < cutoff_iso:
                continue

            # Summary — strip HTML, truncate
            raw_summary = getattr(entry, "summary", "") or ""
            summary = _strip_html(raw_summary)
            if len(summary) > MAX_SUMMARY_CHARS:
                summary = summary[:MAX_SUMMARY_CHARS].rstrip() + "..."

            # Deduplicate
            item_id = _make_id(source, title, link)
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            all_items.append({
                "id": item_id,
                "source": source,
                "title": title,
                "link": link,
                "published_utc": published_utc,
                "summary": summary,
            })

            count += 1
            if count >= max_per_feed:
                break

        logger.debug("Feed '%s' (%s): %d items collected", source, url, count)

    # Sort by published_utc descending (items with None dates go last)
    all_items.sort(
        key=lambda it: it["published_utc"] or "",
        reverse=True,
    )

    # Cap at max total
    if len(all_items) > max_total:
        logger.info(
            "Capping items from %d to max_total_items=%d",
            len(all_items), max_total,
        )
        all_items = all_items[:max_total]

    logger.info("Fetched %d items from %d feeds", len(all_items), len(feeds))
    return all_items
