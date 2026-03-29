"""Profile loader for autofeeder.

Reads a TOML profile from ``profiles/`` and returns a validated dict containing
feeds, interest keywords/narrative, paywalled domains, user publications, output
configs, and per-profile config overrides.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger("autofeeder")


def _parse_feed_entry(raw: str) -> dict[str, str]:
    """Parse a single feed entry string.

    Supported formats:
        - ``"Display Name | https://example.com/feed.xml"``
        - ``"https://example.com/feed.xml"`` (bare URL — name derived later)

    Returns:
        ``{"name": str_or_empty, "url": str}``
    """
    if "|" in raw:
        parts = raw.split("|", maxsplit=1)
        name = parts[0].strip()
        url = parts[1].strip()
    else:
        name = ""
        url = raw.strip()
    return {"name": name, "url": url}


def _validate_profile(profile: dict[str, Any], path: Path) -> None:
    """Raise ``ValueError`` if the profile is missing required fields."""
    feeds = profile.get("feeds", [])
    if not feeds:
        raise ValueError(f"Profile {path} must define at least one feed")


def load_profile(path: str | Path) -> dict[str, Any]:
    """Load and validate a profile TOML file.

    Args:
        path: Path to the profile TOML file (e.g. ``profiles/neuro.toml``).

    Returns:
        A dict with the following keys:

        - ``name`` (str): Display name for the profile.
        - ``description`` (str): Brief description.
        - ``feeds`` (list[dict]): Each entry has ``"name"`` and ``"url"``.
        - ``interests`` (dict): ``"keywords"`` (list[str]) and ``"narrative"`` (str).
        - ``paywalled_domains`` (list[str]): Domains known to be paywalled.
        - ``my_work`` (dict): ``"tools"`` (list[str]) and ``"paper_keywords"`` (list[str]).
        - ``overrides`` (dict): Per-profile config overrides (flat keys).
        - ``outputs`` (dict): Output plugin configs (e.g. slack, obsidian, email).

    Raises:
        FileNotFoundError: If the profile file does not exist.
        ValueError: If the profile is missing required fields.
    """
    profile_path = Path(path)
    if not profile_path.is_file():
        raise FileNotFoundError(f"Profile not found: {profile_path}")

    with open(profile_path, "rb") as f:
        raw = tomllib.load(f)

    # ---- Basic metadata ----
    name: str = raw.get("name", profile_path.stem)
    description: str = raw.get("description", "")

    # ---- Feeds ----
    feeds_section = raw.get("feeds", {})
    feed_list: list[dict[str, str]] = []

    if isinstance(feeds_section, dict):
        raw_list = feeds_section.get("list", [])
        for entry in raw_list:
            if isinstance(entry, str):
                feed_list.append(_parse_feed_entry(entry))
            elif isinstance(entry, dict):
                # Already structured: {"name": ..., "url": ...}
                feed_list.append({
                    "name": entry.get("name", ""),
                    "url": entry.get("url", ""),
                })
    elif isinstance(feeds_section, list):
        # Top-level list (alternative format)
        for entry in feeds_section:
            if isinstance(entry, str):
                feed_list.append(_parse_feed_entry(entry))

    # ---- Interests ----
    interests_section = raw.get("interests", {})
    interests: dict[str, Any] = {
        "keywords": interests_section.get("keywords", []),
        "narrative": interests_section.get("narrative", "").strip(),
    }

    # ---- Paywalled domains ----
    paywalled_domains: list[str] = raw.get("paywalled_domains", [])

    # ---- My work (tools + paper keywords) ----
    my_work_section = raw.get("my_work", {})
    my_work: dict[str, list[str]] = {
        "tools": my_work_section.get("tools", []),
        "paper_keywords": my_work_section.get("paper_keywords", []),
    }

    # ---- Per-profile overrides ----
    overrides: dict[str, Any] = raw.get("overrides", {})

    # ---- Output configs ----
    outputs: dict[str, Any] = raw.get("outputs", {})

    profile: dict[str, Any] = {
        "name": name,
        "description": description,
        "enabled": raw.get("enabled", True),
        "feeds": feed_list,
        "interests": interests,
        "paywalled_domains": paywalled_domains,
        "my_work": my_work,
        "overrides": overrides,
        "outputs": outputs,
    }

    _validate_profile(profile, profile_path)

    logger.info(
        "Loaded profile '%s': %d feeds, %d keywords",
        name,
        len(feed_list),
        len(interests["keywords"]),
    )

    return profile
