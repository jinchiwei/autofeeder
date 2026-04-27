#!/usr/bin/env python3
"""autofeeder — aggregate, score, summarize, and deliver research feeds.

Pipeline:
  1. Load config + profile
  2. Fetch RSS feeds
  3. Keyword prefilter (cheap, local)
  4. Ledger filter (skip previously scored)
  5. LLM triage (Pass 1 — score + rank)
  6. PubMed enrichment (abstract, MeSH, PMCID)
  7. Content extraction (Unpaywall → PMC → direct → archive.ph → fallback)
  8. "Builds on your work" detector
  9. LLM summarize (Pass 2 — per-item key takeaways)
 10. Render + publish (markdown, Slack, Obsidian, email)
 11. Update ledger + feed health
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from glob import glob
from pathlib import Path

from config import load_config, merge_config
from profiles import load_profile
from fetch import fetch_items
from prefilter import prefilter
from ledger import ledger_filter, ledger_update, reset_profile
from feed_health import load_health, record_fetch, analyze_health, save_health, format_health_report
from backends import get_backend
from pubmed import enrich_from_pubmed
from content import fetch_full_content
from cross_profile import find_paper_of_the_week, find_crossover_papers
from outputs import publish_all

logger = logging.getLogger("autofeeder")


def detect_builds_on_your_work(items: list[dict], profile: dict) -> None:
    """Scan full text for references to user's publications/tools."""
    my_work = profile.get("my_work", {})
    tools = [t.lower() for t in my_work.get("tools", [])]
    paper_kws = [k.lower() for k in my_work.get("paper_keywords", [])]
    all_kws = tools + paper_kws

    if not all_kws:
        return

    count = 0
    for item in items:
        text = (item.get("full_text") or item.get("summary") or "").lower()
        title = (item.get("title") or "").lower()
        combined = title + " " + text
        if any(kw in combined for kw in all_kws):
            item["cites_your_work"] = True
            count += 1
        else:
            item["cites_your_work"] = False

    if count:
        logger.info("Found %d papers referencing your work", count)


def build_digest_data(
    profile: dict,
    items: list[dict],
    config: dict,
    feed_health_analysis: dict | None = None,
    tldr: str = "",
    is_first_run: bool = False,
) -> dict:
    """Build the digest data structure consumed by output plugins."""
    from datetime import date

    return {
        "profile_name": profile.get("name", "unknown"),
        "profile_description": profile.get("description", ""),
        "date": date.today().isoformat(),
        "total_scored": len(items),
        "min_score": config["output"]["min_score"],
        "items": items,
        "feed_health": feed_health_analysis,
        "tldr": tldr,
        "is_first_run": is_first_run,
    }


