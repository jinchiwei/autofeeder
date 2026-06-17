#!/usr/bin/env python
"""One-off: force-send the MISSED Monday 2026-06-15 weekly digests.

The Monday run hung (feedparser had no timeout), so the weekly never sent for
any profile except nanomedicine-tcm. It's now mid-week, so the normal
weekly-day gate skips it. This sends each profile's weekly (collated from the
past-7-day sidecars ending Mon 2026-06-15, translated where bilingual) to its
full Monday weekly recipient set (daily_cohort + weekly_cohort, deduped).
nanomedicine-tcm is excluded (its weekly already sent Monday night).
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

from dotenv import load_dotenv  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from config import load_config, merge_config  # noqa: E402
from profiles import load_profile  # noqa: E402
from outputs.email import (  # noqa: E402
    _MAX_ITEMS,
    _build_html,
    _build_html_bilingual,
    _collate_week_digest,
    _send_one,
    _split_cohorts,
    _translate_digest_data,
)

REF = dt.date(2026, 6, 15)          # the missed Monday
OUTPUT_DIR = ROOT / "output"
PROFILES = [
    "china-geopolitics", "venus", "abc-news", "homecare-robotics",
    "stem-cell-therapy", "alzheimers", "agentic-tools",
    "neuroimaging-ai", "frontier-ai",
]

g = load_config("config.toml")
api_key = os.environ.get("RESEND_API_KEY", "")
if not api_key:
    print("RESEND_API_KEY not set, aborting"); sys.exit(1)

for pname in PROFILES:
    try:
        prof = load_profile(f"profiles/{pname}.toml")
    except Exception as exc:
        print(f"[skip] {pname}: cannot load ({exc})"); continue
    ec = prof.get("outputs", {}).get("email", {})
    from_addr = ec.get("from", "autofeeder <digest@autofeeder.dev>")
    pcfg = merge_config(g, prof.get("overrides", {}))
    daily_c, weekly_c, _ = _split_cohorts(ec.get("recipients", []), pcfg, today=REF)
    recipients = list(dict.fromkeys(daily_c + weekly_c))
    if not recipients:
        print(f"[skip] {pname}: no recipients"); continue

    weekly = _collate_week_digest(pname, REF, OUTPUT_DIR, days=7)
    if not weekly or not weekly.get("items"):
        print(f"[skip] {pname}: no items in week ending {REF}"); continue

    translate_to = ec.get("translate_to") or ec.get("weekly_translate_to")
    label = ec.get("translate_label") or ec.get("weekly_translate_label") or "繁體中文"

    if translate_to:
        try:
            for_x = {**weekly, "items": weekly["items"][:_MAX_ITEMS]}
            tr = _translate_digest_data(for_x, translate_to, g)
            html = _build_html_bilingual(weekly, tr, lang_label_native=label)
            subject = f"autofeeder: {pname} weekly — {weekly['date']} (中英)"
            print(f"[send] {pname}: bilingual, {len(weekly['items'])} items -> {recipients}")
        except Exception as exc:
            print(f"[warn] {pname}: translation failed ({str(exc)[:70]}); ENGLISH-ONLY fallback")
            html = _build_html(weekly)
            subject = f"autofeeder: {pname} weekly — {weekly['date']}"
    else:
        html = _build_html(weekly)
        subject = f"autofeeder: {pname} weekly — {weekly['date']}"
        print(f"[send] {pname}: english, {len(weekly['items'])} items -> {recipients}")

    _send_one(api_key=api_key, from_addr=from_addr, recipients=recipients,
              subject=subject, html_body=html)
    print(f"[done] {pname}")

print("=== weekly catchup complete ===")
