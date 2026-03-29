"""Tests for ledger.py — load, save, filter, update, prune."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from ledger import (
    ledger_filter,
    ledger_update,
    load_ledger,
    prune_old_entries,
    save_ledger,
)


class TestLoadLedger:
    """Test loading the seen-items ledger."""

    def test_missing_file_returns_empty(self, tmp_path: Path):
        """A nonexistent ledger file should return an empty dict."""
        result = load_ledger(tmp_path / "nonexistent.json")
        assert result == {}

    def test_corrupted_json_returns_empty(self, tmp_path: Path):
        """A file with invalid JSON should return an empty dict."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json!!", encoding="utf-8")
        result = load_ledger(bad_file)
        assert result == {}

    def test_non_dict_json_returns_empty(self, tmp_path: Path):
        """A JSON file containing a list (not dict) should return empty."""
        list_file = tmp_path / "list.json"
        list_file.write_text("[1, 2, 3]", encoding="utf-8")
        result = load_ledger(list_file)
        assert result == {}

    def test_valid_ledger_loads(self, tmp_path: Path):
        """A valid ledger JSON file should load correctly."""
        ledger_data = {
            "abc123": {
                "score": 0.9,
                "title": "Test Paper",
                "first_seen": "2025-03-10",
                "last_seen": "2025-03-17",
            }
        }
        ledger_file = tmp_path / "seen.json"
        ledger_file.write_text(json.dumps(ledger_data), encoding="utf-8")
        result = load_ledger(ledger_file)
        assert "abc123" in result
        assert result["abc123"]["score"] == 0.9


class TestLedgerFilter:
    """Test filtering items against the ledger."""

    def test_removes_seen_items(self, tmp_path: Path, sample_items: list[dict[str, Any]]):
        """Items already in the ledger should be removed."""
        # Create a ledger with the first item already seen
        ledger_data = {
            sample_items[0]["id"]: {
                "score": 0.8,
                "title": sample_items[0]["title"],
                "first_seen": "2025-03-10",
                "last_seen": "2025-03-17",
            }
        }
        ledger_file = tmp_path / "seen.json"
        ledger_file.write_text(json.dumps(ledger_data), encoding="utf-8")

        config = {"ledger": {"enabled": True, "path": str(ledger_file)}}
        result = ledger_filter(sample_items, config)

        assert len(result) == 4  # 5 - 1 seen = 4 new
        assert all(item["is_new"] for item in result)

    def test_marks_is_new_correctly(self, tmp_path: Path, sample_items: list[dict[str, Any]]):
        """Seen items should get is_new=False, unseen should get is_new=True."""
        ledger_data = {
            sample_items[0]["id"]: {
                "score": 0.8,
                "title": sample_items[0]["title"],
                "first_seen": "2025-03-10",
                "last_seen": "2025-03-17",
            }
        }
        ledger_file = tmp_path / "seen.json"
        ledger_file.write_text(json.dumps(ledger_data), encoding="utf-8")

        config = {"ledger": {"enabled": True, "path": str(ledger_file)}}
        # Call filter - seen items are NOT returned but they do get is_new set
        new_items = ledger_filter(sample_items, config)

        # The returned items are all new
        assert all(item["is_new"] for item in new_items)
        # The first item (seen) was not returned
        assert sample_items[0]["id"] not in [i["id"] for i in new_items]
        # The first item was annotated in-place
        assert sample_items[0]["is_new"] is False

    def test_disabled_ledger_returns_all(self, sample_items: list[dict[str, Any]]):
        """When ledger is disabled, all items should be returned with is_new=True."""
        config = {"ledger": {"enabled": False}}
        result = ledger_filter(sample_items, config)
        assert len(result) == 5
        assert all(item["is_new"] for item in result)


class TestLedgerUpdate:
    """Test adding new items to the ledger."""

    def test_adds_new_items(self, tmp_path: Path):
        """New scored items should be added to the ledger."""
        ledger_file = tmp_path / "seen.json"
        ledger_file.write_text("{}", encoding="utf-8")

        result = {
            "ranked": [
                {"id": "new1", "score": 0.9, "title": "Paper 1"},
                {"id": "new2", "score": 0.8, "title": "Paper 2"},
            ]
        }
        config = {
            "ledger": {
                "enabled": True,
                "path": str(ledger_file),
                "prune_after_days": 90,
            }
        }

        ledger_update(result, config)

        saved = json.loads(ledger_file.read_text())
        assert "new1" in saved
        assert "new2" in saved
        assert saved["new1"]["score"] == 0.9

    def test_updates_existing_last_seen(self, tmp_path: Path):
        """Existing items should have their last_seen updated."""
        ledger_data = {
            "existing1": {
                "score": 0.7,
                "title": "Old Paper",
                "first_seen": "2025-01-01",
                "last_seen": "2025-01-01",
            }
        }
        ledger_file = tmp_path / "seen.json"
        ledger_file.write_text(json.dumps(ledger_data), encoding="utf-8")

        result = {"ranked": [{"id": "existing1", "score": 0.7, "title": "Old Paper"}]}
        config = {
            "ledger": {
                "enabled": True,
                "path": str(ledger_file),
                "prune_after_days": 90,
            }
        }

        ledger_update(result, config)

        saved = json.loads(ledger_file.read_text())
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert saved["existing1"]["last_seen"] == today
        # first_seen should be preserved
        assert saved["existing1"]["first_seen"] == "2025-01-01"


class TestPruneOldEntries:
    """Test pruning stale ledger entries."""

    def test_prune_removes_old_entries(self):
        """Entries older than max_days should be removed."""
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d")
        recent_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        seen = {
            "old_item": {"score": 0.5, "last_seen": old_date},
            "recent_item": {"score": 0.9, "last_seen": recent_date},
        }

        result = prune_old_entries(seen, max_days=90)
        assert "old_item" not in result
        assert "recent_item" in result

    def test_prune_keeps_items_without_date(self):
        """Items without a last_seen date should be kept."""
        seen = {
            "no_date": {"score": 0.5},
            "empty_date": {"score": 0.5, "last_seen": ""},
        }

        result = prune_old_entries(seen, max_days=90)
        assert "no_date" in result
        assert "empty_date" in result

    def test_prune_zero_max_days_keeps_all(self):
        """max_days=0 should keep all entries."""
        seen = {"item": {"score": 0.5, "last_seen": "2020-01-01"}}
        result = prune_old_entries(seen, max_days=0)
        assert "item" in result
