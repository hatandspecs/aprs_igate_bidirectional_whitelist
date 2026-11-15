#!/usr/bin/env python3
"""The longest frame a digipeater will EMIT, and whether longer ones existed.

This answers a different question from digi_length_profile.py. That tool
measures the length a digipeater had to DECODE, which is a statement about its
receiver. This one measures what it put back on the air, which is a statement
about whatever decides to relay. The two differ by the 7 bytes of an inserted
callsign, and that difference is what converts a ceiling on emissions into a
limit on senders.

Found on 2026-11-14: W3YA-1 emits nothing above 97 on-air bytes across 8,218
relayed frames, while 9,517 received frames of 98 bytes or more were on the
channel in the same log. Its own beacons reach 107, so its transmitter is not
the limit. Because it inserts its callsign ahead of the alias it consumes, a
frame of 90 bytes as sent is relayed as 97 and 91 becomes 98, which has never
been observed. So 90 bytes sent is the hard limit for every station, and this
station's gated messages are 91 to 150.

WHY THE RECONSTRUCTION MATTERS
    A frame that a later digipeater also repeated reaches this receiver longer
    than the subject emitted it. Counting only frames where the subject was
    the last used hop avoids that but discards the longest frames, since more
    digipeaters means both a longer frame and a likelier later hop, which can
    manufacture a ceiling. So this removes 7 bytes per used hop that came
    after the subject and keeps every frame. Both cuts are reported, and they
    agreeing is the check that the filter did not invent the answer.

THE SUPPLY COLUMN IS THE CONTROL
    A digipeater that emits nothing above N has either a limit at N or nothing
    longer to relay. Without the supply figure the two are indistinguishable,
    and the conclusion is worth nothing.

    zcat archive/frames/frames-2026-11-*.log.gz | ./digi_emission_ceiling.py --digi W3YA-1 -
"""
import argparse
import collections
import gzip
import re
import sys

RF = re.compile(r"^\[\d+\.\d+(?:\.\d+)?(?: [^\]]*)?\]"
                r"\s+(?P<src>\S+?)>(?P<path>\S+?):(?P<info>.*)$")
HEXBYTE = re.compile(r"<0x[0-9a-fA-F]{2}>")
ALIAS = ("WIDE", "TRACE", "RELAY", "ECHO", "GATE", "ARISS", "SGATE")
ON_AIR_OVERHEAD = 4          # opening flag, FCS, closing flag


def onair(ndigis, infolen):
    """Same arithmetic as digi_length_profile.py and linktest_sweep.py."""
    return 7 * (2 + ndigis) + 1 + 1 + infolen + ON_AIR_OVERHEAD


def is_alias(token):
    return token.upper().startswith(ALIAS)


def streams_for(paths):
    for f in paths:
        if f == "-":
            sys.stdin.reconfigure(encoding="latin-1")
            yield sys.stdin
            continue
        with open(f, "rb") as probe:
            gz = probe.read(2) == b"\x1f\x8b"
        yield gzip.open(f, "rt", encoding="latin-1") if gz else \
            open(f, encoding="latin-1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="+", help="Direwolf text log(s), or - for stdin")
    ap.add_argument("--digi", required=True, help="the digipeater to measure")
    ap.add_argument("--tail", type=int, default=12,
                    help="how many lengths of tail detail to print")
    a = ap.parse_args()
    digi = a.digi.upper()

    own, emitted, lasthop, supply = [], [], [], []
    for stream in streams_for(a.log):
        for line in stream:
            m = RF.match(line.rstrip("\n"))
            if not m:
                continue
            info = HEXBYTE.sub(".", m.group("info"))
            hops = m.group("path").split(",")[1:]
            length = onair(len(hops), len(info))
            supply.append(length)
            if m.group("src").upper() == digi:
                own.append(length)
                continue
            bare = [h.rstrip("*").upper() for h in hops]
            if digi not in bare:
                continue
            used = [i for i, h in enumerate(hops) if h.endswith("*")]
            if not used:
                continue
            idx = bare.index(digi)
            after = [i for i in used if i > idx]
            emitted.append(length - 7 * len(after))
            if not after:
                lasthop.append(length)

    if not emitted and not own:
        sys.exit("no frames involving %s found; if the log is compressed this "
                 "script handles it, so check --digi" % a.digi)

    def summary(name, v):
        if not v:
            print("%-44s none" % name)
            return
        v = sorted(v)
        print("%-44s n=%-6d min=%-4d median=%-4d max=%d"
              % (name, len(v), v[0], v[len(v) // 2], v[-1]))

    print("What %s transmits\n" % a.digi)
    summary("frames it originated", own)
    summary("frames it relayed, reconstructed", emitted)
    summary("  same, only where it was last hop", lasthop)
    summary("every frame this receiver copied (supply)", supply)

    if not emitted:
        return
    top = max(emitted)
    ce, cs = collections.Counter(emitted), collections.Counter(supply)
    print("\nTail of what it relayed, against the supply at those lengths:")
    print("   %6s %8s %12s %12s" % ("bytes", "relayed", "rel at/above", "supply at/above"))
    for L in range(top - a.tail + 1, top + 4):
        ra = sum(n for b, n in ce.items() if b >= L)
        sa = sum(n for b, n in cs.items() if b >= L)
        print("   %6d %8d %12d %15d" % (L, ce[L], ra, sa))
    over = sum(n for b, n in cs.items() if b > top)
    print("\nCeiling: %d on-air bytes. Supply above it: %d frames." % (top, over))
    if over:
        print("Longer frames were available to relay and none was, so the "
              "ceiling is not a supply artifact.")
        print("With a 7-byte callsign insertion that caps any sender at %d "
              "bytes as sent." % (top - 7))
    else:
        print("No longer frames existed to relay, so this is a supply floor "
              "and not a demonstrated ceiling.")


if __name__ == "__main__":
    main()
