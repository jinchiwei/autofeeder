"""Shared fixtures for autofeeder tests."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# Ensure the project root is on sys.path so imports like `config`, `fetch`, etc. work.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def sample_items() -> list[dict[str, Any]]:
    """Five realistic feed items with neuroscience paper titles."""
    return [
        {
            "id": "aaa111",
            "source": "Nature Neuroscience",
            "title": "Aperiodic neural activity reflects cortical excitability in humans",
            "link": "https://www.nature.com/articles/s41593-024-001",
            "published_utc": "2025-03-17T00:00:00+00:00",
            "summary": "We show that the aperiodic exponent tracks E/I balance across wake and sleep using intracranial EEG.",
        },
        {
            "id": "bbb222",
            "source": "Nature Neuroscience",
            "title": "Theta oscillations coordinate hippocampal-prefrontal circuits during memory retrieval",
            "link": "https://www.nature.com/articles/s41593-024-002",
            "published_utc": "2025-03-16T00:00:00+00:00",
            "summary": "High-density EEG and fMRI show 4-8 Hz theta synchronizes hippocampus and prefrontal cortex during episodic memory.",
        },
        {
            "id": "ccc333",
            "source": "bioRxiv",
            "title": "Spectral parameterization reveals age-related changes in neural dynamics",
            "link": "https://www.biorxiv.org/content/10.1101/2024.001",
            "published_utc": "2025-03-15T00:00:00+00:00",
            "summary": "Applying specparam to a large lifespan EEG dataset reveals systematic shifts in periodic and aperiodic components.",
        },
        {
            "id": "ddd444",
            "source": "Neuron",
            "title": "Layer-specific gamma oscillations in visual cortex encode feature binding",
            "link": "https://www.cell.com/neuron/fulltext/S0896-6273(24)00001",
            "published_utc": "2025-03-14T00:00:00+00:00",
            "summary": "Laminar recordings in macaque V1 show gamma bursts carry feature-binding information across cortical layers.",
        },
        {
            "id": "eee555",
            "source": "J Neurosci",
            "title": "Closed-loop brain stimulation modulates alpha oscillations in Parkinson's disease",
            "link": "https://www.jneurosci.org/content/45/1/123",
            "published_utc": "2025-03-13T00:00:00+00:00",
            "summary": "Real-time stimulation locked to alpha phase reduces tremor severity in a cohort of 30 PD patients.",
        },
    ]


@pytest.fixture
def sample_config() -> dict[str, Any]:
    """Minimal config dict matching config.toml structure."""
    return {
        "general": {
            "backend": "anthropic",
            "frequency": "weekly",
            "log_level": "INFO",
        },
        "fetch": {
            "max_items_per_feed": 50,
            "max_total_items": 400,
            "lookback_days": 7,
            "concurrency": 10,
        },
        "triage": {
            "batch_size": 50,
            "prefilter_keep_top": 200,
        },
        "summarize": {
            "enabled": True,
            "max_content_chars": 15000,
        },
        "output": {
            "dir": "output",
            "min_score": 0.65,
            "max_returned": 40,
        },
        "anthropic": {
            "model": "claude-opus-4-6",
            "triage_model": "claude-sonnet-4-6",
            "timeout": 300,
        },
        "ledger": {
            "enabled": True,
            "path": "seen.json",
            "prune_after_days": 90,
        },
    }


@pytest.fixture
def sample_profile() -> dict[str, Any]:
    """Minimal profile dict matching profile TOML structure."""
    return {
        "name": "Neuroscience",
        "description": "Neural oscillations and electrophysiology",
        "feeds": [
            {"name": "Nature Neuroscience", "url": "https://www.nature.com/neuro.rss"},
            {"name": "bioRxiv neuro", "url": "http://connect.biorxiv.org/biorxiv_xml.php?subject=neuroscience"},
        ],
        "interests": {
            "keywords": ["EEG", "neural oscillations", "aperiodic activity", "spectral parameterization"],
            "narrative": "My research focuses on neural dynamics using computational approaches.",
        },
        "paywalled_domains": ["nature.com", "science.org"],
        "my_work": {
            "tools": ["specparam", "fooof"],
            "paper_keywords": ["spectral parameterization", "aperiodic activity"],
        },
        "overrides": {},
        "outputs": {
            "slack": {"webhook": ""},
            "obsidian": {"vault_path": "", "subfolder": "autofeeder/neuro"},
            "email": {"recipients": []},
        },
    }


@pytest.fixture
def sample_interests() -> dict[str, Any]:
    """Interest dict for prompt-building tests."""
    return {
        "keywords": ["EEG", "neural oscillations"],
        "narrative": "I study computational approaches to neural time-series analysis, including oscillation and aperiodic methods.",
    }


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """Temporary directory for output tests."""
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture
def rss_fixture_path() -> Path:
    """Path to the RSS sample fixture file."""
    return FIXTURES_DIR / "rss_sample.xml"


@pytest.fixture
def triage_response_fixture() -> dict[str, Any]:
    """Parsed triage response fixture."""
    return json.loads((FIXTURES_DIR / "triage_response.json").read_text())


@pytest.fixture
def summary_response_fixture() -> dict[str, Any]:
    """Parsed summary response fixture."""
    return json.loads((FIXTURES_DIR / "summary_response.json").read_text())
