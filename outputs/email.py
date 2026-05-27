"""HTML email output via Resend for autofeeder.

Sends a styled digest email to the configured recipient list using the
Resend transactional email API.  Shows TL;DR + top 5-8 items.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _split_cohorts(
    recipients: list[str], config: dict[str, Any], today: dt.date | None = None,
) -> tuple[list[str], list[str], bool]:
    """Split a profile's `recipients` list into (daily_cohort, weekly_cohort, is_weekly_day).

    daily_cohort = addresses in config[cadence][daily_only] (always emailed).
    weekly_cohort = all other addresses (only emailed on weekly_day).
    Case-insensitive match; preserves original order.

    When [cadence] is absent or daily_only is empty, everyone is treated as
    daily_cohort (backward-compatible: every recipient gets every email).
    """
    cadence = config.get("cadence", {})
    daily_only_raw = cadence.get("daily_only", [])
    today = today or dt.date.today()
    weekly_day_str = str(cadence.get("weekly_day", "monday")).lower().strip()
    weekly_day = _WEEKDAY_NAMES.get(weekly_day_str, 0)
    is_weekly_day = today.weekday() == weekly_day
    if not daily_only_raw:
        return recipients, [], is_weekly_day
    daily_only = {a.lower().strip() for a in daily_only_raw}
    daily_cohort = [r for r in recipients if r.lower() in daily_only]
    weekly_cohort = [r for r in recipients if r.lower() not in daily_only]
    return daily_cohort, weekly_cohort, is_weekly_day


def _collate_week_digest(
    profile_name: str, today: dt.date, output_dir: Path, days: int = 7,
) -> dict[str, Any] | None:
    """Build a past-week digest by merging JSON sidecars from the last `days` days.

    Reads `output/{profile_name}/YYYY-MM-DD.json` for each of the last N days,
    merges items (dedup by link / url / title — first occurrence wins), and
    score-ranks the result. Returns None if no sidecars are found.

    The returned dict matches the digest_data shape consumed by _build_html.
    """
    profile_dir = output_dir / profile_name
    if not profile_dir.is_dir():
        return None

    seen: set[str] = set()
    merged_items: list[dict[str, Any]] = []
    daily_tldrs: list[tuple[str, str]] = []
    source_dates: list[str] = []
    min_score_from_sidecar: float | None = None

    for offset in range(days):
        d = today - dt.timedelta(days=offset)
        sidecar = profile_dir / f"{d.isoformat()}.json"
        if not sidecar.is_file():
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable sidecar %s: %s", sidecar, exc)
            continue
        source_dates.append(d.isoformat())
        # Inherit the profile's min_score from the first valid sidecar so the
        # weekly email's count chip displays the actual filter threshold rather
        # than 0.00 (which was the previous behavior and looked like no filter).
        if min_score_from_sidecar is None and "min_score" in data:
            min_score_from_sidecar = float(data["min_score"])
        if data.get("tldr"):
            daily_tldrs.append((d.isoformat(), data["tldr"]))
        for item in data.get("items", []):
            key = (item.get("link") or item.get("url") or item.get("title") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged_items.append(item)

    if not source_dates:
        return None

    merged_items.sort(key=lambda it: it.get("score", 0.0), reverse=True)
    daily_tldrs.sort(key=lambda dt_: dt_[0])  # chronological

    week_label = f"week of {min(source_dates)} – {today.isoformat()}"
    if daily_tldrs:
        tldr_body = "\n\n".join(f"**{d}:** {t}" for d, t in daily_tldrs)
        weekly_tldr = (
            f"Past-week digest collated from {len(source_dates)} daily run(s).\n\n"
            + tldr_body
        )
    else:
        weekly_tldr = f"Past-week digest collated from {len(source_dates)} daily run(s)."

    return {
        "profile_name": profile_name,
        "date": week_label,
        "items": merged_items,
        "tldr": weekly_tldr,
        "min_score": min_score_from_sidecar if min_score_from_sidecar is not None else 0.0,
        "is_first_run": False,
    }


def _send_one(
    *, api_key: str, from_addr: str, recipients: list[str],
    subject: str, html_body: str,
) -> None:
    """Single Resend POST. Logs but never raises."""
    payload = {"from": from_addr, "to": recipients, "subject": subject, "html": html_body}
    logger.info(
        "Sending '%s' to %d recipient(s): %s",
        subject, len(recipients), ", ".join(recipients),
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
            logger.error("Resend API returned %d: %s", resp.status_code, resp.text[:500])
    except httpx.HTTPError:
        logger.exception("Failed to send email via Resend API")

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


def _markdown_inline(text: str) -> str:
    """HTML-escape + minimal markdown→HTML for **bold** and *italic*.

    Safe because we escape FIRST, so any HTML in ``text`` is neutralized
    before regex substitution. Used for fields like TL;DR where the
    upstream LLM (or our weekly collator) emits markdown-style emphasis
    that would otherwise render as literal ``**date**`` in the email.
    """
    import re as _re
    out = _html_escape(text)
    # **bold** first so single-asterisk italic doesn't eat the inner pair
    out = _re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", out)
    # *italic* — single * not adjacent to another *
    out = _re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", out)
    return out


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
            f'<span class="bg-brand-gold" style="background:{_GOLD} !important;color:#000;padding:2px 6px;'
            f'border-radius:3px;font-size:13px;font-weight:bold;margin-right:4px;">'
            f"NEW</span>"
        )
    if cites:
        badges += (
            f'<span class="bg-brand-blueviolet" style="background:{_BLUEVIOLET} !important;color:#fff;padding:2px 6px;'
            f'border-radius:3px;font-size:13px;font-weight:bold;margin-right:4px;">'
            f"Cites your work</span>"
        )

    if link:
        title_html = (
            f'<a href="{_html_escape(link)}" class="brand-turquoise" style="color:{_TURQUOISE} !important;'
            f'text-decoration:none;font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:19px;font-weight:bold;">{title}</a>'
        )
    else:
        title_html = (
            f'<span class="brand-turquoise" style="color:{_TURQUOISE} !important;font-family:Geist,Helvetica,Arial,'
            f'sans-serif;font-size:19px;font-weight:bold;">{title}</span>'
        )

    parts.append(
        f'<div style="margin-bottom:24px;padding:16px;border:1px solid #333;'
        f'border-radius:8px;background:#1a1a2e;">'
    )
    parts.append(f"<div>{badges}{title_html}</div>")
    # Reading-time estimate (~250 words/min, 5 chars/word average).
    # content_chars may reflect summary-only or full text depending on
    # what was extracted — the content_source_label below makes which-is-which clear.
    content_chars = item.get("content_chars", 0)
    length_html = ""
    if content_chars > 0:
        words = max(1, content_chars // 5)
        minutes = max(1, round(words / 250))
        length_html = f' &middot; <span style="color:#999;">~{minutes} min read</span>'

    parts.append(
        f'<div style="font-family:Geist Mono,Consolas,monospace;font-size:14px;'
        f'color:#aaa;margin-top:4px;">'
        f"{source} &middot; Score: "
        f'<span class="brand-deeppink" style="color:{_DEEPPINK} !important;font-weight:bold;">{score:.2f}</span>'
        f"{length_html}"
        f"</div>"
    )

    # Content source label
    if content_source_label:
        parts.append(
            f'<div style="font-family:Geist Mono,Consolas,monospace;font-size:12px;'
            f'color:#888;margin-top:2px;">'
            f"{_html_escape(content_source_label)}</div>"
        )

    # Headline
    if headline:
        parts.append(
            f'<div style="margin-top:12px;padding:8px 12px;border-left:3px solid '
            f'{_TURQUOISE};color:#ddd;font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:15px;font-style:italic;">'
            f"{_html_escape(headline)}</div>"
        )

    # Key takeaways
    if key_takeaways:
        parts.append(
            f'<div style="margin-top:10px;color:#ccc;font-family:Geist,Helvetica,'
            f'Arial,sans-serif;font-size:15px;"><strong>Key takeaways:</strong></div>'
        )
        parts.append(
            '<ul style="margin:4px 0 0 0;padding-left:20px;color:#ccc;'
            'font-family:Geist,Helvetica,Arial,sans-serif;font-size:14px;">'
        )
        for t in key_takeaways:
            parts.append(f"<li>{_html_escape(t)}</li>")
        parts.append("</ul>")

    # Relevance
    if relevance:
        parts.append(
            f'<div style="margin-top:10px;color:#bbb;font-family:Geist,Helvetica,'
            f'Arial,sans-serif;font-size:14px;">'
            f"<strong>Why this matters:</strong> {_html_escape(relevance)}</div>"
        )

    # Tags
    if tags:
        tag_spans = " ".join(
            f'<span class="brand-gold" style="background:#2a2a4a;color:{_GOLD} !important;padding:2px 6px;'
            f'border-radius:3px;font-family:Geist Mono,Consolas,monospace;'
            f'font-size:12px;">{_html_escape(t)}</span>'
            for t in tags
        )
        parts.append(f'<div style="margin-top:10px;">{tag_spans}</div>')

    parts.append("</div>")
    return "\n".join(parts)


def _build_html_inner(digest_data: dict[str, Any]) -> str:
    """Build the body content of the email — first-run note, TL;DR, items, footer note.

    No outer HTML/head/body shell, no header chip. Shared by single-language and
    bilingual renderings (bilingual stacks two inner blocks under one shell).
    """
    items = digest_data.get("items", [])
    total_items = len(items)
    items = items[:_MAX_ITEMS]
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
            f'sans-serif;font-size:15px;line-height:1.6;">{_markdown_inline(p)}</p>'
            for p in paragraphs
        )
        tldr_html = (
            f'<div style="margin-bottom:24px;padding:14px 16px;'
            f'border-left:4px solid {_TURQUOISE};background:#16162b;'
            f'border-radius:0 6px 6px 0;">'
            f'<div style="color:#999;font-family:Geist Mono,Consolas,monospace;'
            f'font-size:12px;text-transform:uppercase;margin-bottom:6px;">TL;DR</div>'
            f'{tldr_body}'
            f"</div>"
        )

    # First-run welcome note
    first_run_html = ""
    if is_first_run:
        first_run_html = (
            f'<div style="margin-bottom:24px;padding:14px 16px;'
            f'border:1px dashed {_GOLD};background:#1a1a2e;border-radius:6px;">'
            f'<div class="brand-gold" style="color:{_GOLD} !important;font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:16px;font-weight:bold;margin-bottom:4px;">'
            f'Welcome to autofeeder!</div>'
            f'<div style="color:#ccc;font-family:Geist,Helvetica,Arial,sans-serif;'
            f'font-size:14px;line-height:1.5;">'
            f'This is your first digest. Scores and rankings will improve as the '
            f'system learns your preferences over time.</div>'
            f"</div>"
        )

    # Footer note about total items
    footer_note = ""
    if total_items > len(items):
        footer_note = (
            f'<div style="text-align:center;margin-top:16px;margin-bottom:8px;'
            f'color:#999;font-family:Geist,Helvetica,Arial,sans-serif;font-size:14px;">'
            f"See all {total_items} papers in your full digest</div>"
        )

    return "\n\n".join(p for p in (first_run_html, tldr_html, items_html, footer_note) if p)


def _build_html_shell(digest_data: dict[str, Any], inner_html: str) -> str:
    """Wrap inner content with autofeeder header, count chip, and HTML shell."""
    profile_name = _html_escape(digest_data.get("profile_name", "unknown"))
    date = _html_escape(digest_data.get("date", "unknown"))
    description = _html_escape(digest_data.get("profile_description", ""))
    total_items = len(digest_data.get("items", []))
    min_score = digest_data.get("min_score", 0.0)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="only dark">
<meta name="supported-color-schemes" content="only dark">
<style>
  :root {{ color-scheme: only dark; }}
  body, table, td {{ background:#0d0d1a !important; color:#eee !important; }}

  /* === Anti-Gmail-auto-darken overrides ===
     Gmail apps tag auto-recolored elements with data-ogsb (Original Gmail
     Style Background) and data-ogsc (Original Gmail Style Color), and
     rewrite inline 'style' values. Class attributes survive — so every
     brand-colored element in _build_*_html carries a matching class. */
  [data-ogsb] {{ background-color: #0d0d1a !important; }}
  [data-ogsc] {{ color: #eee !important; }}
  .brand-turquoise, [data-ogsc].brand-turquoise {{ color: {_TURQUOISE} !important; }}
  .brand-deeppink, [data-ogsc].brand-deeppink {{ color: {_DEEPPINK} !important; }}
  .brand-gold, [data-ogsc].brand-gold {{ color: {_GOLD} !important; }}
  .brand-blueviolet, [data-ogsc].brand-blueviolet {{ color: {_BLUEVIOLET} !important; }}
  .bg-brand-gold, [data-ogsb].bg-brand-gold {{ background: {_GOLD} !important; }}
  .bg-brand-blueviolet, [data-ogsb].bg-brand-blueviolet {{ background: {_BLUEVIOLET} !important; }}

  /* @media (prefers-color-scheme: dark) — explicit dark reassertion.
     Apple Mail + Gmail iOS (system-following theme) respect this. */
  @media (prefers-color-scheme: dark) {{
    body, table, td {{ background:#0d0d1a !important; color:#eee !important; }}
  }}

  /* === Gmail iOS dark-mode-specific override ('u + body' hack) ===
     Gmail iOS sets up the DOM such that selectors prefixed with `u + body`
     match ONLY when Gmail iOS is rendering — other clients see no `<u>`
     before `<body>` and these rules don't apply. Use this to re-pin colors
     after Gmail iOS's auto-darken transform. */
  u + body .ae-canvas {{ background: #0d0d1a !important; }}
  u + body, u + body table, u + body td {{
    background: #0d0d1a !important; color: #eee !important;
  }}
  u + body .brand-turquoise {{ color: {_TURQUOISE} !important; }}
  u + body .brand-deeppink {{ color: {_DEEPPINK} !important; }}
  u + body .brand-gold {{ color: {_GOLD} !important; }}
  u + body .brand-blueviolet {{ color: {_BLUEVIOLET} !important; }}
  u + body .bg-brand-gold {{ background: {_GOLD} !important; }}
  u + body .bg-brand-blueviolet {{ background: {_BLUEVIOLET} !important; }}
</style>
</head>
<!-- Empty <u></u> right before <body> is the Gmail iOS dark-mode detection
     hook. Other clients ignore it; Gmail iOS renders these selectors. -->
<u></u>
<body bgcolor="#0d0d1a" class="ae-canvas" style="margin:0;padding:0;background:#0d0d1a;color:#eee;font-family:Geist,Helvetica,Arial,sans-serif;">
<!-- Outer table = full-width dark canvas. bgcolor attribute is universally
     respected by Gmail / Outlook / Apple Mail; inline-style on divs alone is
     not enough (Gmail web in particular overrides div backgrounds). -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0d0d1a" style="background:#0d0d1a;">
<tr><td align="center" bgcolor="#0d0d1a" style="background:#0d0d1a;padding:24px 0;">
<table role="presentation" width="680" cellpadding="0" cellspacing="0" border="0" bgcolor="#0d0d1a" style="max-width:680px;width:100%;background:#0d0d1a;">
<tr><td bgcolor="#0d0d1a" style="background:#0d0d1a;padding:24px;color:#eee;">

<div style="text-align:center;margin-bottom:32px;">
  <h1 class="brand-turquoise" style="margin:0;font-size:29px;color:{_TURQUOISE} !important;font-family:Geist,Helvetica,Arial,sans-serif;">
    autofeeder
  </h1>
  <p style="margin:8px 0 0 0;color:#999;font-family:Geist Mono,Consolas,monospace;font-size:14px;">
    {profile_name} &middot; {date}
  </p>
</div>

<div style="text-align:center;margin-bottom:24px;padding:12px;background:#16162b;border-radius:6px;">
  <span class="brand-deeppink" style="color:{_DEEPPINK} !important;font-weight:bold;font-size:21px;">{total_items}</span>
  <span style="color:#ccc;font-size:15px;"> items from {description} &middot; score &ge; {min_score:.2f}</span>
</div>

{inner_html}

<div style="text-align:center;margin-top:32px;padding-top:16px;border-top:1px solid #333;
     color:#666;font-size:12px;font-family:Geist Mono,Consolas,monospace;">
  Generated by autofeeder
</div>

</td></tr></table>
</td></tr></table>
</body>
</html>"""


