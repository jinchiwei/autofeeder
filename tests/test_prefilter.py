"""Tests for prefilter.py — keyword matching, recency fallback, edge cases."""

from __future__ import annotations

from typing import Any

import pytest

from prefilter import _count_hits, prefilter


class TestCountHits:
    """Test the keyword hit counter."""

    def test_counts_correctly(self):
        """Each matching keyword should be counted once."""
        text = "EEG and neural oscillations in the brain"
        keywords = ["EEG", "neural oscillations", "theta"]
        assert _count_hits(text, keywords) == 2

    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        assert _count_hits("eeg data", ["EEG"]) == 1
        assert _count_hits("EEG data", ["eeg"]) == 1

    def test_no_matches(self):
        """No matching keywords should return 0."""
        assert _count_hits("something unrelated", ["EEG", "fMRI"]) == 0

    def test_empty_keywords(self):
        """Empty keyword list should return 0."""
        assert _count_hits("EEG data", []) == 0


class TestPrefilter:
    """Test the main prefilter function."""

    def test_keyword_matching_counts_correctly(self, sample_items: list[dict[str, Any]]):
        """Items with more keyword hits should rank higher when enough items match."""
        # Create enough items to trigger keyword ranking (>=50 matching)
        items = []
        for i in range(60):
            items.append({
                "id": f"item_{i}",
                "title": f"EEG study {i} with neural oscillations",
                "summary": "A study about EEG and neural oscillations.",
            })
        # Add some non-matching items
        for i in range(20):
            items.append({
                "id": f"other_{i}",
                "title": f"Unrelated topic {i}",
                "summary": "No relevant keywords here.",
            })

        keywords = ["EEG", "neural oscillations"]
        result = prefilter(items, keywords, keep_top=50)

        assert len(result) == 50
        # All top items should have keyword matches
        for item in result[:50]:
            text = item["title"] + " " + item["summary"]
            assert "eeg" in text.lower() or "neural oscillations" in text.lower()

    def test_recency_fallback_when_few_matches(self, sample_items: list[dict[str, Any]]):
        """When <50 items match keywords, fall back to recency (original order)."""
        keywords = ["EEG"]
        result = prefilter(sample_items, keywords, keep_top=10)
        # Only a few items match, so recency fallback returns first keep_top items
        assert len(result) == len(sample_items)  # all 5 items, since 5 < keep_top

    def test_empty_keywords_returns_all(self, sample_items: list[dict[str, Any]]):
        """Empty keywords should return items up to keep_top by recency."""
        result = prefilter(sample_items, [], keep_top=3)
        assert len(result) == 3
        assert result[0]["id"] == sample_items[0]["id"]

    def test_empty_items_returns_empty(self):
        """Empty items list should return empty."""
        result = prefilter([], ["EEG"], keep_top=10)
        assert result == []

    def test_keep_top_limits_output(self):
        """Output should never exceed keep_top."""
        items = [
            {"id": f"i{i}", "title": f"EEG study {i}", "summary": "EEG data"}
            for i in range(100)
        ]
        result = prefilter(items, ["EEG"], keep_top=10)
        assert len(result) <= 10
