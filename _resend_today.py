#!/usr/bin/env python
"""One-off: re-render today's existing digests with current email.py code + resend.

No re-fetch, no re-triage, no ledger touch. Uses the JSON sidecar already written
by today's run + calls outputs.email.publish() — which respects the cadence so
weekly recipients are skipped automatically when today isn't weekly_day.

Usage:
    python _resend_today.py [YYYY-MM-DD] [profile1 profile2 ...]
"""
from __future__ import annotations

import json
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
from outputs.email import publish as email_publish  # noqa: E402

import datetime as dt

cfg = load_config("config.toml")
date = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()
profiles = sys.argv[2:] if len(sys.argv) > 2 else ["abc-news", "china-geopolitics"]

for pname in profiles:
    prof = load_profile(f"profiles/{pname}.toml")
    sidecar = ROOT / f"output/{pname}/{date}.json"
    if not sidecar.is_file():
        print(f"[skip] {pname}: no sidecar at {sidecar}")
        continue
    digest_data = json.loads(sidecar.read_text(encoding="utf-8"))
    n = len(digest_data.get("items", []))
    print(f"[send] {pname}: {n} items, date={date}")
    email_publish(digest_data, prof, cfg)
    print(f"[done] {pname}")
