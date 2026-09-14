#!/bin/sh
# autofeeder daily run + brain-repo digest push.
# scs is the SOLE runner as of 2026-07-02 (moved off laptop). Sched: systemd timer 00:00 PT.
set -u
AF="$HOME/arcadia/autofeeder"
PY="/data/rauschecker1/jkw/envs/autofeeder/bin/python"
BRAIN="$HOME/arcadia/jkw_obs-brain"
LOG="$AF/logs/daily.log"
mkdir -p "$AF/logs"
{
  echo "===== $(date '+%F %T %Z') autofeeder start ====="
  cd "$AF" && "$PY" autofeeder.py --all --cooldown 0
  rc=$?
  echo "----- autofeeder exit=$rc; pushing digests to brain repo -----"
  cd "$BRAIN" 2>/dev/null && {
    git pull --rebase --autostash origin main || true
    git add 臥龍/Autofeeder || true
    git commit -m "autofeeder digests $(date +%F) [scs]" || echo "(nothing to commit)"
    git push origin main || echo "(push failed; digests still local)"
  }
  echo "===== $(date '+%F %T %Z') done ====="
} >> "$LOG" 2>&1
