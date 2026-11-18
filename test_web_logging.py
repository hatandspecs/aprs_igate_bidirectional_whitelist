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
import collections
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
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

        # Eight rows, not three: a seven-day window plus the day before it.
        # The cost is unchanged because the tail read is a fixed TAIL_BYTES
        # either way -- that is the property under test, not the row count.
        check("three years of summary rows cost only the tail",
              len(out["summary_rows"]) == LoggingStatus.SUMMARY_ROWS,
              len(out["summary_rows"]))
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

        # ---------------------------------------------------------------
        print("\nthe 7-day window sums what is summable and nothing else")
        win = os.path.join(work, "win")
        build_station(win, day_mb=1, summary_days=0, chunks=1)
        # Seven consecutive days, with the day_* columns present. Unique
        # stations per day are 100 each but the union is 250, which is the
        # whole reason the archiver computes it instead of the page.
        hdr = ("date\trx_packets\trx_direct\trx_stations\trx_stations_direct"
               "\twin_frames\twin_beacons\twin_beacon_repeats\twin_gated"
               "\twin_gated_repeats\tchunks_saved\tday_frames\tday_rx_frames"
               "\tday_tx_frames\tday_beacons\tday_beacon_repeats"
               "\tday_gated_up\tday_gated_down\tday_gated_repeats"
               "\tday_is_offered\tday_undated\tday_partial\trx_own"
               "\trx_stations_win\trx_stations_new\n")
        with open(os.path.join(win, "archive", "daily-summary.tsv"), "w") as fh:
            fh.write("# comment\n")
            fh.write(hdr)
            for d in range(10, 17):
                partial = 1 if d == 13 else 0
                fh.write(f"2026-11-{d}\t1000\t400\t100\t40\t30000\t96\t50\t7"
                         f"\t1\t1\t1050\t1000\t50\t12\t6\t950\t7\t1\t8000"
                         f"\t0\t{partial}\t20\t250\t5\n")
        lw = LoggingStatus(win)
        out, _ = timed(lw)
        w = out["window"]
        check("the window is seven days wide",
              (w["first"], w["last"]) == ("2026-11-10", "2026-11-16"), w)
        check("packets heard are summed", w["rx_packets"] == 7000,
              w["rx_packets"])
        check("gated up is summed", w["day_gated_up"] == 6650,
              w["day_gated_up"])
        check("gated down is summed", w["day_gated_down"] == 49,
              w["day_gated_down"])
        # The one that must NOT be a sum. Seven days of 100 is 700; the true
        # number of distinct stations is 250.
        check("unique stations is the archiver's union, not the daily sum",
              w["stations"] == 250, w["stations"])
        check("and is nowhere near the sum of the daily counts",
              w["stations"] != 700, w["stations"])
        check("a partial day is counted and reported",
              w["days_partial"] == 1, w["days_partial"])
        check("all seven days were found", w["days_present"] == 7, w)

        print("\nan incomplete window says so rather than reporting a total")
        gap = os.path.join(work, "gap")
        build_station(gap, day_mb=1, summary_days=0, chunks=1)
        with open(os.path.join(gap, "archive", "daily-summary.tsv"), "w") as fh:
            fh.write(hdr)
            # The station was down for four of the seven days, and one of the
            # days that did run has no day_* columns at all (an older row).
            fh.write("2026-11-14\t1000\t400\t100\t40\t0\t0\t0\t0\t0\t1\n")
            fh.write("2026-11-15\t1000\t400\t100\t40\t30000\t96\t50\t7\t1\t1"
                     "\t1050\t1000\t50\t12\t6\t950\t7\t1\t8000\t0\t0\t20"
                     "\t250\t5\n")
            fh.write("2026-11-16\t1000\t400\t100\t40\t30000\t96\t50\t7\t1\t1"
                     "\t1050\t1000\t50\t12\t6\t950\t7\t1\t8000\t0\t0\t20"
                     "\t250\t5\n")
        lg = LoggingStatus(gap)
        out, _ = timed(lg)
        w = out["window"]
        check("only the days present are counted", w["days_present"] == 3, w)
        check("against the seven expected", w["days_expected"] == 7, w)
        # The short row contributes to rx_packets and not to day_gated_up, so
        # the two have different denominators. A page showing both as "last 7
        # days" without this would be comparing a 3-day sum with a 2-day one.
        check("rx_packets has three days behind it",
              w["rx_packets_days"] == 3, w["rx_packets_days"])
        check("but gated up has only two",
              w["day_gated_up_days"] == 2, w["day_gated_up_days"])
        check("an empty field is not read as a zero",
              w["day_gated_up"] == 1900, w["day_gated_up"])

        print("\nno summary at all does not raise")
        bare = os.path.join(work, "bare")
        build_station(bare, day_mb=1, summary_days=0, chunks=1)
        os.remove(os.path.join(bare, "archive", "daily-summary.tsv"))
        lbare = LoggingStatus(bare)
        out, _ = timed(lbare)
        check("the window is simply absent", out["window"] is None,
              out["window"])

        # ---------------------------------------------------------------
        print("\ntoday's traffic is read incrementally, not rescanned")
        inc = os.path.join(work, "inc")
        build_station(inc, day_mb=1, summary_days=2, chunks=1)
        li = LoggingStatus(inc)
        tt = igate_web.TodayTraffic(inc, li.log_dir, li.mycall)
        first = tt.get()
        check("it counts today's packets", first["packets"] > 0, first)
        check("and the distinct stations behind them",
              first["stations"] >= 1, first)

        # Append a known set and confirm only the new bytes are read.
        today = time.strftime("%Y-%m-%d", time.gmtime())
        path = os.path.join(inc, "logs", today + ".log")
        before = tt.offset
        with open(path, "a") as fh:
            for src in ("W3YA-1", "WO3T-1", "W3YA-1", "KD3CCO-10"):
                fh.write(f"0,1793386908,2026-11-16T19:01:48Z,{src},{src},"
                         f"24(7/4),0,!,{src},S#,39.8,-79.1,,,,,,,KPC-3,,,,x\n")
        tt.at = 0.0
        second = tt.get()
        check("four more packets are counted",
              second["packets"] == first["packets"] + 4,
              (first["packets"], second["packets"]))
        check("the offset advanced by exactly the appended bytes",
              tt.offset == os.path.getsize(path), (tt.offset, before))
        check("the station's own call is broken out, not counted as a station",
              second["own"] == 1, second)
        check("and the two new stations joined the set",
              second["stations"] == first["stations"] + 2,
              (first["stations"], second["stations"]))

        print("\na half-written row is not counted until it is finished")
        with open(path, "a") as fh:
            fh.write("0,1793386908,2026-11-16T19:01:49Z,N3DXC,N3DX")
        tt.at = 0.0
        third = tt.get()
        check("the partial line adds nothing yet",
              third["packets"] == second["packets"], third["packets"])
        with open(path, "a") as fh:
            fh.write("C,24(7/4),0,!,N3DXC,S#,39.8,-79.1,,,,,,,KPC-3,,,,x\n")
        tt.at = 0.0
        fourth = tt.get()
        check("and is counted once the newline arrives",
              fourth["packets"] == second["packets"] + 1, fourth["packets"])
        check("with the callsign reassembled correctly",
              fourth["stations"] == second["stations"] + 1,
              (second["stations"], fourth["stations"]))

        print("\nrotation or truncation resets rather than double-counting")
        with open(path, "w") as fh:          # truncate, as a new day would
            fh.write("chan,utime,isotime,source,heard,level,error,dti,name,"
                     "symbol,latitude,longitude,speed,course,altitude,"
                     "frequency,offset,tone,system,status,telemetry,comment\n")
            fh.write("0,1793386908,2026-11-16T19:01:48Z,WO3T-1,WO3T-1,24,0,!,"
                     ",,,,,,,,,,,,,x\n")
        tt.at = 0.0
        after = tt.get()
        check("the count restarts from the shortened file",
              after["packets"] == 1, after["packets"])
        check("and the station set with it", after["stations"] == 1, after)

        print("\na comma inside a comment cannot shift the source column")
        # source and heard are columns 4 and 5, ahead of every free-text
        # field, which is why splitting on commas is safe here.
        with open(path, "a") as fh:
            fh.write("0,1793386908,2026-11-16T19:02:00Z,N8GD-1,N8GD-1,24,0,!,"
                     ",,,,,,,,,,,,,\"wind 12, gust 20, temp 38\"\n")
        tt.at = 0.0
        comma = tt.get()
        check("the packet is counted", comma["packets"] == 2, comma["packets"])
        check("and attributed to the right station",
              comma["stations"] == 2, comma)

        # ---------------------------------------------------------------
        print("\nthe live counters do not double-count a pipeline restart")
        stream = igate_web.MonitorStream.__new__(igate_web.MonitorStream)
        stream.history = []
        stream.subscribers = []
        stream.lock = threading.Lock()
        stream._counts = dict.fromkeys(set(igate_web.LABEL_METRIC.values()), 0)
        stream._counts_since = time.time()
        stream._counted = 0
        stream._recent = collections.deque(
            maxlen=igate_web.MonitorStream.REPLAY_GUARD)
        stream._recent_set = set()
        stream._since_restart = None

        def ev(t, label, text):
            return {"kind": "event", "time": t, "label": label, "text": text}

        frames = [ev("09:00:0%d" % i, "RF RX", "WO3T-1>APNU3B:!x%d" % i)
                  for i in range(5)]
        frames.append(ev("09:00:06", "RF->IS UP", "WO3T-1>APNU3B:!y"))
        frames.append(ev("09:00:07", "IS GATED", "KD3CCO-1>APRS::KD3CCO-7:hi"))
        frames.append(ev("09:00:08", "IS DROP", "N8GD-1>APRS:!z"))
        for e in frames:
            stream._publish(e)
        c = stream.counts()
        check("five received frames are five", c["rx_frames"] == 5, c)
        check("one gated up", c["gated_up"] == 1, c)
        check("one gated down", c["gated_down"] == 1, c)
        check("one dropped", c["is_dropped"] == 1, c)

        # A restart replays the last lines. tail -F does this with no -n, and
        # `docker logs --tail 30` does it harder.
        stream._since_restart = 0
        for e in frames[-3:]:
            stream._publish(e)
        c = stream.counts()
        check("the replayed lines are not counted again",
              (c["rx_frames"], c["gated_up"], c["gated_down"],
               c["is_dropped"]) == (5, 1, 1, 1), c)
        check("and are not republished to viewers either",
              len(stream.history) == 8, len(stream.history))

        # Past the guard, a genuinely repeated frame counts. A beacon with the
        # same text an hour later is real traffic, not a replay.
        stream._since_restart = None
        stream._publish(ev("10:00:00", "RF RX", "WO3T-1>APNU3B:!x0"))
        check("a real repeat later in the day does count",
              stream.counts()["rx_frames"] == 6, stream.counts())

        print()
        print(f"{PASS} passed, {FAIL} failed")
        if FAIL == 0:
            print("ALL PASS")
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
