#!/usr/bin/env python3
"""Tests for igate_counts.py -- the per-UTC-day traffic counts.

Three classes of error these guard, all of which have happened in this
project:

  1. **A bracket shape the parser does not know reads zero.** Direwolf's -T
     flag moved the timestamp INSIDE the existing bracket on 2026-11-08 and
     twelve parsing sites silently counted nothing for six days, because every
     fixture used the old shape. Every fixture here mixes both.
  2. **A local timestamp bucketed as if it were UTC.** Direwolf stamps the
     console log with localtime_r and names the dated CSV with gmtime_r. An
     evening frame in US Eastern belongs to the NEXT UTC day, and getting that
     wrong moves traffic between days by five hours' worth every night.
  3. **A rolling window reported as a day.** The whole reason this module
     exists. A log that does not span the day must say so.

    ./test_igate_counts.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "igate_counts", os.path.join(HERE, "igate_counts.py"))
ic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ic)

PASS = FAIL = 0
CALL = "KD3CCO-10"


def ok(msg):
    global PASS
    PASS += 1
    print(f"  PASS  {msg}")


def bad(msg, got=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {msg}\n         got: {got}")


def check(msg, cond, got=""):
    ok(msg) if cond else bad(msg, got)


def count(lines, mycall=CALL):
    c = ic.Counter(mycall)
    c.feed(lines)
    return c


def field(counter, day, name):
    return counter.row(day)[ic.CONSOLE_FIELDS.index(name)]


# --------------------------------------------------------------------------
print("both bracket shapes classify the same way")

BARE = [
    "[0L] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&RX iGate",
    "[0.3] WO3T-1>APNU3B,WIDE1-1*:!4029.80NS07807.92W#",
    "[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!4029.80NS07807.92W#",
    "[ig>tx] N8GD-1>APRS,TCPIP*,qAC,T2NALA:!4100.00N/08000.00W#",
]
STAMPED = [
    "[0L 2026-11-16T09:00:00-0500] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&RX iGate",
    "[0.3 2026-11-16T09:00:01-0500] WO3T-1>APNU3B,WIDE1-1*:!4029.80NS07807.92W#",
    "[rx>ig 2026-11-16T09:00:01-0500] WO3T-1>APNU3B,qAR,KD3CCO-10:!4029.80NS07807.92W#",
    "[ig>tx 2026-11-16T09:00:02-0500] N8GD-1>APRS,TCPIP*,qAC,T2NALA:!4100.00N/08000.00W#",
]

c = count(STAMPED)
D = "2026-11-16"
check("a stamped beacon is one beacon", field(c, D, "day_beacons") == 1,
      field(c, D, "day_beacons"))
check("a stamped rx frame is one rx frame", field(c, D, "day_rx_frames") == 1,
      field(c, D, "day_rx_frames"))
check("a stamped rx>ig is one gated up", field(c, D, "day_gated_up") == 1,
      field(c, D, "day_gated_up"))
check("a stamped ig>tx is one offered", field(c, D, "day_is_offered") == 1,
      field(c, D, "day_is_offered"))
check("frames is rx plus tx, which is what reach counts",
      field(c, D, "day_frames") == 2, field(c, D, "day_frames"))

cb = count(BARE)
check("the bare shapes are classified identically, as undated",
      cb.undated == 4, cb.undated)
check("and credited to no day at all", cb.all_days() == [], cb.all_days())

# The failure this is really about: a whole pre-2026-11-08 chunk must not be
# credited to the day it is asked about.
check("an unstamped log reports zero for a day, not its whole contents",
      field(cb, D, "day_frames") == 0, field(cb, D, "day_frames"))
check("and marks itself partial so the zero cannot be read as quiet",
      field(cb, D, "day_partial") == 1, field(cb, D, "day_partial"))
check("and carries the undated count through to the row",
      field(cb, D, "day_undated") == 4, field(cb, D, "day_undated"))

# --------------------------------------------------------------------------
print("\nlocal stamps are bucketed by UTC day")

# 19:30 EST on the 16th is 00:30 UTC on the 17th. This is the one that moves
# five hours of traffic to the wrong day if it is got wrong.
c = count([
    "[0.3 2026-11-16T19:30:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x",
    "[0.3 2026-11-16T18:30:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x",
])
check("an evening Eastern frame lands on the next UTC day",
      field(c, "2026-11-17", "day_rx_frames") == 1,
      c.days)
check("and an afternoon one stays on the same UTC day",
      field(c, "2026-11-16", "day_rx_frames") == 1,
      c.days)
check("the two are on different days", len(c.all_days()) == 2, c.all_days())

# A UTC stamp and an offset stamp naming the same instant must agree.
a = count(["[0.3 2026-11-17T00:30:00+0000] WO3T-1>APNU3B,WIDE1*:!x"])
b = count(["[0.3 2026-11-16T19:30:00-0500] WO3T-1>APNU3B,WIDE1*:!x"])
check("the same instant written two ways lands on the same day",
      a.all_days() == b.all_days() == ["2026-11-17"],
      (a.all_days(), b.all_days()))

# --------------------------------------------------------------------------
print("\nan unstamped igate line takes the stamp above it")

c = count([
    "[0.3 2026-11-16T09:00:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x",
    "[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!x",
    "[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!x",
])
check("the gated-up lines are placed on the frame's day",
      field(c, D, "day_gated_up") == 2, field(c, D, "day_gated_up"))
check("and none of them is counted as undated", c.undated == 0, c.undated)

# Carry-forward, not carry-backward: lines before any stamp are undated.
c = count([
    "[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!x",
    "[0.3 2026-11-16T09:00:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x",
])
check("a line BEFORE the first stamp is undated rather than back-dated",
      c.undated == 1 and field(c, D, "day_gated_up") == 0,
      (c.undated, field(c, D, "day_gated_up")))

# --------------------------------------------------------------------------
print("\nthe APRS-IS keepalive is not a packet")

c = count([
    "[0.3 2026-11-16T09:00:00-0500] WO3T-1>APNU3B,WIDE1*:!x",
    "[rx>ig] #",
    "[rx>ig] # aprsc 2.1.11",
    "[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!x",
])
check("a bare # does not count as gated up",
      field(c, D, "day_gated_up") == 1, field(c, D, "day_gated_up"))
check("and is not counted as undated either", c.undated == 0, c.undated)

# In the real October capture 23,663 of 52,420 rx>ig lines are keepalives.
# Counting them would overstate the uplink by 82%.

# --------------------------------------------------------------------------
print("\nthe gateway is not its own digipeater")

# A third-party payload carries `TCPIP,MYCALL*` AFTER the colon. Matching the
# whole line counts the gateway as the digipeater that repeated it -- the bug
# that was found in `reach`.
c = count([
    "[0L 2026-11-16T09:00:00-0500] KD3CCO-10>APDW17,WIDE1-1,WIDE2-2:"
    "}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello",
])
check("the gated frame is counted once",
      field(c, D, "day_gated_down") == 1, field(c, D, "day_gated_down"))
check("and no repeat is invented from the payload",
      field(c, D, "day_gated_repeats") == 0, field(c, D, "day_gated_repeats"))

# A real repeat has the used hop BEFORE the first colon.
c = count([
    "[0.1 2026-11-16T09:00:00-0500] KD3CCO-10>APDW17,W3YA-1*:!4046.25NR07747.50W&",
    "[0.1 2026-11-16T09:00:01-0500] KD3CCO-10>APDW17,W3TM-10*,WIDE2-1:"
    "}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello",
])
check("a used hop before the colon is a real beacon repeat",
      field(c, D, "day_beacon_repeats") == 1,
      field(c, D, "day_beacon_repeats"))
check("and a real gated repeat",
      field(c, D, "day_gated_repeats") == 1,
      field(c, D, "day_gated_repeats"))
check("own repeats are received frames, not transmitted ones",
      field(c, D, "day_rx_frames") == 2 and field(c, D, "day_tx_frames") == 0,
      (field(c, D, "day_rx_frames"), field(c, D, "day_tx_frames")))

# --------------------------------------------------------------------------
print("\npartial is set when the log does not span the day")

full = count([
    "[0.3 2026-11-15T18:55:00-0500] A>B,WIDE1*:!x",   # 2026-11-15 23:55Z
    "[0.3 2026-11-16T19:20:00-0500] A>B,WIDE1*:!x",   # 2026-11-17 00:20Z
])
check("a log reaching either side of the day is complete",
      field(full, D, "day_partial") == 0, full.first)

# Five minutes the wrong side of midnight is still partial. 19:05 Eastern is
# 00:05 UTC, INSIDE the day, so a log starting there has missed five minutes
# of it -- which is the correct answer and not an off-by-one.
just_inside = count([
    "[0.3 2026-11-15T19:05:00-0500] A>B,WIDE1*:!x",   # 2026-11-16 00:05Z
    "[0.3 2026-11-16T19:20:00-0500] A>B,WIDE1*:!x",
])
check("five minutes short of the day boundary is partial",
      field(just_inside, D, "day_partial") == 1, just_inside.first)

late = count([
    "[0.3 2026-11-16T06:00:00-0500] A>B,WIDE1*:!x",   # starts mid-day
    "[0.3 2026-11-16T19:20:00-0500] A>B,WIDE1*:!x",
])
check("a log that starts inside the day is partial, which is the reboot case",
      field(late, D, "day_partial") == 1, late.first)

early = count([
    "[0.3 2026-11-15T19:05:00-0500] A>B,WIDE1*:!x",
    "[0.3 2026-11-16T12:00:00-0500] A>B,WIDE1*:!x",   # ends inside the day
])
check("a log that ends inside the day is partial too",
      field(early, D, "day_partial") == 1, early.last)

# --------------------------------------------------------------------------
print("\nnon-bracket lines are ignored")

c = count([
    "WO3T-1 audio level = 46(11/6)    ___|||||_",
    "",
    "        Position, WEATHER, Kenwood",
    "Audio input level is too low",
    "[0.3 2026-11-16T09:00:00-0500] A>B,WIDE1*:!x",
])
check("decodes, audio levels and blank lines count as nothing",
      field(c, D, "day_frames") == 1 and c.undated == 0,
      (field(c, D, "day_frames"), c.undated))

# --------------------------------------------------------------------------
print("\nthe station union is a union, not a sum")

work = tempfile.mkdtemp()
HDR = ("chan,utime,isotime,source,heard,level,error,dti,name,symbol,"
       "latitude,longitude,speed,course,altitude,frequency,offset,tone,"
       "system,status,telemetry,comment\n")


def mkcsv(day, pairs):
    path = os.path.join(work, day + ".log")
    with open(path, "w") as fh:
        fh.write(HDR)
        for src, heard in pairs:
            fh.write(f"0,1700000000,{day}T00:00:00Z,{src},{heard},40,0,!,"
                     ",,,,,,,,,,,,,\n")
    return path


# Three stations on one day and three others plus the gateway itself on the
# next, two of them shared. The daily uniques sum to 6; the union is 4.
d1 = mkcsv("2026-11-15", [("WO3T-1", "WO3T-1"), ("W3YA-1", "W3YA-1"),
                          ("N3DXC", "N3DXC")])
d2 = mkcsv("2026-11-16", [("WO3T-1", "WO3T-1"), ("W3YA-1", "W3YA-1"),
                          ("KB3EWY-9", "KB3EWY-9"), (CALL, "W3YA-1")])
row = ic.station_row(d2, [d1, d2], CALL)
vals = dict(zip(ic.STATION_FIELDS, row))
check("the window union is 4, not the 6 the daily counts sum to",
      vals["rx_stations_win"] == 4, vals)
check("one station is new on the day", vals["rx_stations_new"] == 1, vals)
# The gateway's own beacon came back off the air through W3YA-1. It is a
# reception, it is not a station this receiver reached, and it must not pad
# the station count.
check("the station's own call is counted separately",
      vals["rx_own"] == 1, vals)
check("and is not one of the stations heard",
      CALL not in ic.sources(d2, exclude=(CALL,)), ic.sources(d2))

# An uncertain `?` must not make one station into two.
d3 = mkcsv("2026-11-17", [("WO3T-1", "WO3T-1"), ("WO3T-1?", "WO3T-1?")])
check("an uncertain heard-station is the same station",
      len(ic.sources(d3)) == 1, ic.sources(d3))

# --------------------------------------------------------------------------
print("\nagainst the real 2026-10-30 capture")

REAL = os.path.join(HERE, "archive-2026-10", "frames-15ft-2026-10-30.log.gz")
if os.path.exists(REAL):
    c = ic.Counter(CALL)
    with ic._open(REAL) as fh:
        c.feed(fh)
    # This capture predates -T, so every line is undated. The number is the
    # point: 128,904 is rx 29,803 + tx 444 + gated up 28,757 + offered 69,900,
    # the four counts `grep -c` gives on the same file. The classifier sees
    # every line those greps see and invents none.
    check("every classified line is accounted for (128,904)",
          c.undated == 128904, c.undated)
    check("and not one of them is credited to a day",
          c.all_days() == [], c.all_days())
    check("so the row for any day is zeros, flagged partial and undated",
          c.row("2026-10-30") == [0] * 9 + [128904, 1],
          c.row("2026-10-30"))
else:
    print("  SKIP  archive-2026-10/ not present")

print()
print(f"{PASS} passed, {FAIL} failed")
if FAIL == 0:
    print("ALL PASS")
sys.exit(1 if FAIL else 0)
