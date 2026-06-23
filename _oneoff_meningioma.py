#!/usr/bin/env python
"""One-off: weekly-style meningioma digest from archived profile.

- Loads profiles/archive/meningioma.toml
- Runs the autofeeder per-profile pipeline (fetch / triage / summarize /
  render) BUT with recipients temporarily blanked so publish() doesn't
  fire its split-cohort emails (which would send Jin both a daily AND a
  weekly email, since today is Monday).
- After the pipeline writes today's JSON sidecar, manually sends ONE
  email labeled 'meningioma weekly — <date>' to ALL recipients listed
  in the archive profile.

Profile stays in archive after this runs — not added to the daily
rotation. To re-run later, just re-execute this script.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import tomllib
from dotenv import load_dotenv  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from config import load_config  # noqa: E402
from autofeeder import run_profile  # noqa: E402
from outputs.email import _build_html, _send_one  # noqa: E402

ARCHIVE_PATH = ROOT / "profiles/archive/meningioma.toml"
TEMP_PATH = Path("/tmp/_oneoff_meningioma.toml")

api_key = os.environ.get("RESEND_API_KEY", "")
if not api_key:
    print("RESEND_API_KEY not set, aborting"); sys.exit(1)

# Step 1: parse the archive profile to grab recipients + from address
with open(ARCHIVE_PATH, "rb") as f:
    prof_dict = tomllib.load(f)
original_recipients = prof_dict["outputs"]["email"]["recipients"]
from_addr = prof_dict["outputs"]["email"]["from"]
print(f"meningioma recipients: {original_recipients}")

# Step 2: write temp profile with recipients = [] so pipeline's email step skips
raw = ARCHIVE_PATH.read_text()
# Match the recipients list (multiline, until first ] at start of indent)
modified = re.sub(
    r"recipients\s*=\s*\[[^\]]*\]",
    "recipients = []",
    raw,
    count=1,
    flags=re.DOTALL,
)
TEMP_PATH.write_text(modified)
print(f"temp profile (recipients=[]): {TEMP_PATH}")

# Step 3: run the pipeline. Writes sidecar to output/meningioma/<today>.json.
print("running meningioma pipeline (this calls the LLM for triage + summarize — ~3-8 min)...")
cfg = load_config("config.toml")
run_profile(str(TEMP_PATH), cfg)

# Step 4: load sidecar and send ONE weekly email to all recipients
today = dt.date.today()
sidecar = ROOT / f"output/meningioma/{today.isoformat()}.json"
if not sidecar.is_file():
    print(f"no sidecar at {sidecar} — pipeline may have produced no items")
    sys.exit(0)

digest_data = json.loads(sidecar.read_text(encoding="utf-8"))
n_items = len(digest_data.get("items", []))
if n_items == 0:
    print(f"sidecar has 0 items — nothing useful to send")
    sys.exit(0)

# Hint to the email rendering that this is a weekly retrospective (mostly
# cosmetic; the HTML render doesn't care about the date string format).
digest_data["date"] = f"weekly — {today.isoformat()}"

subject = f"autofeeder: meningioma weekly — {today.isoformat()}"
html = _build_html(digest_data)
print(f"sending '{subject}' to {len(original_recipients)} recipient(s)...")
_send_one(
    api_key=api_key, from_addr=from_addr, recipients=original_recipients,
    subject=subject, html_body=html,
)
print("done")
