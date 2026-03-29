"""Markdown digest output for autofeeder.

Writes a human-readable Markdown file containing the scored and summarised
papers for a single profile run, organised into tiered sections.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("autofeeder")

# ---------------------------------------------------------------------------
# Per-item formatters
# ---------------------------------------------------------------------------

def _format_item_full(item: dict[str, Any]) -> str:
    """Render one digest item as a full Markdown section.

    Used for "Builds on your work" and "Top picks" tiers.
    """
    title = item.get("title", "Untitled")
    link = item.get("link", "")
    source = item.get("source", "Unknown")
    score = item.get("score", 0.0)
    published = item.get("published_utc") or ""
    is_new = item.get("is_new", False)
    cites = item.get("cites_your_work", False)
    content_source_label = item.get("content_source_label", "")
    headline = item.get("headline")
    key_takeaways = item.get("key_takeaways")
    relevance = item.get("relevance")
    tags = item.get("tags", [])

    lines: list[str] = []

    # Title
    if link:
        lines.append(f"### [{title}]({link})")
    else:
        lines.append(f"### {title}")

    # Meta line
    pub_str = f" · Published: {published[:10]}" if published else ""
    lines.append(f"*{source}* · Score: **{score:.2f}**{pub_str}")

    # Badges
    badges: list[str] = []
    if is_new:
        badges.append("NEW")
    if cites:
        badges.append("Cites your work")
    if badges:
        # Prefix with sparkle / microscope for visual scanning
        prefix_parts: list[str] = []
        if is_new:
            prefix_parts.append(f"\u2728 `NEW`")
        if cites:
            prefix_parts.append(f"\U0001f52c `Cites your work`")
        lines.append(" ".join(prefix_parts))

    # Content source label
    if content_source_label:
        lines.append(content_source_label)

    # Headline
    if headline:
        lines.append("")
        lines.append(f"> {headline}")

    # Key takeaways
    if key_takeaways:
        lines.append("")
        lines.append("**Key takeaways:**")
        for t in key_takeaways:
            lines.append(f"- {t}")

    # Relevance
    if relevance:
        lines.append("")
        lines.append(f"**Why this matters to you:** {relevance}")

    # Tags
    if tags:
        lines.append("")
        lines.append("Tags: " + " ".join(f"`{t}`" for t in tags))

    return "\n".join(lines)


def _format_item_condensed(item: dict[str, Any]) -> str:
    """Render one digest item in condensed format.

    Used for the "Also relevant" tier — no key_takeaways, no full text details.
    """
    title = item.get("title", "Untitled")
    link = item.get("link", "")
    source = item.get("source", "Unknown")
    score = item.get("score", 0.0)
    headline = item.get("headline")
    tags = item.get("tags", [])

    lines: list[str] = []

    # Title
    if link:
        lines.append(f"### [{title}]({link})")
    else:
        lines.append(f"### {title}")

    # Meta line
    lines.append(f"*{source}* · Score: **{score:.2f}**")

    # Headline
    if headline:
        lines.append(f"> {headline}")

    # Tags
    if tags:
        lines.append("Tags: " + " ".join(f"`{t}`" for t in tags))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def publish(
    digest_data: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Write the digest as a Markdown file.

    File is written to ``{config.output.dir}/{profile_name}.md``.
    Creates the output directory if it does not exist.
    """
    profile_name = digest_data.get("profile_name", "unknown")
    date = digest_data.get("date", "unknown")
    items = digest_data.get("items", [])
    min_score = digest_data.get("min_score", 0.0)
    tldr = digest_data.get("tldr", "")
    is_first_run = digest_data.get("is_first_run", False)

    output_dir = Path(config.get("output", {}).get("dir", "output")) / profile_name
    output_dir.mkdir(parents=True, exist_ok=True)

    out_path = output_dir / f"{date}.md"

    # ---- Partition items into tiers ----
    TOP_PICK_THRESHOLD = 0.80

    cites_items: list[dict[str, Any]] = []
    top_picks: list[dict[str, Any]] = []
    also_relevant: list[dict[str, Any]] = []

    for item in items:
        if item.get("cites_your_work", False):
            cites_items.append(item)
        elif item.get("score", 0.0) >= TOP_PICK_THRESHOLD:
            top_picks.append(item)
        else:
            also_relevant.append(item)

    total_scored = len(items)
    total_included = len(items)

    lines: list[str] = []

    # ---- Header ----
    lines.append(f"# autofeeder \u2014 {profile_name} ({date})")
    lines.append("")

    # First-run note
    if is_first_run:
        lines.append(
            f"*This is your first autofeeder run! All {total_scored} items are new. "
            "Future runs will highlight only papers published since your last digest.*"
        )
        lines.append("")

    # ---- TL;DR ----
    if tldr:
        lines.append("## TL;DR")
        lines.append("")
        lines.append(tldr)
        lines.append("")

    # ---- Builds on your work ----
    if cites_items:
        lines.append("## \U0001f52c Builds on your work")
        lines.append("")
        for item in cites_items:
            lines.append(_format_item_full(item))
            lines.append("")

    # ---- Top picks ----
    if top_picks:
        lines.append("## Top picks")
        lines.append("")
        for item in top_picks:
            lines.append(_format_item_full(item))
            lines.append("")

    # ---- Also relevant ----
    if also_relevant:
        lines.append("## Also relevant")
        lines.append("")
        for item in also_relevant:
            lines.append(_format_item_condensed(item))
            lines.append("")

    # ---- Feed health ----
    feed_health = digest_data.get("feed_health")
    if feed_health:
        lines.append("<details><summary>Feed health</summary>")
        lines.append("")
        dead_feeds = feed_health.get("dead_feeds", [])
        lines.append(
            f"- **{feed_health.get('healthy', 0)}** healthy feeds"
        )
        lines.append(
            f"- **{feed_health.get('dead', 0)}** dead feeds"
        )
        lines.append(
            f"- **{feed_health.get('noisy', 0)}** noisy feeds"
        )
        if dead_feeds:
            lines.append("")
            lines.append("Dead feeds:")
            for df in dead_feeds:
                lines.append(f"- {df}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ---- Footer ----
    lines.append("---")
    lines.append(f"{total_scored} items scored, {total_included} included \u00b7 Generated by autofeeder")

    content = "\n".join(lines)

    try:
        out_path.write_text(content, encoding="utf-8")
        logger.info("Markdown digest written to %s (%d items)", out_path, len(items))
    except OSError:
        logger.exception("Failed to write markdown digest to %s", out_path)
        raise
