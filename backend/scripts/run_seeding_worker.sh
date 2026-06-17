#!/usr/bin/env bash
#
# run_seeding_worker.sh -- cron wrapper around the cold-start Seeding_Worker.
#
# Runs ONE paced pass of scripts/seeding_worker.py (run_once). The worker is
# self-pacing and resumable: it drains the persisted Topic_Frontier a bounded
# chunk at a time, never overspends the affordable YouTube quota, and stops
# cleanly on an empty backlog / per-run cap / exhausted budget. That makes it
# safe to invoke repeatedly from cron -- a run with no affordable quota simply
# stops having done nothing.
#
# This wrapper adds the operational glue cron needs:
#   * resolves the backend dir regardless of where cron invokes it from
#   * prefers the project venv (.venv) but falls back to system python3
#   * a flock single-instance lock so a slow run can never overlap the next
#     tick and double-spend quota
#   * appends timestamped output to a log file (default backend/logs/seeding_worker.log)
#
# Usage:
#   scripts/run_seeding_worker.sh            # default per-run cap (25)
#   scripts/run_seeding_worker.sh 10         # process at most 10 items this run
#
# Environment overrides:
#   SEEDING_WORKER_LOG   log file path (default: <backend>/logs/seeding_worker.log)
#
# ASCII only.
set -euo pipefail

# Resolve the backend directory (parent of this scripts/ dir), following symlinks.
SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$(cd -P "$(dirname "$SOURCE")" >/dev/null 2>&1 && pwd)"
BACKEND_DIR="$(cd -P "$SCRIPT_DIR/.." >/dev/null 2>&1 && pwd)"

cd "$BACKEND_DIR"

# Pick an interpreter: project venv first, then system python3.
if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
  PYTHON="$BACKEND_DIR/.venv/bin/python"
else
  PYTHON="$(command -v python3 || command -v python)"
fi

CAP="${1:-}"
LOG_FILE="${SEEDING_WORKER_LOG:-$BACKEND_DIR/logs/seeding_worker.log}"
mkdir -p "$(dirname "$LOG_FILE")"

LOCK_FILE="${TMPDIR:-/tmp}/edureel_seeding_worker.lock"

run() {
  echo "===== seeding_worker run @ $(date -u +%Y-%m-%dT%H:%M:%SZ) (cap=${CAP:-default}) ====="
  # shellcheck disable=SC2086
  "$PYTHON" -m scripts.seeding_worker $CAP
  echo "===== seeding_worker run complete @ $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
}

# Single-instance lock so overlapping cron ticks can never double-spend quota.
# flock is present on Linux (Render); on macOS without flock we fall through and
# run directly (local crontab ticks are minutes apart, overlap is unlikely).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "[seeding_worker] previous run still holding $LOCK_FILE; skipping this tick" >>"$LOG_FILE"
    exit 0
  fi
  run >>"$LOG_FILE" 2>&1
else
  run >>"$LOG_FILE" 2>&1
fi
