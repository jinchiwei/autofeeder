"""HTML email output via Resend for autofeeder.

Sends a styled digest email to the configured recipient list using the
Resend transactional email API.  Shows TL;DR + top 5-8 items.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("autofeeder")

_RESEND_URL = "https://api.resend.com/emails"

# Branding colours
_TURQUOISE = "#40E0D0"
_DEEPPINK = "#FF1493"
_GOLD = "#FFD700"
_BLUEVIOLET = "#8A2BE2"

# How many items to include in the email.
_MAX_ITEMS = 12


def _html_escape(text: str) -> str:
    """Minimal HTML escaping for user-generated content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_item_html(item: dict[str, Any]) -> str:
    """Render a single digest item as an HTML block with inline styles."""
    title = _html_escape(item.get("title", "Untitled"))
    link = item.get("link", "")
    source = _html_escape(item.get("source", "Unknown"))
    score = item.get("score", 0.0)
    is_new = item.get("is_new", False)
    cites = item.get("cites_your_work", False)
    headline = item.get("headline")
    key_takeaways = item.get("key_takeaways")
    relevance = item.get("relevance")
    tags = item.get("tags", [])
    content_source_label = item.get("content_source_label", "")

    parts: list[str] = []

    # Title row
    badges = ""
    if is_new:
        badges += (
            f'<span style="background:{_GOLD};color:#000;padding:2px 6px;'
            f'border-radius:3px;font-size:12px;font-weight:bold;margin-right:4px;">'
            f"NEW</span>"
        )
    if cites:
        badges += (
            f'<span style="background:{_BLUEVIOLET};color:#fff;padding:2px 6px;'
            f'border-radius:3px;font-size:12px;font-weight:bold;margin-right:4px;">'
            f"Cites your work</span>"
        )

    if link:
        title_html = (
            f'<a href="{_html_escape(link)}" style="color:{_TURQUOISE};'
            f'text-decoration:none;font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:18px;font-weight:bold;">{title}</a>'
        )
    else:
        title_html = (
            f'<span style="color:{_TURQUOISE};font-family:Geist,Helvetica,Arial,'
            f'sans-serif;font-size:18px;font-weight:bold;">{title}</span>'
        )

    parts.append(
        f'<div style="margin-bottom:24px;padding:16px;border:1px solid #333;'
        f'border-radius:8px;background:#1a1a2e;">'
    )
    parts.append(f"<div>{badges}{title_html}</div>")
    parts.append(
        f'<div style="font-family:Geist Mono,Consolas,monospace;font-size:13px;'
        f'color:#aaa;margin-top:4px;">'
        f"{source} &middot; Score: "
        f'<span style="color:{_DEEPPINK};font-weight:bold;">{score:.2f}</span>'
        f"</div>"
    )

    # Content source label
    if content_source_label:
        parts.append(
            f'<div style="font-family:Geist Mono,Consolas,monospace;font-size:11px;'
            f'color:#888;margin-top:2px;">'
            f"{_html_escape(content_source_label)}</div>"
        )

    # Headline
    if headline:
        parts.append(
            f'<div style="margin-top:12px;padding:8px 12px;border-left:3px solid '
            f'{_TURQUOISE};color:#ddd;font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:14px;font-style:italic;">'
            f"{_html_escape(headline)}</div>"
        )

    # Key takeaways
    if key_takeaways:
        parts.append(
            f'<div style="margin-top:10px;color:#ccc;font-family:Geist,Helvetica,'
            f'Arial,sans-serif;font-size:14px;"><strong>Key takeaways:</strong></div>'
        )
        parts.append(
            '<ul style="margin:4px 0 0 0;padding-left:20px;color:#ccc;'
            'font-family:Geist,Helvetica,Arial,sans-serif;font-size:13px;">'
        )
        for t in key_takeaways:
            parts.append(f"<li>{_html_escape(t)}</li>")
        parts.append("</ul>")

    # Relevance
    if relevance:
        parts.append(
            f'<div style="margin-top:10px;color:#bbb;font-family:Geist,Helvetica,'
            f'Arial,sans-serif;font-size:13px;">'
            f"<strong>Why this matters:</strong> {_html_escape(relevance)}</div>"
        )

    # Tags
    if tags:
        tag_spans = " ".join(
            f'<span style="background:#2a2a4a;color:{_GOLD};padding:2px 6px;'
            f'border-radius:3px;font-family:Geist Mono,Consolas,monospace;'
            f'font-size:11px;">{_html_escape(t)}</span>'
            for t in tags
        )
        parts.append(f'<div style="margin-top:10px;">{tag_spans}</div>')

    parts.append("</div>")
    return "\n".join(parts)


