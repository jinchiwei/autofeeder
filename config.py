"""Configuration loader for autofeeder.

Reads config.toml (stdlib tomllib), merges with environment variable overrides,
and resolves $VAR-style secret references. Falls back to hardcoded defaults if
config.toml is missing.
"""

from __future__ import annotations

import copy
import logging
import os
import tomllib
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger("autofeeder")

# ---------------------------------------------------------------------------
# Hardcoded defaults — used when config.toml is absent
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    "general": {
        "backend": "anthropic",
        "frequency": "weekly",
        "log_level": "INFO",
    },
    "fetch": {
        "max_items_per_feed": 50,
        "max_total_items": 400,
        "lookback_days": 7,
        "concurrency": 10,
    },
    "triage": {
        "batch_size": 50,
        "prefilter_keep_top": 200,
    },
    "summarize": {
        "enabled": True,
        "max_content_chars": 15000,
        "archive_ph_enabled": True,
        "unpaywall_email": "",
    },
    "output": {
        "dir": "output",
        "min_score": 0.65,
        "max_returned": 40,
    },
    "anthropic": {
        "model": "claude-opus-4-6",
        "triage_model": "claude-sonnet-4-6",
        "timeout": 300,
    },
    "openai": {
        "model": "gpt-4o",
        "timeout": 300,
    },
    "local": {
        "base_url": "http://localhost:1234/v1",
        "model": "qwen2.5-72b",
        "structured_output": False,
    },
    "ledger": {
        "enabled": True,
        "path": "seen.json",
        "prune_after_days": 90,
    },
    "sync": {
        "vault_path": "",
        "subfolder": "autofeeder",
    },
}

# ---------------------------------------------------------------------------
# Env-var → config-key mapping
# ---------------------------------------------------------------------------

