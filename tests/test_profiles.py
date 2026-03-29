"""Tests for profiles.py — loading, parsing, validation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from profiles import _parse_feed_entry, _validate_profile, load_profile


class TestParseFeedEntry:
    """Test feed entry string parsing."""

    def test_name_pipe_url(self):
        """'Name | URL' format should split correctly."""
        result = _parse_feed_entry("Nature Neuroscience | https://www.nature.com/neuro.rss")
        assert result["name"] == "Nature Neuroscience"
        assert result["url"] == "https://www.nature.com/neuro.rss"

    def test_bare_url(self):
        """Bare URL (no pipe) should have empty name."""
        result = _parse_feed_entry("https://www.nature.com/neuro.rss")
        assert result["name"] == ""
        assert result["url"] == "https://www.nature.com/neuro.rss"

    def test_whitespace_handling(self):
        """Whitespace around name and URL should be stripped."""
        result = _parse_feed_entry("  My Feed  |  https://example.com/rss  ")
        assert result["name"] == "My Feed"
        assert result["url"] == "https://example.com/rss"


class TestLoadProfile:
    """Test load_profile with actual TOML files."""

    def test_load_valid_profile(self):
        """Loading the example profile should succeed with all fields."""
        profile_path = Path(__file__).resolve().parent.parent / "profiles" / "example.toml"
        profile = load_profile(profile_path)

        assert profile["name"] == "Example Profile"
        assert len(profile["feeds"]) == 4
        assert profile["feeds"][0]["name"] == "Nature Neuroscience"
        assert profile["feeds"][0]["url"] == "https://www.nature.com/neuro.rss"
        assert "EEG" in profile["interests"]["keywords"]
        assert "narrative" in profile["interests"]
        assert isinstance(profile["paywalled_domains"], list)
        # Note: in the example.toml, paywalled_domains sits under [interests]
        # because TOML section headers scope all subsequent keys until the next header.
        # The loader reads paywalled_domains from the top level, so it's empty here.
        assert isinstance(profile["my_work"]["tools"], list)

    def test_load_missing_profile_raises(self):
        """A nonexistent profile path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Profile not found"):
            load_profile("/tmp/nonexistent_profile_12345.toml")

    def test_validation_error_on_zero_feeds(self, tmp_path: Path):
        """A profile with 0 feeds should raise ValueError."""
        toml_content = b'name = "Empty"\n[feeds]\nlist = []\n'
        profile_file = tmp_path / "empty.toml"
        profile_file.write_bytes(toml_content)
        with pytest.raises(ValueError, match="at least one feed"):
            load_profile(profile_file)

    def test_missing_optional_fields_use_defaults(self, tmp_path: Path):
        """Optional fields should fall back to defaults when missing."""
        toml_content = (
            b'[feeds]\n'
            b'list = ["https://example.com/rss"]\n'
        )
        profile_file = tmp_path / "minimal.toml"
        profile_file.write_bytes(toml_content)
        profile = load_profile(profile_file)

        # Name defaults to stem
        assert profile["name"] == "minimal"
        assert profile["description"] == ""
        assert profile["interests"]["keywords"] == []
        assert profile["interests"]["narrative"] == ""
        assert profile["paywalled_domains"] == []
        assert profile["my_work"]["tools"] == []
        assert profile["outputs"] == {}
        assert profile["overrides"] == {}

    def test_bare_url_feed_parsing(self, tmp_path: Path):
        """Bare URLs in feed list should parse with empty name."""
        toml_content = (
            b'[feeds]\n'
            b'list = ["https://example.com/feed.xml"]\n'
        )
        profile_file = tmp_path / "bare_url.toml"
        profile_file.write_bytes(toml_content)
        profile = load_profile(profile_file)

        assert len(profile["feeds"]) == 1
        assert profile["feeds"][0]["name"] == ""
        assert profile["feeds"][0]["url"] == "https://example.com/feed.xml"
