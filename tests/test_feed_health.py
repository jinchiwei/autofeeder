"""Tests for feed_health.py — recording, analysis, reporting."""

from __future__ import annotations

from typing import Any

import pytest

from feed_health import (
    analyze_health,
    format_health_report,
    record_fetch,
)


class TestRecordFetch:
    """Test recording fetch outcomes."""

    def test_record_success(self):
        """Successful fetch should update stats correctly."""
        health: dict[str, dict[str, Any]] = {}
        record_fetch(health, "https://example.com/feed.xml", 10, True)

        entry = health["https://example.com/feed.xml"]
        assert entry["total_fetches"] == 1
        assert entry["successful_fetches"] == 1
        assert entry["total_items"] == 10
        assert entry["consecutive_failures"] == 0
        assert entry["last_success"] is not None

    def test_record_failure(self):
        """Failed fetch should increment consecutive_failures."""
        health: dict[str, dict[str, Any]] = {}
        record_fetch(health, "https://example.com/feed.xml", 0, False)

        entry = health["https://example.com/feed.xml"]
        assert entry["total_fetches"] == 1
        assert entry["successful_fetches"] == 0
        assert entry["consecutive_failures"] == 1

    def test_record_multiple_fetches(self):
        """Multiple fetches should accumulate stats."""
        health: dict[str, dict[str, Any]] = {}
        record_fetch(health, "https://example.com/feed.xml", 5, True)
        record_fetch(health, "https://example.com/feed.xml", 3, True)

        entry = health["https://example.com/feed.xml"]
        assert entry["total_fetches"] == 2
        assert entry["successful_fetches"] == 2
        assert entry["total_items"] == 8

    def test_success_resets_consecutive_failures(self):
        """A successful fetch should reset the consecutive_failures counter."""
        health: dict[str, dict[str, Any]] = {}
        record_fetch(health, "https://example.com/feed.xml", 0, False)
        record_fetch(health, "https://example.com/feed.xml", 0, False)
        assert health["https://example.com/feed.xml"]["consecutive_failures"] == 2

        record_fetch(health, "https://example.com/feed.xml", 5, True)
        assert health["https://example.com/feed.xml"]["consecutive_failures"] == 0

    def test_empty_url_is_ignored(self):
        """Empty feed URL should not be recorded."""
        health: dict[str, dict[str, Any]] = {}
        record_fetch(health, "", 0, True)
        assert len(health) == 0


class TestAnalyzeHealth:
    """Test feed health analysis/classification."""

    def test_detects_broken_feeds(self):
        """Feeds with 3+ consecutive failures should be classified as broken."""
        health = {
            "https://example.com/broken": {
                "total_fetches": 5,
                "successful_fetches": 2,
                "total_items": 10,
                "consecutive_failures": 3,
                "last_success": "2025-03-01",
            }
        }
        result = analyze_health(health)
        assert "https://example.com/broken" in result["broken"]
        assert "https://example.com/broken" not in result["healthy"]

    def test_detects_dead_feeds(self):
        """Feeds that succeed but return 0 items for 3+ fetches should be dead."""
        health = {
            "https://example.com/dead": {
                "total_fetches": 5,
                "successful_fetches": 5,
                "total_items": 0,
                "consecutive_failures": 0,
                "last_success": "2025-03-17",
            }
        }
        result = analyze_health(health)
        assert "https://example.com/dead" in result["dead"]

    def test_detects_healthy_feeds(self):
        """Active feeds with items and no failure streak should be healthy."""
        health = {
            "https://example.com/good": {
                "total_fetches": 10,
                "successful_fetches": 9,
                "total_items": 50,
                "consecutive_failures": 0,
                "last_success": "2025-03-17",
            }
        }
        result = analyze_health(health)
        assert "https://example.com/good" in result["healthy"]

    def test_empty_health_data(self):
        """Empty health data should produce empty classifications."""
        result = analyze_health({})
        assert result["healthy"] == []
        assert result["dead"] == []
        assert result["broken"] == []


class TestFormatHealthReport:
    """Test the human-readable health report formatter."""

    def test_all_healthy_report(self):
        """All healthy feeds should produce a positive message."""
        analysis = {"healthy": ["a", "b"], "dead": [], "broken": []}
        report = format_health_report(analysis)
        assert "2/2 feeds healthy" in report
        assert "All feeds operating normally" in report

    def test_report_with_dead_and_broken(self):
        """Report should list dead and broken feeds."""
        analysis = {
            "healthy": ["a"],
            "dead": ["https://dead.com/rss"],
            "broken": ["https://broken.com/rss"],
        }
        report = format_health_report(analysis)
        assert "Dead feeds" in report
        assert "https://dead.com/rss" in report
        assert "Broken feeds" in report
        assert "https://broken.com/rss" in report

    def test_no_feeds_tracked(self):
        """No feeds tracked should produce an appropriate message."""
        analysis = {"healthy": [], "dead": [], "broken": []}
        report = format_health_report(analysis)
        assert "no feeds tracked yet" in report
