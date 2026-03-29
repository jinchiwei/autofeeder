"""Shared utilities for autofeeder LLM backends.

Provides JSON schemas, prompt builders, response parsers, and retry logic
shared across Anthropic, OpenAI, and local backends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("autofeeder")

# ---------------------------------------------------------------------------
# JSON Schemas (OpenAI structured-output format)
# ---------------------------------------------------------------------------

TRIAGE_SCHEMA: dict[str, Any] = {
    "name": "triage_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "notes": {
                "type": "string",
                "description": "Optional scratchpad or reasoning notes from the model.",
            },
            "ranked": {
                "type": "array",
                "description": "Items ranked by relevance score, descending.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "link": {"type": "string"},
                        "source": {"type": "string"},
                        "published_utc": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "score": {"type": "number"},
                        "why": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "id",
                        "title",
                        "link",
                        "source",
                        "published_utc",
                        "score",
                        "why",
                        "tags",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["notes", "ranked"],
        "additionalProperties": False,
    },
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "name": "summary_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "One plain-English sentence summarizing the paper.",
            },
            "key_takeaways": {
                "type": "array",
                "description": "2-4 concrete bullet points.",
                "items": {"type": "string"},
            },
            "relevance": {
                "type": "string",
                "description": "Why this matters given the user's interests.",
            },
            "tags": {
                "type": "array",
                "description": "Short categorization labels.",
                "items": {"type": "string"},
            },
        },
        "required": ["headline", "key_takeaways", "relevance", "tags"],
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt_template(path: str | Path) -> str:
    """Read a prompt template file and return its contents as a string."""
    p = Path(path)
    if not p.is_absolute():
        p = _PROMPTS_DIR / p
    return p.read_text(encoding="utf-8")


def build_triage_prompt(
    interests: dict[str, Any],
    items: list[dict[str, Any]],
    summary_max_chars: int = 500,
) -> tuple[str, list[dict[str, Any]]]:
    """Build the triage prompt from template + data.

    Args:
        interests: Dict with ``keywords`` (list[str]) and ``narrative`` (str).
        items: Raw feed items (each must have id, source, title, link,
               published_utc, and optionally summary).
        summary_max_chars: Truncate each item's summary to this length.

    Returns:
        A tuple of (prompt_string, lean_items) where lean_items is the
        list of trimmed item dicts actually sent to the model.
    """
    template = load_prompt_template("triage.txt")

    # Build lean items with truncated summaries
    lean_items: list[dict[str, Any]] = []
    for item in items:
        summary_raw = item.get("summary", "") or ""
        summary_trunc = summary_raw[:summary_max_chars]
        if len(summary_raw) > summary_max_chars:
            summary_trunc += "..."
        lean_items.append(
            {
                "id": item["id"],
                "source": item.get("source", ""),
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "published_utc": item.get("published_utc"),
                "summary": summary_trunc,
            }
        )

    keywords_str = ", ".join(interests.get("keywords", []))
    narrative_str = interests.get("narrative", "")
    items_json = json.dumps(lean_items, indent=2, ensure_ascii=False)

    prompt = (
        template.replace("{{KEYWORDS}}", keywords_str)
        .replace("{{NARRATIVE}}", narrative_str)
        .replace("{{ITEMS}}", items_json)
    )
    return prompt, lean_items


def build_summary_prompt(
    interests: dict[str, Any],
    item: dict[str, Any],
) -> str:
    """Build the summary prompt for a single item.

    Args:
        interests: Dict with ``narrative`` (str).
        item: Must have ``title``, ``source``, and ``full_text``.

    Returns:
        Filled-in prompt string.
    """
    template = load_prompt_template("summarize.txt")
    return (
        template.replace("{{NARRATIVE}}", interests.get("narrative", ""))
        .replace("{{TITLE}}", item.get("title", ""))
        .replace("{{SOURCE}}", item.get("source", ""))
        .replace("{{CONTENT}}", item.get("full_text", ""))
    )


def build_tldr_prompt(
    interests: dict[str, Any],
    top_items: list[dict[str, Any]],
) -> str:
    """Build the TL;DR overview prompt from the top scored+summarised items.

    Args:
        interests: Dict with ``narrative`` (str).
        top_items: Top 5 items, each with title, source, score, headline,
                   key_takeaways, cites_your_work.

    Returns:
        Filled-in prompt string.
    """
    template = load_prompt_template("tldr.txt")

    paper_summaries: list[str] = []
    for item in top_items[:5]:
        parts = [f"Title: {item.get('title', 'Untitled')}"]
        parts.append(f"Source: {item.get('source', 'Unknown')}")
        parts.append(f"Score: {item.get('score', 0):.2f}")
        if item.get("headline"):
            parts.append(f"Headline: {item['headline']}")
        if item.get("key_takeaways"):
            parts.append("Key takeaways: " + "; ".join(item["key_takeaways"]))
        if item.get("cites_your_work"):
            parts.append("** This paper cites your work/methods **")
        paper_summaries.append("\n".join(parts))

    papers_text = "\n\n---\n\n".join(paper_summaries)

    return (
        template.replace("{{NARRATIVE}}", interests.get("narrative", ""))
        .replace("{{PAPERS}}", papers_text)
    )


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def _extract_json(text: str) -> Any:
    """Extract and parse JSON from text, handling markdown code fences."""
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code fences
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Last resort: find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from response text: {text[:200]}...")


def parse_structured_response(text: str) -> dict[str, Any]:
    """Parse a triage response and validate it contains ``ranked``.

    Handles common model variations: the ranked list may be under
    ``ranked``, ``items``, ``results``, ``papers``, or the response
    may be a bare array.

    Args:
        text: Raw model output (JSON string, possibly wrapped in markdown).

    Returns:
        Parsed dict with a ``ranked`` key (normalized).

    Raises:
        ValueError: If parsing fails or no ranked items found.
    """
    data = _extract_json(text)

    # Handle bare array response
    if isinstance(data, list):
        return {"notes": "", "ranked": data}

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    # Try common key names
    if "ranked" in data:
        return data
    for alt_key in ("items", "results", "papers", "articles"):
        if alt_key in data and isinstance(data[alt_key], list):
            data["ranked"] = data.pop(alt_key)
            return data

    # If there's any list value, use it
    for key, val in data.items():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            data["ranked"] = data.pop(key)
            return data

    raise ValueError("Triage response missing 'ranked' key")


def parse_summary_response(text: str) -> dict[str, Any]:
    """Parse a summary response and validate it contains ``headline``.

    Args:
        text: Raw model output (JSON string, possibly wrapped in markdown).

    Returns:
        Parsed dict with at least a ``headline`` key.

    Raises:
        ValueError: If parsing fails or ``headline`` is missing.
    """
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    if "headline" not in data:
        raise ValueError("Summary response missing 'headline' key")
    return data


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

async def retry_with_backoff(
    fn: Any,
    max_attempts: int = 6,
    retry_exceptions: tuple[type[BaseException], ...] = (),
) -> Any:
    """Call an async callable with exponential backoff on specified exceptions.

    Delay = min(2 ** attempt, 60) seconds between retries.

    Args:
        fn: An async callable (no arguments) to invoke.
        max_attempts: Maximum number of attempts before re-raising.
        retry_exceptions: Tuple of exception types that trigger a retry.

    Returns:
        The return value of *fn* on the first successful call.

    Raises:
        The last caught exception if all attempts are exhausted.
    """
    if not retry_exceptions:
        logger.warning("retry_with_backoff called with no retry_exceptions — retries disabled")
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except retry_exceptions as exc:  # type: ignore[misc]
            last_exc = exc
            delay = min(2 ** attempt, 60)
            logger.warning(
                "Attempt %d/%d failed (%s: %s) — retrying in %ds",
                attempt + 1,
                max_attempts,
                type(exc).__name__,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
