# Splits

Separate from `canonical/` on purpose: **nothing here modifies the data.** This
directory only labels it. Deleting or rewriting these files costs nothing.

```
v2.csv            CURRENT. match -> cv | sealed, plus group, tier, prior_use
v2.meta.json      how v2 was built: universe, sealed groups, rules
v1.csv, v1.meta.json   superseded (24 explore / 10 validation / 6 sealed), kept for audit
access_log.csv    appended on every read: ts, match, leg, kind, split, caller
```

The loader always uses the highest-numbered version, so v2 is live.

## Current: v2 (2026-10-16) — 21 recombination pool / 6 sealed

Universe: the **27 depth_clean matches** (`canonical/leg_quality.csv`). Those are the
matches whose every leg was verified against an independent reconstruction after the
cold-start repair. The other tiers (`price_flow_only`, `gaps`) are not in the splits.

| split | groups | matches | book rows |
|---|---|---|---|
| **cv** (recombination pool) | 9 | 21 | 5,367,238 |
| **sealed** | 2 | 6 | 1,662,495 |

| group | teams | split | matches |
|---|---|---|---|
| 1 | AUS / PAR / TUR / USA | cv | 3 |
| 3 | BIH / CAN / QAT / SUI | cv | 1 |
| 4 | BRA / HTI / MAR / SCO | cv | 2 |
| 5 | CIV / CUW / ECU / GER | cv | 4 |
| 6 | COD / COL / POR / UZB | cv | 2 |
| 7 | CPV / ESP / KSA / URU | cv | 4 |
| 8 | CRO / ENG / GHA / PAN | cv | 1 |
| 9 | CZE / KOR / MEX / RSA | cv | 2 |
| 10 | FRA / IRQ / NOR / SEN | cv | 2 |
| 2 | BEL / EGY / IRI / NZL | **sealed** | 3 |
| 11 | JPN / NED / SWE / TUN | **sealed** | 3 |

**Sealed matches — do not open:** 26JUN15BELEGY, 26JUN15IRINZL, 26JUN26NZLBEL,
26JUN14NEDJPN, 26JUN14SWETUN, 26JUN25JPNSWE.

### Why v1 was replaced
v1 was drawn over 40 matches before the cold-start defect was found. After the repair,
only 27 matches have fully trustworthy books. v1's explore/validation sets mixed
tiers, and its component search had a bug: groups were built from the recorded
matches only, which could split a real group in two. v2 builds groups from the full
72-fixture graph.

### No fixed validation set — recombination instead
With only 9 independent groups, a single validation split would rest on 2–3 groups
and say little. The pool is instead used for **combinatorial purged cross-validation**
(AFML ch. 12):
- choose k of the N = 9 groups as test, train on the rest;
- every combination is one split; with k = 2 that's 36 splits and 8 complete
  out-of-sample paths;
- results are reported as a **distribution over paths**, not one number.

Groups are indivisible. Purging and embargoing apply within the time dimension as
usual.

### Prior use is recorded, not hidden
12 of the 21 pool matches were used before v2 existed, as `prior_use` in `v2.csv`
records: the H8 run on the v1 explore set, and Stage 0 diagnostics. That's acceptable
for the pool, which is for searching and cross-validation, and it's exactly why none
of those matches could go into sealed.

### Decision recorded before any results: 26JUN26NZLBEL-TIE (2026-10-23)
This sealed leg is the only depth_clean leg that could not be verified externally:
Predexon returned a single snapshot for it. It is **kept in the sealed set** and stays
marked `verified = False` in `leg_quality.csv`. When the sealed set is spent, results
are reported **with and without** this leg. Nothing about it is known to be wrong;
it's only unmeasured. This decision is fixed now so it can't be made after seeing a
result.

## The one rule

**A split file is editable. One direction of edit is void.**

- **sealed → pool** is always safe. You're only allowing yourself data you hadn't used.
- **pool → sealed** is **void**, whatever the file says. Once a match has been mined —
  fitted on, used to tune a threshold, or merely eyeballed before deciding what to try
  next — that knowledge is inside every later choice. Relabelling it "sealed" makes a
  contaminated test that *looks* clean, which is worse than no test.

`access_log.csv` makes "is this match still clean?" a lookup, not a memory test.
Check it before moving anything *into* sealed.

## Why groups, not matches or teams

A per-match split breaks exactly what we want to test. BRA–MAR in training and another
BRA match in test makes a team effect look like generalisation. Teams aren't
independent either: each match is an **edge** between two teams, and in the group
stage the teams form clusters. The indivisible unit is the **connected component of
the full 72-fixture team graph**, which is the tournament group.

## Using it

```python
import sys; sys.path.insert(0, 'pipeline')
import canon_load as cl                    # run with CANON_SUFFIX=_v2

pool = cl.split_matches('cv')              # 21 matches
groups = cl.splits().loc[pool, 'group']    # the CV unit: never split a group
```

`CANON_SUFFIX` is required. With it unset, every read raises an error.

Reading a sealed match **raises `PermissionError`**. To spend the sealed set
deliberately, once, at the end:

```python
cl.ALLOW_SEALED = True        # or CANON_ALLOW_SEALED=1; recorded in access_log.csv
```

Extraction scripts for the ticker panels (`ticker_extract.py`,
`ticker_extract_all.py`) drop the sealed groups at extraction, so derived panels never
contain them.

## Re-splitting

```bash
python3 pipeline/stage1_splits_v2.py   # writes the next vN.csv; old versions are kept
```

Check `access_log.csv` before moving anything *into* sealed.
