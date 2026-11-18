#!/bin/bash
# Reconstruct every covered match. MUST run day files in chronological order:
# each leg's book is carried across the UTC boundary in canonical/_state/books.pkl,
# because an evening-ET kickoff lands in the next UTC file.
cd "$(dirname "$0")/.."
DONE=canonical/.recon_done; BUDGET=${1:-150}; START=$(date +%s)
touch $DONE
while read -r f; do
  grep -qx "$f" $DONE && continue
  python3 pipeline/stage0_reconstruct.py "$f" || exit 1
  echo "$f" >> $DONE
  NOW=$(date +%s); [ $((NOW-START)) -ge $BUDGET ] && { echo "BUDGET REACHED"; exit 0; }
done < pipeline/order.txt
echo "RECONSTRUCTION COMPLETE"
