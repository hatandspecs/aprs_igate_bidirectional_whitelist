#!/usr/bin/env python3
"""Bounds on the web monitor's logging panel.

The monitor has been taken down before by work that was cheap on day one and
linear in something that only grows. It has run a month without that since the
last round of fixes, so a new panel on it carries the burden of proof.

This builds a station far past anything realistic -- a dated log an order of
magnitude bigger than a real day, years of summary rows, thousands of archived
chunks -- and asserts that `LoggingStatus.get()` stays fast and stays small.
Not that it works: that it does not grow.

    ./test_web_logging.py
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location(
    "igate_web", os.path.join(HERE, "igate_web.py"))
igate_web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(igate_web)
LoggingStatus = igate_web.LoggingStatus

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


def build_station(root, *, day_mb, summary_days, chunks):
    """A station directory with the given amount of accumulated data."""
    os.makedirs(os.path.join(root, "run"), exist_ok=True)
    logs = os.path.join(root, "logs")
    frames = os.path.join(root, "archive", "frames")
    os.makedirs(logs, exist_ok=True)
    os.makedirs(frames, exist_ok=True)
    with open(os.path.join(root, "igate.conf"), "w") as fh:
        fh.write(f"MYCALL = KD3CCO-10\nLOG_DIR = {logs}\n"
                 f"ARCHIVE_DIR = {os.path.join(root, 'archive')}\n")

    # Today's dated log, at the requested size.
    today = time.strftime("%Y-%m-%d", time.gmtime())
    row = ("0,1793386908,2026-10-30T19:01:48Z,NJ3T-4,W3YA-1,24(7/4),0,!,"
           "NJ3T-4,S#,39.807000,-79.172167,,,,,,,KPC-3,,,, comment\n")
    with open(os.path.join(logs, today + ".log"), "w") as fh:
        fh.write("chan,utime,isotime,source,heard,level,error,dti,name,"
                 "symbol,latitude,longitude,speed,course,altitude,frequency,"
                 "offset,tone,system,status,telemetry,comment\n")
        written = 0
        target = day_mb * 1024 * 1024
        while written < target:
            fh.write(row)
            written += len(row)

    # Years of summary rows.
    with open(os.path.join(root, "archive", "daily-summary.tsv"), "w") as fh:
        fh.write("# header comment\n")
        fh.write("date\trx_packets\trx_direct\trx_stations\trx_stations_direct"
                 "\twin_frames\twin_beacons\twin_beacon_repeats\twin_gated"
                 "\twin_gated_repeats\tchunks_saved\n")
        for i in range(summary_days):
            fh.write(f"2024-01-01\t{i}\t{i}\t{i}\t{i}\t{i}\t{i}\t{i}\t{i}\t{i}\t1\n")

    # A deep archive.
    for i in range(chunks):
        with open(os.path.join(frames, f"frames-2026-01-01-{i:08x}.log.gz"),
                  "wb") as fh:
            fh.write(b"\x1f\x8b\x08\x00" + bytes(64))

    # A live packet log and its rotated generations.
    with open(os.path.join(root, "run", "direwolf.log"), "w") as fh:
        fh.write("[0L] KD3CCO-10>APDW17,WIDE1-1:!test\n" * 1000)
    for suffix in (".1.gz", ".2.gz"):
        with open(os.path.join(root, "run", "direwolf.log" + suffix), "wb") as fh:
            fh.write(b"\x1f\x8b\x08\x00" + bytes(64))


def timed(ls):
    ls.at = 0.0                       # defeat the cache; measure real work
    t0 = time.monotonic()
    out = ls.get()
    return out, time.monotonic() - t0


def main():
    work = tempfile.mkdtemp()
    try:
        print("a station on its first day")
        small = os.path.join(work, "small")
        build_station(small, day_mb=1, summary_days=2, chunks=2)
        ls = LoggingStatus(small)
        out, dt = timed(ls)
        check("it reports today's log", out["today"]["present"], out["today"])
        check("with a row count while the file is small",
              out["today"]["rows"] is not None, out["today"])
        check("and two chunks", out["chunks"] == 2, out["chunks"])
        check("quickly", dt < 1.0, f"{dt:.3f}s")

        print("\na station that has filled up: 40 MB day, 3 years, 4000 chunks")
        big = os.path.join(work, "big")
        build_station(big, day_mb=40, summary_days=1100, chunks=4000)
        lb = LoggingStatus(big)
        out, dt = timed(lb)

        # The whole point. 40 MB is ~15x a real day; the panel must not read it.
        check("the oversized dated log is NOT line-counted",
              out["today"]["rows"] is None, out["today"])
        check("but its size is still reported",
              out["today"]["bytes"] > 40_000_000, out["today"]["bytes"])
        check("and its age, which is what answers 'is it filling'",
              out["today"]["age_s"] is not None, out["today"])

        check("three years of summary rows cost only the tail",
              len(out["summary_rows"]) == 3, len(out["summary_rows"]))
        check("and the header is still found",
              out["summary_header"][0] == "date", out["summary_header"])

        check("4000 chunks are counted", out["chunks"] == 4000, out["chunks"])

        # The hard number: a filled station must stay well under the poll
        # interval, on hardware much slower than this laptop.
        check(f"collection stays fast ({dt:.3f}s)", dt < 2.0, f"{dt:.3f}s")

        payload = json.dumps(out)
        check(f"the JSON payload stays small ({len(payload)} bytes)",
              len(payload) < 8192, len(payload))

        print("\nthe scan limit stops a runaway directory")
        huge = os.path.join(work, "huge")
        build_station(huge, day_mb=1, summary_days=2,
                      chunks=LoggingStatus.SCAN_LIMIT + 500)
        lh = LoggingStatus(huge)
        out, dt = timed(lh)
        check("it stops at the limit rather than walking forever",
              out["chunks"] <= LoggingStatus.SCAN_LIMIT, out["chunks"])
        check("and says so, instead of reporting a wrong total",
              out["chunks_truncated"] is True, out["chunks_truncated"])
        check(f"still fast ({dt:.3f}s)", dt < 3.0, f"{dt:.3f}s")

        print("\nnothing present at all")
        empty = os.path.join(work, "empty")
        os.makedirs(os.path.join(empty, "run"), exist_ok=True)
        with open(os.path.join(empty, "igate.conf"), "w") as fh:
            fh.write("MYCALL = KD3CCO-10\n")
        le = LoggingStatus(empty)
        out, dt = timed(le)
        check("a station with no logs yet does not raise",
              isinstance(out, dict) and "error" not in out, out)
        check("today is simply absent", out["today"]["present"] is False,
              out["today"])
        check("and no chunks", out["chunks"] == 0, out["chunks"])

        print("\na broken directory does not take /api/status with it")
        broken = os.path.join(work, "broken")
        build_station(broken, day_mb=1, summary_days=2, chunks=2)
        lbk = LoggingStatus(broken)
        lbk.get()
        # Make the archive unreadable the way a bad card or a bad mount would.
        os.chmod(os.path.join(broken, "archive", "frames"), 0o000)
        try:
            out, _ = timed(lbk)
            check("an unreadable archive degrades rather than raising",
                  isinstance(out, dict), out)
            check("reporting zero chunks instead of crashing",
                  out.get("chunks") == 0 or "error" in out, out)
        finally:
            os.chmod(os.path.join(broken, "archive", "frames"), 0o755)

        print("\nthe cache actually caches")
        lb.at = 0.0
        lb.get()
        t0 = time.monotonic()
        lb.get()
        cached = time.monotonic() - t0
        check(f"a second call inside the TTL is nearly free ({cached:.4f}s)",
              cached < 0.01, f"{cached:.4f}s")

        print()
        print(f"{PASS} passed, {FAIL} failed")
        if FAIL == 0:
            print("ALL PASS")
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
