#!/usr/bin/env bash
# Launch setup+training FULLY DETACHED on a Molab GPU box (survives SSH disconnect).
# Run ON the box, wrapped in setsid (see below). Idempotent-ish: safe to rerun,
# it refuses if train.pid is alive.
#
#   setsid bash scripts/remote_launch.sh <config> <output-dir> [max-length] < /dev/null > /dev/null 2>&1 &
#
# Logs: <output-dir>/setup.log + <output-dir>/train.log, pid in <output-dir>/train.pid
set -uo pipefail

CONFIG="${1:?usage: remote_launch.sh <config> <output-dir> [max-length]}"
OUTDIR="${2:?usage: remote_launch.sh <config> <output-dir> [max-length]}"
MAXLEN="${3:-}"

mkdir -p "$OUTDIR"
if [[ -f "$OUTDIR/train.pid" ]] && kill -0 "$(cat "$OUTDIR/train.pid")" 2>/dev/null; then
  echo "already running pid=$(cat "$OUTDIR/train.pid"), NOT launching"
  exit 0
fi

{
  echo "=== setup $(date -u +%FT%TZ) ==="
  if ! bash scripts/colab_setup.sh > "$OUTDIR/setup.log" 2>&1; then
    echo "SETUP_FAILED, see $OUTDIR/setup.log"
    exit 1
  fi
  echo "=== train $(date -u +%FT%TZ) ==="
  # shellcheck disable=SC2086
  exec python scripts/train.py --config "$CONFIG" --output-dir "$OUTDIR" ${MAXLEN:+--max-length $MAXLEN} \
    >> "$OUTDIR/train.log" 2>&1
} &
echo $! > "$OUTDIR/train.pid"
echo "launched pid=$(cat "$OUTDIR/train.pid") setup=$OUTDIR/setup.log train=$OUTDIR/train.log"
