"""Tests for fetch.py — RSS fetching, dedup, date filtering, max caps."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

import feedparser

from fetch import _make_id, _parse_date, _strip_html, fetch_items

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _make_feedparser_result(fixture_path: Path) -> Any:
    """Parse the fixture RSS XML with feedparser and return the result."""
    return feedparser.parse(str(fixture_path))


class TestHelpers:
    """Test fetch helper functions."""

    def test_make_id_deterministic(self):
        """Same inputs should produce the same ID."""
        id1 = _make_id("Nature", "Title A", "https://example.com/1")
        id2 = _make_id("Nature", "Title A", "https://example.com/1")
        assert id1 == id2

    def test_make_id_different_inputs(self):
        """Different inputs should produce different IDs."""
        id1 = _make_id("Nature", "Title A", "https://example.com/1")
        id2 = _make_id("Nature", "Title B", "https://example.com/2")
        assert id1 != id2

    def test_parse_date_struct_time(self):
        """feedparser struct_time should be parsed to ISO string."""
        entry = SimpleNamespace(
            published_parsed=time.strptime("2025-03-15", "%Y-%m-%d"),
            updated_parsed=None,
        )
        result = _parse_date(entry)
        assert result is not None
        assert "2025-03-15" in result

    def test_parse_date_string_fallback(self):
        """String date fields should be parsed via dateutil."""
        entry = SimpleNamespace(
            published_parsed=None,
            updated_parsed=None,
            published="Mon, 17 Mar 2025 10:30:00 +0000",
        )
        result = _parse_date(entry)
        assert result is not None
        assert "2025-03-17" in result

    def test_parse_date_none_when_missing(self):
        """Entries with no date fields should return None."""
        entry = SimpleNamespace(
            published_parsed=None,
            updated_parsed=None,
        )
        # Remove optional string attributes
        result = _parse_date(entry)
        assert result is None

    def test_strip_html(self):
        """HTML tags should be removed from text."""
        html = "<p>Hello <strong>world</strong></p>"
        assert _strip_html(html) == "Hello world"


class TestFetchItems:
    """Test the main fetch_items function with mocked feedparser."""

    def test_fetch_with_fixture_rss(self, rss_fixture_path: Path, sample_config: dict[str, Any]):
        """Fetching should return items from the fixture RSS XML."""
        parsed = _make_feedparser_result(rss_fixture_path)

        # The fixture dates are from March 2025, so we need a large lookback
        config = {**sample_config}
        config["fetch"] = {**config["fetch"], "lookback_days": 9999}

        feeds = [{"name": "Nature Neuroscience", "url": "https://www.nature.com/neuro.rss"}]

        with patch("fetch.feedparser.parse", return_value=parsed):
            items = fetch_items(feeds, config)

        assert len(items) == 3
        assert all("id" in item for item in items)
        assert all("title" in item for item in items)
        assert all("link" in item for item in items)
        assert items[0]["source"] == "Nature Neuroscience"

    def test_dedup_by_id(self, rss_fixture_path: Path, sample_config: dict[str, Any]):
        """Duplicate items (same source+title+link) should be deduplicated."""
        parsed = _make_feedparser_result(rss_fixture_path)

        # The fixture dates are from March 2025, so we need a large lookback
        config = {**sample_config}
        config["fetch"] = {**config["fetch"], "lookback_days": 9999}

        # Two feeds returning the same parsed data
        feeds = [
            {"name": "Nature Neuroscience", "url": "https://www.nature.com/neuro.rss"},
            {"name": "Nature Neuroscience", "url": "https://www.nature.com/neuro.rss"},
        ]

        with patch("fetch.feedparser.parse", return_value=parsed):
            items = fetch_items(feeds, config)

        # Same source+title+link -> same ID -> deduped
        assert len(items) == 3

    def test_date_filtering_excludes_old_items(self, sample_config: dict[str, Any]):
        """Items older than lookback_days should be excluded."""
        config = {**sample_config}
        config["fetch"] = {**config["fetch"], "lookback_days": 1}

        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        )

        # Create a feedparser-like result with one old entry
        old_entry = SimpleNamespace(
            title="Old Paper",
            link="https://example.com/old",
            published=old_date,
            published_parsed=None,
            updated_parsed=None,
            summary="An old paper",
        )
        recent_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        new_entry = SimpleNamespace(
            title="New Paper",
            link="https://example.com/new",
            published=recent_date,
            published_parsed=None,
            updated_parsed=None,
            summary="A new paper",
        )

        mock_parsed = SimpleNamespace(
            bozo=False,
            entries=[old_entry, new_entry],
            feed=SimpleNamespace(title="Test Feed"),
        )

        feeds = [{"name": "Test", "url": "https://example.com/rss"}]
        with patch("fetch.feedparser.parse", return_value=mock_parsed):
            items = fetch_items(feeds, config)

        assert len(items) == 1
        assert items[0]["title"] == "New Paper"

    def test_max_items_per_feed_cap(self, sample_config: dict[str, Any]):
        """No more than max_items_per_feed items should be returned from a single feed."""
        config = {**sample_config}
        config["fetch"] = {**config["fetch"], "max_items_per_feed": 2}

        entries = []
        for i in range(10):
            entries.append(SimpleNamespace(
                title=f"Paper {i}",
                link=f"https://example.com/{i}",
                published_parsed=None,
                updated_parsed=None,
                summary=f"Summary {i}",
            ))

        mock_parsed = SimpleNamespace(
            bozo=False,
            entries=entries,
            feed=SimpleNamespace(title="Test Feed"),
        )

        feeds = [{"name": "Test", "url": "https://example.com/rss"}]
        with patch("fetch.feedparser.parse", return_value=mock_parsed):
            items = fetch_items(feeds, config)

        assert len(items) == 2

    def test_graceful_skip_on_feed_failure(self, sample_config: dict[str, Any]):
        """A feed that raises an exception should be skipped gracefully."""
        feeds = [
            {"name": "Bad Feed", "url": "https://example.com/bad"},
            {"name": "Good Feed", "url": "https://example.com/good"},
        ]

        good_entry = SimpleNamespace(
            title="Good Paper",
            link="https://example.com/good/1",
            published_parsed=None,
            updated_parsed=None,
            summary="A good paper",
        )
        good_parsed = SimpleNamespace(
            bozo=False,
            entries=[good_entry],
            feed=SimpleNamespace(title="Good Feed"),
        )

        def mock_parse(url):
            if "bad" in url:
                raise Exception("Network error")
            return good_parsed

        with patch("fetch.feedparser.parse", side_effect=mock_parse):
            items = fetch_items(feeds, sample_config)

        assert len(items) == 1
        assert items[0]["title"] == "Good Paper"
