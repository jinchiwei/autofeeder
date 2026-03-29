"""Slack Block Kit output for autofeeder.

Posts a compact notification digest (TL;DR + top 5) to a Slack incoming
webhook URL.  The full digest is delivered via other outputs (email,
Obsidian, etc.) — Slack is intentionally a summary notification.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("autofeeder")

# Slack imposes a hard limit of ~50 blocks per message and we also need to
# stay under the webhook payload size limit.  We chunk on serialised size.
_MAX_PAYLOAD_CHARS = 40_000

# How many items to include in the Slack notification.
_TOP_N = 5


def _resolve_webhook(profile: dict[str, Any]) -> str | None:
    """Return the resolved webhook URL, or None if not usable."""
    slack_cfg = profile.get("outputs", {}).get("slack", {})
    raw = slack_cfg.get("webhook", "")
    if not raw:
        return None
    if raw.startswith("$"):
        resolved = os.environ.get(raw[1:], "")
        if not resolved:
            logger.warning(
                "Slack webhook env var %s is not set — skipping Slack output", raw
            )
            return None
        return resolved
    return raw


def _active_outputs(profile: dict[str, Any]) -> str:
    """Return a human-readable string of non-Slack outputs that are enabled."""
    outputs_cfg = profile.get("outputs", {})
    names: list[str] = []

    email_recipients = outputs_cfg.get("email", {}).get("recipients", [])
    if email_recipients:
        names.append("email")

    obsidian_vault = outputs_cfg.get("obsidian", {}).get("vault_path", "")
    if obsidian_vault:
        names.append("Obsidian")

    if not names:
        return "Full digest available in output/"
    return f"Full digest delivered to {' + '.join(names)}"


def _item_blocks(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Block Kit blocks for a single digest item (compact format)."""
    title = item.get("title", "Untitled")
    link = item.get("link", "")
    source = item.get("source", "Unknown")
    score = item.get("score", 0.0)
    is_new = item.get("is_new", False)
    cites = item.get("cites_your_work", False)
    headline = item.get("headline")
    content_source_label = item.get("content_source_label", "")

    blocks: list[dict[str, Any]] = []

    # Title with optional badges
    prefix = ""
    if cites:
        prefix += "🔬 "
    if is_new:
        prefix += "✨ "

    if link:
        title_text = f"{prefix}<{link}|{title}>"
    else:
        title_text = f"{prefix}{title}"

    # Source line: score + source on same line
    source_line = f"Score: *{score:.2f}* · {source}"
    if content_source_label:
        source_line += f" · {content_source_label}"

    # Combine title + score/source + headline into one section block
    text_parts = [f"*{title_text}*", source_line]
    if headline:
        text_parts.append(headline)

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "\n".join(text_parts),
        },
    })

    return blocks


def _build_blocks(
    digest_data: dict[str, Any],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the full list of Block Kit blocks for the notification."""
    profile_name = digest_data.get("profile_name", "unknown")
    date = digest_data.get("date", "unknown")
    items = digest_data.get("items", [])
    total_scored = digest_data.get("total_scored", 0)
    tldr = digest_data.get("tldr", "")

    blocks: list[dict[str, Any]] = []

    # --- Empty digest path ---
    if not items:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Quiet week for *{profile_name}*. "
                    f"{total_scored if total_scored else 'No'} papers scanned, "
                    f"none above threshold."
                ),
            },
        })
        return blocks

    # --- Header ---
    header_text = (
        f"autofeeder · {profile_name} · {date} — "
        f"{total_scored} papers scanned, {len(items)} worth your time"
    )
    header_text = header_text[:150]
    blocks.append({
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": header_text,
            "emoji": True,
        },
    })

    # --- TL;DR ---
    if tldr:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": tldr,
            },
        })

    blocks.append({"type": "divider"})

    # --- Top N items ---
    top_items = items[:_TOP_N]
    for item in top_items:
        blocks.extend(_item_blocks(item))

    blocks.append({"type": "divider"})

    # --- Footer ---
    footer_text = _active_outputs(profile)
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": footer_text}],
    })

    return blocks


def _chunk_blocks(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split blocks into chunks that each serialise under the payload limit."""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0

    for block in blocks:
        block_json = json.dumps(block)
        block_size = len(block_json)

        if current and (current_size + block_size) > _MAX_PAYLOAD_CHARS:
            chunks.append(current)
            current = []
            current_size = 0

        current.append(block)
        current_size += block_size

    if current:
        chunks.append(current)

    return chunks


def publish(
    digest_data: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Post the digest notification to a Slack incoming webhook.

    Posts a compact summary: TL;DR + top 5 items.  The full digest is
    delivered via other configured outputs (email, Obsidian, etc.).

    Resolves the webhook URL from the profile config (supports ``$ENV_VAR``
    references).  If the payload exceeds 40 000 chars it is automatically
    chunked into multiple POST requests.

    Logs errors on failure but never raises — output failures must not crash
    the pipeline.
    """
    webhook_url = _resolve_webhook(profile)
    if not webhook_url:
        logger.info("Slack output skipped — no webhook configured")
        return

    all_blocks = _build_blocks(digest_data, profile)
    chunks = _chunk_blocks(all_blocks)

    logger.info(
        "Posting Slack digest (%d blocks in %d message(s))",
        len(all_blocks),
        len(chunks),
    )

    for i, chunk in enumerate(chunks):
        payload = {"blocks": chunk}
        try:
            resp = httpx.post(
                webhook_url,
                json=payload,
                timeout=30.0,
            )
            if resp.status_code != 200:
                logger.error(
                    "Slack webhook returned %d for chunk %d/%d: %s",
                    resp.status_code,
                    i + 1,
                    len(chunks),
                    resp.text[:500],
                )
            else:
                logger.debug("Slack chunk %d/%d posted", i + 1, len(chunks))
        except httpx.HTTPError:
            logger.exception("Slack webhook request failed for chunk %d/%d", i + 1, len(chunks))
