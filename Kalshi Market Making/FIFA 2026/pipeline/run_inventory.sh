#!/bin/bash
# Resumable, time-budgeted inventory scan. Skips files already in inventory.csv,
# stops after BUDGET seconds so each remote call returns before its timeout.
cd "$(dirname "$0")/.."
OUT=canonical/inventory.csv
BUDGET=${1:-150}
START=$(date +%s)
for f in kalshi-2026-*.jsonl.gz kalshi-2026-*.jsonl; do
  [ -e "$f" ] || continue
  # one scan per day: prefer the .gz, skip the uncompressed twin
  if [[ "$f" == *.jsonl ]] && [ -e "$f.gz" ]; then continue; fi
  if [ -e "$OUT" ] && grep -q "^$f," "$OUT"; then continue; fi
  python3 pipeline/stage0_inventory.py "$f" "$OUT"
  NOW=$(date +%s)
  if [ $((NOW-START)) -ge $BUDGET ]; then echo "BUDGET REACHED after $((NOW-START))s"; exit 0; fi
done
echo "INVENTORY COMPLETE"
