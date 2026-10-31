#!/usr/bin/env python3
"""Checks on heard_spatial.py.

Every assertion in here corresponds to a wrong answer this tool actually gave
before it was fixed, because a receive-coverage map is a plausible-looking
picture whether or not it is true:

  * counting `[0L]` put this station's own beacons into its own coverage,
  * counting object and third-party payloads put a repeater 98 miles away,
  * taking a station's LAST position put a mobile at 126 miles when every
    packet copied directly from it came from inside 14,
  * and one lucky frame in 1742 became a 98-mile path.

The geometry has its own checks for the same reason the sibling WSPR project
keeps them: a great-circle number that is wrong looks exactly like one that is
right.

    ./test_heard_spatial.py
"""
import importlib.util
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("heard_spatial",
                                              os.path.join(HERE, "heard_spatial.py"))
hs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hs)

PASS = FAIL = 0


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


def near(msg, a, b, tol):
    check(f"{msg} ({a:.4f} ~ {b:.4f})", abs(a - b) <= tol, f"|{a}-{b}| > {tol}")


def console(lines):
    import io
    return list(hs.read_console(io.StringIO("\n".join(lines) + "\n")))


POS_W3YA = "N 40 43.0400, W 077 53.5300"


def main():
    print("Maidenhead and the ellipsoid")
    lat, lon = hs.grid_square_center("FN10cs")
    # FN10cs spans 40.750-40.79167 N and 77.833-77.750 W; the centre is fixed.
    near("FN10cs centre latitude", lat, 40.770833, 1e-5)
    near("FN10cs centre longitude", lon, -77.791667, 1e-5)
    check("a short locator is refused rather than guessed",
          _raises(lambda: hs.grid_square_center("FN10")))
    mlat, mlon = hs.metres_per_degree(40.77)
    near("a degree of latitude at 40.77 N, km", mlat / 1000, 111.05, 0.05)
    near("a degree of longitude at 40.77 N, km", mlon / 1000, 84.4, 0.1)
    check("longitude degrees are shorter than latitude ones here", mlon < mlat,
          f"{mlon} vs {mlat}")

    o = hs.Origin(lat, lon)
    near("the origin is zero miles from itself", o.distance_miles(lat, lon), 0.0, 1e-9)
    # W3YA-1's beacon, 4043.04N/07753.53W, measured independently at 6.44 miles.
    near("W3YA-1 lands where the map put it",
         o.distance_miles(40 + 43.04 / 60, -(77 + 53.53 / 60)), 6.44, 0.03)
    e, n = o.offset_km(lat + 0.1, lon)
    check("north of the origin is positive north", n > 0 and abs(e) < 1e-6, (e, n))
    e, n = o.offset_km(lat, lon + 0.1)
    check("east of the origin is positive east", e > 0 and abs(n) < 1e-6, (e, n))

    print("\nthe console reader keeps receptions and nothing else")
    recs = console([
        "[0.3] W3YA-1>APN391:!4043.04NS07753.53W#PHG7680/W3,WPAn digi", POS_W3YA,
    ])
    check("a received position packet is kept", len(recs) == 1, recs)
    check("and marked direct when no hop was used", recs and recs[0][1] is True, recs)

    # `[0L]` is this station TRANSMITTING.
    recs = console([
        "[0L] KD3CCO-10>APDW17,WIDE1-1:!4046.25N/07747.50W#own beacon",
        "N 40 46.2500, W 077 47.5000",
    ])
    check("this station's own transmission is NOT a reception", recs == [], recs)

    recs = console([
        "[0.5] NJ3T-4>APN382,W3YA-1*,WIDE2-1:!3948.42NS07910.33W#PHG7870",
        "N 39 48.4200, W 079 10.3300",
    ])
    check("a used digipeater in the path means not direct",
          len(recs) == 1 and recs[0][1] is False, recs)

    print("\npositions that belong to somebody else are not this sender's")
    for dti, label, line in (
        (";", "an object report",
         "[0.5] NJ3T-4>APN382,WIDE2-1:;443.725PA*111111z3948.43N/07910.31Wr"),
        ("}", "a third-party payload",
         "[0.4] N3DXC-2>APN001,WIDE1-1:}VR2WQY-1>APWW11,TCPIP,N3DXC-2*::msg"),
        (")", "an item report",
         "[0.4] W3ABC>APRS,WIDE1-1:)AID#2!3948.00N/07910.00W"),
        (">", "a status packet",
         "[0.4] W3ABC>APRS,WIDE1-1:>just a status line"),
    ):
        recs = console([line, "N 39 48.4300, W 079 10.3100"])
        check(f"{label} ({dti}) is not placed as the sender's position", recs == [], recs)

    print("\nthe dated CSV reader")
    import io
    csv_text = (
        "chan,utime,isotime,source,heard,level,error,dti,name,symbol,latitude,longitude\n"
        "0,1,t,NJ3T-4,W3YA-1,24,0,!,,,39.807,-79.172\n"
        "0,2,t,K3DNA,K3DNA,24,0,!,,,40.900,-77.700\n"
        "0,3,t,WO3T-1?,WO3T-1?,24,0,!,,,40.500,-78.100\n"
        "0,4,t,W3TM-10,,24,0,!,,,40.800,-77.800\n"
        "0,5,t,NOPOS,NOPOS,24,0,!,,,,\n"
    )
    rows = list(hs.read_dated_csv(io.StringIO(csv_text)))
    by = {r[0]: r[1] for r in rows}
    check("a row with no position is dropped", "NOPOS" not in by, by)
    check("heard != source is digipeated", by.get("NJ3T-4") is False, by)
    check("heard == source is direct", by.get("K3DNA") is True, by)
    # Direwolf appends `?` when unsure which station it copied. Comparing the
    # raw strings counted every uncertain direct copy as digipeated.
    check("an uncertain `?` is stripped from both sides before comparing",
          by.get("WO3T-1") is True, by)
    check("and `?` does not survive into the station name", "WO3T-1?" not in by, by)
    check("an empty heard column counts as direct", by.get("W3TM-10") is True, by)

    print("\ndistance belongs to the packets actually copied directly")
    cov = hs.Coverage(hs.Origin(40.770833, -77.791667))
    cov.add("MOBILE-9", True, 40.80, -77.80)      # a few miles away, copied direct
    cov.add("MOBILE-9", False, 38.00, -80.00)     # far away, and only digipeated
    d = cov.stations["MOBILE-9"]["dist"]
    check("only the direct packet contributes a distance", len(d) == 1, d)
    check("and it is the near one, not the far one", d[0] < 10, d)
    check("the digipeated packet still counts in the denominator",
          cov.stations["MOBILE-9"]["total"] == 2, cov.stations["MOBILE-9"])

    print("\nthis station's own beacons, heard back after a repeat")
    cov = hs.Coverage(hs.Origin(40.770833, -77.791667), exclude=("KD3CCO-10",))
    for _ in range(306):
        cov.add("KD3CCO-10", False, 40.770833, -77.791667)   # own beacon, repeated
    for _ in range(4):
        cov.add("W3TM-10", True, 40.770833, -77.791667)      # somebody else, same cell
    cov.add("W3TM-10", False, 40.770833, -77.791667)
    check("own beacons are counted as excluded, not as traffic",
          cov.excluded == 306 and cov.placed == 5, (cov.excluded, cov.placed))
    check("and leave no station entry behind",
          "KD3CCO-10" not in cov.stations, list(cov.stations))
    # With them in, this cell reads 4/311 = 1.3% and the home cell looks like
    # the worst on the map. Without them it reads 4/5 = 80%.
    d, t = cov.cells[(0, 0)]
    check("the home cell reports the share of OTHER stations' traffic",
          (d, t) == (4, 5), (d, t))
    keep = hs.Coverage(hs.Origin(40.770833, -77.791667))
    for _ in range(306):
        keep.add("KD3CCO-10", False, 40.770833, -77.791667)
    check("--include-own keeps them, for anyone who wants that",
          keep.cells[(0, 0)][1] == 306, keep.cells[(0, 0)])

    print("\nthe small-sample screen")
    cov = hs.Coverage(hs.Origin(40.770833, -77.791667))
    for i in range(1264):
        cov.add("NJ3T-4", False, 39.807, -79.172)
    cov.add("NJ3T-4", True, 39.807, -79.172)      # one lucky frame in 1265
    for i in range(400):
        cov.add("K3DNA", i % 3 == 0, 40.900, -77.700)
    solid, artefact = cov.solid_stations()
    check("one frame in 1265 is an artefact, not a 98-mile path",
          "NJ3T-4" in artefact and "NJ3T-4" not in solid, (list(solid), list(artefact)))
    check("a station copied a third of the time is a real path",
          "K3DNA" in solid, list(solid))

    print("\ncells and the file they are written to")
    cov = hs.Coverage(hs.Origin(40.770833, -77.791667))
    cov.add("A", True, 40.770833, -77.791667)
    check("the origin falls in cell (0,0) by construction",
          (0, 0) in cov.cells, list(cov.cells))
    far = cov.origin.cell(40.770833, -77.791667 + 0.2)
    check("a cell to the east has a larger x index", far[0] > 0, far)
    cov.add("B", False, 40.90, -77.70)
    work = tempfile.mkdtemp()
    p = os.path.join(work, "cells-t.tsv")
    hs.write_cells(cov, p, "t", ["fixture"])
    meta, back = hs.read_cells(p)
    check("cells survive the round trip", back == dict(cov.cells), (back, dict(cov.cells)))
    check("the origin is recorded so a later compare can refuse a mismatch",
          meta["origin_lat"].startswith("40.7708"), meta)

    q = os.path.join(work, "cells-u.tsv")
    hs.write_cells(hs.Coverage(hs.Origin(41.5, -77.0)), q, "u", ["fixture"])
    check("comparing captures with different origins is refused",
          _raises(lambda: hs.compare(p, q)))

    print("\nagainst the real 2026-10-30 capture")
    real = os.path.join(HERE, "archive-2026-10", "frames-15ft-2026-10-30.log.gz")
    if os.path.isfile(real):
        conf = os.path.join(HERE, "igate.conf")
        mycall = hs.conf_get(conf, "MYCALL")
        check("MYCALL is readable from the config", bool(mycall), repr(mycall))
        cov = hs.Coverage(hs.Origin(*hs.load_config(conf)), exclude=(mycall,))
        with hs._open(real) as fh:
            for rec in hs.read_console(fh):
                cov.add(*rec)
        solid, artefact = cov.solid_stations()
        direct = sum(c[0] for c in cov.cells.values())
        check(f"direct packets ({direct:,})", direct == 4430, direct)
        check(f"cells carrying traffic ({len(cov.cells)})", len(cov.cells) == 1236, len(cov.cells))
        check("cells with a direct copy",
              sum(1 for c in cov.cells.values() if c[0]) == 46,
              sum(1 for c in cov.cells.values() if c[0]))
        check(f"stations with a real direct path ({len(solid)})", len(solid) == 14, len(solid))
        check(f"stations screened out ({len(artefact)})", len(artefact) == 8, len(artefact))
        check(f"own beacons heard back and excluded ({cov.excluded})",
              cov.excluded == 306, cov.excluded)
        check("this station does not appear in its own receive map",
              mycall not in cov.stations, [c for c in cov.stations if c.startswith("KD3CCO")])
        home = cov.cells[(0, 0)]
        check(f"the home cell reads {100*home[0]/home[1]:.0f}% direct, not 1%",
              home[1] < 20 and home[0] / home[1] > 0.5, home)
        # WO3T-1 is a mountaintop digipeater copied on 97% of its frames.
        check("WO3T-1 is copied directly and lands near 26 miles",
              "WO3T-1" in solid and 25 < sorted(solid["WO3T-1"]["dist"])[len(solid["WO3T-1"]["dist"])//2] < 27,
              solid.get("WO3T-1", {}).get("direct"))
    else:
        print("  SKIP  real-capture checks (archive-2026-10/ not present)")

    print()
    print(f"{PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("ALL PASS")
    return 1 if FAIL else 0


def _raises(fn):
    try:
        fn()
    except BaseException:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
