"""Backend registry for autofeeder LLM backends.

Supports Anthropic (Claude), OpenAI, and local LLM servers.
Auto-detects the correct backend from environment variables unless
``AUTOFEEDER_BACKEND`` is explicitly set.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("autofeeder")


# ---------------------------------------------------------------------------
# Backend factories — each returns {"triage_fn", "summarize_fn"}
# ---------------------------------------------------------------------------

def _make_anthropic(config: dict[str, Any]) -> dict[str, Any]:
    """Factory for the Anthropic backend."""
    from .anthropic_backend import call_summarize, call_tldr, call_triage, make_client

    client = make_client(config)

    async def triage_fn(interests: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        return await call_triage(client, interests, items, config)

    async def summarize_fn(interests: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        return await call_summarize(client, interests, item, config)

    async def tldr_fn(interests: dict[str, Any], top_items: list[dict[str, Any]]) -> str:
        return await call_tldr(client, interests, top_items, config)

    return {"triage_fn": triage_fn, "summarize_fn": summarize_fn, "tldr_fn": tldr_fn}


def _make_openai_compat(config: dict[str, Any], backend_type: str) -> dict[str, Any]:
    """Shared factory for OpenAI-compatible backends (openai, local)."""
    from .openai_backend import call_summarize, call_tldr, call_triage, make_client

    client = make_client(config, backend_type=backend_type)

    async def triage_fn(interests: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
        return await call_triage(client, interests, items, config, backend_type=backend_type)

    async def summarize_fn(interests: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        return await call_summarize(client, interests, item, config, backend_type=backend_type)

    async def tldr_fn(interests: dict[str, Any], top_items: list[dict[str, Any]]) -> str:
        return await call_tldr(client, interests, top_items, config, backend_type=backend_type)

    return {"triage_fn": triage_fn, "summarize_fn": summarize_fn, "tldr_fn": tldr_fn}


def _make_openai(config: dict[str, Any]) -> dict[str, Any]:
    """Factory for the OpenAI backend."""
    return _make_openai_compat(config, backend_type="openai")


def _make_local(config: dict[str, Any]) -> dict[str, Any]:
    """Factory for local LLM backends (uses OpenAI-compatible API)."""
    return _make_openai_compat(config, backend_type="local")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, Any] = {
    "anthropic": _make_anthropic,
    "openai": _make_openai,
    "local": _make_local,
}


def get_backend(config: dict[str, Any]) -> dict[str, Any]:
    """Return the active backend as ``{"triage_fn": ..., "summarize_fn": ...}``.

    Detection order:
        1. ``AUTOFEEDER_BACKEND`` environment variable (explicit override).
        2. ``ANTHROPIC_API_KEY`` is set -> ``"anthropic"``.
        3. ``OPENAI_API_KEY`` is set -> ``"openai"``.
        4. Raise an error.

    Each returned function has the signature:
        - ``triage_fn(interests, items) -> dict``
        - ``summarize_fn(interests, item) -> dict``

    Args:
        config: Full application config dict.

    Returns:
        Dict with ``triage_fn`` and ``summarize_fn`` async callables.

    Raises:
        RuntimeError: If no backend can be determined.
    """
    name = os.environ.get("AUTOFEEDER_BACKEND", "").strip().lower()

    if not name:
        # Auto-detect from available API keys
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AWS_ACCESS_KEY_ID"):
            name = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            name = "openai"
        else:
            raise RuntimeError(
                "No LLM backend configured. Set AUTOFEEDER_BACKEND, "
                "ANTHROPIC_API_KEY, AWS_ACCESS_KEY_ID, or OPENAI_API_KEY."
            )

    if name not in _BACKENDS:
        available = ", ".join(sorted(_BACKENDS))
        raise RuntimeError(
            f"Unknown backend '{name}'. Available: {available}"
        )

    logger.info("Initializing LLM backend: %s", name)
    factory = _BACKENDS[name]
    return factory(config)
