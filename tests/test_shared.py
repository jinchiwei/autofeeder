"""Tests for backends/_shared.py — prompt building, response parsing, JSON repair."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backends._shared import (
    _extract_json,
    build_summary_prompt,
    build_tldr_prompt,
    build_triage_prompt,
    parse_structured_response,
    parse_summary_response,
)


class TestBuildTriagePrompt:
    """Test triage prompt builder."""

    def test_fills_placeholders(self, sample_interests: dict[str, Any], sample_items: list[dict[str, Any]]):
        """The triage prompt should contain keywords, narrative, and item data."""
        prompt, lean_items = build_triage_prompt(sample_interests, sample_items)

        assert "EEG" in prompt
        assert "neural oscillations" in prompt
        assert sample_interests["narrative"] in prompt
        # Items should be JSON-embedded
        assert sample_items[0]["title"] in prompt
        assert len(lean_items) == len(sample_items)

    def test_summary_truncation(self, sample_interests: dict[str, Any]):
        """Long summaries should be truncated in the prompt."""
        items = [{
            "id": "trunc1",
            "title": "Test",
            "link": "http://x",
            "published_utc": None,
            "summary": "A" * 1000,
        }]
        prompt, lean_items = build_triage_prompt(sample_interests, items, summary_max_chars=100)

        assert len(lean_items[0]["summary"]) == 103  # 100 + "..."
        assert lean_items[0]["summary"].endswith("...")


class TestBuildSummaryPrompt:
    """Test summary prompt builder."""

    def test_fills_placeholders(self, sample_interests: dict[str, Any]):
        """The summary prompt should contain narrative, title, source, and content."""
        item = {
            "title": "Test Paper Title",
            "source": "Nature Neuroscience",
            "full_text": "This paper shows that EEG aperiodic activity tracks E/I balance.",
        }
        prompt = build_summary_prompt(sample_interests, item)

        assert sample_interests["narrative"] in prompt
        assert "Test Paper Title" in prompt
        assert "Nature Neuroscience" in prompt
        assert "EEG aperiodic activity" in prompt


class TestBuildTldrPrompt:
    """Test TL;DR prompt builder."""

    def test_fills_placeholders(self, sample_interests: dict[str, Any]):
        """The TL;DR prompt should contain narrative and paper summaries."""
        top_items = [
            {
                "title": "Paper 1",
                "source": "Nature",
                "score": 0.95,
                "headline": "Big finding about EEG",
                "key_takeaways": ["Takeaway 1", "Takeaway 2"],
            },
            {
                "title": "Paper 2",
                "source": "Neuron",
                "score": 0.88,
                "headline": "Neural oscillation discovery",
                "key_takeaways": ["Takeaway 3"],
            },
        ]
        prompt = build_tldr_prompt(sample_interests, top_items)

        assert sample_interests["narrative"] in prompt
        assert "Paper 1" in prompt
        assert "Paper 2" in prompt
        assert "Big finding about EEG" in prompt

    def test_cites_your_work_mentioned(self, sample_interests: dict[str, Any]):
        """Items citing user's work should be flagged in the prompt."""
        top_items = [{
            "title": "Paper citing me",
            "source": "Nature",
            "score": 0.99,
            "headline": "Uses specparam",
            "key_takeaways": [],
            "cites_your_work": True,
        }]
        prompt = build_tldr_prompt(sample_interests, top_items)
        assert "cites your work" in prompt.lower()


class TestParseStructuredResponse:
    """Test parsing triage responses with various formats."""

    def test_valid_json(self, triage_response_fixture: dict[str, Any]):
        """Valid JSON with 'ranked' key should parse directly."""
        text = json.dumps(triage_response_fixture)
        result = parse_structured_response(text)
        assert "ranked" in result
        assert len(result["ranked"]) == 3

    def test_alternative_keys(self):
        """Alternative keys like 'items', 'results', 'papers' should be normalized to 'ranked'."""
        for key in ("items", "results", "papers", "articles"):
            text = json.dumps({key: [{"id": "1", "score": 0.9}]})
            result = parse_structured_response(text)
            assert "ranked" in result
            assert len(result["ranked"]) == 1

    def test_bare_array(self):
        """A bare JSON array should be wrapped as {'ranked': [...]}."""
        text = json.dumps([{"id": "1", "score": 0.9}])
        result = parse_structured_response(text)
        assert "ranked" in result
        assert result["ranked"][0]["id"] == "1"

    def test_markdown_code_fences(self, triage_response_fixture: dict[str, Any]):
        """JSON inside ```json ... ``` blocks should be extracted."""
        text = "Here are the results:\n```json\n" + json.dumps(triage_response_fixture) + "\n```\n"
        result = parse_structured_response(text)
        assert "ranked" in result
        assert len(result["ranked"]) == 3

    def test_missing_ranked_key_raises(self):
        """A dict without any recognizable list key should raise ValueError."""
        text = json.dumps({"notes": "nothing here", "count": 5})
        with pytest.raises(ValueError, match="missing 'ranked' key"):
            parse_structured_response(text)

    def test_unnamed_list_key(self):
        """A dict with a single list of dicts under an unknown key should still work."""
        text = json.dumps({"scored_items": [{"id": "1", "score": 0.8}]})
        result = parse_structured_response(text)
        assert "ranked" in result


class TestExtractJson:
    """Test JSON extraction and repair logic."""

    def test_direct_json(self):
        """Valid JSON should parse directly."""
        data = _extract_json('{"key": "value"}')
        assert data["key"] == "value"

    def test_code_fences(self):
        """JSON inside code fences should be extracted."""
        text = "```json\n{\"key\": \"value\"}\n```"
        data = _extract_json(text)
        assert data["key"] == "value"

    def test_surrounding_text(self):
        """JSON embedded in surrounding text should be extracted via first-{ to last-}."""
        text = 'Here is the response: {"ranked": []} and some more text'
        data = _extract_json(text)
        assert "ranked" in data

    def test_invalid_json_raises(self):
        """Completely invalid text should raise ValueError."""
        with pytest.raises(ValueError, match="Could not extract JSON"):
            _extract_json("This is not JSON at all and has no braces")


class TestParseSummaryResponse:
    """Test parsing summary responses."""

    def test_valid_summary(self, summary_response_fixture: dict[str, Any]):
        """Valid summary JSON should parse correctly."""
        text = json.dumps(summary_response_fixture)
        result = parse_summary_response(text)
        assert "headline" in result
        assert "key_takeaways" in result
        assert len(result["key_takeaways"]) == 3

    def test_missing_headline_raises(self):
        """A summary without 'headline' should raise ValueError."""
        text = json.dumps({"key_takeaways": ["a"], "relevance": "b"})
        with pytest.raises(ValueError, match="missing 'headline'"):
            parse_summary_response(text)

    def test_summary_in_code_fences(self, summary_response_fixture: dict[str, Any]):
        """Summary inside markdown code fences should still parse."""
        text = "```\n" + json.dumps(summary_response_fixture) + "\n```"
        result = parse_summary_response(text)
        assert "headline" in result
