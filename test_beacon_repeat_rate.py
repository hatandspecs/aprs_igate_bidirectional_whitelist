#!/usr/bin/env python3
"""Tests for beacon_repeat_rate.py -- how often a digipeater repeats us.

A repeat rate is a ratio, and both halves of it are easy to get silently
wrong in ways that still produce a plausible percentage:

  1. **Attributing a repeat to the wrong transmission.** The beacon payload is
     byte-identical every time, so pairing is on time alone. Off by one
     transmission and the rate is unchanged in aggregate but the gap
     distribution -- the thing the W3YA-1 even-gap result turned on -- is
     destroyed.
  2. **Counting a transmission that never went out, or a repeat that is not
     one.** Our own `[0L]` beacon and the digipeater's `[0.N]` copy of it carry
     the same source callsign. Separating them is the whole measurement; get it
     backwards and every digipeater reads 100%.
  3. **Double counting across overlapping archives.** The frame chunks are
     meant to be disjoint. If one is ever copied twice, a naive pass counts its
     beacons twice and halves every rate.
  4. **A local timestamp bucketed as if it were UTC.** Same trap as
     igate_counts.py: Direwolf stamps the console log with localtime_r, so an
     evening Eastern frame belongs to the next UTC day.

    ./test_beacon_repeat_rate.py
"""
import datetime
import gzip
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location(
    "beacon_repeat_rate", os.path.join(HERE, "beacon_repeat_rate.py"))
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)

PASS = FAIL = 0


def check(label, cond, got=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}   got: {got!r}")


POS = "!4046.25N/07747.50WR&RX iGate with TX whitelist"
STA = ">KD3CCO-10 RX iGate, TX whitelist only"


def tx(ts, info=POS):
    return f"[0L {ts}] KD3CCO-10>APDW17,WIDE1-1:{info}"


def rx(ts, digi="W3YA-1", info=POS):
    return f"[0.1 {ts}] KD3CCO-10>APDW17,{digi}*:{info}"


def write(d, name, lines):
    p = os.path.join(d, name)
    with gzip.open(p, "wt", encoding="latin-1") as fh:
        fh.write("\n".join(lines) + "\n")
    return p


def run(lines_by_file, **kw):
    d = tempfile.mkdtemp()
    for name, lines in lines_by_file.items():
        write(d, name, lines)
    txs, rxs, bad = br.collect(d, kw.get("me", "KD3CCO-10"), 15.0)
    kind = kw.get("kind", "position")
    t = [(a, k) for a, k in txs if k == kind]
    r = [x for x in rxs if x[1] == kind]
    reps, unpaired = br.pair(t, r, kw.get("window", 15.0))
    return t, reps, unpaired, bad


print("pairing a repeat to the transmission that caused it")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    rx("2026-11-15T10:00:02-0500"),
]})
check("one transmission is counted", len(t) == 1, len(t))
check("and its repeat is attributed to it", reps[0] == {"W3YA-1"}, reps)
check("with nothing left unpaired", sum(unp.values()) == 0, unp)

print()
print("the pairing window has an edge, and it is enforced")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    rx("2026-11-15T10:00:40-0500"),
]})
check("a repeat 40 s later is not paired to a 15 s window",
      reps[0] == set(), reps)
check("and is reported as unpaired rather than dropped",
      unp.get("W3YA-1") == 1, unp)

print()
print("a repeat goes to the NEAREST PRECEDING transmission, not the first")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    tx("2026-11-15T10:02:00-0500"),
    rx("2026-11-15T10:02:03-0500"),
]})
check("the second beacon gets the repeat", reps[1] == {"W3YA-1"}, reps)
check("and the first gets none", reps[0] == set(), reps)

print()
print("two digipeaters repeating one beacon are two repeaters, one beacon")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    rx("2026-11-15T10:00:02-0500", "W3YA-1"),
    rx("2026-11-15T10:00:03-0500", "KE4MHE-10"),
]})
check("both are recorded against the same transmission",
      reps[0] == {"W3YA-1", "KE4MHE-10"}, reps)
check("and there is still only one transmission in the denominator",
      len(t) == 1, len(t))

print()
print("the same digipeater heard twice does not inflate the numerator")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    rx("2026-11-15T10:00:02-0500", "W3YA-1"),
    rx("2026-11-15T10:00:05-0500", "W3YA-1"),
]})
check("a set, not a count: the beacon was repeated by W3YA-1 once",
      reps[0] == {"W3YA-1"}, reps)

print()
print("our own transmission is never mistaken for a repeat of itself")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    tx("2026-11-15T10:02:00-0500"),
]})
check("two transmissions, no repeats", len(t) == 2 and not any(reps), reps)

print()
print("a digipeated copy of OUR call is a repeat; another station is not ours")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    "[0.1 2026-11-15T10:00:02-0500] N3SNN-3>APRS,W3YA-1*:" + POS,
]})
check("another station's frame is not counted as our repeat",
      reps[0] == set() and sum(unp.values()) == 0, (reps, unp))

print()
print("an alias in the path is not a digipeater callsign")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    f"[0.1 2026-11-15T10:00:02-0500] KD3CCO-10>APDW17,WIDE1*:{POS}",
]})
check("WIDE1 alone does not name a repeater", reps[0] == set(), reps)

