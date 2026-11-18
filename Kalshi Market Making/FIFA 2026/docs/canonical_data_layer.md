# Canonical data layer — Kalshi FIFA World Cup 2026 game markets

Built once from the raw tapes. **No experiment should ever open a raw `.jsonl[.gz]`
again.** Read data only through `pipeline/canon_load.py`. If a number looks wrong, it
is wrong here, and it gets fixed here once — for every experiment at the same time.

## Start here: three trees, three tiers

The original build had a defect: books were reconstructed from deltas starting from
an **empty** book, so size resting before the recorder's first message was invisible
and the deep book was close to worthless. It was repaired by seeding each book from
the last populated snapshot in the tapes, then verified leg by leg against
Predexon's independent reconstruction (H10 in `../hypotheses.md`). The data now sits
in three trees, one per quality tier:

| tier | matches | tree | ladder agreement vs Predexon | supports |
|---|---|---|---|---|
| **depth_clean** | **27** | `books_v2/`, `raw_v2/` | **99.91%** median, every leg > 99.5%, deep book 100% | everything: prices, sizes, queue, depth, flow |
| **price_flow_only** | 9 | `books_priceonly/`, `raw_priceonly/` | 27.6% (touch 76.9%, deep 4.6%) | prices, trades, raw-delta flow decomposition. **Not** queue size or depth |
| **gaps** | 4 | `books_gaps/`, `raw_gaps/` | 20.3% (touch 74.1%) | nothing safely — dropped messages, so the deltas themselves are incomplete |

- **price_flow_only** sits behind the June 17–21 recording hole, so no clean seed
  exists. Seeding across the hole would make sizes wrong in both directions; an empty
  start keeps them a one-directional lower bound (never too large).
- **gaps** = `26JUN12CANBIH, 26JUN22FRAIRQ, 26JUN22JORDZA, 26JUN23COLCOD`. A single
  dropped message leaves thousands of stale levels. `seq_gaps` in the manifest is the
  detector; it is zero for all 27 clean matches.
- `books/` and `raw/` are the **original 47-match build, untouched**. Not for analysis.
- Per-leg tier, seq gaps and measured agreement: `leg_quality.csv`.

```bash
CANON_SUFFIX=_v2        python3 ...   # the 27 depth_clean matches  <- use this
CANON_SUFFIX=_priceonly python3 ...   # the 9 price_flow_only matches
CANON_SUFFIX=_gaps      python3 ...   # the 4 gaps matches
# unset                                  original build -- do not analyse
```

```python
import sys; sys.path.insert(0, 'pipeline')
from canon_load import matches, load_book, load_book_clean, load_trades, legs, teams

for m in matches():                     # the 27 depth_clean matches (from leg_quality.csv)
    for leg in legs(m):                 # e.g. ['COL', 'POR', 'TIE']
        bk = load_book_clean(m, leg)    # full ladder, cross-leg-consistent states only
        tr = load_trades(m, leg)        # ts, price, size, aggressor (YES terms)
```

`matches()` also takes `'price_flow_only'`, `'gaps'`, or a comma-separated
combination. The old letter tiers (`matches('AB')`) still work for old scripts but
describe the pre-repair build; on the live tree they return 40 matches including the 4
with dropped messages.

**Guards in the loader.** `load_book` warns per leg when it serves sizes from a
non-clean tier, and stays silent on clean legs. A rebuilt tree must carry its own
derived files (`validation_v2.csv`, `consistency_v2/`); the loader refuses to pair
rebuilt books with flags computed from the old ones. The two copy trees
(`_priceonly`, `_gaps`) are byte copies of the original books and correctly share its
derived files. Reading a match in the sealed split raises unless
`CANON_ALLOW_SEALED=1`, and every read is logged to `../splits/access_log.csv`.

## What is in here

