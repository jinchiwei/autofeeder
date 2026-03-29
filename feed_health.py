"""Feed health tracking for autofeeder.

Monitors the reliability of RSS feeds over time: tracks fetch success rates,
detects dead or broken feeds, and generates a human-readable health report
for the digest footer.

Health entry schema::

    {
        "feed_url": {
            "total_fetches": int,
            "successful_fetches": int,
            "total_items": int,
            "last_success": str | None,   # ISO date or None
            "consecutive_failures": int
        }
    }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("autofeeder")

# Thresholds
_DEAD_CONSECUTIVE_FAILURES = 3
_BROKEN_CONSECUTIVE_FAILURES = 3


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_health(path: str | Path = "feed_health.json") -> dict[str, dict[str, Any]]:
    """Load existing feed health data from a JSON file.

    Args:
        path: Path to the health data file.

    Returns:
        Health dict mapping feed URLs to their tracking data. Returns an empty
        dict if the file does not exist or contains invalid JSON.
    """
    health_path = Path(path)

    if not health_path.is_file():
        logger.debug("Health file not found at %s — starting fresh", health_path)
        return {}

    try:
        with open(health_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Corrupted health file at %s — starting fresh: %s",
            health_path, exc,
        )
        return {}
    except OSError as exc:
        logger.warning("Could not read health file at %s: %s", health_path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "Health file at %s has unexpected type %s — starting fresh",
            health_path, type(data).__name__,
        )
        return {}

    logger.debug("Loaded health data for %d feeds from %s", len(data), health_path)
    return data


def save_health(health: dict[str, dict[str, Any]], path: str | Path = "feed_health.json") -> None:
    """Write feed health data to a JSON file.

    Args:
        health: Health dict to persist.
        path: Destination file path.
    """
    health_path = Path(path)

    try:
        health_path.parent.mkdir(parents=True, exist_ok=True)
        with open(health_path, "w", encoding="utf-8") as f:
            json.dump(health, f, indent=2, ensure_ascii=False)
        logger.debug("Saved health data for %d feeds to %s", len(health), health_path)
    except OSError as exc:
        logger.error("Failed to save health data to %s: %s", health_path, exc)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record_fetch(
    health: dict[str, dict[str, Any]],
    feed_url: str,
    items_count: int,
    success: bool,
) -> None:
    """Record the result of a single feed fetch.

    Updates the health dict in-place with the fetch outcome.

    Args:
        health: Health dict (mutated in-place).
        feed_url: The URL of the feed that was fetched.
        items_count: Number of items returned (0 if the fetch failed).
        success: Whether the fetch completed without errors.
    """
    if not feed_url:
        return

    entry = health.setdefault(feed_url, {
        "total_fetches": 0,
        "successful_fetches": 0,
        "total_items": 0,
        "last_success": None,
        "consecutive_failures": 0,
    })

    entry["total_fetches"] = entry.get("total_fetches", 0) + 1

    if success:
        entry["successful_fetches"] = entry.get("successful_fetches", 0) + 1
        entry["total_items"] = entry.get("total_items", 0) + items_count
        entry["last_success"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry["consecutive_failures"] = 0
        logger.debug("Feed %s: success, %d items", feed_url, items_count)
    else:
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        logger.debug(
            "Feed %s: failure (%d consecutive)",
            feed_url, entry["consecutive_failures"],
        )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_health(
    health: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Analyze feed health data and classify feeds.

    Args:
        health: Health dict mapping feed URLs to tracking data.

    Returns:
        Dict with classification lists:

        - ``"healthy"`` — feeds with recent successes and no failure streaks.
        - ``"dead"`` — feeds returning 0 items for 3+ consecutive fetches
          (all fetches succeeded but yielded nothing).
        - ``"broken"`` — feeds with 3+ consecutive fetch failures.
    """
    healthy: list[str] = []
    dead: list[str] = []
    broken: list[str] = []

    for url, entry in health.items():
        consecutive_failures = entry.get("consecutive_failures", 0)
        total_fetches = entry.get("total_fetches", 0)
        successful_fetches = entry.get("successful_fetches", 0)
        total_items = entry.get("total_items", 0)

        if consecutive_failures >= _BROKEN_CONSECUTIVE_FAILURES:
            broken.append(url)
        elif (
            total_fetches >= _DEAD_CONSECUTIVE_FAILURES
            and successful_fetches >= _DEAD_CONSECUTIVE_FAILURES
            and total_items == 0
        ):
            # All fetches succeeded but never returned any items
            dead.append(url)
        else:
            healthy.append(url)

    logger.debug(
        "Health analysis: %d healthy, %d dead, %d broken",
        len(healthy), len(dead), len(broken),
    )

    return {
        "healthy": healthy,
        "dead": dead,
        "broken": broken,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_health_report(analysis: dict[str, list[str]]) -> str:
    """Format a short health summary for logging and digest footers.

    Args:
        analysis: Output from :func:`analyze_health`.

    Returns:
        A multi-line string summarizing feed health. Returns a single
        "all healthy" line if there are no issues.
    """
    healthy = analysis.get("healthy", [])
    dead = analysis.get("dead", [])
    broken = analysis.get("broken", [])

    total = len(healthy) + len(dead) + len(broken)

    if total == 0:
        return "Feed health: no feeds tracked yet."

    lines: list[str] = []
    lines.append(f"Feed health: {len(healthy)}/{total} feeds healthy")

    if dead:
        lines.append(f"  Dead feeds ({len(dead)}):")
        for url in dead:
            lines.append(f"    - {url}")

    if broken:
        lines.append(f"  Broken feeds ({len(broken)}):")
        for url in broken:
            lines.append(f"    - {url}")

    if not dead and not broken:
        lines.append("  All feeds operating normally.")

    return "\n".join(lines)
