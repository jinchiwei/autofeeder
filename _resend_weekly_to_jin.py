#!/usr/bin/env python
"""One-off: re-send today's WEEKLY collated digests to mrjinch only.

Used to backfill Jin's inbox with the same Monday retrospective other
recipients received this morning. Goes forward, the publish() code now
includes daily_cohort in the weekly send automatically — but for one-time
catchup today, this script bypasses the cohort logic and sends only to Jin.

No re-fetch / re-triage. Just collate past-7-day sidecars + (optional)
translate + send to mrjinch@gmail.com.
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

from config import load_config  # noqa: E402
from profiles import load_profile  # noqa: E402
from outputs.email import (  # noqa: E402
    _MAX_ITEMS,
    _build_html,
    _build_html_bilingual,
    _collate_week_digest,
    _send_one,
    _translate_digest_data,
)

RECIPIENTS = ["mrjinch@gmail.com"]
TODAY = dt.date.today()
OUTPUT_DIR = ROOT / "output"

cfg = load_config("config.toml")
api_key = os.environ.get("RESEND_API_KEY", "")
if not api_key:
    print("RESEND_API_KEY not set, aborting"); sys.exit(1)

profiles = sys.argv[1:] or [
    "abc-news",
    "alzheimers",
    "neuroimaging-ai",
    "china-geopolitics",
    "frontier-ai",
    "agentic-tools",
]

for pname in profiles:
    try:
        prof = load_profile(f"profiles/{pname}.toml")
    except Exception as exc:
        print(f"[skip] {pname}: cannot load profile ({exc})")
        continue
    email_cfg = prof.get("outputs", {}).get("email", {})
    from_addr = email_cfg.get("from", "autofeeder <digest@autofeeder.dev>")

    weekly = _collate_week_digest(pname, TODAY, OUTPUT_DIR, days=7)
    if not weekly or not weekly.get("items"):
        print(f"[skip] {pname}: no items in past 7 daily sidecars")
        continue

    translate_to = email_cfg.get("translate_to") or email_cfg.get("weekly_translate_to")
    label = (
        email_cfg.get("translate_label")
        or email_cfg.get("weekly_translate_label")
        or "繁體中文"
    )

    if translate_to:
        try:
            for_xlate = {**weekly, "items": weekly["items"][:_MAX_ITEMS]}
            translated = _translate_digest_data(for_xlate, translate_to, cfg)
            html = _build_html_bilingual(weekly, translated, lang_label_native=label)
            subject = f"autofeeder: {pname} weekly — {weekly['date']} (中英)"
            print(f"[send] {pname}: bilingual, {len(weekly['items'])} items")
        except Exception as exc:
            print(f"[warn] {pname}: translation failed ({exc}); English-only")
            html = _build_html(weekly)
            subject = f"autofeeder: {pname} weekly — {weekly['date']}"
    else:
        html = _build_html(weekly)
        subject = f"autofeeder: {pname} weekly — {weekly['date']}"
        print(f"[send] {pname}: english-only, {len(weekly['items'])} items")

    _send_one(
        api_key=api_key, from_addr=from_addr, recipients=RECIPIENTS,
        subject=subject, html_body=html,
    )
    print(f"[done] {pname}")
