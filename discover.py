"""Feed discovery for autofeeder.

Given a topic description, uses an LLM to generate a starter list of RSS feeds,
keywords, and narrative — outputting a ready-to-use profile TOML.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("autofeeder")

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_discover_prompt(topic: str) -> str:
    """Load and fill the discover prompt template."""
    template_path = _PROMPTS_DIR / "discover.txt"
    template = template_path.read_text(encoding="utf-8")
    return template.replace("{{TOPIC}}", topic)


async def _call_anthropic(prompt: str, config: dict[str, Any]) -> str:
    """Call Anthropic API for discovery. Supports direct API and Bedrock."""
    import anthropic

    timeout = config.get("anthropic", {}).get("timeout", 300)
    model = config.get("anthropic", {}).get("model", "claude-opus-4-6")

    aws_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    if aws_key:
        from anthropic import AnthropicBedrock
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
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
        client = AnthropicBedrock(**kwargs)
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RuntimeError("No Anthropic credentials found")
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    logger.info("Discover: using Anthropic model=%s", model)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text  # type: ignore[union-attr]


async def _call_openai(prompt: str, config: dict[str, Any], backend_type: str = "openai") -> str:
    """Call OpenAI-compatible API for discovery."""
    import httpx
    from openai import OpenAI

    backend_cfg = config.get(backend_type, {})
    model = backend_cfg.get("model", "gpt-4o")

    if backend_type == "local":
        base_url = backend_cfg.get("base_url", "http://localhost:1234/v1")
        client = OpenAI(
            api_key="not-needed",
            base_url=base_url,
            http_client=httpx.Client(timeout=httpx.Timeout(300, connect=30.0)),
        )
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        client = OpenAI(
            api_key=api_key,
            http_client=httpx.Client(timeout=httpx.Timeout(300, connect=30.0)),
        )

    logger.info("Discover: using OpenAI-compatible model=%s", model)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


async def discover_feeds(topic: str, config: dict[str, Any]) -> str:
    """Generate a starter profile for a topic using an LLM.

    Args:
        topic: Free-text description of the topic to track.
        config: Application config dict.

    Returns:
        LLM-generated text containing TOML-ready feed list, keywords,
        and narrative.
    """
    prompt = _load_discover_prompt(topic)

    # Use the configured backend
    backend = (
        config.get("general", {}).get("backend", "")
        or os.environ.get("AUTOFEEDER_BACKEND", "")
    ).strip().lower()
    if not backend:
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("AWS_ACCESS_KEY_ID"):
            backend = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            backend = "openai"
        else:
            raise RuntimeError(
                "No LLM backend configured. Set ANTHROPIC_API_KEY, AWS_ACCESS_KEY_ID, or OPENAI_API_KEY."
            )

    if backend == "anthropic":
        return await _call_anthropic(prompt, config)
    elif backend in ("openai", "local"):
        return await _call_openai(prompt, config, backend_type=backend)
    else:
        raise RuntimeError(f"Unknown backend for discovery: {backend}")


def discover_feeds_sync(topic: str, config: dict[str, Any]) -> str:
    """Sync wrapper for discover_feeds."""
    return asyncio.run(discover_feeds(topic, config))


def save_discovered_profile(
    topic: str,
    llm_output: str,
    profile_name: str,
    output_dir: str = "profiles",
) -> Path:
    """Save LLM discovery output as a new profile TOML file.

    The LLM output is saved with a header comment. The user is expected
    to review and edit before using.

    Args:
        topic: Original topic description.
        llm_output: Raw LLM response with TOML content.
        profile_name: Name for the profile file (without .toml).
        output_dir: Directory to save the profile.

    Returns:
        Path to the created file.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{profile_name}.toml"

    safe_topic = topic.replace('\\', '\\\\').replace('"', '\\"')
    safe_name = profile_name.replace('\\', '\\\\').replace('"', '\\"')
    header = (
        f'# Auto-generated profile for: {safe_topic}\n'
        f'# Review and edit before using!\n'
        f'# Some RSS URLs may need verification.\n\n'
        f'name = "{safe_name}"\n'
        f'description = "Auto-discovered feeds for: {safe_topic}"\n\n'
    )

    # Try to extract just the TOML content if the LLM wrapped it
    content = llm_output
    if "```toml" in content:
        # Extract content between ```toml and ```
        start = content.find("```toml") + 7
        end = content.find("```", start)
        if end > start:
            content = content[start:end].strip()

    full_content = header + content + "\n"
    out_path.write_text(full_content, encoding="utf-8")
    logger.info("Discovered profile written to %s", out_path)
    return out_path
