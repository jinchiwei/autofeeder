"""Async content extraction with a 5-step cascade.

For each item that passed triage, attempt to fetch full article text through
progressively broader strategies:

1. Unpaywall — legal OA version by DOI
2. PubMed Central — NIH-mandated OA by PMCID
3. Direct fetch — open access sites, news, blogs
4. archive.ph — paywalled news articles
5. Fallback — PubMed abstract or RSS summary

First successful extraction (>200 chars) wins.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import aiohttp
import trafilatura

from pubmed import extract_doi

logger = logging.getLogger("autofeeder")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_CONTENT_CHARS = 200

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

from pubmed import EUTILS_BASE, _get_ncbi_api_key, ncbi_throttled_request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_domain(url: str) -> str:
    """Extract the bare domain from a URL (e.g. 'nature.com')."""
    try:
        host = urlparse(url).hostname or ""
        # Strip leading 'www.'
        if host.startswith("www."):
            host = host[4:]
        return host.lower()
    except Exception:
        return ""


def _is_paywalled(url: str, paywalled_domains: list[str]) -> bool:
    """Check if a URL belongs to a known paywalled domain."""
    domain = _extract_domain(url)
    return any(domain == d or domain.endswith(f".{d}") for d in paywalled_domains)


def _extract_text_from_pmc_xml(xml_text: str) -> str | None:
    """Extract article body text from PMC full-text XML.

    Walks ``<body>`` sections and concatenates paragraph text.

    Args:
        xml_text: Raw XML string from PMC efetch.

    Returns:
        Extracted plain text, or ``None`` if parsing fails.
    """
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception as exc:
        logger.debug("PMC XML parse error: %s", exc)
        return None

    parts: list[str] = []
    # PMC XML: <article><body><sec><p>...</p></sec></body></article>
    for body in root.iter("body"):
        for p in body.iter("p"):
            text = "".join(p.itertext()).strip()
            if text:
                parts.append(text)

    return "\n\n".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Cascade steps
# ---------------------------------------------------------------------------

async def try_unpaywall(
    doi: str,
    email: str,
    session: aiohttp.ClientSession,
) -> str | None:
    """Step 1: Look up a legal OA version via Unpaywall.

    Args:
        doi: The article DOI.
        email: Email for Unpaywall API identification.
        session: Shared aiohttp session.

    Returns:
        Extracted full text, or ``None`` if unavailable.
    """
    if not doi or not email:
        return None
    url = f"https://api.unpaywall.org/v2/{doi}"
    try:
        async with session.get(
            url,
            params={"email": email},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.debug("Unpaywall returned HTTP %d for doi=%s", resp.status, doi)
                return None
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        logger.warning("Unpaywall request failed for doi=%s: %s", doi, exc)
        return None

    best_oa = data.get("best_oa_location") or {}
    oa_url = best_oa.get("url_for_pdf") or best_oa.get("url_for_landing_page")
    if not oa_url:
        logger.debug("Unpaywall: no OA location for doi=%s", doi)
        return None

    # Fetch the OA page and extract text
    try:
        async with session.get(
            oa_url,
            headers=BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                logger.debug("Unpaywall OA fetch returned HTTP %d for %s", resp.status, oa_url)
                return None
            html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("Unpaywall OA fetch failed for %s: %s", oa_url, exc)
        return None

    text = trafilatura.extract(html)
    return text if text and len(text) > MIN_CONTENT_CHARS else None


async def try_pmc(
    pmcid: str,
    session: aiohttp.ClientSession,
) -> str | None:
    """Step 2: Fetch full text from PubMed Central by PMCID.

    Args:
        pmcid: The PMC identifier (e.g. ``PMC1234567``).
        session: Shared aiohttp session.

    Returns:
        Extracted full text, or ``None`` if unavailable.
    """
    if not pmcid:
        return None
    params: dict[str, str] = {
        "db": "pmc",
        "id": pmcid,
        "rettype": "xml",
        "retmode": "xml",
    }
    url = f"{EUTILS_BASE}efetch.fcgi"
    try:
        # Use shared NCBI rate limiter to avoid 429s
        resp = await ncbi_throttled_request(session, url, params)
        if resp.status != 200:
            logger.debug("PMC efetch returned HTTP %d for pmcid=%s", resp.status, pmcid)
            return None
        xml_text = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("PMC efetch failed for pmcid=%s: %s", pmcid, exc)
        return None

    text = _extract_text_from_pmc_xml(xml_text)
    return text if text and len(text) > MIN_CONTENT_CHARS else None


async def try_direct_fetch(
    url: str,
    session: aiohttp.ClientSession,
) -> str | None:
    """Step 3: Fetch URL directly and extract text with trafilatura.

    Args:
        url: The article URL.
        session: Shared aiohttp session.

    Returns:
        Extracted full text, or ``None`` if extraction fails or is too short.
    """
    try:
        async with session.get(
            url,
            headers=BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=20),
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                logger.debug("Direct fetch returned HTTP %d for %s", resp.status, url)
                return None
            html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("Direct fetch failed for %s: %s", url, exc)
        return None

    text = trafilatura.extract(html)
    return text if text and len(text) > MIN_CONTENT_CHARS else None


async def try_archive_ph(
    url: str,
    session: aiohttp.ClientSession,
) -> str | None:
    """Step 4: Fetch an archived copy from archive.ph.

    Includes a 2-second delay for rate limiting (archive.ph has no public API).

    Args:
        url: The original article URL.
        session: Shared aiohttp session.

    Returns:
        Extracted full text, or ``None`` if no archive exists or extraction fails.
    """
    archive_url = f"https://archive.ph/newest/{url}"

    # Rate limit: be respectful of archive.ph
    await asyncio.sleep(2)

    try:
        async with session.get(
            archive_url,
            headers=BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=25),
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                logger.debug("archive.ph returned HTTP %d for %s", resp.status, url)
                return None
            html = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("archive.ph failed for %s: %s", url, exc)
        return None

    text = trafilatura.extract(html)
    return text if text and len(text) > MIN_CONTENT_CHARS else None


# ---------------------------------------------------------------------------
# Cascade runner
# ---------------------------------------------------------------------------

async def run_cascade(
    item: dict[str, Any],
    profile: dict[str, Any],
    config: dict[str, Any],
    session: aiohttp.ClientSession,
) -> None:
    """Run the 5-step content extraction cascade on a single item.

    Tries each step in order; the first to return >200 chars of text wins.
    Mutates ``item`` in place, setting ``full_text``, ``content_source``,
    and ``content_chars``.

    Args:
        item: The feed item dict.
        profile: The active profile dict (used for ``paywalled_domains``).
        config: Global configuration dict.
        session: Shared aiohttp session.
    """
    link = item.get("link", "")
    doi = extract_doi(link)
    email = config.get("summarize", {}).get("unpaywall_email", "")
    paywalled_domains: list[str] = profile.get("paywalled_domains", [])
    archive_enabled: bool = config.get("summarize", {}).get("archive_ph_enabled", True)

    # --- Step 1: Unpaywall ---
    if doi and email:
        try:
            text = await try_unpaywall(doi, email, session)
            if text:
                item["full_text"] = text
                item["content_source"] = "unpaywall"
                item["content_chars"] = len(text)
                logger.debug("Content via Unpaywall for %r", item.get("title", "")[:60])
                return
        except Exception as exc:
            logger.warning("Unpaywall error for %r: %s", link, exc)

    # --- Step 2: PMC ---
    pmcid = item.get("pmcid")
    if pmcid:
        try:
            text = await try_pmc(pmcid, session)
            if text:
                item["full_text"] = text
                item["content_source"] = "pmc"
                item["content_chars"] = len(text)
                logger.debug("Content via PMC for %r", item.get("title", "")[:60])
                return
        except Exception as exc:
            logger.warning("PMC error for %r: %s", link, exc)

    # --- Step 3: Direct fetch (skip known paywalled domains) ---
    if not _is_paywalled(link, paywalled_domains):
        try:
            text = await try_direct_fetch(link, session)
            if text:
                item["full_text"] = text
                item["content_source"] = "direct"
                item["content_chars"] = len(text)
                logger.debug("Content via direct fetch for %r", item.get("title", "")[:60])
                return
        except Exception as exc:
            logger.warning("Direct fetch error for %r: %s", link, exc)

    # --- Step 4: archive.ph ---
    if archive_enabled:
        try:
            text = await try_archive_ph(link, session)
            if text:
                item["full_text"] = text
                item["content_source"] = "archive_ph"
                item["content_chars"] = len(text)
                logger.debug("Content via archive.ph for %r", item.get("title", "")[:60])
                return
        except Exception as exc:
            logger.warning("archive.ph error for %r: %s", link, exc)

    # --- Step 5: Fallback ---
    pubmed_abstract = item.get("pubmed_abstract", "")
    rss_summary = item.get("summary", "")

    if pubmed_abstract and len(pubmed_abstract) >= len(rss_summary):
        item["full_text"] = pubmed_abstract
        item["content_source"] = "pubmed_abstract"
    elif rss_summary:
        item["full_text"] = rss_summary
        item["content_source"] = "rss_summary"
    else:
        item["full_text"] = ""
        item["content_source"] = "rss_summary"

    item["content_chars"] = len(item["full_text"])
    logger.debug(
        "Content fallback (%s, %d chars) for %r",
        item["content_source"], item["content_chars"],
        item.get("title", "")[:60],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_full_content(
    items: list[dict[str, Any]],
    profile: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch full article text for all items via the 5-step cascade.

    Processes items concurrently, bounded by a semaphore
    (``config["fetch"]["concurrency"]``, default 10).

    Args:
        items: List of item dicts (post-triage, post-PubMed enrichment).
        profile: The active profile dict.
        config: Global configuration dict.

    Returns:
        The same list of items, mutated in place with ``full_text``,
        ``content_source``, and ``content_chars`` fields.
    """
    if not items:
        return items

    concurrency = config.get("fetch", {}).get("concurrency", 10)
    semaphore = asyncio.Semaphore(concurrency)

    logger.info("Content extraction: processing %d items (concurrency=%d)", len(items), concurrency)

    async with aiohttp.ClientSession() as session:

        async def _extract_one(item: dict[str, Any]) -> None:
            async with semaphore:
                await run_cascade(item, profile, config, session)

        results = await asyncio.gather(
            *[_extract_one(item) for item in items],
            return_exceptions=True,
        )

        # Log any unexpected exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Content extraction failed for %r: %s",
                    items[i].get("title", "")[:60],
                    result,
                )
                # Ensure fallback fields are set even on exception
                if "full_text" not in items[i]:
                    items[i]["full_text"] = items[i].get("summary", "")
                    items[i]["content_source"] = "rss_summary"
                    items[i]["content_chars"] = len(items[i]["full_text"])

    # Summary stats
    sources: dict[str, int] = {}
    for item in items:
        src = item.get("content_source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    logger.info("Content extraction complete: %s", sources)

    return items