def _build_html(digest_data: dict[str, Any]) -> str:
    """Build the full HTML email body with inline CSS."""
    profile_name = _html_escape(digest_data.get("profile_name", "unknown"))
    date = _html_escape(digest_data.get("date", "unknown"))
    description = _html_escape(digest_data.get("profile_description", ""))
    items = digest_data.get("items", [])
    total_items = len(items)
    items = items[:_MAX_ITEMS]
    min_score = digest_data.get("min_score", 0.0)
    tldr = digest_data.get("tldr", "")
    is_first_run = digest_data.get("is_first_run", False)

    items_html = "\n".join(_build_item_html(item) for item in items)

    # TL;DR section
    tldr_html = ""
    if tldr:
        # Split on blank lines to get paragraphs
        paragraphs = [p.strip() for p in tldr.split("\n\n") if p.strip()]
        # If model returned a single block, force-split every 3 sentences
        if len(paragraphs) == 1:
            import re
            sentences = re.split(r'(?<=[.!?])\s+', paragraphs[0])
            paragraphs = []
            for i in range(0, len(sentences), 3):
                chunk = " ".join(sentences[i:i + 3])
                if chunk:
                    paragraphs.append(chunk)
        tldr_body = "".join(
            f'<p style="margin:0 0 12px 0;color:#ddd;font-family:Geist,Helvetica,Arial,'
            f'sans-serif;font-size:14px;line-height:1.6;">{_html_escape(p)}</p>'
            for p in paragraphs
        )
        tldr_html = (
            f'<div style="margin-bottom:24px;padding:14px 16px;'
            f'border-left:4px solid {_TURQUOISE};background:#16162b;'
            f'border-radius:0 6px 6px 0;">'
            f'<div style="color:#999;font-family:Geist Mono,Consolas,monospace;'
            f'font-size:11px;text-transform:uppercase;margin-bottom:6px;">TL;DR</div>'
            f'{tldr_body}'
            f"</div>"
        )

    # First-run welcome note
    first_run_html = ""
    if is_first_run:
        first_run_html = (
            f'<div style="margin-bottom:24px;padding:14px 16px;'
            f'border:1px dashed {_GOLD};background:#1a1a2e;border-radius:6px;">'
            f'<div style="color:{_GOLD};font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:15px;font-weight:bold;margin-bottom:4px;">'
            f'Welcome to autofeeder!</div>'
            f'<div style="color:#ccc;font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:13px;line-height:1.5;">'
            f'This is your first digest. Scores and rankings will improve as the '
            f'system learns your preferences over time.</div>'
            f"</div>"
        )

    # Footer note about total items
    footer_note = ""
    if total_items > len(items):
        footer_note = (
            f'<div style="text-align:center;margin-top:16px;margin-bottom:8px;'
            f'color:#999;font-family:Geist,Helvetica,Arial,sans-serif;font-size:13px;">'
            f"See all {total_items} papers in your full digest</div>"
        )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<style>:root {{ color-scheme: dark; }}</style>
</head>
<body style="margin:0;padding:0;background:#0d0d1a;color:#eee;font-family:Geist,Helvetica,Arial,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:24px;">

<div style="text-align:center;margin-bottom:32px;">
  <h1 style="margin:0;font-size:28px;color:{_TURQUOISE};font-family:Geist,Helvetica,Arial,sans-serif;">
    autofeeder
  </h1>
  <p style="margin:8px 0 0 0;color:#999;font-family:Geist Mono,Consolas,monospace;font-size:13px;">
    {profile_name} &middot; {date}
  </p>
</div>

<div style="text-align:center;margin-bottom:24px;padding:12px;background:#16162b;border-radius:6px;">
  <span style="color:{_DEEPPINK};font-weight:bold;font-size:20px;">{total_items}</span>
  <span style="color:#ccc;font-size:14px;"> items from {description} &middot; score &ge; {min_score:.2f}</span>
</div>

{first_run_html}

{tldr_html}

{items_html}

{footer_note}

<div style="text-align:center;margin-top:32px;padding-top:16px;border-top:1px solid #333;
     color:#666;font-size:11px;font-family:Geist Mono,Consolas,monospace;">
  Generated by autofeeder
</div>

</div>
</body>
</html>"""


def publish(
    digest_data: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Send the digest as an HTML email via the Resend API.

    Reads recipients from ``profile["outputs"]["email"]["recipients"]`` and
    the API key from the ``RESEND_API_KEY`` environment variable.

    Skips silently (with a log message) if recipients or the API key are
    not configured.  Does not send if the digest contains no items.
    HTTP errors are logged but never raised.
    """
    # Don't send email if no items
    items = digest_data.get("items", [])
    if not items:
        logger.info("Email output skipped — no items in digest, nothing to send")
        return

    email_cfg = profile.get("outputs", {}).get("email", {})
    recipients: list[str] = email_cfg.get("recipients", [])

    if not recipients:
        logger.info("Email output skipped — no recipients configured")
        return

    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.info("Email output skipped — RESEND_API_KEY not set")
        return

    profile_name = digest_data.get("profile_name", "unknown")
    date = digest_data.get("date", "unknown")
    subject = f"autofeeder: {profile_name} digest — {date}"

    html_body = _build_html(digest_data)

    from_addr = email_cfg.get("from", "autofeeder <digest@autofeeder.dev>")

    payload = {
        "from": from_addr,
        "to": recipients,
        "subject": subject,
        "html": html_body,
    }

    logger.info(
        "Sending email digest to %d recipient(s): %s",
        len(recipients),
        ", ".join(recipients),
    )

    try:
        resp = httpx.post(
            _RESEND_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        if resp.status_code in (200, 201):
            logger.info("Email sent successfully (status %d)", resp.status_code)
        else:
            logger.error(
                "Resend API returned %d: %s", resp.status_code, resp.text[:500]
            )
    except httpx.HTTPError:
        logger.exception("Failed to send email via Resend API")
