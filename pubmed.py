"""PubMed enrichment via NCBI E-utilities.

Looks up items on PubMed by DOI (preferred) or title, then enriches each item
with: pubmed_abstract, mesh_terms, pmcid, related_pmids, citation_count.

Rate limiting respects NCBI's 3 req/s default (10/s with NCBI_API_KEY).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any

import aiohttp

logger = logging.getLogger("autofeeder")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"


def _get_ncbi_api_key() -> str | None:
    """Get NCBI API key lazily (after .env is loaded)."""
    return os.environ.get("NCBI_API_KEY")


def _get_max_concurrent() -> int:
    """NCBI allows 3 req/s without key, 10/s with key."""
    return 3 if _get_ncbi_api_key() else 1


# Shared NCBI rate limiter — used by both pubmed.py and content.py
# to avoid conflicting concurrent requests to E-utilities
_ncbi_semaphore: asyncio.Semaphore | None = None


def get_ncbi_semaphore() -> asyncio.Semaphore:
    """Get the shared NCBI rate-limiting semaphore (lazy init)."""
    global _ncbi_semaphore
    if _ncbi_semaphore is None:
        _ncbi_semaphore = asyncio.Semaphore(_get_max_concurrent())
    return _ncbi_semaphore


async def ncbi_throttled_request(session: aiohttp.ClientSession, url: str, params: dict) -> aiohttp.ClientResponse:
    """Make a rate-limited request to NCBI E-utilities. Shared across all modules."""
    sem = get_ncbi_semaphore()
    async with sem:
        await asyncio.sleep(0.4 if _get_ncbi_api_key() else 1.0)
        api_key = _get_ncbi_api_key()
        if api_key:
            params["api_key"] = api_key
        return await session.get(url, params=params)

# Regex for DOI extraction from URLs
_DOI_RE = re.compile(
    r"(?:https?://)?(?:dx\.)?doi\.org/(10\.\d{4,9}/[^\s\"'<>;,]+)",
    re.IGNORECASE,
)
# Broader DOI pattern for non-URL DOI strings
_DOI_BARE_RE = re.compile(r"(10\.\d{4,9}/[^\s\"'<>;,]+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_doi(url: str) -> str | None:
    """Extract a DOI from a URL containing doi.org or dx.doi.org.

    Args:
        url: A URL string that may contain a DOI.

    Returns:
        The DOI string (e.g. ``10.1038/s41586-024-00001-2``) or ``None``.
    """
    if not url:
        return None
    m = _DOI_RE.search(url)
    if m:
        # Strip trailing punctuation that sometimes leaks in from HTML
        return m.group(1).rstrip(".")
    return None


def _api_params(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return base query params, appending api_key if available."""
    params: dict[str, str] = {}
    api_key = _get_ncbi_api_key()
    if api_key:
        params["api_key"] = api_key
    if extra:
        params.update(extra)
    return params


def _parse_abstract(article: ET.Element) -> str:
    """Extract abstract text from a PubMed article XML element."""
    abstract_el = article.find(".//Abstract")
    if abstract_el is None:
        return ""
    parts: list[str] = []
    for text_el in abstract_el.iter("AbstractText"):
        label = text_el.get("Label", "")
        body = "".join(text_el.itertext()).strip()
        if label and body:
            parts.append(f"{label}: {body}")
        elif body:
            parts.append(body)
    return "\n".join(parts)


def _parse_mesh_terms(article: ET.Element) -> list[str]:
    """Extract MeSH descriptor names from a PubMed article XML element."""
    terms: list[str] = []
    for heading in article.findall(".//MeshHeading/DescriptorName"):
        name = heading.text
        if name:
            terms.append(name.strip())
    return terms


def _parse_pmcid(article: ET.Element) -> str | None:
    """Extract PMCID from article-id elements."""
    for aid in article.findall(".//ArticleIdList/ArticleId"):
        if aid.get("IdType") == "pmc":
            text = (aid.text or "").strip()
            if text:
                return text
    return None


# ---------------------------------------------------------------------------
# NCBI API calls
# ---------------------------------------------------------------------------

