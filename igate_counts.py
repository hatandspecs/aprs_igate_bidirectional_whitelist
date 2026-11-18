#!/usr/bin/env python3
"""Exact per-UTC-day traffic counts for the APRS iGate.

Why this exists as its own module
---------------------------------
`igate-archive.sh` used to count transmit activity over the WHOLE console log
and write the result into a row labeled with a single date. That is a rolling
window of roughly eight days wearing a daily column heading, and at
`BEACON_EVERY = 2:00:00` it reads about eight times high. Those columns are
still written for continuity, now prefixed `win_`; the `day_` columns come from
here and are exact for the named UTC day.

The counts live in one place because the classification has been wrong twice
already (see igate_web.py's header). `igate-archive.sh` calls the CLI below and
the web monitor imports the same constants, so neither can drift from the other.

What a line is
--------------
Direwolf's console log is a stream of bracketed records:

    [0.3 2026-11-08T21:56:02-0500] WO3T-1>APNU3B,WIDE1*:!...   heard on RF
    [0L 2026-11-08T21:56:02-0500] KD3CCO-10>APDW17,WIDE1-1:!…  put on the air
    [rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!…                     gated UP to APRS-IS
    [rx>ig] #                                                  APRS-IS keepalive
    [ig>tx] N8GD-1>APRS,TCPIP*,qAC,T2…:…                       offered BY APRS-IS
    [ig] …                                                     server chatter

`[0L]` against `[0.N]` is the transmit/receive split, and `^\\[0` taken
together is what `deploy_igate.sh reach` calls a frame, so `day_frames` is
directly comparable with a `reach` run.

Timestamps, and the one assumption in here
------------------------------------------
Direwolf writes a timestamp only under `-T`, which this station has passed
since 2026-11-08, and it puts it INSIDE the bracket after the channel token.
It formats it with `localtime_r` while the dated CSV is named with `gmtime_r`,
so `%z` is mandatory and every stamp is converted to UTC before bucketing. A
day here is therefore the same day the dated CSV means.

**The igate lines `[rx>ig]`, `[ig>tx]` and `[ig]` are not known to carry the
stamp**, so an unstamped line is attributed to the last stamp SEEN BEFORE IT.
That direction is the correct one rather than a convenience: Direwolf prints
`[rx>ig]` immediately after the `[0.N]` frame that caused it, so the carried
stamp is the frame's own, to the second. An `[ig>tx]` line on a quiet channel
can carry a stamp some minutes old, which can misplace a handful of frames
across a midnight boundary and nothing else.

**Lines before the first stamp in the file are counted as `undated`, never as
a day.** Logs from before 2026-11-08 carry no stamps at all, so a whole
archived chunk from that era reports `undated` and zeros rather than silently
crediting its traffic to one day. Reading zero as "no traffic" is exactly how
the `-T` change went unnoticed for six days.

Usage
-----
    igate_counts.py --fields                      # the column names, tab-separated
    igate_counts.py --day 2026-11-16 LOG...       # that day's values, tab-separated
    igate_counts.py --all-days LOG...             # one row per day found
    igate_counts.py --day 2026-11-16 --json LOG...
    igate_counts.py --mode stations --day 2026-11-16 CSV...

In `--mode stations` the window is exactly the set of CSVs passed, so the
caller chooses it by choosing the files; one of them must be named for `--day`.

`LOG` may be plain or gzipped and `-` reads stdin. `--all-days` over
`archive/frames/*.log.gz` rebuilds the per-day history for every chunk still
held, which is how these columns get filled in for days that predate them.
"""

import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# The event labels `deploy_igate.sh monitor` emits, mapped to the metric each
# one belongs to. The web monitor's live counters use this so the page and the
# archiver cannot disagree about what "gated up" means.
LABEL_METRIC = {
    "RF RX": "rx_frames",
    "RF->IS UP": "gated_up",
    "IS GATED": "gated_down",
    "IS DROP": "is_dropped",
    "TX LOCAL": "tx_local",
}

# Field order for the TSV the archiver appends. Append-only: every existing
# column in daily-summary.tsv keeps the index it has today, because
# test_archive.sh and anything else reading that file address columns by
# number.
CONSOLE_FIELDS = (
    "day_frames",          # ^\[0 — what `reach` counts, rx and tx together
    "day_rx_frames",       # heard on the air
    "day_tx_frames",       # put on the air, whether or not RF left the antenna
    "day_beacons",         # own position beacons transmitted
    "day_beacon_repeats",  # own beacons heard back after a digipeater
    "day_gated_up",        # frames this station gated TO APRS-IS
    "day_gated_down",      # frames gated FROM APRS-IS to RF
    "day_gated_repeats",   # own gated frames heard back after a digipeater
    "day_is_offered",      # frames APRS-IS sent this gateway, before filtering
    "day_undated",         # lines with no stamp to place them: see module docs
    "day_partial",         # 1 if the log does not cover the whole UTC day
)

