"""Tests for output modules — markdown, obsidian, slack, email."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from outputs.markdown import publish as md_publish
from outputs.obsidian import publish as obsidian_publish, _build_frontmatter, _slugify
from outputs.slack import _build_blocks
from outputs.email import _build_html


def _make_digest_data(
    items: list[dict[str, Any]] | None = None,
    tldr: str = "This week: big EEG findings.",
    is_first_run: bool = False,
) -> dict[str, Any]:
    """Helper to build a digest_data dict."""
    if items is None:
        items = [
            {
                "title": "Aperiodic neural activity in EEG",
                "link": "https://example.com/1",
                "source": "Nature Neuroscience",
                "score": 0.92,
                "published_utc": "2025-03-17T00:00:00+00:00",
                "is_new": True,
                "cites_your_work": False,
                "headline": "Aperiodic exponent tracks E/I balance",
                "key_takeaways": ["Finding 1", "Finding 2"],
                "relevance": "Directly relevant to spectral parameterization.",
                "tags": ["aperiodic", "EEG"],
                "content_source": "direct",
                "content_source_label": "",
            },
            {
                "title": "Theta oscillations in memory",
                "link": "https://example.com/2",
                "source": "Neuron",
                "score": 0.78,
                "published_utc": "2025-03-16T00:00:00+00:00",
                "is_new": True,
                "cites_your_work": False,
                "headline": "Theta synchronizes hippocampal-PFC circuits",
                "key_takeaways": ["Takeaway A"],
                "relevance": "Related to oscillation methods.",
                "tags": ["theta", "memory"],
                "content_source": "rss_summary",
                "content_source_label": "",
            },
            {
                "title": "Specparam in aging dataset",
                "link": "https://example.com/3",
                "source": "bioRxiv",
                "score": 0.95,
                "published_utc": "2025-03-15T00:00:00+00:00",
                "is_new": True,
                "cites_your_work": True,
                "headline": "Specparam reveals lifespan neural changes",
                "key_takeaways": ["T1", "T2", "T3"],
                "relevance": "Uses your specparam tool.",
                "tags": ["specparam", "aging"],
                "content_source": "unpaywall",
                "content_source_label": "Full text via Unpaywall (OA)",
            },
        ]

    return {
        "profile_name": "neuro",
        "date": "2025-03-17",
        "items": items,
        "min_score": 0.65,
        "total_scored": len(items),
        "tldr": tldr,
        "is_first_run": is_first_run,
        "profile_description": "Neural oscillations and electrophysiology",
    }


class TestMarkdownPublish:
    """Test markdown digest output."""

    def test_creates_file_with_correct_structure(
        self, tmp_output_dir: Path, sample_profile: dict[str, Any], sample_config: dict[str, Any],
    ):
        """Markdown output should create a file with TL;DR and tiered items."""
        config = {**sample_config, "output": {"dir": str(tmp_output_dir), "min_score": 0.65}}
        digest = _make_digest_data()
        md_publish(digest, sample_profile, config)

        out_file = tmp_output_dir / "neuro.md"
        assert out_file.exists()

        content = out_file.read_text()
        assert "# autofeeder" in content
        assert "## TL;DR" in content
        assert "This week: big EEG findings." in content
        # Tiered sections
        assert "Builds on your work" in content  # cites_your_work item
        assert "Top picks" in content  # score >= 0.80
        assert "Also relevant" in content  # score < 0.80

    def test_empty_items(
        self, tmp_output_dir: Path, sample_profile: dict[str, Any], sample_config: dict[str, Any],
    ):
        """Markdown with empty items should still create a valid file."""
        config = {**sample_config, "output": {"dir": str(tmp_output_dir), "min_score": 0.65}}
        digest = _make_digest_data(items=[])
        md_publish(digest, sample_profile, config)

        out_file = tmp_output_dir / "neuro.md"
        assert out_file.exists()
        content = out_file.read_text()
        assert "# autofeeder" in content
        assert "0 items scored" in content

    def test_first_run_flag(
        self, tmp_output_dir: Path, sample_profile: dict[str, Any], sample_config: dict[str, Any],
    ):
        """First-run digest should include a welcome note."""
        config = {**sample_config, "output": {"dir": str(tmp_output_dir), "min_score": 0.65}}
        digest = _make_digest_data(is_first_run=True)
        md_publish(digest, sample_profile, config)

        content = (tmp_output_dir / "neuro.md").read_text()
        assert "first autofeeder run" in content.lower()


class TestObsidianPublish:
    """Test Obsidian vault output."""

    def test_creates_files_with_yaml_frontmatter(
        self, tmp_path: Path, sample_config: dict[str, Any],
    ):
        """Each item should produce a note with YAML frontmatter."""
        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        profile = {
            "name": "neuro",
            "outputs": {
                "obsidian": {
                    "vault_path": str(vault_path),
                    "subfolder": "autofeeder",
                },
            },
        }
        digest = _make_digest_data()
        obsidian_publish(digest, profile, sample_config)

        target_dir = vault_path / "autofeeder"
        assert target_dir.exists()
        notes = list(target_dir.glob("*.md"))
        assert len(notes) == 3

        # Check frontmatter of the first note
        content = notes[0].read_text()
        assert content.startswith("---")
        assert "title:" in content
        assert "source:" in content
        assert "score:" in content
        assert "tags:" in content

    def test_skips_when_vault_path_empty(
        self, sample_config: dict[str, Any],
    ):
        """Empty vault_path should cause the output to be skipped (no error)."""
        profile = {
            "name": "neuro",
            "outputs": {"obsidian": {"vault_path": ""}},
        }
        digest = _make_digest_data()
        # Should not raise
        obsidian_publish(digest, profile, sample_config)

    def test_slugify(self):
        """Title slugification should produce filesystem-safe names."""
        assert _slugify("Hello World: A Paper!") == "hello-world-a-paper"
        assert len(_slugify("A" * 200)) <= 60


class TestSlackBuildBlocks:
    """Test Slack Block Kit block generation (no HTTP calls)."""

    def test_build_blocks_structure(self, sample_profile: dict[str, Any]):
        """Blocks should have header, TL;DR, divider, items, footer."""
        digest = _make_digest_data()
        blocks = _build_blocks(digest, sample_profile)

        # Check that we have the expected block types
        block_types = [b["type"] for b in blocks]
        assert "header" in block_types
        assert "section" in block_types
        assert "divider" in block_types
        assert "context" in block_types

        # Header should mention profile name
        header = next(b for b in blocks if b["type"] == "header")
        assert "neuro" in header["text"]["text"]

    def test_build_blocks_empty_items(self, sample_profile: dict[str, Any]):
        """Empty items should produce a 'quiet week' message."""
        digest = _make_digest_data(items=[])
        blocks = _build_blocks(digest, sample_profile)

        # Should have a section with "Quiet week"
        texts = [b.get("text", {}).get("text", "") for b in blocks if b["type"] == "section"]
        assert any("quiet week" in t.lower() for t in texts)

    def test_build_blocks_limits_to_top_5(self, sample_profile: dict[str, Any]):
        """Slack should include at most 5 items."""
        items = []
        for i in range(10):
            items.append({
                "title": f"Paper {i}",
                "link": f"https://example.com/{i}",
                "source": "Nature",
                "score": 0.9 - i * 0.05,
                "is_new": True,
                "cites_your_work": False,
                "headline": f"Finding {i}",
                "tags": [],
                "content_source_label": "",
            })
        digest = _make_digest_data(items=items)
        blocks = _build_blocks(digest, sample_profile)

        # Count section blocks that contain paper titles (item blocks)
        # Items are section blocks; filter out header, TL;DR, and footer
        item_sections = [
            b for b in blocks
            if b["type"] == "section"
            and "Paper " in b.get("text", {}).get("text", "")
        ]
        assert len(item_sections) == 5


class TestEmailBuildHtml:
    """Test email HTML generation."""

    def test_produces_valid_html_with_branding(self):
        """Email HTML should contain branding colors and structure."""
        digest = _make_digest_data()
        html = _build_html(digest)

        assert "<!DOCTYPE html>" in html
        assert "#40E0D0" in html  # turquoise
        assert "#FF1493" in html  # deeppink
        assert "#FFD700" in html  # gold
        assert "Geist" in html  # font
        assert "autofeeder" in html
        assert "neuro" in html

    def test_html_contains_items(self):
        """All items should appear in the email HTML."""
        digest = _make_digest_data()
        html = _build_html(digest)

        assert "Aperiodic neural activity" in html
        assert "Theta oscillations" in html
        assert "Specparam in aging" in html

    def test_html_with_tldr(self):
        """TL;DR section should appear when present."""
        digest = _make_digest_data(tldr="Important EEG discoveries this week.")
        html = _build_html(digest)
        assert "TL;DR" in html
        assert "Important EEG discoveries this week." in html

    def test_html_with_first_run(self):
        """First-run flag should include a welcome note."""
        digest = _make_digest_data(is_first_run=True)
        html = _build_html(digest)
        assert "Welcome to autofeeder" in html

    def test_html_escapes_special_chars(self):
        """HTML special characters in titles should be escaped."""
        items = [{
            "title": "A <b>Bold</b> & \"Quoted\" Paper",
            "link": "https://example.com/1",
            "source": "Nature",
            "score": 0.9,
            "is_new": False,
            "cites_your_work": False,
            "headline": None,
            "key_takeaways": None,
            "relevance": None,
            "tags": [],
            "content_source_label": "",
        }]
        digest = _make_digest_data(items=items)
        html = _build_html(digest)

        assert "&lt;b&gt;" in html
        assert "&amp;" in html
