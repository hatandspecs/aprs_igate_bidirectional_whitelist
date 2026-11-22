#!/usr/bin/env python3
"""How often a digipeater repeats this station's own beacon.

    ./beacon_repeat_rate.py <dir-of-direwolf-logs> [--me KD3CCO-10] [--digi W3YA-1]

THE DENOMINATOR IS THE POINT. "W3YA-1 repeated me 113 times" is not a rate
until it is divided by the number of transmissions that gave it the chance.
Both numbers come from the same console log here: `[0L]` lines are what this
station put on the air, `[0.N]` lines carrying our own call with a used
digipeater are the copies that came back. Nothing from APRS-IS is used --
see AGENTS.md, "a digi appearing in a path is real evidence, a digi absent is
no evidence": an APRS-IS path is written by whichever iGate won the dedupe
race, so absence there says nothing. The gateway's own receiver is the only
authority for "did anything repeat this".

PAIRING IS ON TIME, NOT CONTENT. Every position beacon carries a byte-identical
information field (fixed grid square, fixed comment), so a repeat cannot be
matched to its original by payload. A repeat lands 1.5-3 s after the
transmission and the beacons are minutes apart, so the nearest preceding
transmission within --window seconds is unambiguous. A repeat with no
transmission in that window is counted as unpaired and reported rather than
dropped silently.

DO NOT POOL ACROSS 2026-11-14. `BEACON_EVERY` was misspelled `2:00:00` from
2026-11-14 to 2026-11-20, which direwolf reads as 2 minutes and not 2 hours,
so the station beaconed 30 times an hour instead of once every two. The
per-transmission rate is still measurable in that window -- that is what this
script is for, and the large n is the one good thing the fault produced -- but
a beacon from the fault window and one from outside it are samples from
different duty cycles at different times of day. The report separates them.

THE CONFIDENCE INTERVAL IS ACROSS DAYS, NOT ACROSS BEACONS. Consecutive
beacons are not independent trials: propagation, local noise and the
digipeater's own load persist for minutes to hours, so a binomial interval on
n = 4,000 beacons would claim a precision the data does not have. The interval
reported here is a t interval over the per-day rates, which lets the real
day-to-day scatter set the width. The binomial interval is printed beside it
for contrast, labeled as the optimistic one.
"""
import collections
import datetime
import math
import os
import statistics
import sys

# Run this with `python3 -I` when the logs came off the gateway: isolated mode
# keeps the download directory and PYTHONPATH off sys.path, so a file dropped
# next to the data cannot be imported. That also drops this script's own
# directory, so put it back, and only it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from digi_relay_audit import iter_frames, logfiles, real_calls, used_digis

# The fault window: BEACON_EVERY = 2:00:00 (read as 2 minutes) was committed
# on 2026-11-14 and corrected on 2026-11-20.
FAULT_FROM = datetime.date(2026, 11, 14)
FAULT_TO = datetime.date(2026, 11, 20)

INFO_KIND = {"!": "position", "=": "position", ">": "status", "}": "gated"}


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    # Clamped: at k = 0 the arithmetic lands on -2e-17 and the report then
    # prints a repeat rate of -0.0%, which is not a thing.
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def t_interval(xs, crit=2.45, lo=0.0, hi=100.0):
    """A t interval over per-day rates, in percent. crit is the 0.975 point
    for ~6 df; with few days the interval is wide, which is the honest
    outcome. Clamped to [lo, hi] for the same reason as wilson(): a Gaussian
    interval on a proportion near its boundary runs off the end of the scale,
    and a reported rate of -0.7% invites the reader to distrust the rest."""
    if len(xs) < 2:
        return (float("nan"), float("nan"))
    m = statistics.mean(xs)
    se = statistics.stdev(xs) / math.sqrt(len(xs))
    return (max(lo, m - crit * se), min(hi, m + crit * se))


def collect(logdir, me, window):
    """Transmitted beacons and the repeats paired to them, by UTC day."""
    seen = set()
    tx = []   # (utc datetime, info kind)
    rx = []   # (utc datetime, info kind, repeating station)
    unparsed_ts = 0

    for f in iter_frames(logfiles(logdir)):
        if f.src != me:
            continue
        key = (f.ts, f.slicer, f.raw)
        if key in seen:
            continue
        seen.add(key)
        t = parse_ts(f.ts)
        if t is None:
            unparsed_ts += 1
            continue
        t = t.astimezone(datetime.timezone.utc)
        kind = INFO_KIND.get(f.info[:1], "other")
        if f.kind == "tx":
            if not f.digis or not any(d.endswith("*") for d in f.digis):
                tx.append((t, kind))
        elif f.kind == "rx":
            rc = real_calls(used_digis(f))
            if rc:
                rx.append((t, kind, rc[-1]))

    tx.sort()
    rx.sort()
    return tx, rx, unparsed_ts