STATION_FIELDS = (
    "rx_own",              # own call as a source: beacons returning off the air
    "rx_stations_win",     # unique stations over the window, a UNION not a sum
    "rx_stations_new",     # stations in the day absent from the rest of the window
)
# Both station columns exclude MYCALL; `rx_stations`, the older per-day column,
# includes it. See sources().

# The leading bracket and, inside it, the channel token and an optional stamp.
BRACKET = re.compile(r"^\[([^\]\s]+)(?:\s+([^\]]+))?\]")

# Accepted stamp shapes. The first is what this station writes; the rest are
# tolerated so an old or hand-edited capture still parses. A naive stamp is
# read as local time, which is what direwolf would have written.
TS_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
              "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S")


def parse_stamp(text):
    """A bracket's stamp as an aware UTC datetime, or None.

    Measured cost, 2026-11-17: a pass over this station's whole 10 MB packet
    log, about 250,000 lines, takes 13 s on the Pi 3B+. That is bounded
    rather than growing -- logrotate caps the log at 8 MB with two
    generations -- and `igate-archive.service` runs it once a day at Nice=10
    with idle I/O. Memoizing this function was tried and returned 13.2 s to
    12.5 s, so strptime is not where the time goes; the per-line regex is.
    Do not add a cache here expecting more.
    """
    if not text:
        return None
    text = text.strip()
    for fmt in TS_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.astimezone()          # naive: direwolf wrote local time
        return dt.astimezone(timezone.utc)
    return None


def _open(path):
    """Plain, gzipped, or stdin. errors='replace' throughout.

    Never strict: the log carries other stations' APRS comment text verbatim
    and plenty of it is Latin-1, so a degree sign in a bearing is 0xB0.
    UnicodeDecodeError is a ValueError and a broad handler upstream will
    swallow it and look like a clean end of file.
    """
    if path == "-":
        return io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8",
                                errors="replace")
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


class Counter:
    """Per-UTC-day console-log counts, plus the span the log actually covers.

    Call `feed()` with lines in the order they were written, from oldest chunk
    to newest, so the carried stamp moves forward in time.
    """

    def __init__(self, mycall):
        self.mycall = mycall
        self.days = {}                 # "YYYY-MM-DD" -> {field: int}
        self.undated = 0
        self.first = None              # earliest stamp seen, UTC
        self.last = None               # latest stamp seen, UTC
        self._carry = None
        # Built once per instance rather than per line: MYCALL is fixed and
        # these run over millions of lines when rebuilding from the archive.
        c = re.escape(mycall)
        self._own_beacon = re.compile(r"^%s>[^:]*:!" % c)
        self._own_gated = re.compile(r"^%s>[^:]*:\}" % c)
        # A used hop BEFORE the first colon. A third-party payload contains
        # `TCPIP,MYCALL*` after the colon, and matching the whole line counts
        # the gateway as its own digipeater -- the bug that broke `reach`.
        self._own_repeat_beacon = re.compile(r"^%s>[^:]*\*[^:]*:!" % c)
        self._own_repeat_gated = re.compile(r"^%s>[^:]*\*[^:]*:\}" % c)

    def _bump(self, day, *fields):
        row = self.days.get(day)
        if row is None:
            row = self.days[day] = dict.fromkeys(CONSOLE_FIELDS, 0)
        for f in fields:
            row[f] += 1

    def feed(self, lines):
        for line in lines:
            m = BRACKET.match(line)
            if not m:
                continue                      # audio levels, decodes, blanks
            kind, stamp_text = m.group(1), m.group(2)
            stamp = parse_stamp(stamp_text)
            if stamp is not None:
                self._carry = stamp
                if self.first is None or stamp < self.first:
                    self.first = stamp
                if self.last is None or stamp > self.last:
                    self.last = stamp
            when = self._carry
            body = line[m.end():].lstrip()

            fields = self._classify(kind, body)
            if not fields:
                continue
            if when is None:
                self.undated += 1
                continue
            self._bump(when.strftime("%Y-%m-%d"), *fields)

    def _classify(self, kind, body):
        """Which metrics this line belongs to. Order mirrors monitor_filter."""
        if kind == "rx>ig":
            # The server's keepalive is a bare `#` and is not a packet.
            return () if body.startswith("#") else ("day_gated_up",)
        if kind == "ig>tx":
            return ("day_is_offered",)
        if kind == "ig":
            return ()
        if kind == "0L":
            out = ["day_frames", "day_tx_frames"]
            if self._own_beacon.match(body):
                out.append("day_beacons")
            elif self._own_gated.match(body):
                out.append("day_gated_down")
            return tuple(out)
        if kind.startswith("0."):
            out = ["day_frames", "day_rx_frames"]
            if self._own_repeat_beacon.match(body):
                out.append("day_beacon_repeats")
            elif self._own_repeat_gated.match(body):
                out.append("day_gated_repeats")
            return tuple(out)
        return ()

    def row(self, day):
        """One day's values in CONSOLE_FIELDS order.

        `day_partial` is the honest half of this module. The console log lives
        in a tmpfs that a reboot clears and rotates away at eight days, so a
        day it does not fully cover is undercounted -- while the receive
        columns, which come from the card, are intact for the same day. A row
        that does not say which of those it is cannot be used.
        """
        row = dict(self.days.get(day, dict.fromkeys(CONSOLE_FIELDS, 0)))
        row["day_undated"] = self.undated
        row["day_partial"] = 1 if self.partial(day) else 0
        return [row[f] for f in CONSOLE_FIELDS]

    def partial(self, day):
        if self.first is None:
            return True                      # nothing stamped: covers nothing
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return self.first > start or self.last < start + timedelta(days=1)

    def all_days(self):
        return sorted(self.days)