async def run_profile_async(profile_path: str, config: dict) -> dict | None:
    """Run the full pipeline for a single profile. Returns triage result for cross-profile."""
    profile = load_profile(profile_path)
    profile_name = profile.get("name", Path(profile_path).stem)
    logger.info("=" * 60)
    logger.info("Profile: %s", profile_name)
    logger.info("=" * 60)

    # Merge profile overrides into config
    pconfig = merge_config(config, profile.get("overrides", {}))

    # --- Stage 1: Fetch RSS ---
    start = time.time()
    feeds = profile.get("feeds", [])
    health = load_health(pconfig.get("feed_health_path", "feed_health.json"))

    items = fetch_items(feeds, pconfig)
    fetched_count = len(items)
    # Record feed health — match by URL substring in item links since source names may differ
    feed_urls_seen = set()
    for feed_info in feeds:
        url = feed_info["url"]
        # Count items whose link contains the feed's domain
        from urllib.parse import urlparse
        feed_domain = urlparse(url).netloc
        feed_item_count = sum(1 for it in items if feed_domain and feed_domain in it.get("link", ""))
        has_items = feed_item_count > 0
        record_fetch(health, url, feed_item_count, has_items)
    logger.info("Fetched %d items from %d feeds (%.1fs)", fetched_count, len(feeds), time.time() - start)

    if not items:
        logger.warning("No items found — writing empty digest")
        digest_data = build_digest_data(profile, [], pconfig)
        publish_all(digest_data, profile, pconfig)
        return None

    # --- Stage 2: Keyword prefilter ---
    keywords = profile.get("interests", {}).get("keywords", [])
    keep_top = pconfig["triage"]["prefilter_keep_top"]
    items = prefilter(items, keywords, keep_top)
    prefiltered_count = len(items)
    logger.info("After prefilter: %d items", prefiltered_count)

    # --- Stage 3: Ledger filter ---
    ledger_path = pconfig["ledger"]["path"]
    is_first_run = not os.path.exists(ledger_path)
    items = ledger_filter(items, pconfig)
    new_count = sum(1 for it in items if it.get("is_new", True))
    logger.info("After ledger: %d items (%d new)", len(items), new_count)
    if is_first_run:
        # First run: suppress NEW badges (everything is new)
        for it in items:
            it["is_new"] = False
        logger.info("First run detected — suppressing NEW badges")

    if not items:
        logger.info("All items previously seen — writing empty digest")
        digest_data = build_digest_data(profile, [], pconfig)
        publish_all(digest_data, profile, pconfig)
        return None

    # --- Stage 4: LLM Triage (Pass 1) — parallel batches ---
    backend = get_backend(pconfig)
    triage_fn = backend["triage_fn"]
    interests = profile.get("interests", {})

    batch_size = pconfig["triage"]["batch_size"]
    triage_concurrency = pconfig["triage"].get("concurrency", 4)
    all_ranked = []
    notes_parts = []

    batches = [items[i : i + batch_size] for i in range(0, len(items), batch_size)]
    total_batches = len(batches)
    logger.info("Triage: %d batches of %d items (concurrency=%d)", total_batches, batch_size, triage_concurrency)

    triage_sem = asyncio.Semaphore(triage_concurrency)
    triage_start = time.time()

    async def _run_triage_batch(batch_num: int, batch: list[dict]) -> dict | None:
        async with triage_sem:
            try:
                return await triage_fn(interests, batch)
            except Exception:
                # Retry with smaller sub-batches before giving up
                if len(batch) > 5:
                    logger.warning("Triage batch %d/%d failed, retrying in sub-batches of 5", batch_num, total_batches)
                    combined = {"ranked": [], "notes": ""}
                    for i in range(0, len(batch), 5):
                        sub = batch[i:i + 5]
                        try:
                            result = await triage_fn(interests, sub)
                            combined["ranked"].extend(result.get("ranked", []))
                            if result.get("notes", "").strip():
                                combined["notes"] += " " + result["notes"].strip()
                        except Exception:
                            # Last resort: try each item individually
                            logger.warning("  sub-batch %d-%d failed, trying individually", i + 1, min(i + 5, len(batch)))
                            for j, item in enumerate(sub):
                                try:
                                    result = await triage_fn(interests, [item])
                                    combined["ranked"].extend(result.get("ranked", []))
                                except Exception:
                                    logger.warning("    item '%s' failed, skipping", item.get("title", "?")[:60])
                    return combined if combined["ranked"] else None
                else:
                    logger.warning("Triage batch %d/%d failed, skipping %d items", batch_num, total_batches, len(batch))
                    return None

    triage_results = await asyncio.gather(
        *[_run_triage_batch(i + 1, b) for i, b in enumerate(batches)],
        return_exceptions=True,
    )

    failures = 0
    for result in triage_results:
        if isinstance(result, Exception):
            logger.error("Triage batch raised: %s", result)
            failures += 1
        elif result is None:
            failures += 1
        else:
            if result.get("notes", "").strip():
                notes_parts.append(result["notes"].strip())
            all_ranked.extend(result.get("ranked", []))

    logger.info("Triage: %d batches in %.1fs (%d concurrent, %d failures)",
                total_batches, time.time() - triage_start, triage_concurrency, failures)

    # Merge: best score per item ID, filter hallucinated IDs
    valid_ids = {it["id"] for it in items}
    best = {}
    for r in all_ranked:
        rid = r.get("id", "")
        if rid not in valid_ids:
            logger.warning("Filtered hallucinated ID: %s", rid)
            continue
        if rid not in best or r.get("score", 0) > best[rid].get("score", 0):
            best[rid] = r

    ranked = sorted(best.values(), key=lambda x: x.get("score", 0), reverse=True)
    min_score = pconfig["output"]["min_score"]
    max_returned = pconfig["output"]["max_returned"]
    top_items = [r for r in ranked if r.get("score", 0) >= min_score][:max_returned]

    logger.info(
        "Triage complete: %d scored, %d above threshold (%.2f), keeping top %d",
        len(ranked), len(top_items), min_score, len(top_items),
    )

    if not top_items:
        logger.info("No items above threshold")
        digest_data = build_digest_data(profile, [], pconfig)
        publish_all(digest_data, profile, pconfig)
        # Still update ledger with all scored items
        ledger_update({"ranked": ranked, "profile_name": profile_name}, pconfig)
        return {"ranked": ranked, "notes": " ".join(notes_parts)}

    # Merge original item data into ranked results — original data is authoritative
    # for core fields (title, link, source, etc.) since LLM may truncate or omit them
    items_by_id = {it["id"]: it for it in items}
    for item in top_items:
        original = items_by_id.get(item["id"], {})
        # Always prefer original for these fields (LLM output may be partial)
        for key in ("title", "link", "source", "published_utc"):
            if key in original and original[key]:
                item[key] = original[key]
        # Only fill in if missing
        for key in ("summary", "is_new"):
            if key in original and key not in item:
                item[key] = original[key]

    # --- Stage 5: PubMed enrichment ---
    logger.info("Enriching %d items from PubMed", len(top_items))
    await enrich_from_pubmed(top_items, pconfig)

    # --- Stage 6: Content extraction ---
    logger.info("Extracting content for %d items", len(top_items))
    await fetch_full_content(top_items, profile, pconfig)

    sources = {}
    for it in top_items:
        src = it.get("content_source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    logger.info("Content sources: %s", ", ".join(f"{k}={v}" for k, v in sorted(sources.items())))

    # --- Stage 7: Builds on your work ---
    detect_builds_on_your_work(top_items, profile)

    # --- Stage 8: LLM Summarize (Pass 2) — parallel per-item ---
    if pconfig["summarize"]["enabled"]:
        summarize_fn = backend["summarize_fn"]
        summarize_concurrency = pconfig["summarize"].get("concurrency", 3)
        summarize_sem = asyncio.Semaphore(summarize_concurrency)
        summarize_start = time.time()
        logger.info("Summarizing %d items (concurrency=%d)", len(top_items), summarize_concurrency)

        async def _run_summarize(item: dict) -> dict | None:
            async with summarize_sem:
                try:
                    return await summarize_fn(interests, item)
                except Exception:
                    logger.exception("Summarize failed: %s", item.get("title", "?")[:60])
                    return None

        summarize_results = await asyncio.gather(
            *[_run_summarize(item) for item in top_items],
            return_exceptions=True,
        )

        success = 0
        for item, result in zip(top_items, summarize_results):
            if isinstance(result, Exception):
                logger.error("Summarize raised: %s for %s", result, item.get("title", "?")[:60])
                item["headline"] = None
                item["key_takeaways"] = None
                item["relevance"] = None
            elif result is None:
                item["headline"] = None
                item["key_takeaways"] = None
                item["relevance"] = None
            else:
                item["headline"] = result.get("headline")
                item["key_takeaways"] = result.get("key_takeaways")
                item["relevance"] = result.get("relevance")
                if result.get("tags"):
                    item["tags"] = result["tags"]
                success += 1

        logger.info("Summarize: %d/%d items in %.1fs (%d concurrent)",
                    success, len(top_items), time.time() - summarize_start, summarize_concurrency)

    # --- Stage 9: Content source indicators ---
    _CONTENT_SOURCE_LABELS = {
        "unpaywall": "📄 Full text via Unpaywall",
        "pmc": "📄 Full text via PubMed Central",
        "direct": "📄 Full text (direct access)",
        "archive_ph": "📄 Full text via archive.ph",
        "pubmed_abstract": "⚠️ Abstract only (PubMed)",
        "rss_summary": "⚠️ Summary only — full text unavailable",
    }
    for item in top_items:
        src = item.get("content_source", "rss_summary")
        item["content_source_label"] = _CONTENT_SOURCE_LABELS.get(src, f"⚠️ {src}")

    # --- Stage 10: TL;DR generation ---
    tldr = ""
    if pconfig["summarize"]["enabled"] and top_items:
        tldr_fn = backend.get("tldr_fn")
        if tldr_fn:
            try:
                logger.info("Generating TL;DR overview")
                tldr = await tldr_fn(interests, top_items)
                logger.info("TL;DR generated (%d chars)", len(tldr))
            except Exception:
                logger.exception("TL;DR generation failed — continuing without it")

    # --- Stage 11: Render + publish ---
    feed_analysis = analyze_health(health) if health else None
    digest_data = build_digest_data(
        profile, top_items, pconfig, feed_analysis,
        tldr=tldr, is_first_run=is_first_run,
    )
    publish_all(digest_data, profile, pconfig)

    # --- Stage 12: Update ledger + feed health ---
    ledger_update({"ranked": ranked}, pconfig)
    save_health(health, pconfig.get("feed_health_path", "feed_health.json"))

    # Run summary
    logger.info("─" * 40)
    logger.info("Run complete: %s", profile_name)
    logger.info("  Items: %d fetched → %d prefiltered → %d new → %d above threshold",
                fetched_count, prefiltered_count, new_count, len(top_items))
    logger.info("  Content: %s", ", ".join(f"{v} {k}" for k, v in sorted(sources.items())))
    cites = sum(1 for it in top_items if it.get("cites_your_work"))
    if cites:
        logger.info("  Builds on your work: %d papers", cites)
    if feed_analysis:
        logger.info("  Feed health: %s", format_health_report(feed_analysis))
    logger.info("─" * 40)

    return {"ranked": ranked, "notes": " ".join(notes_parts)}


def _write_paper_of_the_week(potw: dict, config: dict) -> None:
    """Write Paper of the Week to a dated markdown file."""
    from datetime import date

    output_dir = Path(config.get("output", {}).get("dir", "output")) / "paper-of-the-week"
    output_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    out_path = output_dir / f"{today}.md"

    title = potw.get("title", "Untitled")
    link = potw.get("link", "")
    source = potw.get("source", "Unknown")
    score = potw.get("score", 0.0)
    profiles = potw.get("profiles", [])
    why = potw.get("why", "")
    headline = potw.get("headline", "")
    key_takeaways = potw.get("key_takeaways", [])
    relevance = potw.get("relevance", "")
    tags = potw.get("tags", [])

    lines = [
        f"# Paper of the Week ({today})",
        "",
        f"## [{title}]({link})" if link else f"## {title}",
        f"*{source}* · Score: **{score:.2f}** · Appeared in: {', '.join(profiles)}",
        "",
    ]

    if headline:
        lines += [f"> {headline}", ""]

    if key_takeaways:
        lines += ["**Key takeaways:**"]
        for t in key_takeaways:
            lines.append(f"- {t}")
        lines.append("")

    if relevance:
        lines += [f"**Why this matters:** {relevance}", ""]

    if why and not relevance:
        lines += [f"**Why:** {why}", ""]

    if tags:
        lines += ["Tags: " + " ".join(f"`{t}`" for t in tags), ""]

    lines += ["---", f"Selected by autofeeder from {len(profiles)} profile(s)"]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Paper of the Week written to %s", out_path)


def run_profile(profile_path: str, config: dict) -> dict | None:
    """Sync wrapper for run_profile_async."""
    return asyncio.run(run_profile_async(profile_path, config))


async def run_all_profiles_async(profile_paths: list[str], config: dict) -> list[tuple[str, dict]]:
    """Run all profiles concurrently in a single event loop.

    Profiles with ``enabled = false`` are skipped.
    """
    # Filter out disabled profiles
    active_paths = []
    for p in profile_paths:
        try:
            prof = load_profile(p)
            if prof.get("enabled", True) is False:
                logger.info("Skipping disabled profile: %s", Path(p).stem)
                continue
        except Exception:
            logger.warning("Could not read profile %s, skipping", p)
            continue
        active_paths.append(p)

    if not active_paths:
        logger.error("No enabled profiles found")
        return []

    profile_paths = active_paths
    logger.info("Running %d profiles in parallel", len(profile_paths))

    async def _safe_run(path: str) -> tuple[str, dict | None]:
        name = Path(path).stem
        try:
            result = await run_profile_async(path, config)
            return (name, result)
        except Exception:
            logger.exception("Profile %s failed", name)
            return (name, None)

    results = await asyncio.gather(*[_safe_run(p) for p in profile_paths])
    return [(name, result) for name, result in results if result is not None]


def _sync_output_to_vault(config: dict) -> None:
    """Mirror the output/ directory into an Obsidian vault subfolder.

    Reads ``[sync]`` from config. Silently skipped when ``vault_path`` is empty,
    the vault path doesn't exist, or there is no output dir to sync.
    """
    import shutil

    sync_cfg = config.get("sync", {})
    vault_path_raw = sync_cfg.get("vault_path", "")
    if not vault_path_raw:
        logger.debug("Vault sync skipped — [sync].vault_path is empty")
        return

    vault_path = Path(vault_path_raw).expanduser()
    if not vault_path.is_dir():
        logger.warning("Vault sync skipped — path does not exist: %s", vault_path)
        return

    output_dir = Path(config.get("output", {}).get("dir", "output"))
    if not output_dir.is_dir():
        logger.debug("Vault sync skipped — no output/ directory")
        return

    subfolder = sync_cfg.get("subfolder", "autofeeder")
    target_dir = vault_path / subfolder
    target_dir.mkdir(parents=True, exist_ok=True)

    def _skip(_src, names):
        return [n for n in names if n == ".DS_Store"]

    copied = failed = 0
    for src in output_dir.iterdir():
        if src.name.startswith("."):
            continue
        dst = target_dir / src.name
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_skip)
            else:
                shutil.copy2(src, dst)
            copied += 1
        except OSError as exc:
            logger.warning("Sync: failed to copy %s → %s: %s", src, dst, exc)
            failed += 1

    logger.info("Synced output/ → %s (%d ok, %d failed)", target_dir, copied, failed)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="autofeeder — aggregate, score, summarize, and deliver research feeds",
    )
    parser.add_argument("--profile", "-p", help="Profile name (without .toml extension)")
    parser.add_argument("--all", "-a", action="store_true", help="Run all profiles in profiles/")
    parser.add_argument("--cooldown", type=int, default=7, help="Skip profiles run within N days (default: 7, 0 to disable)")
    parser.add_argument("--diff-only", action="store_true", help="Only show new items since last run")
    parser.add_argument("--discover", "-d", help="Discover RSS feeds for a topic (e.g., 'Alzheimer EEG')")
    parser.add_argument("--discover-name", help="Profile name for discovered feeds (default: derived from topic)")
    parser.add_argument("--setup", action="store_true", help="Run the interactive setup wizard")
    parser.add_argument("--config", default="config.toml", help="Path to config file")
    parser.add_argument("--reset", help="Reset a profile: clear its seen items and output, then re-run")
    parser.add_argument("--log-level", help="Override log level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Setup logging
    level = args.log_level or config["general"]["log_level"]
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # --- Setup wizard ---
    if args.setup:
        from setup_wizard import run_setup
        run_setup()
        return

    # Default to --all if no mode specified
    if not args.profile and not args.all and not args.discover and not args.setup and not args.reset:
        args.all = True

    # --- Reset mode ---
    if args.reset:
        import shutil
        profile_name = args.reset
        reset_profile(profile_name, config)
        output_dir = Path(config.get("output", {}).get("dir", "output")) / profile_name
        if output_dir.exists():
            shutil.rmtree(output_dir)
            logger.info("Removed output directory: %s", output_dir)
        # Set profile to run after reset
        args.profile = profile_name
        logger.info("Profile '%s' reset — will re-run now", profile_name)

    if args.diff_only:
        # Force ledger filtering — items already seen will be excluded
        config["ledger"]["enabled"] = True

    start_time = time.time()

    # --- Discover mode ---
    if args.discover:
        from discover import discover_feeds_sync, save_discovered_profile
        import re

        topic = args.discover
        logger.info("Discovering feeds for: %s", topic)

        result = discover_feeds_sync(topic, config)
        print("\n" + result + "\n")

        # Derive profile name from topic if not specified
        profile_name = args.discover_name
        if not profile_name:
            profile_name = re.sub(r"[^\w\s-]", "", topic.lower())
            profile_name = re.sub(r"[\s]+", "-", profile_name).strip("-")[:40]

        path = save_discovered_profile(topic, result, profile_name)
        logger.info("Profile saved to %s — review and edit before running!", path)
        print(f"\nProfile saved to: {path}")
        print("Review the file and edit feeds/keywords before running:")
        print(f"  python autofeeder.py --profile {profile_name}")
        return

    if args.all:
        profiles = sorted(glob("profiles/*.toml"))
        if not profiles:
            logger.error("No profiles found in profiles/")
            sys.exit(1)

        # Cooldown: skip profiles that ran within --cooldown days
        if args.cooldown > 0:
            output_dir = Path(config.get("output", {}).get("dir", "output"))
            cutoff = datetime.now() - timedelta(days=args.cooldown)
            filtered = []
            for p in profiles:
                name = Path(p).stem
                profile_dir = output_dir / name
                if profile_dir.exists():
                    outputs = sorted(profile_dir.glob("*.md"), reverse=True)
                    if outputs:
                        try:
                            last_date = datetime.strptime(outputs[0].stem, "%Y-%m-%d")
                            if last_date >= cutoff:
                                logger.info("Skipping %s — last run %s (within %d-day cooldown)",
                                            name, outputs[0].stem, args.cooldown)
                                continue
                        except ValueError:
                            pass
                filtered.append(p)
            profiles = filtered
            if not profiles:
                logger.info("All profiles within cooldown period, nothing to run")
                return

        all_results = asyncio.run(run_all_profiles_async(profiles, config))

        # Cross-profile aggregation
        if len(all_results) > 1:
            potw = find_paper_of_the_week(all_results)
            if potw:
                logger.info("Paper of the Week: %s (score %.2f across %s)",
                            potw["title"], potw["score"], ", ".join(potw["profiles"]))
                _write_paper_of_the_week(potw, config)

            crossovers = find_crossover_papers(all_results)
            if crossovers:
                logger.info("%d crossover papers found across profiles", len(crossovers))
    else:
        if not args.profile:
            parser.error("Specify --profile <name> or --all")

        profile_path = f"profiles/{args.profile}.toml"
        if not os.path.exists(profile_path):
            logger.error("Profile not found: %s", profile_path)
            sys.exit(1)

        run_profile(profile_path, config)

    _sync_output_to_vault(config)

    elapsed = time.time() - start_time
    logger.info("Total time: %.1fs", elapsed)


if __name__ == "__main__":
    main()