async def _esearch(
    query: str,
    field: str | None,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> list[str]:
    """Run an ESearch query and return a list of PMIDs.

    Args:
        query: The search term (DOI or title).
        field: Optional field qualifier (e.g. ``"doi"`` or ``"Title"``).
        session: Shared aiohttp session.
        semaphore: Rate-limiting semaphore.

    Returns:
        A list of PMID strings (may be empty).
    """
    params = _api_params({
        "db": "pubmed",
        "retmode": "json",
        "retmax": "5",
        "term": f"{query}[{field}]" if field else query,
    })
    url = f"{EUTILS_BASE}esearch.fcgi"
    async with semaphore:
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("esearch returned HTTP %d for query=%r", resp.status, query)
                    return []
                data = await resp.json(content_type=None)
                return data.get("esearchresult", {}).get("idlist", [])
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("esearch failed for query=%r: %s", query, exc)
            return []


async def _efetch_pubmed(
    pmids: list[str],
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> ET.Element | None:
    """Fetch PubMed XML for the given PMIDs.

    Returns the root XML Element, or ``None`` on failure.
    """
    if not pmids:
        return None
    params = _api_params({
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "xml",
        "retmode": "xml",
    })
    url = f"{EUTILS_BASE}efetch.fcgi"
    async with semaphore:
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.warning("efetch returned HTTP %d for pmids=%s", resp.status, pmids)
                    return None
                text = await resp.text()
                return ET.fromstring(text)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("efetch failed for pmids=%s: %s", pmids, exc)
            return None
        except ET.ParseError as exc:
            logger.warning("efetch XML parse error for pmids=%s: %s", pmids, exc)
            return None


async def _elink_cited_by(
    pmid: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> tuple[list[str], int]:
    """Use ELink to get related articles and citation count.

    Returns:
        (related_pmids, citation_count) tuple.
    """
    params = _api_params({
        "dbfrom": "pubmed",
        "db": "pubmed",
        "id": pmid,
        "cmd": "neighbor",
        "retmode": "json",
    })
    url = f"{EUTILS_BASE}elink.fcgi"
    async with semaphore:
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    return [], 0
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            logger.debug("elink failed for pmid=%s: %s", pmid, exc)
            return [], 0

    related: list[str] = []
    cite_count = 0
    try:
        linksets = data.get("linksets", [])
        if not linksets:
            return [], 0
        for linksetdb in linksets[0].get("linksetdbs", []):
            link_name = linksetdb.get("linkname", "")
            link_ids = [str(lid) for lid in linksetdb.get("links", [])]
            if link_name == "pubmed_pubmed_citedin":
                cite_count = len(link_ids)
            elif link_name == "pubmed_pubmed":
                # Related articles — take top 5, excluding self
                related = [pid for pid in link_ids[:6] if pid != pmid][:5]
    except (KeyError, IndexError, TypeError):
        pass
    return related, cite_count


# ---------------------------------------------------------------------------
# Single-item enrichment
# ---------------------------------------------------------------------------

async def _enrich_one(
    item: dict[str, Any],
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> None:
    """Enrich a single item with PubMed metadata.

    Tries DOI-based search first, falls back to title search. Populates:
    ``pubmed_abstract``, ``mesh_terms``, ``pmcid``, ``related_pmids``,
    ``citation_count``.
    """
    title = item.get("title", "")
    link = item.get("link", "")

    # Rate-limit: wait between requests to stay under NCBI limits
    await asyncio.sleep(0.4 if _get_ncbi_api_key() else 1.0)

    # --- Step 1: Find PMID ---
    pmids: list[str] = []

    # DOI-first lookup
    doi = extract_doi(link)
    if doi:
        pmids = await _esearch(doi, "doi", session, semaphore)

    # Fallback: title search
    if not pmids and title:
        # Clean title for search — remove brackets, special chars
        clean_title = re.sub(r"[^\w\s]", " ", title).strip()
        if len(clean_title) > 10:
            pmids = await _esearch(clean_title, "Title", session, semaphore)

    if not pmids:
        logger.debug("No PubMed match for %r", title[:80])
        return

    pmid = pmids[0]

    # --- Step 2: Fetch full metadata ---
    root = await _efetch_pubmed([pmid], session, semaphore)
    if root is None:
        return

    article = root.find(".//PubmedArticle")
    if article is None:
        logger.debug("No PubmedArticle in efetch response for pmid=%s", pmid)
        return

    # Parse abstract
    abstract = _parse_abstract(article)
    if abstract:
        item["pubmed_abstract"] = abstract

    # Parse MeSH terms
    mesh = _parse_mesh_terms(article)
    if mesh:
        item["mesh_terms"] = mesh

    # Parse PMCID
    pmcid = _parse_pmcid(article)
    if pmcid:
        item["pmcid"] = pmcid

    # Store PMID for reference
    item["pmid"] = pmid

    # --- Step 3: Related articles + citation count ---
    related, cite_count = await _elink_cited_by(pmid, session, semaphore)
    if related:
        item["related_pmids"] = related
    item["citation_count"] = cite_count

    logger.debug(
        "Enriched %r: pmid=%s, abstract=%d chars, mesh=%d, pmcid=%s, cites=%d",
        title[:60], pmid, len(abstract), len(mesh), pmcid, cite_count,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def enrich_from_pubmed(
    items: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Enrich items with PubMed metadata (abstract, MeSH, PMCID, etc.).

    For each item, searches PubMed by DOI first, then by title. Adds fields:
    ``pubmed_abstract``, ``mesh_terms``, ``pmcid``, ``related_pmids``,
    ``citation_count``.

    Respects NCBI rate limits via semaphore (3 req/s default, 10/s with
    ``NCBI_API_KEY``).

    Args:
        items: List of item dicts from the triage stage.
        config: Global configuration dict.

    Returns:
        The same list of items, mutated in place with added PubMed fields.
    """
    if not items:
        return items

    logger.info("PubMed enrichment: processing %d items", len(items))
    semaphore = asyncio.Semaphore(_get_max_concurrent())

    async with aiohttp.ClientSession() as session:
        tasks = [_enrich_one(item, session, semaphore) for item in items]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any unexpected exceptions (items are enriched in-place)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "PubMed enrichment failed for %r: %s",
                    items[i].get("title", "")[:60],
                    result,
                )

    enriched_count = sum(1 for it in items if it.get("pmid"))
    logger.info(
        "PubMed enrichment complete: %d/%d items matched",
        enriched_count, len(items),
    )
    return items
