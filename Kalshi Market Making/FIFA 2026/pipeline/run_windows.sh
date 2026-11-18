#!/bin/bash
cd "$(dirname "$0")/.."
OUT=canonical/trade_minutes.csv
BUDGET=${1:-150}; START=$(date +%s)
for f in kalshi-2026-*.jsonl.gz kalshi-2026-*.jsonl; do
  [ -e "$f" ] || continue
  if [[ "$f" == *.jsonl ]] && [ -e "$f.gz" ]; then continue; fi
  if [ -e "$OUT" ] && grep -q "^$f," "$OUT"; then continue; fi
  if [ -e canonical/.windows_done ] && grep -qx "$f" canonical/.windows_done; then continue; fi
  python3 pipeline/stage0_windows.py "$f" "$OUT"
  echo "$f" >> canonical/.windows_done
  NOW=$(date +%s); [ $((NOW-START)) -ge $BUDGET ] && { echo "BUDGET REACHED"; exit 0; }
done
echo "WINDOWS COMPLETE"