def pair(tx, rx, window):
    """Attribute each received repeat to the nearest preceding transmission."""
    import bisect
    times = [t for t, _ in tx]
    # repeaters[i] = set of stations that repeated transmission i
    repeaters = [set() for _ in tx]
    unpaired = collections.Counter()
    for t, kind, who in rx:
        i = bisect.bisect_right(times, t) - 1
        if i >= 0 and (t - times[i]).total_seconds() <= window and tx[i][1] == kind:
            repeaters[i].add(who)
        else:
            unpaired[who] += 1
    return repeaters, unpaired


def report(logdir, me="KD3CCO-10", digi="W3YA-1", window=15.0, kind="position"):
    tx_all, rx_all, bad_ts = collect(logdir, me, window)
    tx = [(t, k) for t, k in tx_all if k == kind]
    rx = [r for r in rx_all if r[1] == kind]
    repeaters, unpaired = pair(tx, rx, window)

    print("Beacon repeat rate for %s, info type %r" % (me, kind))
    print("source: %s   pairing window: %.0f s" % (logdir, window))
    if bad_ts:
        print("NOTE %d frames had no parseable timestamp and were skipped "
              "(log written without -T)" % bad_ts)
    if not tx:
        print("no transmissions of that type in these logs")
        return

    span_lo, span_hi = tx[0][0], tx[-1][0]
    print("transmissions: %d from %s to %s UTC"
          % (len(tx), span_lo.strftime("%Y-%m-%d %H:%M"),
             span_hi.strftime("%Y-%m-%d %H:%M")))
    print("repeat copies received: %d, of which %d could not be paired to a "
          "transmission" % (len(rx), sum(unpaired.values())))
    if unpaired:
        print("  unpaired by station: %s"
              % ", ".join("%s %d" % kv for kv in unpaired.most_common(6)))

    # --- every station that ever repeated us, with its own denominator ---
    tally = collections.Counter()
    for s in repeaters:
        for who in s:
            tally[who] += 1
    any_rep = sum(1 for s in repeaters if s)
    print()
    print("%-12s %8s %8s %18s" % ("repeater", "copies", "rate", "95% CI (binomial)"))
    for who, k in tally.most_common():
        lo, hi = wilson(k, len(tx))
        print("%-12s %8d %7.1f%% %8.1f%% - %-6.1f%%"
              % (who, k, 100 * k / len(tx), 100 * lo, 100 * hi))
    lo, hi = wilson(any_rep, len(tx))
    print("%-12s %8d %7.1f%% %8.1f%% - %-6.1f%%"
          % ("ANY", any_rep, 100 * any_rep / len(tx), 100 * lo, 100 * hi))

    # --- every digipeater, day by day, so the named one has a control ---
    # A rate that moves for one station and not the others is about that
    # station. A rate that moves for all of them is about the channel, the
    # receiver, or the beacon itself, and says nothing about any digipeater.
    byday_n = collections.Counter()
    byday_k = collections.defaultdict(collections.Counter)
    for (t, _), s in zip(tx, repeaters):
        d = t.date()
        byday_n[d] += 1
        for who in s:
            byday_k[d][who] += 1

    cols = [w for w, _ in tally.most_common(5)]
    if digi not in cols:
        cols.append(digi)
    days = sorted(byday_n)

    # A day counts as full if it holds at least 80% of the busiest day in its
    # own regime; the two regimes differ 15-fold in beacons per day.
    def regime(d):
        return "fault" if FAULT_FROM <= d <= FAULT_TO else "normal"
    peak = {}
    for d in days:
        peak[regime(d)] = max(peak.get(regime(d), 0), byday_n[d])
    full = {d: byday_n[d] >= 0.8 * peak[regime(d)] for d in days}

    print()
    print("Repeat rate by UTC day, every digipeater (the control)")
    print("%-12s %7s %s %6s  %s"
          % ("UTC day", "beacons", "".join("%10s" % c for c in cols),
             "ANY", "regime"))
    for d in days:
        n = byday_n[d]
        cells = "".join("%9.1f%%" % (100 * byday_k[d][c] / n) for c in cols)
        anyk = sum(1 for (t, _), s in zip(tx, repeaters) if t.date() == d and s)
        print("%-12s %7d %s %5.1f%%  %s%s"
              % (d, n, cells, 100 * anyk / n, regime(d),
                 "" if full[d] else " (partial)"))

    print()
    print("Regime summary, full days only")
    for who in cols + ["ANY"]:
        line = []
        for reg in ("normal", "fault"):
            ds = [d for d in days if regime(d) == reg and full[d]]
            if not ds:
                line.append("%-14s n/a" % reg)
                continue
            ns = sum(byday_n[d] for d in ds)
            if who == "ANY":
                ks = sum(1 for (t, _), s in zip(tx, repeaters)
                         if t.date() in set(ds) and s)
            else:
                ks = sum(byday_k[d][who] for d in ds)
            rates = [100 * (byday_k[d][who] / byday_n[d]) for d in ds] \
                if who != "ANY" else \
                [100 * sum(1 for (t, _), s in zip(tx, repeaters)
                           if t.date() == d and s) / byday_n[d] for d in ds]
            tlo, thi = t_interval(rates)
            blo, bhi = wilson(ks, ns)
            ci = ("%.1f-%.1f" % (tlo, thi)) if len(rates) > 1 \
                else ("%.1f-%.1f*" % (100 * blo, 100 * bhi))
            line.append("%s %d/%d = %.1f%% [%s]"
                        % (reg, ks, ns, 100 * ks / ns, ci))
        print("  %-11s %s" % (who, "   ".join(line)))
    print("  CI is across days where there are several; * marks a binomial CI")
    print("  on a single day, which assumes independence and is optimistic.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("logdir")
    ap.add_argument("--me", default="KD3CCO-10")
    ap.add_argument("--digi", default="W3YA-1")
    ap.add_argument("--window", type=float, default=15.0)
    ap.add_argument("--kind", default="position",
                    choices=("position", "status", "gated", "other"))
    a = ap.parse_args()
    report(a.logdir, a.me, a.digi, a.window, a.kind)


def gap_report(logdir, me="KD3CCO-10", window=15.0, kind="position",
               digis=("W3YA-1", "KE4MHE-10", "WO3T-1", "W3TM-10"),
               lo=FAULT_FROM, hi=FAULT_TO):
    """Time between one repeat and the next, per digipeater.

    THIS IS THE FALSIFIER for "W3YA-1 throttles repeats of one station".
    Independent per-beacon loss -- a marginal path, collisions, a busy
    receiver -- is memoryless, so the gaps between successive repeats come out
    geometric: the single commonest gap is the beacon interval itself and the
    counts fall away smoothly from there. A suppression or throttle window
    instead puts a floor under the gap, so short gaps are not merely rare but
    close to absent, and the distribution has a hard left edge at the window
    length. The two shapes are not subtle and do not need a test statistic to
    tell apart.

    **BUT A FLOOR IS NOT PROOF OF A WINDOW, and this is how it went wrong
    once.** On 2026-11-20 this report found 411 of 411 even gaps for W3YA-1
    and the conclusion drawn was duplicate suppression. The real cause was at
    this end: BEACON_EVERY 120 s and STATUS_EVERY 240 s put two frames in the
    same second every fourth minute, and a half-duplex station cannot hear a
    2-second echo of the first while it keys the second. Anything periodic in
    THIS station's own transmit pattern forges the same hard left edge.
    Before reading a floor as a remote window, check that the beacons in the
    denominator went out alone.
    """
    tx_all, rx_all, _ = collect(logdir, me, window)
    tx = [(t, k) for t, k in tx_all if k == kind and lo <= t.date() <= hi]
    rx = [r for r in rx_all if r[1] == kind and lo <= r[0].date() <= hi]
    repeaters, _ = pair(tx, rx, window)

    # The modal spacing, not a capped one: this has to work at 120 s and at
    # 1800 s, so an upper bound of 600 s silently reported 0 for the 30-minute
    # regime and made the "at the beacon interval" column meaningless there.
    ivals = collections.Counter(
        round((b - a).total_seconds())
        for (a, _), (b, _) in zip(tx, tx[1:])
        if 0 < (b - a).total_seconds() < 7200)
    beacon_iv = ivals.most_common(1)[0][0] if ivals else 0
    print("Gaps between successive repeats, %s to %s UTC" % (lo, hi))
    print("beacon interval in this window: about %d s" % beacon_iv)
    print()
    for who in digis:
        ts = [t for (t, _), s in zip(tx, repeaters) if who in s]
        if len(ts) < 3:
            print("%-11s too few repeats (%d)" % (who, len(ts)))
            continue
        gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(ts, ts[1:])]
        # Only gaps inside one continuous run matter; a 6-hour gap is an
        # outage or a dead band, not evidence about a suppression window.
        g = sorted(x for x in gaps if x < 120)
        n_short = sum(1 for x in g if x <= beacon_iv * 1.5 / 60.0)
        print("%-11s %d repeats, %d gaps under 2 h" % (who, len(ts), len(g)))
        print("%-11s   min %.1f min, median %.1f, 90th %.1f"
              % ("", g[0], g[len(g) // 2], g[int(len(g) * 0.9)]))
        print("%-11s   gaps at the beacon interval (<= %.1f min): %d of %d = %.0f%%"
              % ("", beacon_iv * 1.5 / 60.0, n_short, len(g),
                 100 * n_short / len(g)))
        buckets = [0] * 8
        edges = [2, 4, 6, 10, 15, 20, 30, 10 ** 9]
        for x in g:
            for i, e in enumerate(edges):
                if x <= e:
                    buckets[i] += 1
                    break
        labels = ["<=2", "2-4", "4-6", "6-10", "10-15", "15-20", "20-30", ">30"]
        bar = "  ".join("%s:%d" % (l, b) for l, b in zip(labels, buckets))
        print("%-11s   %s" % ("", bar))
        print()