| path | contents |
|---|---|
| `books<tree>/<match>/<ticker>__<day>.book.pNNN.parquet` | one row per **top-of-book change**: `ts, bid, ask, bid_size, ask_size, mid, microprice, spread, n_bid_levels, n_ask_levels, bid_depth, ask_depth` + the **full ladder** (`bid_prices, bid_sizes, ask_prices, ask_sizes`) |
| `books<tree>/<match>/<ticker>__<day>.trades.pNNN.parquet` | `ts, price, size, aggressor` |
| `raw<tree>/<match>/<ticker>__<day>.raw.pNNN.parquet` | every source delta, lossless, in Kalshi's string fields: `ts, sequence, kind, side, price_dollars, amount_fp` |
| `consistency/`, `consistency_v2/` | per-state cross-leg check: `mid_sum, ask_sum, bid_sum, max_spread, max_age_ms, consistent` |
| `validation.csv`, `validation_v2.csv` | per match: every check below plus the legacy A/B/C/F `tier` |
| `manifest.csv`, `manifest_v2.csv`, `_manifest*/` | per leg-day: rows written, seq gaps, negative clamps, snapshot provenance, uncross prunes |
| `leg_quality.csv` | **per leg: data-quality tier, seq gaps, external agreement, `verified`** |
| `state_cmp_all.csv` | per-leg comparison against Predexon (agreement by distance from the touch) |
| `seed_feasibility.csv` | per leg: last populated snapshot before the window and whether the replay path is unbroken |
| `size_deficit_*.csv`, `rebuild_acceptance.csv` | cumulative-delta deficit measurements, before and after the repair |
| `predexon_raw/` | cached Predexon snapshots; re-analysis costs no API requests |
| `inventory.csv` | every `KXWCGAME-*` leg in every tape: event counts by type, first/last exchange ts, populated-snapshot times |
| `trade_minutes.csv`, `windows.csv` | per-minute trade counts; per-match kickoff, final whistle, extraction window, `covered` |

Timestamps are Kalshi **exchange** time (`ts_ms`) in epoch ms, not recorder receive time.
Prices are integer cents, 1–99. `aggressor` is in YES terms: `buy` lifted the ask,
`sell` hit the bid.

## Coverage

38 tape files, 1 Jun – 13 Jul 2026, ~16 GB raw, 79.2 M book/trade messages.

- **72** matches in the ticker universe; **47 covered** (in-play data present).
- 25 not covered, for two reasons that are in the data, not in the code:
  - **June 17–21 was never recorded.** Those fixtures appear only as pre-match trading.
  - **The June 16 tape stops at 23:08** (recorder died), truncating that evening mid-match.
- **No `KXWCGAME-*` markets exist in any July tape, or on June 29–30.** Those files
  contain only tournament-level markets (outright winner, group winner, goal leader):
  the recorder's `tickers.json` was never refreshed with the knockout-round game
  markets, so per-game books for the knockouts were never subscribed. **This is the
  single biggest gap in the dataset and it is not recoverable from these tapes.**
- Of the 47 covered: 27 depth_clean, 9 price_flow_only, 4 gaps, and 7 legacy tier-C
  matches (a blackout over two minutes, or material crossing) never carried forward.

## Checks that pass on the depth_clean tree (`validation_v2.csv`)

- **External agreement.** 31,004,065 ladder-level comparisons against Predexon at
  exactly matched milliseconds; 80 of 80 measured legs above 99.5%, deep book (31+ ticks)
  never below 99.97%. One leg unmeasured: `26JUN26NZLBEL-TIE` (Predexon returned a
  single snapshot) — not known bad, marked `verified = False`.
- **Settlement 27/27**, winners identical to the original build. Exactly one leg's last
  trade prints ≥ 90 and the other two ≤ 10 — an end-to-end test of the whole chain
  including the NO→YES inversion.
- **Provenance 27/27** seeded by a populated snapshot before the window opened.
- **Crossed books 0/27; sequence gaps 0/27.**
- **Executable leg sum.** Median ask-sum 102, median bid-sum 99.
- **No three-leg arbitrage.** On internally consistent states the ask-sum minimum is
  exactly **100.00**. Apparent sub-100 states are a stale-leg artefact — see below.