# --------------------------------------------------------------------------
# stations: set arithmetic over Direwolf's dated CSVs
# --------------------------------------------------------------------------
def sources(path, exclude=()):
    """The distinct `source` values in one dated CSV.

    Direwolf appends `?` when it is unsure which station it copied; the raw
    strings would then count one station twice.

    `exclude` is how MYCALL is kept out. This station's own beacons come back
    off the air after a digipeater repeats them -- 319 frames in the October
    capture -- so the gateway appears in its own heard list. It is a reception
    but it is not a station this receiver reached, and how many come back is a
    function of antenna height rather than of coverage.

    **This differs on purpose from the older `rx_stations` column**, which
    `igate-archive.sh` computes in awk and which DOES include MYCALL. That
    column has months of history behind it and redefining it would break the
    series, so the own-call packets are broken out into `rx_own` instead and
    the window columns here exclude them.
    """
    drop = set(exclude)
    out = set()
    try:
        with _open(path) as fh:
            for row in csv.DictReader(fh):
                src = (row.get("source") or "").strip()
                if src.endswith("?"):
                    src = src[:-1]
                if src and src not in drop:
                    out.add(src)
    except OSError:
        pass
    return out


def station_row(day_path, window_paths, mycall):
    """Own-call count for the day, plus the window union and what is new in it.

    The union is the point. Seven daily unique-station counts added together
    is not the number of stations heard in seven days, because most stations
    are heard on more than one of them, and nothing recovers the true figure
    from the sum afterwards.
    """
    own = 0
    try:
        with _open(day_path) as fh:
            for row in csv.DictReader(fh):
                src = (row.get("source") or "").strip().rstrip("?")
                if src == mycall:
                    own += 1
    except OSError:
        pass
    today = sources(day_path, exclude=(mycall,))
    others = set()
    for p in window_paths:
        if os.path.abspath(p) != os.path.abspath(day_path):
            others |= sources(p, exclude=(mycall,))
    return [own, len(today | others), len(today - others)]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="exact per-UTC-day traffic counts for the APRS iGate")
    p.add_argument("files", nargs="*", metavar="LOG",
                   help="console logs (plain or .gz, oldest first), or dated "
                        "CSVs with --mode stations; - reads stdin")
    p.add_argument("--mode", choices=("console", "stations"), default="console")
    p.add_argument("--day", help="UTC day to report, YYYY-MM-DD")
    p.add_argument("--all-days", action="store_true",
                   help="one row per day present, instead of one day")
    p.add_argument("--mycall", default="KD3CCO-10")
    p.add_argument("--fields", action="store_true",
                   help="print the column names for the chosen mode and exit")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    fields = CONSOLE_FIELDS if a.mode == "console" else STATION_FIELDS
    if a.fields:
        print("\t".join(fields))
        return 0

    if a.mode == "stations":
        if not a.day or not a.files:
            p.error("--mode stations needs --day and the dated CSVs")
        day_path = next((f for f in a.files
                         if os.path.basename(f).startswith(a.day)), None)
        if day_path is None:
            # No CSV for the day: zeros, not an error. A day with no log is a
            # real state and the archiver must still write its row.
            vals = [0, 0, 0]
        else:
            vals = station_row(day_path, a.files, a.mycall)
        print(json.dumps(dict(zip(fields, vals))) if a.json
              else "\t".join(str(v) for v in vals))
        return 0

    counter = Counter(a.mycall)
    for path in a.files:
        try:
            with _open(path) as fh:
                counter.feed(fh)
        except OSError as exc:
            print(f"igate_counts: {path}: {exc}", file=sys.stderr)

    if a.all_days:
        days = counter.all_days()
        if a.json:
            print(json.dumps({d: dict(zip(fields, counter.row(d)))
                              for d in days}))
        else:
            print("day\t" + "\t".join(fields))
            for d in days:
                print(d + "\t" + "\t".join(str(v) for v in counter.row(d)))
        return 0

    if not a.day:
        p.error("--day is required unless --all-days or --fields")
    vals = counter.row(a.day)
    print(json.dumps(dict(zip(fields, vals))) if a.json
          else "\t".join(str(v) for v in vals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