def _build_html(digest_data: dict[str, Any]) -> str:
    """Build the full HTML email body with inline CSS (single language)."""
    return _build_html_shell(digest_data, _build_html_inner(digest_data))


def _build_html_bilingual(
    digest_data: dict[str, Any],
    digest_translated: dict[str, Any],
    lang_label_native: str = "繁體中文",
) -> str:
    """One email with translated content on top, then English version below.

    Outer header / shell uses English (profile slug is untranslated). The two
    inner blocks are stacked with a visual divider.
    """
    divider = (
        f'<div style="margin:48px 0 32px;padding:12px 0 8px;'
        f'border-top:2px solid {_TURQUOISE};text-align:center;'
        f'color:{_TURQUOISE} !important;font-family:Geist Mono,Consolas,monospace;'
        f'font-size:13px;text-transform:uppercase;letter-spacing:2px;">'
        f"English version below &middot; {_html_escape(lang_label_native)} above"
        f"</div>"
    )
    combined = (
        _build_html_inner(digest_translated)
        + "\n\n"
        + divider
        + "\n\n"
        + _build_html_inner(digest_data)
    )
    return _build_html_shell(digest_data, combined)


def _translate_digest_data(
    digest_data: dict[str, Any],
    target_lang: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Return a deep copy of digest_data with text fields translated to target_lang.

    Translatable fields:
      - top-level: tldr, profile_description
      - per-item: title, summary, why_relevant, tags (list)

    Uses one Anthropic LLM call via the same backend autofeeder is configured
    with. Falls back to returning the original (untranslated) deep copy on any
    failure — caller decides whether to use the translation or not.
    """
    import copy
    import json as _json
    import sys
    import time
    from pathlib import Path as _Path

    # Lazy import — avoids hard dep at module load time
    _af_root = _Path(__file__).resolve().parent.parent
    if str(_af_root) not in sys.path:
        sys.path.insert(0, str(_af_root))
    from backends.anthropic_backend import make_client  # type: ignore[import-not-found]

    def _build_batch_payload(item_range, include_top_level: bool) -> dict[str, str]:
        p: dict[str, str] = {}
        if include_top_level:
            if digest_data.get("tldr"):
                p["__tldr"] = digest_data["tldr"]
            if digest_data.get("profile_description"):
                p["__desc"] = digest_data["profile_description"]
        for i in item_range:
            item = digest_data["items"][i]
            for field in ("title", "headline", "relevance"):
                v = item.get(field)
                if isinstance(v, str) and v.strip():
                    p[f"i{i}.{field}"] = v
            tags = item.get("tags")
            if isinstance(tags, list) and tags:
                p[f"i{i}.tags"] = " | ".join(str(t) for t in tags)
            kt = item.get("key_takeaways")
            if isinstance(kt, list) and kt:
                p[f"i{i}.kt"] = " ||| ".join(str(s) for s in kt)
        return p

    def _call_translate_llm(batch_payload: dict[str, str]) -> dict[str, str]:
        prompt = (
            f"Translate the VALUES of the following JSON object to {target_lang}.\n\n"
            f"Rules:\n"
            f"- Return a JSON object with the SAME keys; only values change.\n"
            f"- Preserve URLs, scores, dates, well-known acronyms (US, CCP, NPC, BRI, AUKUS, etc.)\n"
            f"  and proper nouns that have no standard translation.\n"
            f"- For commonly-translated names (Xi Jinping → 習近平, Taiwan → 台灣, etc.) use the\n"
            f"  standard {target_lang} form.\n"
            f"- Preserve markdown (**, *, `, [text](url)) in values that contain it.\n"
            f"- For keys ending in '.tags', values are pipe-separated ('a | b | c'); translate each\n"
            f"  tag, keep the ' | ' separators.\n"
            f"- For keys ending in '.kt' (key takeaways), values are triple-pipe-separated\n"
            f"  ('point1 ||| point2 ||| point3'); translate each point, keep ' ||| ' separators.\n"
            f"- Tone: formal news / analyst, professional.\n"
            f"- Output ONLY a valid JSON object. No prose, no markdown fences.\n\n"
            f"Input:\n{_json.dumps(batch_payload, ensure_ascii=False)}\n\n"
            f"Output:"
        )
        # Retry up to MAX_ATTEMPTS times: Versa 504s and the occasional
        # malformed-JSON response are transient. One failed call must NOT
        # discard the whole digest's translation, so we keep trying with
        # exponential backoff before giving up on this single batch.
        MAX_ATTEMPTS = 10
        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=8192,
                    messages=[{"role": "user", "content": prompt}],
                )
                t = resp.content[0].text.strip()
                if t.startswith("```"):
                    nl_idx = t.find("\n")
                    if nl_idx != -1:
                        t = t[nl_idx + 1 :]
                    if t.endswith("```"):
                        t = t[:-3]
                    t = t.strip()
                return _json.loads(t)
            except Exception as exc:  # noqa: BLE001 — retry on any transient error (504, JSON parse, etc.)
                last_exc = exc
                if attempt < MAX_ATTEMPTS:
                    logger.warning(
                        "translate batch attempt %d/%d failed (%s); retrying",
                        attempt, MAX_ATTEMPTS, type(exc).__name__,
                    )
                    time.sleep(min(2 ** (attempt - 1), 30))
        # All attempts exhausted — propagate so the caller falls back to English.
        assert last_exc is not None
        raise last_exc

    items = digest_data.get("items", [])
    n_items = len(items)

    if n_items == 0 and not digest_data.get("tldr") and not digest_data.get("profile_description"):
        return copy.deepcopy(digest_data)

    client = make_client(config)
    model = config.get("anthropic", {}).get("model", "us.anthropic.claude-opus-4-6-v1")

    # One item per LLM request — the smallest possible payload, so each call
    # clears UCSF Versa's 504 timeout window with maximum margin. Combined with
    # the per-batch retry (up to 10 attempts) in _call_translate_llm, a single
    # transient 504 no longer discards the whole digest's translation.
    BATCH_SIZE = 1
    translated: dict[str, str] = {}

    # --- Top-level fields, translated on their own (NOT bundled with item 0) ---
    # The weekly tldr is 7 daily tldrs joined by "\n\n" and can run to ~17k
    # chars. Translating that in one call generates ~17k chars of output and
    # blows past Versa's gateway timeout (deterministic 504, even alone). So we
    # split the tldr on its paragraph boundaries into <=CHUNK_CHARS pieces and
    # translate each piece in its own retryable call, then rejoin with "\n\n".
    if digest_data.get("profile_description"):
        translated.update(_call_translate_llm({"__desc": digest_data["profile_description"]}))
    tldr = digest_data.get("tldr")
    if tldr:
        CHUNK_CHARS = 2000
        paras = tldr.split("\n\n")
        groups: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for para in paras:
            if cur and cur_len + len(para) > CHUNK_CHARS:
                groups.append("\n\n".join(cur))
                cur, cur_len = [], 0
            cur.append(para)
            cur_len += len(para)
        if cur:
            groups.append("\n\n".join(cur))
        out_chunks: list[str] = []
        for gi, g in enumerate(groups):
            key = f"__tldrchunk{gi}"
            r = _call_translate_llm({key: g})
            out_chunks.append(r.get(key, g))
        translated["__tldr"] = "\n\n".join(out_chunks)

    # --- Items, one per LLM call ---
    for batch_start in range(0, n_items, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, n_items)
        payload = _build_batch_payload(range(batch_start, batch_end), include_top_level=False)
        if not payload:
            continue
        translated.update(_call_translate_llm(payload))

    result = copy.deepcopy(digest_data)
    if "__tldr" in translated:
        result["tldr"] = translated["__tldr"]
    if "__desc" in translated:
        result["profile_description"] = translated["__desc"]
    for i, item in enumerate(result.get("items", [])):
        for field in ("title", "headline", "relevance"):
            k = f"i{i}.{field}"
            if k in translated:
                item[field] = translated[k]
        tag_k = f"i{i}.tags"
        if tag_k in translated:
            item["tags"] = [t.strip() for t in translated[tag_k].split("|") if t.strip()]
        kt_k = f"i{i}.kt"
        if kt_k in translated:
            item["key_takeaways"] = [s.strip() for s in translated[kt_k].split("|||") if s.strip()]
    return result


def publish(
    digest_data: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> None:
    """Send the digest as an HTML email via the Resend API.

    Two cohorts derived from config[cadence]:
      - daily_cohort (addresses in cadence.daily_only): receive today's digest
        on every run.
      - weekly_cohort (everyone else): receive a *past-week collated* digest
        on cadence.weekly_day, built from the past 7 JSON sidecars so they see
        everything the daily-cohort saw during the week.

    Skips silently (with a log message) when recipients / API key / items are
    missing. HTTP errors are logged but never raised.
    """
    items = digest_data.get("items", [])
    email_cfg = profile.get("outputs", {}).get("email", {})
    recipients: list[str] = email_cfg.get("recipients", [])

    if not recipients:
        logger.info("Email output skipped — no recipients configured")
        return

    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        logger.info("Email output skipped — RESEND_API_KEY not set")
        return

    from_addr = email_cfg.get("from", "autofeeder <digest@autofeeder.dev>")
    profile_name = digest_data.get("profile_name", "unknown")
    daily_cohort, weekly_cohort, is_weekly_day = _split_cohorts(recipients, config)

    # Helper: pick bilingual or English-only rendering based on profile config.
    # `translate_to` is the new key; `weekly_translate_to` kept for back-compat.
    translate_to = email_cfg.get("translate_to") or email_cfg.get("weekly_translate_to")
    lang_label_native = (
        email_cfg.get("translate_label")
        or email_cfg.get("weekly_translate_label")
        or "繁體中文"
    )

    def _render(payload: dict[str, Any], subject_base: str) -> tuple[str, str]:
        """Return (subject, html_body). Bilingual if translate_to set; falls back
        to English-only on translation failure."""
        if not translate_to:
            return subject_base, _build_html(payload)
        # Translate only the items that'll actually render (avoids burning LLM
        # budget on items _build_html_inner would cap off).
        for_xlate = {**payload, "items": payload.get("items", [])[:_MAX_ITEMS]}
        try:
            translated = _translate_digest_data(for_xlate, translate_to, config)
            html = _build_html_bilingual(payload, translated, lang_label_native=lang_label_native)
            return f"{subject_base} (中英)", html
        except Exception:
            logger.exception("Translation to %s failed; falling back to English-only", translate_to)
            return subject_base, _build_html(payload)

    # --- Daily cohort: today's digest (skip when there are no items) ---
    if daily_cohort and items:
        subj_base = f"autofeeder: {profile_name} digest — {digest_data.get('date', 'unknown')}"
        subject, html_body = _render(digest_data, subj_base)
        _send_one(
            api_key=api_key, from_addr=from_addr, recipients=daily_cohort,
            subject=subject, html_body=html_body,
        )
    elif daily_cohort and not items:
        logger.info("Daily cohort skipped — no items in today's digest")

    # --- Weekly past-week collated digest on weekly_day ---
    # Goes to daily_cohort AND weekly_cohort, deduplicated. Daily-cohort
    # addresses see it so they know what the weekly-cohort recipients receive
    # (e.g. Jin gets the same Monday retrospective that goes out to the lab).
    if not is_weekly_day:
        if weekly_cohort:
            logger.info(
                "Weekly cohort (%d recipient(s)) skipped — not weekly_day yet",
                len(weekly_cohort),
            )
        return
    output_dir = Path(config.get("output", {}).get("dir", "output"))
    weekly = _collate_week_digest(profile_name, dt.date.today(), output_dir, days=7)
    if not weekly or not weekly["items"]:
        logger.info(
            "Weekly send skipped — no items found in past 7 daily sidecars for %s",
            profile_name,
        )
        return
    # Deduplicate while preserving order. dict.fromkeys is idiomatic for this.
    weekly_recipients = list(dict.fromkeys(daily_cohort + weekly_cohort))
    if not weekly_recipients:
        return
    subj_base = f"autofeeder: {profile_name} weekly — {weekly['date']}"
    subject, html_body = _render(weekly, subj_base)
    _send_one(
        api_key=api_key, from_addr=from_addr, recipients=weekly_recipients,
        subject=subject, html_body=html_body,
    )