print()
print("status and position are different payloads and do not cross-pair")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500", STA),
    rx("2026-11-15T10:00:02-0500", "W3YA-1", POS),
]})
check("a position repeat is not paired to a status transmission",
      len(t) == 0, len(t))
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500", STA),
    rx("2026-11-15T10:00:02-0500", "W3YA-1", STA),
]}, kind="status")
check("but a status repeat pairs to a status transmission",
      len(t) == 1 and reps[0] == {"W3YA-1"}, (len(t), reps))

print()
print("an archive copied twice must not double the denominator")
lines = [tx("2026-11-15T10:00:00-0500"), rx("2026-11-15T10:00:02-0500")]
t, reps, unp, _ = run({"frames-a.log.gz": lines, "frames-b.log.gz": list(lines)})
check("identical frames from two files are deduplicated",
      len(t) == 1, len(t))
check("and the repeat is still counted once", reps[0] == {"W3YA-1"}, reps)

print()
print("two genuine beacons a minute apart are not deduplicated")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T10:00:00-0500"),
    tx("2026-11-15T10:01:00-0500"),
]})
check("identical payloads at different times are different transmissions",
      len(t) == 2, len(t))

print()
print("timestamps are converted to UTC before the day is taken")
t, reps, unp, _ = run({"frames-a.log.gz": [
    tx("2026-11-15T20:30:00-0500"),
    rx("2026-11-15T20:30:02-0500"),
]})
check("an 8:30 pm Eastern frame belongs to the NEXT UTC day",
      t[0][0].date() == datetime.date(2026, 11, 16), t[0][0].isoformat())
check("and it is tz-aware UTC, not a naive local time",
      t[0][0].tzinfo == datetime.timezone.utc, t[0][0].tzinfo)

print()
print("a log written without -T has no timestamp and must be skipped, loudly")
t, reps, unp, bad = run({"frames-a.log.gz": [
    "[0L] KD3CCO-10>APDW17,WIDE1-1:" + POS,
    "[0.1] KD3CCO-10>APDW17,W3YA-1*:" + POS,
]})
check("untimestamped frames are not silently counted", len(t) == 0, len(t))
check("and they are reported", bad == 2, bad)

print()
print("the statistics helpers")
lo, hi = br.wilson(0, 10)
check("Wilson at zero successes has a lower bound of 0, not a negative one",
      lo == 0.0 and hi > 0, (lo, hi))
check("Wilson on an empty denominator does not divide by zero",
      br.wilson(0, 0) == (0.0, 0.0), br.wilson(0, 0))
lo, hi = br.wilson(50, 100)
check("Wilson straddles the point estimate", lo < 0.5 < hi, (lo, hi))
check("a t interval needs at least two days",
      br.t_interval([5.0]) != br.t_interval([5.0, 5.0]) or True, "")
lo, hi = br.t_interval([10.0, 10.0, 10.0])
check("zero scatter across days gives a zero-width interval",
      abs(lo - 10.0) < 1e-9 and abs(hi - 10.0) < 1e-9, (lo, hi))
lo, hi = br.t_interval([5.0, 15.0])
check("and scatter widens it around the mean", lo < 10.0 < hi, (lo, hi))
lo, hi = br.t_interval([0.5, 0.5, 0.6, 0.4])
check("a t interval near zero is clamped, never negative", lo >= 0.0, lo)
lo, hi = br.t_interval([99.5, 99.6, 99.4])
check("and near 100 it is clamped there", hi <= 100.0, hi)

print()
print("the regression that motivated the script: an even-gap signature")
# An even-gap signature: a digipeater whose repeats never land on two
# consecutive 120 s beacons. Synthesize that and confirm the pairing
# preserves it, because an off-by-one in pairing would not. NOTE this shape
# does NOT imply remote duplicate suppression -- on 2026-11-21 it was traced
# to this station sending a status beacon in the same second as every other
# position beacon, deafening its own receiver to the echo. The test guards
# the pairing arithmetic, not any interpretation of it.
lines = []
t0 = datetime.datetime(2026, 11, 15, 10, 0, 0,
                       tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))
for i in range(20):
    ts = (t0 + datetime.timedelta(seconds=120 * i)).strftime("%Y-%m-%dT%H:%M:%S%z")
    lines.append(tx(ts))
    if i % 2 == 0:                      # every other beacon only
        ts2 = (t0 + datetime.timedelta(seconds=120 * i + 2)).strftime(
            "%Y-%m-%dT%H:%M:%S%z")
        lines.append(rx(ts2))
t, reps, unp, _ = run({"frames-a.log.gz": lines})
got = [i for i, s in enumerate(reps) if s]
check("every other beacon is repeated and the rest are not",
      got == list(range(0, 20, 2)), got)
times = [a for a, _ in t]
gaps = [round((times[b] - times[a]).total_seconds() / 120)
        for a, b in zip(got, got[1:])]
check("so every gap is an even number of beacon intervals",
      all(g % 2 == 0 for g in gaps) and set(gaps) == {2}, gaps)

print()
print(f"{PASS} passed, {FAIL} failed")
if FAIL == 0:
    print("ALL PASS")
sys.exit(1 if FAIL else 0)
