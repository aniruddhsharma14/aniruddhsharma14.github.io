"""
Stage 0a2 -- MATCH WINDOWS.

Three definitions were tried; the first two are recorded here because the failures
are the reason for the third.

 (1) Raw per-minute flow thresholded at 25% of peak. FAILED: football flow is
     spiky, so the run above 25%-of-peak collapses onto a single goal. Median
     detected "match" was 5 minutes against a true ~115.
 (2) Shortest contiguous interval holding 90% of the match's trade volume.
     FAILED WORSE (median 1138 min): pre-match speculation is a LARGE share of
     total volume and is spread over days, so a volume-mass interval spans days.
 (3) USED: anchor on the END. Trading stops dead at settlement, so the last minute
     of real flow is the final whistle and is robust to both spikes and pre-match
     bleed. Kickoff is then end - ~125 min, SNAPPED to the nearest :00/:30 slot
     because real fixtures start on the hour or half hour.

Validated two ways: against the two kickoffs the user confirmed (MEX-RSA 19:00 UTC,
KOR-CZE 02:00 UTC), and against the independent fact that kickoffs land on clock
slots -- the pre-snap estimates clustered at :52-:06, which is what makes snapping
legitimate rather than cosmetic.

A match is only COVERED if its detected end falls near its own fixture date. A
match in the June 17-21 recording gap has no in-play data at all; its "end" is just
the last pre-match trade, and it is marked covered=False rather than silently
analysed as if it were a match.
"""
import pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta

DUR_MIN   = 125    # kickoff -> final whistle, including half-time
END_FRAC  = 0.005  # a minute counts as live flow at >=0.5% of peak. 2% cut the
                   # settlement tail: 26JUN12CANBIH's window closed with TIE at
                   # 63.5, i.e. mid-match, so the settlement check had nothing to
                   # check. Settlement pinning is low-volume but decisive.
SLOT_MIN  = 30     # fixtures start on the hour or half hour
PAD_BEFORE_S = 1800
PAD_AFTER_S  = 3600   # settlement can print well after the last busy minute
MONTHS = dict(JAN=1, FEB=2, MAR=3, APR=4, MAY=5, JUN=6,
              JUL=7, AUG=8, SEP=9, OCT=10, NOV=11, DEC=12)

iso = lambda ms: datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')


def fixture_date(match):
    """'26JUN11KORCZE' -> date(2026, 6, 11). The ticker carries its own fixture date."""
    return datetime(2000 + int(match[:2]), MONTHS[match[2:5]], int(match[5:7]),
                    tzinfo=timezone.utc)


def utc_day(ms):
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).strftime('%Y-%m-%d')


# last timestamp actually recorded on each UTC day, from the inventory. This is how
# we know the June 16 tape stops at 23:08 rather than running to midnight.
_inv = pd.read_csv('canonical/inventory.csv')
_inv['day'] = _inv.file.str.extract(r'(\d{4}-\d{2}-\d{2})')[0]
DATA_END = _inv.groupby('day').ts_last.max().to_dict()


def main():
    tm = (pd.read_csv('canonical/trade_minutes.csv')
            .groupby(['match', 'minute'], as_index=False).trades.sum())
    rows = []
    for m, g in tm.groupby('match'):
        idx = np.arange(g.minute.min(), g.minute.max() + 1)
        s = pd.Series(0.0, index=idx); s.loc[g.minute.values] = g.trades.values
        peak = s.max()
        live = s[s >= max(1.0, END_FRAC * peak)]
        end_min = int(live.index.max())
        raw_kick = end_min - DUR_MIN
        kick_min = int(round(raw_kick / SLOT_MIN) * SLOT_MIN)
        snap_off = abs(raw_kick - kick_min)

        fd = fixture_date(m)
        end_ms = (end_min + 1) * 60000
        days_off = (datetime.fromtimestamp(end_ms/1000, tz=timezone.utc) - fd) / timedelta(days=1)

        # COVERED is a statement about the data, not about dates. Every UTC day the
        # window touches must have a tape, AND the tape must still be recording when
        # the window closes. Both failure modes were real:
        #   - five 26JUN17* fixtures fall in the June 17-21 gap. Their last pre-gap
        #     trading sits late on June 16, so a +/-1-day tolerance admitted them and
        #     they "failed settlement" because the match is simply not in the data.
        #   - the June 16 tape stops at 23:08 (recorder died), truncating that
        #     evening's matches mid-play.
        t0 = kick_min * 60000 - PAD_BEFORE_S * 1000
        t1 = end_ms + PAD_AFTER_S * 1000
        need = sorted({utc_day(t) for t in (t0, kick_min*60000, end_ms, t1)})
        have_all = all(d in DATA_END for d in need)
        covered = have_all and (t1 <= DATA_END[need[-1]] + 60000) and fd.strftime('%Y-%m-%d') in DATA_END

        rows.append(dict(
            match=m, fixture_date=fd.strftime('%Y-%m-%d'),
            kickoff_ms=kick_min * 60000, end_ms=end_ms, t0_ms=t0, t1_ms=t1,
            snap_off_min=round(snap_off, 1), days_off=round(days_off, 2),
            covered=covered, peak_minute_trades=int(peak), total_trades=int(s.sum())))

    w = pd.DataFrame(rows)
    w['kickoff_utc'] = w.kickoff_ms.map(iso)
    w['end_utc'] = w.end_ms.map(iso)
    w = w.sort_values('kickoff_ms')
    w.to_csv('canonical/windows.csv', index=False)

    cov = w[w.covered]
    print(f"{len(w)} matches in the ticker universe")
    print(f"{len(cov)} COVERED (in-play data present) | {len(w)-len(cov)} not covered (recording gap)")
    print(f"\nsnap offset for covered matches (how far the raw estimate sat from a clock slot):")
    print(cov.snap_off_min.describe().to_string())
    print(f"  ambiguous snaps (offset >= 12 min, i.e. halfway between slots): "
          f"{(cov.snap_off_min >= 12).sum()}")
    print("\nNOT COVERED -- fixture falls in a recording gap, only pre-match trading seen:")
    print(w[~w.covered][['match','fixture_date','end_utc','total_trades']].to_string(index=False))
    print("\nCOVERED matches:")
    print(cov[['match','kickoff_utc','end_utc','snap_off_min','peak_minute_trades','total_trades']]
          .to_string(index=False))


if __name__ == '__main__':
    main()
