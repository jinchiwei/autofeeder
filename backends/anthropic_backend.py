"""Anthropic (Claude) backend for autofeeder triage and summarization."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import anthropic

from ._shared import (
    build_summary_prompt,
    build_tldr_prompt,
    build_triage_prompt,
    parse_structured_response,
    parse_summary_response,
    retry_with_backoff,
)

logger = logging.getLogger("autofeeder")


def _repair_triage_json(text: str) -> dict[str, Any]:
    """Attempt to repair truncated triage JSON.

    Common failure: response truncated mid-array, missing closing `]}`.
    Strategy: try suffix repairs, then delegate normalization to
    ``parse_structured_response``, falling back to regex extraction.
    """
    import re

    # Find the start of JSON
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON found in response")

    fragment = text[start:]

    # Try progressively aggressive suffix repairs to close truncated JSON
    for suffix in ["", "}", "]}", '"]}', '"}]}']:
        try:
            repaired_text = fragment + suffix
            data = json.loads(repaired_text)
            if isinstance(data, dict) or isinstance(data, list):
                # Delegate key-name normalization to the shared parser
                result = parse_structured_response(repaired_text)
                logger.info("JSON repaired with suffix %r (%d ranked items)", suffix, len(result.get("ranked", [])))
                return result
        except (json.JSONDecodeError, ValueError):
            continue

    # Try parse_structured_response directly (handles code fences, key normalization, etc.)
    try:
        result = parse_structured_response(fragment)
        logger.info("Repaired JSON via parse_structured_response (%d ranked items)", len(result.get("ranked", [])))
        return result
    except ValueError:
        pass

    # Last resort: regex extraction of individual item objects
    items = []
    for m in re.finditer(r'\{[^{}]*"id"\s*:\s*"[^"]*"[^{}]*\}', fragment):
        try:
            item = json.loads(m.group())
            if "id" in item and "score" in item:
                items.append(item)
        except json.JSONDecodeError:
            continue

    if items:
        logger.info("Extracted %d items via regex fallback", len(items))
        return {"notes": "", "ranked": items}

    raise ValueError(f"Could not repair JSON: {text[:300]}...")


# Exceptions worth retrying
_RETRY_EXCEPTIONS = (
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
)


def make_client(config: dict[str, Any]) -> anthropic.Anthropic:
    """Create an Anthropic client — supports direct API and AWS Bedrock.

    Detection order:
        1. If ``AWS_ACCESS_KEY_ID`` is set → use AnthropicBedrock
           (supports UCSF Versa and other Bedrock deployments).
        2. If ``ANTHROPIC_API_KEY`` is set → use direct Anthropic API.
        3. Raise an error.

    Bedrock env vars:
        - ``AWS_ACCESS_KEY_ID`` (required)
        - ``AWS_SECRET_ACCESS_KEY`` (required)
        - ``AWS_REGION`` or ``AWS_DEFAULT_REGION`` (default: us-west-2)
        - ``ANTHROPIC_BEDROCK_BASE_URL`` (optional, for custom endpoints like UCSF Versa)
        - ``AWS_SESSION_TOKEN`` (optional, for temporary credentials)

    Args:
        config: Application config dict (``anthropic`` section used for
                timeout if present).

    Returns:
        An Anthropic client instance (either direct or Bedrock).

    Raises:
        RuntimeError: If no credentials are configured.
    """
    timeout = config.get("anthropic", {}).get("timeout", 300)

    # Check for Bedrock credentials first
    aws_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    if aws_key:
        from anthropic import AnthropicBedrock

        region = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-west-2"
        )
        base_url = os.environ.get("ANTHROPIC_BEDROCK_BASE_URL")

        kwargs: dict[str, Any] = {
            "aws_access_key": aws_key,
            "aws_secret_key": os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            "aws_region": region,
            "timeout": timeout,
        }
        if base_url:
            kwargs["base_url"] = base_url
        session_token = os.environ.get("AWS_SESSION_TOKEN")
        if session_token:
            kwargs["aws_session_token"] = session_token

        logger.info("Using Anthropic via Bedrock (region=%s, custom_url=%s)", region, bool(base_url))
        return AnthropicBedrock(**kwargs)  # type: ignore[return-value]

    # Fall back to direct API
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "No Anthropic credentials found. Set either:\n"
            "  - ANTHROPIC_API_KEY (direct API), or\n"
            "  - AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (Bedrock)"
        )

    logger.info("Using Anthropic direct API")
    return anthropic.Anthropic(api_key=api_key, timeout=timeout)


async def call_triage(
    client: anthropic.Anthropic,
    interests: dict[str, Any],
    items: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Score and rank items using Claude.

    Args:
        client: Anthropic client from :func:`make_client`.
        interests: User interests dict (keywords + narrative).
        items: Feed items to triage.
        config: Full application config.

    Returns:
        Parsed triage response dict with ``ranked`` list.
    """
    prompt, lean_items = build_triage_prompt(interests, items)

    anthropic_cfg = config.get("anthropic", {})
    model = anthropic_cfg.get("triage_model", anthropic_cfg.get("model", "claude-sonnet-4-6"))

    logger.info(
        "Anthropic triage: model=%s, items=%d", model, len(lean_items)
    )

    # Append JSON instruction to prompt (Bedrock doesn't support response_format)
    json_instruction = (
        "\n\nIMPORTANT: Return ONLY a valid JSON object. No markdown fences, no commentary, no text before or after the JSON."
    )

    async def _call() -> dict[str, Any]:
        response = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=16384,
            messages=[{"role": "user", "content": prompt + json_instruction}],
        )
        if not response.content:
            raise RuntimeError("Bedrock returned empty content array")
        text = response.content[0].text  # type: ignore[union-attr]
        logger.debug("Anthropic triage raw response (first 1000): %s", text[:1000])
        try:
            return parse_structured_response(text)
        except ValueError as exc:
            logger.warning("JSON parse failed (%s), attempting repair", exc)
            return _repair_triage_json(text)

    return await retry_with_backoff(
        _call, max_attempts=6, retry_exceptions=_RETRY_EXCEPTIONS
    )


