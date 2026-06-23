#!/usr/bin/env python
"""One-off: re-send Monday 2026-05-25's WEEKLY bilingual (中英) digests.

Backfill for the run where 2/3 Chinese digests fell back to English-only after
a transient Versa 504 in one batch. Re-collates the same 7-day window
(2026-05-19..2026-05-25), translates with the new batch=1 + retry path, and
sends bilingual to each profile's configured recipients.

EXCLUSION (this run only, config untouched): wenchi.wei@gmail.com is dropped
from every recipient list for this catch-up send.
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

REF_DATE = dt.date(2026, 5, 25)            # Monday's weekly window anchor
EXCLUDE = {"wenchi.wei@gmail.com"}          # this run only
OUTPUT_DIR = ROOT / "output"
PROFILES = ["china-geopolitics", "venus"]  # abc-news already sent successfully

cfg = load_config("config.toml")
api_key = os.environ.get("RESEND_API_KEY", "")
if not api_key:
    print("RESEND_API_KEY not set, aborting"); sys.exit(1)

for pname in PROFILES:
    try:
        prof = load_profile(f"profiles/{pname}.toml")
    except Exception as exc:
        print(f"[skip] {pname}: cannot load profile ({exc})")
        continue
    email_cfg = prof.get("outputs", {}).get("email", {})
    from_addr = email_cfg.get("from", "autofeeder <digest@autofeeder.dev>")

    recipients = [r for r in email_cfg.get("recipients", []) if r.lower() not in EXCLUDE]
    if not recipients:
        print(f"[skip] {pname}: no recipients left after exclusion")
        continue

    weekly = _collate_week_digest(pname, REF_DATE, OUTPUT_DIR, days=7)
    if not weekly or not weekly.get("items"):
        print(f"[skip] {pname}: no items in 7-day window ending {REF_DATE}")
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
            print(f"[send] {pname}: bilingual, {len(weekly['items'])} items -> {recipients}")
        except Exception as exc:
            print(f"[ABORT] {pname}: translation STILL failed ({exc}); NOT sending English-only")
            continue
    else:
        print(f"[skip] {pname}: profile has no translate_to (not a Chinese digest)")
        continue

    _send_one(
        api_key=api_key, from_addr=from_addr, recipients=recipients,
        subject=subject, html_body=html,
    )
    print(f"[done] {pname}")
