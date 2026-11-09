#!/usr/bin/env python3
"""What frame lengths does a digipeater actually repeat?

Reads a Direwolf text log and reports, for one digipeater, the distribution of
the frame lengths it put on the air. Counts only frames where the digipeater
was the first hop, so it heard them off air directly rather than from another
digipeater, and only frames where no other callsign was used after it, so the
frame as logged is what the digipeater transmitted.

Decoded length is the retransmitted length less 7 bytes where the digipeater
inserted its own callsign ahead of an alias, because that address was not
present in the frame it had to decode.

On-air length = 7 * addresses + control + PID + information + flag + FCS + flag,
the same arithmetic linktest_sweep.py uses, so the numbers are comparable to a
length sweep.

With --mine, also reports that station's own transmissions by length against
how many the digipeater repeated. That is the measurement with a denominator:
the station table alone shows what got through and never what was missed.

    python3 digi_length_profile.py --digi W3YA-1 frames.log
    zcat archive/frames/frames-2026-11-*.log.gz |
        python3 digi_length_profile.py --digi W3YA-1 -

TIMESTAMPS AND WHY THEY MATTER
------------------------------
Direwolf writes a timestamp only when started with -T, and puts it INSIDE the
existing bracket: "[0.3]" becomes "[0.3 2026-11-08T21:56:02-0500]", and "[0L]"
becomes "[0L 2026-11-08T21:56:02-0500]". This station passes
-T "%Y-%m-%dT%H:%M:%S%z"; see deploy_igate.sh and entrypoint.sh.

Logs written before that change carry no time at all. They still parse, and the
report says how many lines were undated.

The timestamps are the only defense against two real errors. An archived chunk
is 8 MB of logrotate output named for the day the archiver first saw it, not
for the day it covers, so a file called frames-2026-11-08 may hold several days
ending near then. And Direwolf logs its own transmit lines whether or not RF
left the antenna, so frames queued during an antenna outage inflate the
denominator and depress every rate computed over that window. --since and
--until exclude such a window by time.
"""
import argparse
import collections
import datetime
import re
import statistics
import sys

# The optional -T timestamp sits inside the bracket after the channel. A third
# number appears on receive when both multiple subchannels and multiple slicers
# are configured; an "F" appears on transmit in some FX.25 builds.
RF = re.compile(
    r"^\[\d+\.\d+(?:\.\d+)?(?: (?P<ts>[^\]]*))?\]"
    r"\s+(?P<src>\S+?)>(?P<path>\S+?):(?P<info>.*)$")
TX = re.compile(
    r"^\[\d+[LH]F?(?: (?P<ts>[^\]]*))?\]"
    r"\s+(?P<src>\S+?)>(?P<path>\S+?):(?P<info>.*)$")
HEXBYTE = re.compile(r"<0x[0-9a-fA-F]{2}>")
ALIAS = ("WIDE", "TRACE", "RELAY", "ECHO", "GATE", "ARISS", "SGATE")
ON_AIR_OVERHEAD = 4
TS_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
              "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S")


def is_alias(token):
    return token.upper().startswith(ALIAS)


def onair(digis, infolen):
    return 7 * (2 + len(digis)) + 1 + 1 + infolen + ON_AIR_OVERHEAD