async def call_summarize(
    client: anthropic.Anthropic,
    interests: dict[str, Any],
    item: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Summarize a single item using Claude.

    Args:
        client: Anthropic client from :func:`make_client`.
        interests: User interests dict (narrative).
        item: Item dict with title, source, full_text.
        config: Full application config.

    Returns:
        Parsed summary response dict with headline, key_takeaways, etc.
    """
    prompt = build_summary_prompt(interests, item)

    anthropic_cfg = config.get("anthropic", {})
    model = anthropic_cfg.get("model", "claude-opus-4-6")

    logger.info(
        "Anthropic summarize: model=%s, title=%s",
        model,
        item.get("title", "")[:80],
    )

    json_instruction = (
        "\n\nIMPORTANT: Return ONLY a valid JSON object. No markdown fences, no commentary, no text before or after the JSON."
    )

    async def _call() -> dict[str, Any]:
        response = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt + json_instruction}],
        )
        if not response.content:
            raise RuntimeError("Bedrock returned empty content array")
        text = response.content[0].text  # type: ignore[union-attr]
        logger.debug("Anthropic summary raw response: %s", text[:500])
        return parse_summary_response(text)

    return await retry_with_backoff(
        _call, max_attempts=6, retry_exceptions=_RETRY_EXCEPTIONS
    )


async def call_tldr(
    client: anthropic.Anthropic,
    interests: dict[str, Any],
    top_items: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    """Generate a TL;DR overview of the top papers using Claude.

    Returns:
        Plain text overview (~12 sentences).
    """
    prompt = build_tldr_prompt(interests, top_items)

    anthropic_cfg = config.get("anthropic", {})
    model = anthropic_cfg.get("model", "claude-opus-4-6")

    logger.info("Anthropic TL;DR: model=%s, items=%d", model, len(top_items))

    async def _call() -> str:
        response = await asyncio.to_thread(
            client.messages.create,
            model=model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text  # type: ignore[union-attr]

    return await retry_with_backoff(
        _call, max_attempts=6, retry_exceptions=_RETRY_EXCEPTIONS
    )
