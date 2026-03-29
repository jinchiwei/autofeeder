"""Tests for cross_profile.py — paper of the week, crossover detection."""

from __future__ import annotations

from typing import Any

import pytest

from cross_profile import find_crossover_papers, find_paper_of_the_week


def _make_results(*profiles: tuple[str, list[dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """Helper to build the all_results format from (name, items) tuples."""
    return [(name, {"ranked": items}) for name, items in profiles]


class TestFindPaperOfTheWeek:
    """Test picking the single highest-scoring item."""

    def test_picks_highest_score(self):
        """Should return the item with the highest score across all profiles."""
        results = _make_results(
            ("neuro", [
                {"id": "a", "title": "Paper A", "link": "http://a", "source": "S1", "score": 0.85, "why": "good"},
                {"id": "b", "title": "Paper B", "link": "http://b", "source": "S1", "score": 0.70, "why": "ok"},
            ]),
            ("ai", [
                {"id": "c", "title": "Paper C", "link": "http://c", "source": "S2", "score": 0.95, "why": "great"},
            ]),
        )

        best = find_paper_of_the_week(results)
        assert best is not None
        assert best["title"] == "Paper C"
        assert best["score"] == 0.95

    def test_duplicate_items_across_profiles(self):
        """Same item in 2 profiles should track both profiles and use max score."""
        results = _make_results(
            ("neuro", [
                {"id": "shared", "title": "Shared Paper", "link": "http://s", "source": "S1", "score": 0.80, "why": "v1"},
            ]),
            ("ai", [
                {"id": "shared", "title": "Shared Paper", "link": "http://s", "source": "S1", "score": 0.90, "why": "v2"},
            ]),
        )

        best = find_paper_of_the_week(results)
        assert best is not None
        assert best["score"] == 0.90
        assert "neuro" in best["profiles"]
        assert "ai" in best["profiles"]

    def test_empty_input_returns_none(self):
        """No results should return None."""
        assert find_paper_of_the_week([]) is None

    def test_empty_ranked_lists_returns_none(self):
        """Profiles with empty ranked lists should return None."""
        results = [("neuro", {"ranked": []}), ("ai", {"ranked": []})]
        assert find_paper_of_the_week(results) is None


class TestFindCrossoverPapers:
    """Test finding items that appear in multiple profiles."""

    def test_finds_items_in_two_plus_profiles(self):
        """Items in 2+ profiles should be identified as crossovers."""
        results = _make_results(
            ("neuro", [
                {"id": "cross1", "title": "Crossover", "link": "http://x", "source": "S1", "score": 0.85, "why": "a"},
                {"id": "neuro_only", "title": "Neuro Only", "link": "http://n", "source": "S1", "score": 0.75, "why": "b"},
            ]),
            ("ai", [
                {"id": "cross1", "title": "Crossover", "link": "http://x", "source": "S1", "score": 0.90, "why": "c"},
                {"id": "ai_only", "title": "AI Only", "link": "http://a", "source": "S2", "score": 0.80, "why": "d"},
            ]),
        )

        crossovers = find_crossover_papers(results)
        assert len(crossovers) == 1
        assert crossovers[0]["title"] == "Crossover"
        assert crossovers[0]["score"] == 0.90  # max of 0.85 and 0.90
        assert set(crossovers[0]["profiles"]) == {"neuro", "ai"}

    def test_no_crossovers(self):
        """When no items appear in 2+ profiles, should return empty."""
        results = _make_results(
            ("neuro", [
                {"id": "a", "title": "Paper A", "link": "http://a", "source": "S1", "score": 0.85, "why": "x"},
            ]),
            ("ai", [
                {"id": "b", "title": "Paper B", "link": "http://b", "source": "S2", "score": 0.90, "why": "y"},
            ]),
        )

        crossovers = find_crossover_papers(results)
        assert crossovers == []

    def test_empty_input(self):
        """Empty results should return empty list."""
        assert find_crossover_papers([]) == []

    def test_sorted_by_score_descending(self):
        """Crossover results should be sorted by score descending."""
        results = _make_results(
            ("p1", [
                {"id": "x", "title": "X", "link": "http://x", "source": "S", "score": 0.70, "why": ""},
                {"id": "y", "title": "Y", "link": "http://y", "source": "S", "score": 0.90, "why": ""},
            ]),
            ("p2", [
                {"id": "x", "title": "X", "link": "http://x", "source": "S", "score": 0.75, "why": ""},
                {"id": "y", "title": "Y", "link": "http://y", "source": "S", "score": 0.85, "why": ""},
            ]),
        )

        crossovers = find_crossover_papers(results)
        assert len(crossovers) == 2
        assert crossovers[0]["score"] >= crossovers[1]["score"]
