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
  echo "----- exporting digests to the reader (autofeeder-reader -> Cloudflare Pages) -----"
  # systemd's PATH has no nvm node; pin it, fall back to whatever is on PATH.
  NODE="$HOME/.nvm/versions/node/v23.6.0/bin/node"; [ -x "$NODE" ] || NODE="$(command -v node || true)"
  READER="$HOME/arcadia/autofeeder-reader"
  if [ -d "$READER" ] && [ -n "$NODE" ]; then
    cd "$READER" && {
      git pull --rebase --autostash origin main || true
      if "$PY" tools/export_digests.py --src "$BRAIN/臥龍/Autofeeder" && "$NODE" tools/smoke.mjs; then
        git add webapp/data || true
        git commit -m "digests $(date +%F) [scs]" || echo "(nothing to commit)"
        git push origin main || echo "(reader push failed)"
      else
        echo "(reader export or smoke failed; not pushing)"
      fi
    }
  else
    echo "(reader skipped: dir or node missing)"
  fi
  echo "===== $(date '+%F %T %Z') done ====="
} >> "$LOG" 2>&1
