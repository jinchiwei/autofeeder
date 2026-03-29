"""OpenAI (and local-LLM-compatible) backend for autofeeder triage and summarization."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
import openai
from openai import OpenAI

from ._shared import (
    SUMMARY_SCHEMA,
    TRIAGE_SCHEMA,
    build_summary_prompt,
    build_tldr_prompt,
    build_triage_prompt,
    parse_structured_response,
    parse_summary_response,
    retry_with_backoff,
)

logger = logging.getLogger("autofeeder")

# Exceptions worth retrying
_RETRY_EXCEPTIONS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
)


def make_client(
    config: dict[str, Any],
    backend_type: str = "openai",
) -> OpenAI:
    """Create an OpenAI-compatible client.

    For ``backend_type="local"``, the client uses ``config["local"]["base_url"]``
    and skips API-key validation (sets a dummy key so the SDK doesn't complain).

    Args:
        config: Application config dict.
        backend_type: ``"openai"`` or ``"local"``.

    Returns:
        An ``openai.OpenAI`` client instance.

    Raises:
        RuntimeError: If the OpenAI API key is missing or malformed (openai mode only).
    """
    backend_cfg = config.get(backend_type, {})
    timeout_secs = backend_cfg.get("timeout", 300)

    http_client = httpx.Client(
        timeout=httpx.Timeout(timeout_secs, connect=30.0),
    )

    if backend_type == "local":
        base_url = backend_cfg.get("base_url", "http://localhost:1234/v1")
        logger.info("Creating local LLM client: base_url=%s", base_url)
        return OpenAI(
            api_key="not-needed",
            base_url=base_url,
            http_client=http_client,
        )

    # Standard or custom-endpoint OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = backend_cfg.get("base_url")

    # Custom endpoint (e.g., UCSF Versa) — use AWS keys or any available key
    if base_url:
        effective_key = api_key or os.environ.get("AWS_ACCESS_KEY_ID", "") or "not-needed"
        logger.info("Creating OpenAI client: base_url=%s", base_url)
        return OpenAI(
            api_key=effective_key,
            base_url=base_url,
            http_client=http_client,
        )

    if not api_key or not api_key.startswith("sk-"):
        raise RuntimeError(
            "OPENAI_API_KEY must be set and start with 'sk-'. "
            "Export it or switch to a different backend."
        )

    return OpenAI(
        api_key=api_key,
        http_client=http_client,
    )


# ---------------------------------------------------------------------------
# Helpers for structured vs. unstructured output
# ---------------------------------------------------------------------------

def _schema_instruction(schema: dict[str, Any]) -> str:
    """Format a JSON schema as an instruction block to append to prompts."""
    schema_body = json.dumps(schema["schema"], indent=2, ensure_ascii=False)
    return (
        "\n\n--- RESPONSE FORMAT ---\n"
        "You MUST respond with a single JSON object matching this schema:\n"
        f"```json\n{schema_body}\n```\n"
        "Return ONLY the JSON — no prose, no markdown fences around the JSON."
    )


def _extract_json_from_text(text: str) -> str:
    """Find the first top-level JSON object in free-form text."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in model response: {text[:300]}...")
    return text[start : end + 1]


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------

async def call_triage(
    client: OpenAI,
    interests: dict[str, Any],
    items: list[dict[str, Any]],
    config: dict[str, Any],
    backend_type: str = "openai",
) -> dict[str, Any]:
    """Score and rank items using an OpenAI-compatible model.

    Args:
        client: OpenAI client from :func:`make_client`.
        interests: User interests dict (keywords + narrative).
        items: Feed items to triage.
        config: Full application config.
        backend_type: ``"openai"`` or ``"local"``.

    Returns:
        Parsed triage response dict with ``ranked`` list.
    """
    prompt, lean_items = build_triage_prompt(interests, items)

    backend_cfg = config.get(backend_type, {})
    model = backend_cfg.get("model", "gpt-4o")
    use_structured = backend_cfg.get("structured_output", True)

    logger.info(
        "OpenAI triage: backend=%s, model=%s, items=%d, structured=%s",
        backend_type,
        model,
        len(lean_items),
        use_structured,
    )

    async def _call() -> dict[str, Any]:
        if use_structured:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_schema", "json_schema": TRIAGE_SCHEMA},
            )
            text = response.choices[0].message.content or ""
        else:
            full_prompt = prompt + _schema_instruction(TRIAGE_SCHEMA)
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
            )
            raw = response.choices[0].message.content or ""
            text = _extract_json_from_text(raw)

        logger.debug("OpenAI triage raw response: %s", text[:500])
        return parse_structured_response(text)

    return await retry_with_backoff(
        _call, max_attempts=6, retry_exceptions=_RETRY_EXCEPTIONS
    )


# ---------------------------------------------------------------------------
# Summarize
# ---------------------------------------------------------------------------

async def call_summarize(
    client: OpenAI,
    interests: dict[str, Any],
    item: dict[str, Any],
    config: dict[str, Any],
    backend_type: str = "openai",
) -> dict[str, Any]:
    """Summarize a single item using an OpenAI-compatible model.

    Args:
        client: OpenAI client from :func:`make_client`.
        interests: User interests dict (narrative).
        item: Item dict with title, source, full_text.
        config: Full application config.
        backend_type: ``"openai"`` or ``"local"``.

    Returns:
        Parsed summary response dict with headline, key_takeaways, etc.
    """
    prompt = build_summary_prompt(interests, item)

    backend_cfg = config.get(backend_type, {})
    model = backend_cfg.get("model", "gpt-4o")
    use_structured = backend_cfg.get("structured_output", True)

    logger.info(
        "OpenAI summarize: backend=%s, model=%s, title=%s",
        backend_type,
        model,
        item.get("title", "")[:80],
    )

    async def _call() -> dict[str, Any]:
        if use_structured:
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_schema", "json_schema": SUMMARY_SCHEMA},
            )
            text = response.choices[0].message.content or ""
        else:
            full_prompt = prompt + _schema_instruction(SUMMARY_SCHEMA)
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=model,
                messages=[{"role": "user", "content": full_prompt}],
            )
            raw = response.choices[0].message.content or ""
            text = _extract_json_from_text(raw)

        logger.debug("OpenAI summary raw response: %s", text[:500])
        return parse_summary_response(text)

    return await retry_with_backoff(
        _call, max_attempts=6, retry_exceptions=_RETRY_EXCEPTIONS
    )


async def call_tldr(
    client: OpenAI,
    interests: dict[str, Any],
    top_items: list[dict[str, Any]],
    config: dict[str, Any],
    backend_type: str = "openai",
) -> str:
    """Generate a TL;DR overview of the top papers.

    Returns:
        Plain text overview (~12 sentences).
    """
    prompt = build_tldr_prompt(interests, top_items)

    backend_cfg = config.get(backend_type, {})
    model = backend_cfg.get("model", "gpt-4o")

    logger.info("OpenAI TL;DR: backend=%s, model=%s, items=%d", backend_type, model, len(top_items))

    async def _call() -> str:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    return await retry_with_backoff(
        _call, max_attempts=6, retry_exceptions=_RETRY_EXCEPTIONS
    )