- Median inter-update gap 30 ms; median largest in-play gap 3.8 s, worst 35.5 s.

## Known limitations — read before using

1. **~4% of states are internally inconsistent** (median 96.3% consistent; the three
   June 27 matches are worst at 85–89%). The three legs must price to 100, and in these
   states they do not, because one leg's touch is stale. Each leg looks healthy in
   isolation, so no per-leg check finds them, and they are **concentrated in the second
   half**, where the price action is. → Use `load_book_clean()`, or filter on
   `consistency<tree>/<match>.parquet`. This is what produced an apparent "buy all
   three outcomes for 43¢" state with three clean 1-tick spreads.
2. **Sizes outside depth_clean are lower bounds.** On price_flow_only legs the touch size
   agrees with an independent reconstruction only ~77% of the time and the deep book
   almost never. Prices, trades and raw deltas there are sound.
3. **Negative-size clamps still occur on depth_clean** (median 1,173 per match). They
   happen in the warm-up before the seeding snapshot arrives; the snapshot then resets
   the book exactly, and the external comparison confirms the in-window book. The
   manifest does not record clamp timing, so this is inferred, not separately measured.
4. **Stale-level pruning is applied.** A resting bid at or above the best ask cannot
   exist, so when the book crosses, the just-updated side is treated as authoritative
   and crossed levels on the other side are dropped (`uncross_prunes`). It was written
   for `26JUN12CANBIH`, which turned out to carry 266 dropped messages and is now in the
   gaps tier.
5. **Kickoff times are inferred, not authoritative.** Final whistle (last minute of real
   trade flow) minus ~125 min, snapped to :00/:30. Exact on the two kickoffs known
   independently (MEX-RSA 19:00 UTC, KOR-CZE 02:00 UTC). Median snap offset 5 min, max 15.
6. **Fees are not in this layer.** Any PnL claim needs Kalshi's real **maker** schedule;
   `fee_rate = 0.07` in older strategy files is the **taker** coefficient.
7. **Lesson recorded.** The original version of this README already said depth was a
   lower bound on delta-seeded matches. The caveat was documented and never enforced in
   the read path, so later work used the sizes anyway. Caveats here are now enforced in
   `canon_load.py`, not just written down.

## Rebuilding

Original build:

```bash
./pipeline/run_inventory.sh 150        # repeat until "INVENTORY COMPLETE"
./pipeline/run_windows.sh 150          # repeat until "WINDOWS COMPLETE"
python3 pipeline/make_windows.py       # -> windows.csv
./pipeline/run_reconstruct.sh 45       # repeat until "RECONSTRUCTION COMPLETE" (chronological; resumable)
python3 pipeline/stage0_validate.py    # -> validation.csv
python3 pipeline/stage0_consistency.py # -> consistency/
```

Seeded rebuild into the v2 tree (one tape per call, chronological):

```bash
export OUT_SUFFIX=_v2 SEED_EXTEND=1 ONLY_MATCHES=<comma-separated matches>
python3 pipeline/stage0_reconstruct.py <tape>          # each tape from the seed day onward
CANON_SUFFIX=_v2 python3 pipeline/stage0_validate.py   # -> validation_v2.csv
CANON_SUFFIX=_v2 CANON_ALLOW_SEALED=1 python3 pipeline/stage0_consistency.py   # -> consistency_v2/
python3 pipeline/compare_all.py                        # external check vs Predexon (needs PREDEXON_API_KEY)
```

`SEED_EXTEND` activates each leg from the day of its last populated snapshot, only where
`seed_feasibility.csv` shows no missing recording days in between. Large uncompressed
tapes can be pre-filtered with `grep -F` to the relevant tickers to fit the 180 s call
limit. Day files **must** run in chronological order: each leg's book is carried across
the UTC boundary in `_state<tree>/books.pkl`. Crash-safe — state is committed only after
every Parquet part for the day is on disk.
