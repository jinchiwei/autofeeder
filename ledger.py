"""Seen-item dedup ledger for autofeeder.

Tracks which items have already been processed using a JSON file. Supports
filtering new items, updating the ledger with scored results, and pruning
stale entries.

Ledger schema::

    {
        "item_id": {
            "score": float,
            "title": str,
            "first_seen": "YYYY-MM-DD",
            "last_seen": "YYYY-MM-DD"
        }
    }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("autofeeder")


# ---------------------------------------------------------------------------
# Load / Save
# ---------------------------------------------------------------------------

def load_ledger(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load the seen-item ledger from a JSON file.

    Args:
        path: Path to the ``seen.json`` file.

    Returns:
        Ledger dict mapping item IDs to their metadata. Returns an empty dict
        if the file does not exist or contains corrupted JSON.
    """
    ledger_path = Path(path)

    if not ledger_path.is_file():
        logger.debug("Ledger file not found at %s — starting fresh", ledger_path)
        return {}

    try:
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Corrupted ledger at %s — ignoring and starting fresh: %s",
            ledger_path, exc,
        )
        return {}
    except OSError as exc:
        logger.warning("Could not read ledger at %s: %s", ledger_path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "Ledger at %s has unexpected type %s — starting fresh",
            ledger_path, type(data).__name__,
        )
        return {}

    logger.debug("Loaded ledger with %d entries from %s", len(data), ledger_path)
    return data


def save_ledger(seen: dict[str, dict[str, Any]], path: str | Path) -> None:
    """Write the ledger dict to a JSON file with indent=2.

    Args:
        seen: Ledger dict to persist.
        path: Destination file path.
    """
    ledger_path = Path(path)

    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_path, "w", encoding="utf-8") as f:
            json.dump(seen, f, indent=2, ensure_ascii=False)
        logger.debug("Saved ledger with %d entries to %s", len(seen), ledger_path)
    except OSError as exc:
        logger.error("Failed to save ledger to %s: %s", ledger_path, exc)


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def ledger_filter(
    items: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Remove items whose ID is already in the ledger.

    Each item is annotated with ``is_new`` (bool) indicating whether it was
    previously unseen. When the ledger is disabled in config, all items are
    returned with ``is_new = True``.

    Args:
        items: List of item dicts, each containing at least an ``"id"`` key.
        config: Global config dict (uses ``config["ledger"]``).

    Returns:
        Filtered list of items not present in the ledger.
    """
    ledger_cfg = config.get("ledger", {})

    if not ledger_cfg.get("enabled", True):
        logger.debug("Ledger disabled — returning all %d items as new", len(items))
        for item in items:
            item["is_new"] = True
        return items

    ledger_path = ledger_cfg.get("path", "seen.json")
    seen = load_ledger(ledger_path)

    new_items: list[dict[str, Any]] = []
    skipped = 0

    for item in items:
        item_id = item.get("id", "")
        if item_id in seen:
            item["is_new"] = False
            skipped += 1
        else:
            item["is_new"] = True
            new_items.append(item)

    logger.info(
        "Ledger filter: %d items in, %d new, %d already seen",
        len(items), len(new_items), skipped,
    )

    return new_items


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def ledger_update(
    result: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Add newly scored items to the ledger and update existing entries.

    For items already in the ledger, only ``last_seen`` is updated.
    For new items, a full entry is created with score, title, first_seen,
    and last_seen.

    Args:
        result: Result dict containing a ``"ranked"`` list of item dicts.
            Each item should have ``"id"``, ``"score"``, and ``"title"`` keys.
        config: Global config dict (uses ``config["ledger"]``).
    """
    ledger_cfg = config.get("ledger", {})

    if not ledger_cfg.get("enabled", True):
        logger.debug("Ledger disabled — skipping update")
        return

    ledger_path = ledger_cfg.get("path", "seen.json")
    prune_days = ledger_cfg.get("prune_after_days", 90)

    seen = load_ledger(ledger_path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ranked = result.get("ranked", [])

    if not ranked:
        logger.debug("No ranked items to update in ledger")
        return

    added = 0
    updated = 0

    for item in ranked:
        item_id = item.get("id", "")
        if not item_id:
            continue

        profile_name = result.get("profile_name", "")
        if item_id in seen:
            seen[item_id]["last_seen"] = today
            # Track which profiles have scored this item
            profiles = seen[item_id].get("profiles", [])
            if profile_name and profile_name not in profiles:
                profiles.append(profile_name)
            seen[item_id]["profiles"] = profiles
            updated += 1
        else:
            seen[item_id] = {
                "score": item.get("score", 0.0),
                "title": item.get("title", ""),
                "first_seen": today,
                "last_seen": today,
                "profiles": [profile_name] if profile_name else [],
            }
            added += 1

    # Prune old entries
    if prune_days > 0:
        before = len(seen)
        seen = prune_old_entries(seen, prune_days)
        pruned = before - len(seen)
        if pruned > 0:
            logger.info("Pruned %d stale ledger entries (older than %d days)", pruned, prune_days)

    save_ledger(seen, ledger_path)
    logger.info("Ledger update: %d added, %d refreshed, %d total", added, updated, len(seen))


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def prune_old_entries(
    seen: dict[str, dict[str, Any]],
    max_days: int,
) -> dict[str, dict[str, Any]]:
    """Remove ledger entries whose ``last_seen`` date is older than *max_days*.

    Args:
        seen: Ledger dict.
        max_days: Maximum age in days. Entries with ``last_seen`` older than
            this are removed.

    Returns:
        A new dict with stale entries removed.
    """
    if max_days <= 0:
        return seen

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    pruned: dict[str, dict[str, Any]] = {}

    for item_id, entry in seen.items():
        last_seen = entry.get("last_seen", "")
        if not last_seen:
            # No date recorded — keep the entry to be safe
            pruned[item_id] = entry
            continue

        try:
            if last_seen >= cutoff_str:
                pruned[item_id] = entry
        except TypeError:
            # Non-string last_seen — keep the entry
            pruned[item_id] = entry

    return pruned


# ---------------------------------------------------------------------------
# Reset per-profile
# ---------------------------------------------------------------------------

def reset_profile(profile_name: str, config: dict[str, Any]) -> int:
    """Remove all ledger entries belonging to a specific profile.

    If an item belongs to multiple profiles, only this profile is removed
    from its profiles list. If it was the only profile, the entry is deleted.

    Args:
        profile_name: Name of the profile to reset.
        config: Global config dict (uses ``config["ledger"]``).

    Returns:
        Number of entries removed or modified.
    """
    ledger_cfg = config.get("ledger", {})
    ledger_path = ledger_cfg.get("path", "seen.json")
    seen = load_ledger(ledger_path)

    removed = 0
    to_delete = []

    for item_id, entry in seen.items():
        profiles = entry.get("profiles", [])
        if profile_name in profiles:
            profiles.remove(profile_name)
            removed += 1
            if not profiles:
                to_delete.append(item_id)
            else:
                entry["profiles"] = profiles

    for item_id in to_delete:
        del seen[item_id]

    save_ledger(seen, ledger_path)
    logger.info("Reset profile '%s': %d entries removed/modified, %d fully deleted",
                profile_name, removed, len(to_delete))
    return removed