# Each entry: (ENV_VAR, config_section, config_key, type_cast)
ENV_MAP: list[tuple[str, str, str, type]] = [
    ("AUTOFEEDER_BACKEND", "general", "backend", str),
    ("MAX_ITEMS_PER_FEED", "fetch", "max_items_per_feed", int),
    ("MAX_TOTAL_ITEMS", "fetch", "max_total_items", int),
    ("LOOKBACK_DAYS", "fetch", "lookback_days", int),
    ("BATCH_SIZE", "triage", "batch_size", int),
    ("PREFILTER_KEEP_TOP", "triage", "prefilter_keep_top", int),
    ("MIN_SCORE", "output", "min_score", float),
    ("MAX_RETURNED", "output", "max_returned", int),
    ("LOG_LEVEL", "general", "log_level", str),
    ("ANTHROPIC_MODEL", "anthropic", "model", str),
    ("ANTHROPIC_TRIAGE_MODEL", "anthropic", "triage_model", str),
    ("OPENAI_MODEL", "openai", "model", str),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_copy(d: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of a nested dict."""
    return copy.deepcopy(d)


def _resolve_env_refs(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve string values that start with ``$`` from environment variables.

    For example, if a TOML value is ``"$SLACK_WEBHOOK_URL"``, it will be
    replaced with ``os.environ.get("SLACK_WEBHOOK_URL", "")``.  Only top-level
    string values within each section are resolved (one level of nesting is
    sufficient for the current schema).
    """
    for section_key, section_val in cfg.items():
        if isinstance(section_val, dict):
            for key, val in section_val.items():
                if isinstance(val, str) and val.startswith("$"):
                    env_name = val[1:]
                    resolved = os.environ.get(env_name, "")
                    cfg[section_key][key] = resolved
                    if resolved:
                        logger.debug("Resolved $%s for [%s].%s", env_name, section_key, key)
                    else:
                        logger.debug(
                            "$%s referenced in [%s].%s but not set in environment",
                            env_name, section_key, key,
                        )
                # Handle deeper nesting (e.g., [outputs.slack])
                elif isinstance(val, dict):
                    for sub_key, sub_val in val.items():
                        if isinstance(sub_val, str) and sub_val.startswith("$"):
                            env_name = sub_val[1:]
                            resolved = os.environ.get(env_name, "")
                            cfg[section_key][key][sub_key] = resolved
                            if resolved:
                                logger.debug(
                                    "Resolved $%s for [%s.%s].%s",
                                    env_name, section_key, key, sub_key,
                                )
    return cfg


def _apply_env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    """Override config values with environment variables per ``ENV_MAP``."""
    for env_var, section, key, cast in ENV_MAP:
        raw = os.environ.get(env_var)
        if raw is not None:
            try:
                cfg.setdefault(section, {})[key] = cast(raw)
                logger.debug("Env override: %s -> [%s].%s = %r", env_var, section, key, cfg[section][key])
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Ignoring invalid env override %s=%r: %s", env_var, raw, exc
                )
    return cfg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def merge_config(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *overrides* into a copy of *base*.

    Profile-level overrides use a flat dict (e.g. ``{"lookback_days": 3,
    "min_score": 0.8}``).  This function maps known flat keys to their nested
    locations, and also supports nested dicts for arbitrary overrides.

    Returns a new dict — *base* is not mutated.
    """
    result = _deep_copy(base)

    # Flat-key → (section, key) mapping for profile overrides
    flat_map: dict[str, tuple[str, str]] = {
        "lookback_days": ("fetch", "lookback_days"),
        "max_items_per_feed": ("fetch", "max_items_per_feed"),
        "max_total_items": ("fetch", "max_total_items"),
        "batch_size": ("triage", "batch_size"),
        "prefilter_keep_top": ("triage", "prefilter_keep_top"),
        "min_score": ("output", "min_score"),
        "max_returned": ("output", "max_returned"),
        "backend": ("general", "backend"),
        "log_level": ("general", "log_level"),
    }

    for key, value in overrides.items():
        if key in flat_map:
            section, skey = flat_map[key]
            result.setdefault(section, {})[skey] = value
        elif isinstance(value, dict) and key in result and isinstance(result[key], dict):
            # Nested override — merge one level deeper
            for sub_key, sub_val in value.items():
                result[key][sub_key] = sub_val
        else:
            # Unknown key — store at top level
            result[key] = value

    return result


def load_config(
    path: str | Path = "config.toml",
    *,
    dotenv_path: str | Path | None = ".env",
) -> dict[str, Any]:
    """Load configuration with the following precedence (highest wins):

    1. Environment variables (via ``ENV_MAP``)
    2. ``.env`` file (loaded by python-dotenv, does **not** overwrite existing env vars)
    3. ``config.toml``
    4. Hardcoded ``DEFAULTS``

    Args:
        path: Path to the TOML config file.
        dotenv_path: Path to ``.env`` file.  Set to ``None`` to skip.

    Returns:
        Nested configuration dict.
    """
    # Load .env (existing env vars are NOT overwritten)
    if dotenv_path is not None:
        env_file = Path(dotenv_path)
        if env_file.is_file():
            load_dotenv(env_file, override=False)
            logger.debug("Loaded .env from %s", env_file.resolve())

    # Start with defaults
    cfg = _deep_copy(DEFAULTS)

    # Layer config.toml on top
    config_path = Path(path)
    if config_path.is_file():
        with open(config_path, "rb") as f:
            toml_data = tomllib.load(f)
        # Deep-merge TOML onto defaults
        for section, values in toml_data.items():
            if isinstance(values, dict) and section in cfg and isinstance(cfg[section], dict):
                cfg[section].update(values)
            else:
                cfg[section] = values
        logger.debug("Loaded config from %s", config_path.resolve())
    else:
        logger.info("No config.toml found at %s — using defaults", config_path)

    # Resolve $VAR references
    cfg = _resolve_env_refs(cfg)

    # Apply env-var overrides (highest priority)
    cfg = _apply_env_overrides(cfg)

    return cfg
