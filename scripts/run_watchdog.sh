#!/usr/bin/env bash
# Watchdog for long SFT runs — lean, no bloat.
# Runs scripts/train.py, watches heartbeat (log growth), kills on stall/OOM, lists checkpoints.
#
# Usage:
#   bash scripts/run_watchdog.sh --config configs/training/pilot.yaml --max-length 8192 \
#       --output-dir models/checkpoints/pilot_t3_8192 [--stall-secs 600]
#
# Must be run from repo root. Logs to <output-dir>/watchdog.log
set -uo pipefail

CONFIG=""
MAXLEN=""
OUTDIR=""
STALL_SECS=600
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --max-length) MAXLEN="$2"; shift 2 ;;
    --output-dir) OUTDIR="$2"; shift 2 ;;
    --stall-secs) STALL_SECS="$2"; shift 2 ;;
    --) shift; EXTRA_ARGS+=("$@"); break ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

if [[ -z "$CONFIG" || -z "$OUTDIR" ]]; then
  echo "Usage: $0 --config <yaml> --output-dir <dir> [--max-length N] [--stall-secs 600]"
  exit 2
fi

mkdir -p "$OUTDIR"
LOG="$OUTDIR/watchdog.log"
echo "=== Watchdog start $(date -u +%FT%TZ) ===" | tee "$LOG"
echo "config=$CONFIG max_length=${MAXLEN:-<yaml>} out=$OUTDIR stall=${STALL_SECS}s" | tee -a "$LOG"
nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv 2>&1 | tee -a "$LOG" || true
python -c "import torch; print('cuda:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU-only', '| bf16:', torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False)" 2>&1 | tee -a "$LOG" || true

CMD=(python scripts/train.py --config "$CONFIG" --output-dir "$OUTDIR")
if [[ -n "$MAXLEN" ]]; then CMD+=(--max-length "$MAXLEN"); fi
CMD+=("${EXTRA_ARGS[@]}")
echo "cmd: ${CMD[*]}" | tee -a "$LOG"

# start training in background, unbuffered
stdbuf -oL -eL "${CMD[@]}" >>"$LOG" 2>&1 &
PID=$!
echo "train pid=$PID" | tee -a "$LOG"
trap 'echo "WATCHDOG: interrupted, killing $PID" | tee -a "$LOG"; kill "$PID" 2>/dev/null || true' INT TERM

last_size=$(stat -c%s "$LOG" 2>/dev/null || stat -f%z "$LOG" 2>/dev/null || echo 0)
last_change=$(date +%s)
fails=0

while kill -0 "$PID" 2>/dev/null; do
  sleep 30
  size=$(stat -c%s "$LOG" 2>/dev/null || stat -f%z "$LOG" 2>/dev/null || echo 0)
  now=$(date +%s)
  if [[ "$size" != "$last_size" ]]; then
    last_size=$size; last_change=$now; fails=0
  fi
  idle=$((now - last_change))
  # heartbeat line every loop
  tail -n 1 "$LOG" | cut -c1-160
  nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu --format=csv,noheader 2>/dev/null || true
  # OOM / fatal keywords -> stop watching, let process exit itself
  if tail -n 5 "$LOG" | grep -qE "OutOfMemoryError|CUDA error|ValueError: Attempting to unscale"; then
    echo "WATCHDOG: fatal error detected, waiting for process to exit..." | tee -a "$LOG"
    wait "$PID"; code=$?
    echo "WATCHDOG: exited code=$code" | tee -a "$LOG"
    break
  fi
  if [[ "$idle" -ge "$STALL_SECS" ]]; then
    echo "WATCHDOG: no log growth for ${idle}s (>= $STALL_SECS) — killing $PID" | tee -a "$LOG"
    kill "$PID" 2>/dev/null || true
    sleep 10
    kill -9 "$PID" 2>/dev/null || true
    wait "$PID"; code=$?
    echo "WATCHDOG: killed, exit code=$code (stall)" | tee -a "$LOG"
    break
  fi
done

wait "$PID" 2>/dev/null; code=$?
echo "=== Watchdog end $(date -u +%FT%TZ) exit=$code ===" | tee -a "$LOG"
echo "--- tail ---"; tail -n 20 "$LOG"
echo "--- checkpoints ---"; ls -lh "$OUTDIR" || true
exit "$code"
