"""Tests for discover.py — prompt loading, profile saving."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from discover import _load_discover_prompt, save_discovered_profile


class TestLoadDiscoverPrompt:
    """Test discover prompt template loading and filling."""

    def test_fills_topic_placeholder(self):
        """The discover prompt should have {{TOPIC}} replaced with the topic."""
        prompt = _load_discover_prompt("neural oscillations in sleep")
        assert "{{TOPIC}}" not in prompt
        assert "neural oscillations in sleep" in prompt

    def test_contains_instructions(self):
        """The prompt should contain the standard instructions."""
        prompt = _load_discover_prompt("any topic")
        assert "RSS" in prompt
        assert "TOML" in prompt or "toml" in prompt


class TestSaveDiscoveredProfile:
    """Test saving LLM-generated profile content to a TOML file."""

    def test_creates_file(self, tmp_path: Path):
        """save_discovered_profile should create a .toml file."""
        llm_output = '[feeds]\nlist = ["Test Feed | https://example.com/rss"]'
        result_path = save_discovered_profile(
            topic="neural oscillations",
            llm_output=llm_output,
            profile_name="neuro_discover",
            output_dir=str(tmp_path),
        )

        assert result_path.exists()
        assert result_path.name == "neuro_discover.toml"

        content = result_path.read_text()
        assert "Auto-generated profile for:" in content
        assert "neural oscillations" in content
        assert 'name = "neuro_discover"' in content
        assert "Test Feed" in content

    def test_escapes_quotes_in_topic(self, tmp_path: Path):
        """Double quotes in the topic should be escaped."""
        llm_output = "[feeds]\nlist = []"
        result_path = save_discovered_profile(
            topic='topic with "quotes" inside',
            llm_output=llm_output,
            profile_name="quoted",
            output_dir=str(tmp_path),
        )

        content = result_path.read_text()
        assert '\\"quotes\\"' in content

    def test_extracts_toml_from_code_fences(self, tmp_path: Path):
        """If LLM wraps output in ```toml ... ```, the content should be extracted."""
        llm_output = (
            "Here is your profile:\n"
            "```toml\n"
            "[feeds]\n"
            'list = ["Feed A | https://example.com/a"]\n'
            "```\n"
            "Let me know if you need changes."
        )
        result_path = save_discovered_profile(
            topic="test topic",
            llm_output=llm_output,
            profile_name="fenced",
            output_dir=str(tmp_path),
        )

        content = result_path.read_text()
        assert "Feed A" in content
        # The code fences themselves should be stripped
        assert "```" not in content

    def test_creates_output_directory(self, tmp_path: Path):
        """save_discovered_profile should create the output directory if missing."""
        nested_dir = tmp_path / "a" / "b" / "c"
        assert not nested_dir.exists()

        save_discovered_profile(
            topic="test",
            llm_output="[feeds]\nlist = []",
            profile_name="deep",
            output_dir=str(nested_dir),
        )

        assert nested_dir.exists()
        assert (nested_dir / "deep.toml").exists()
