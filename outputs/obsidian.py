"""Obsidian vault output for autofeeder.

Writes one Markdown note per paper into an Obsidian vault, complete with
YAML frontmatter so the notes are queryable via Obsidian Dataview or
similar plugins.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("autofeeder")

_SLUG_MAX = 60


def _slugify(title: str) -> str:
    """Convert a paper title to a filesystem-safe slug.

    Lowercase, hyphens for separators, max ``_SLUG_MAX`` characters.
    """
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:_SLUG_MAX]


def _yaml_escape(value: str) -> str:
    """Escape a string for safe inclusion in YAML frontmatter."""
    if any(c in value for c in (':', '"', "'", "\n", "#", "{", "}", "[", "]")):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return f'"{value}"'


def _build_frontmatter(item: dict[str, Any], profile_name: str) -> str:
    """Build YAML frontmatter block for a single paper note."""
    tags = item.get("tags", [])
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in tags) + "]" if tags else "[]"

    lines = [
        "---",
        f"title: {_yaml_escape(item.get('title', 'Untitled'))}",
        f"source: {_yaml_escape(item.get('source', 'Unknown'))}",
        f"score: {item.get('score', 0.0):.2f}",
        f"tags: {tags_yaml}",
        f"date: {(item.get('published_utc') or '')[:10] or 'unknown'}",
        f"link: {_yaml_escape(item.get('link', ''))}",
        f"profile: {_yaml_escape(profile_name)}",
        f"cites_my_work: {'true' if item.get('cites_your_work', False) else 'false'}",
        f"is_new: {'true' if item.get('is_new', False) else 'false'}",
        f"content_source: {_yaml_escape(item.get('content_source', 'unknown'))}",
        f"content_source_label: {_yaml_escape(item.get('content_source_label', ''))}",
        "---",
    ]
    return "\n".join(lines)


def _build_body(item: dict[str, Any]) -> str:
    """Build the note body with headline, takeaways, and relevance."""
    lines: list[str] = []

    headline = item.get("headline")
    content_source_label = item.get("content_source_label", "")
    key_takeaways = item.get("key_takeaways")
    relevance = item.get("relevance")

    if headline:
        lines.append(f"> {headline}")
        lines.append("")

    if content_source_label:
        lines.append(content_source_label)
        lines.append("")

    if key_takeaways:
        lines.append("## Key takeaways")
        lines.append("")
        for t in key_takeaways:
            lines.append(f"- {t}")
        lines.append("")

    if relevance:
        lines.append("## Why this matters")
        lines.append("")
        lines.append(relevance)
        lines.append("")

    return "\n".join(lines)


def publish(
    digest_data: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Write one Markdown note per paper into an Obsidian vault.

    Notes are placed in ``{vault_path}/{subfolder}/{date}-{slug}.md`` and
    include YAML frontmatter for Dataview compatibility.

    If the vault path is empty or does not exist, the output is skipped
    with a warning.  Existing files are overwritten (idempotent).
    """
    obsidian_cfg = profile.get("outputs", {}).get("obsidian", {})
    vault_path_raw = obsidian_cfg.get("vault_path", "")

    if not vault_path_raw:
        logger.warning("Obsidian output skipped — vault_path is empty")
        return

    vault_path = Path(vault_path_raw).expanduser()
    if not vault_path.is_dir():
        logger.warning(
            "Obsidian output skipped — vault path does not exist: %s", vault_path
        )
        return

    subfolder = obsidian_cfg.get("subfolder", "autofeeder")
    target_dir = vault_path / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)

    profile_name = digest_data.get("profile_name", "unknown")
    date = digest_data.get("date", "unknown")
    items = digest_data.get("items", [])

    written = 0
    for item in items:
        title = item.get("title", "Untitled")
        slug = _slugify(title)
        filename = f"{date}-{slug}.md"
        filepath = target_dir / filename

        frontmatter = _build_frontmatter(item, profile_name)
        body = _build_body(item)
        content = f"{frontmatter}\n\n{body}"

        try:
            filepath.write_text(content, encoding="utf-8")
            written += 1
        except OSError:
            logger.exception("Failed to write Obsidian note: %s", filepath)

    logger.info(
        "Obsidian output: wrote %d/%d notes to %s", written, len(items), target_dir
    )