def parse_ts(text):
    if not text:
        return None
    for fmt in TS_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_bound(text, end):
    """A --since/--until value: a date, or a date and time."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            t = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end:
            t += datetime.timedelta(days=1)
        return t
    raise SystemExit(f"digi_length_profile: cannot read {text!r} as a time")


def comparable(when, bound):
    """Compare naive and aware stamps without crashing on a mixed log."""
    if when.tzinfo is not None and bound.tzinfo is None:
        return when.replace(tzinfo=None)
    if when.tzinfo is None and bound.tzinfo is not None:
        return when.replace(tzinfo=bound.tzinfo)
    return when


class Window:
    def __init__(self, since, until):
        self.since = since
        self.until = until
        self.first = self.last = None
        self.dated = 0
        self.undated = 0
        self.excluded = 0

    def keep(self, raw):
        when = parse_ts(raw)
        if when is None:
            self.undated += 1
            return self.since is None and self.until is None
        self.dated += 1
        if self.first is None or when < self.first:
            self.first = when
        if self.last is None or when > self.last:
            self.last = when
        if self.since and comparable(when, self.since) < self.since:
            self.excluded += 1
            return False
        if self.until and comparable(when, self.until) >= self.until:
            self.excluded += 1
            return False
        return True

    def report(self):
        if self.first:
            print(f"Dated frames ({self.dated}) cover "
                  f"{self.first.isoformat()} to {self.last.isoformat()}")
        if self.undated:
            note = ("counted" if self.since is None and self.until is None
                    else "SKIPPED, because --since/--until was given")
            print(f"Undated frames (logged before direwolf -T was added): "
                  f"{self.undated}, {note}")
        if self.undated and self.dated:
            print("This log straddles the change, so the span above describes "
                  "only part of it.")
        if self.excluded:
            print(f"Frames outside the requested window: {self.excluded}")
        if self.first or self.undated:
            print()


def profile(lines, digi, mine, window):
    digi = digi.upper()
    mine = (mine or "").upper()
    direct, relayed = [], []
    by_source = collections.defaultdict(list)
    sent = collections.Counter()      # own transmissions, by on-air length
    echoed = collections.Counter()    # own frames this digipeater repeated

    for line in lines:
        line = line.rstrip("\n")

        t = TX.match(line)
        if t:
            if mine and t.group("src").upper() == mine and window.keep(t.group("ts")):
                digis = t.group("path").split(",")[1:]
                sent[onair(digis, len(HEXBYTE.sub("X", t.group("info"))))] += 1
            continue

        m = RF.match(line)
        if not m:
            continue
        source, pathpart = m.group("src"), m.group("path")
        digis = pathpart.split(",")[1:]
        used = [i for i, x in enumerate(digis) if x.endswith("*")]
        if not used:
            continue
        last_used = used[-1]
        here = [i for i, x in enumerate(digis)
                if x.rstrip("*").upper() == digi]
        if not here or here[0] > last_used:
            continue          # in the path but not yet used
        at = here[0]
        after = [x.rstrip("*") for x in digis[at + 1:last_used + 1]]
        if any(not is_alias(x) for x in after):
            continue          # someone else repeated it after this digipeater
        if not window.keep(m.group("ts")):
            continue
        before = [x.rstrip("*") for x in digis[:at]]
        first_hop = not any(not is_alias(x) for x in before)

        infolen = len(HEXBYTE.sub("X", m.group("info")))
        tx = onair(digis, infolen)
        rx = tx - (7 if after else 0)
        (direct if first_hop else relayed).append(rx)
        if first_hop:
            by_source[source].append(rx)
            if mine and source.upper() == mine:
                echoed[rx] += 1

    return direct, relayed, by_source, sent, echoed


def report(name, lengths, thresholds):
    if not lengths:
        print(f"{name}: none")
        return
    lengths = sorted(lengths)
    print(f"{name}: {len(lengths)} frames, "
          f"min {lengths[0]}, median {statistics.median(lengths):.0f}, "
          f"max {lengths[-1]}")
    for t in thresholds:
        n = sum(1 for x in lengths if x > t)
        print(f"   over {t:3d} bytes: {n:6d}  ({100 * n / len(lengths):.1f}%)")
    bins = collections.Counter((x // 10) * 10 for x in lengths)
    for k in sorted(bins):
        print(f"   {k:3d}-{k + 9:3d}  {bins[k]:6d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="+",
                    help="Direwolf text log(s), or - for stdin")
    ap.add_argument("--digi", required=True, help="digipeater callsign-SSID")
    ap.add_argument("--over", type=int, nargs="*", default=[75, 82, 90],
                    help="thresholds to count above")
    ap.add_argument("--mine", default="KD3CCO-10",
                    help="own callsign; always reported, however short its frames")
    ap.add_argument("--stations", type=int, default=20,
                    help="how many source stations to list")
    ap.add_argument("--since", help="ignore frames before this date or time")
    ap.add_argument("--until", help="ignore frames at or after this date or time")
    args = ap.parse_args()

    window = Window(parse_bound(args.since, False) if args.since else None,
                    parse_bound(args.until, True) if args.until else None)

    if args.log == ["-"]:
        streams = [sys.stdin]
    else:
        streams = [open(f, encoding="latin-1") for f in args.log]

    direct, relayed = [], []
    by_source = collections.defaultdict(list)
    sent, echoed = collections.Counter(), collections.Counter()
    for stream in streams:
        d, r, b, s_, e_ = profile(stream, args.digi, args.mine, window)
        direct += d
        relayed += r
        for k, v in b.items():
            by_source[k] += v
        sent += s_
        echoed += e_

    print(f"Frames {args.digi} repeated, by the length it had to decode\n")
    window.report()
    report("heard off air directly (the comparable set)", direct, args.over)
    print()
    report("heard from another digipeater", relayed, args.over)

    print("\nSource stations, by the longest frame it repeated for them:")
    rows = sorted(((max(v), len(v), s) for s, v in by_source.items()),
                  reverse=True)
    mine = args.mine.upper()
    shown = rows[:args.stations]
    if not any(r[2].upper() == mine for r in shown):
        shown += [r for r in rows if r[2].upper() == mine]
    for longest, count, source in shown:
        mark = "  <- own station" if source.upper() == mine else ""
        print(f"   {longest:3d} bytes  {source:12s}  {count:6d} frames{mark}")
    if not any(r[2].upper() == mine for r in rows):
        print(f"   {args.mine}: no frames repeated on a first hop in this log")

    if sent:
        print(f"\n{args.mine} transmissions in this log, and how many "
              f"{args.digi} repeated.")
        print("A length this station never sent carries no information about "
              "it.")
        print("Transmit lines are logged even with the antenna down or the "
              "radio off,")
        print("so exclude any known outage with --since/--until before "
              "trusting a rate.\n")
        print(f"   {'on-air bytes':>12}  {'sent':>6}  {'repeated':>8}  rate")
        for n in sorted(set(sent) | set(echoed)):
            rate = f"{100 * echoed[n] / sent[n]:.0f}%" if sent[n] else "n/a"
            print(f"   {n:12d}  {sent[n]:6d}  {echoed[n]:8d}  {rate:>5}")
        long_sent = sum(v for k, v in sent.items() if k > 82)
        long_echo = sum(v for k, v in echoed.items() if k > 82)
        print(f"\n   over 82 bytes: sent {long_sent}, repeated {long_echo}")


if __name__ == "__main__":
    main()
