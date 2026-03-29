"""Tests for config.py — loading, env overrides, $VAR resolution, merging."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from config import (
    DEFAULTS,
    _apply_env_overrides,
    _resolve_env_refs,
    load_config,
    merge_config,
)


class TestLoadConfig:
    """Test loading config.toml with various scenarios."""

    def test_load_existing_config_toml(self):
        """Loading the real config.toml should return a dict with expected sections."""
        cfg = load_config(
            path=Path(__file__).resolve().parent.parent / "config.toml",
            dotenv_path=None,
        )
        assert "general" in cfg
        assert "fetch" in cfg
        assert "triage" in cfg
        assert isinstance(cfg["fetch"]["max_items_per_feed"], int)

    def test_missing_config_falls_back_to_defaults(self):
        """A non-existent config.toml should produce a config equal to DEFAULTS."""
        cfg = load_config(path="/tmp/nonexistent_autofeeder_config.toml", dotenv_path=None)
        assert cfg["general"]["backend"] == DEFAULTS["general"]["backend"]
        assert cfg["fetch"]["lookback_days"] == DEFAULTS["fetch"]["lookback_days"]
        assert cfg["output"]["min_score"] == DEFAULTS["output"]["min_score"]

    def test_env_var_overrides(self):
        """Environment variables in ENV_MAP should override config values."""
        with patch.dict(os.environ, {"MAX_ITEMS_PER_FEED": "99", "MIN_SCORE": "0.42"}):
            cfg = load_config(path="/tmp/nonexistent_autofeeder_config.toml", dotenv_path=None)
        assert cfg["fetch"]["max_items_per_feed"] == 99
        assert cfg["output"]["min_score"] == pytest.approx(0.42)

    def test_dollar_var_resolution(self):
        """String values starting with $ should be resolved from the environment."""
        cfg: dict[str, Any] = {
            "cross_profile": {"slack_webhook": "$MY_WEBHOOK_URL"},
        }
        with patch.dict(os.environ, {"MY_WEBHOOK_URL": "https://hooks.slack.com/test"}):
            result = _resolve_env_refs(cfg)
        assert result["cross_profile"]["slack_webhook"] == "https://hooks.slack.com/test"

    def test_dollar_var_unset_resolves_empty(self):
        """$VAR references for unset env vars should resolve to empty string."""
        cfg: dict[str, Any] = {
            "outputs": {"webhook": "$TOTALLY_UNSET_VAR_XYZ"},
        }
        env_backup = os.environ.pop("TOTALLY_UNSET_VAR_XYZ", None)
        try:
            result = _resolve_env_refs(cfg)
            assert result["outputs"]["webhook"] == ""
        finally:
            if env_backup is not None:
                os.environ["TOTALLY_UNSET_VAR_XYZ"] = env_backup

    def test_nested_dollar_var_resolution(self):
        """$VAR in doubly-nested dicts (e.g. [outputs.slack]) should resolve."""
        cfg: dict[str, Any] = {
            "outputs": {
                "slack": {"webhook": "$NESTED_HOOK"},
            },
        }
        with patch.dict(os.environ, {"NESTED_HOOK": "https://nested.hook"}):
            result = _resolve_env_refs(cfg)
        assert result["outputs"]["slack"]["webhook"] == "https://nested.hook"


class TestMergeConfig:
    """Test merge_config with profile overrides."""

    def test_flat_key_override(self, sample_config: dict[str, Any]):
        """Flat keys like lookback_days should map to their nested location."""
        merged = merge_config(sample_config, {"lookback_days": 3})
        assert merged["fetch"]["lookback_days"] == 3
        # Original should not be mutated
        assert sample_config["fetch"]["lookback_days"] == 7

    def test_nested_dict_override(self, sample_config: dict[str, Any]):
        """Nested dict overrides should merge one level deeper."""
        merged = merge_config(sample_config, {"output": {"min_score": 0.8}})
        assert merged["output"]["min_score"] == 0.8
        # Other output keys preserved
        assert merged["output"]["max_returned"] == 40

    def test_unknown_key_stored_at_top_level(self, sample_config: dict[str, Any]):
        """Unknown override keys should be stored at the top level."""
        merged = merge_config(sample_config, {"custom_thing": "hello"})
        assert merged["custom_thing"] == "hello"

    def test_multiple_flat_overrides(self, sample_config: dict[str, Any]):
        """Multiple flat overrides should all apply."""
        merged = merge_config(sample_config, {
            "lookback_days": 14,
            "min_score": 0.9,
            "batch_size": 100,
        })
        assert merged["fetch"]["lookback_days"] == 14
        assert merged["output"]["min_score"] == 0.9
        assert merged["triage"]["batch_size"] == 100
