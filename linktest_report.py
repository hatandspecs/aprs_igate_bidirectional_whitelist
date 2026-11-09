#!/usr/bin/env python3
"""Recompute the link length sweep from Direwolf's text log.

The sweep script scores echoes live off the KISS socket. This reads the same
run back out of `run/direwolf.log`, which is the independent check: if the live
tally and this disagree, trust this one, because it reads what Direwolf
actually decoded rather than what a second KISS client thought it saw.

Pairing rule, the same one the sweep uses: a cycle counts only if the target
digipeater repeated that cycle's CONTROL frame. A cycle whose control went
unheard says nothing about the test frame and is excluded rather than scored as
a failure.

Reads `linktest-sent.tsv` for what was transmitted, so lengths that were sent
and never echoed are distinguishable from lengths that were never sent.
"""
import argparse
import collections
import os
import re
import sys

ALIAS_PREFIXES = ("WIDE", "TRACE", "RELAY", "ECHO", "GATE", "ARISS", "SGATE")
# Direwolf puts an optional -T timestamp INSIDE the bracket, after the channel:
# "[0.3]" without -T, "[0.3 2026-11-08T21:56:02-0500]" with it. A third number
# appears when both multiple subchannels and multiple slicers are configured.
RF_LINE = re.compile(
    r"^\[0\.[0-9]+(?:\.[0-9]+)?(?: [^\]]*)?\]\s+(\S+?)>(\S+?):(.*)$")
MARK = re.compile(r"^>LT ([CT])(\d{3}) b(\d{3}) ")


def repeaters_from_path(path_field):
    """TNC2 marks only the LAST used entry with '*'. Everything up to and
    including it has been through a digipeater."""
    entries = path_field.split(",")[1:]          # drop the destination
    last_used = -1
    for i, e in enumerate(entries):
        if e.endswith("*"):
            last_used = i
    out = []
    for e in entries[:last_used + 1]:
        e = e.rstrip("*")
        if not e or any(e.upper().startswith(p) for p in ALIAS_PREFIXES):
            continue
        out.append(e)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", default="/opt/aprs-igate/run/direwolf.log")
    ap.add_argument("--sent", default="linktest-sent.tsv")
    ap.add_argument("--call", default="KD3CCO-10")
    ap.add_argument("--target", default="W3YA-1")
    ap.add_argument("--min-informative", type=int, default=6)
    args = ap.parse_args()
    target = args.target.upper()

    sent = {}                                   # (cycle, role) -> bytes
    if os.path.exists(args.sent):
        with open(args.sent, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                f = line.rstrip("\n").split("\t")
                if len(f) >= 4:
                    try:
                        sent[(int(f[1]), f[2])] = int(f[3])
                    except ValueError:
                        pass
    else:
        print(f"note: {args.sent} not found, denominators come from echoes only")

    echo = collections.defaultdict(set)         # (cycle, role) -> {digis}
    bylen = collections.defaultdict(collections.Counter)
    seen_bytes = {}
    try:
        fh = open(args.log, encoding="utf-8", errors="replace")
    except OSError as exc:
        sys.exit(f"linktest-report: cannot read {args.log} ({exc})")
    with fh:
        for line in fh:
            m = RF_LINE.match(line.rstrip("\n"))
            if not m:
                continue
            src, path, info = m.group(1), m.group(2), m.group(3)
            if src.upper() != args.call.upper():
                continue
            mk = MARK.match(info)
            if not mk:
                continue
            role, cycle, nbytes = mk.group(1), int(mk.group(2)), int(mk.group(3))
            seen_bytes[(cycle, role)] = nbytes
            reps = repeaters_from_path(path)
            if reps:
                echo[(cycle, role)].update(r.upper() for r in reps)
                for r in reps:
                    bylen[nbytes][r.upper()] += 1

    cycle_len = {c: b for (c, r), b in (sent or seen_bytes).items() if r == "T"}
    if not cycle_len:
        sys.exit("linktest-report: no test frames found; was the sweep run?")
    lengths = sorted(set(cycle_len.values()))

    print(f"log      {args.log}")
    print(f"target   {args.target}")
    print(f"cycles   {len(cycle_len)} test frames across {len(lengths)} lengths")
    print()
    print(f"  {'bytes':>6} {'sent':>5} {'informative':>12} {'repeated':>9}  verdict")
    verdicts = {}
    for L in lengths:
        cycles = [c for c, b in cycle_len.items() if b == L]
        informative = [c for c in cycles if target in echo.get((c, "C"), ())]
        hit = [c for c in informative if target in echo.get((c, "T"), ())]
        if len(hit) >= 2:
            v = "repeated"
        elif len(informative) >= args.min_informative and not hit:
            v = "blocked"
        else:
            v = "undecided"
        verdicts[L] = v
        print(f"  {L:>6} {len(cycles):>5} {len(informative):>12} "
              f"{len(hit):>9}  {v}")

    print()
    print("  TEST-frame echoes per length, per digipeater (controls separate,")
    print("  because a control is 75 bytes too and would inflate that row):")
    allcalls = sorted({d for v in echo.values() for d in v})
    if allcalls:
        print("    bytes  " + "  ".join(f"{c:>11}" for c in allcalls))
        for L in lengths:
            cells = []
            for c in allcalls:
                n = sum(1 for cy, b in cycle_len.items()
                        if b == L and c in echo.get((cy, "T"), ()))
                cells.append(f"{n:>11}")
            print(f"    {L:>5}  " + "  ".join(cells))
        cells = []
        for c in allcalls:
            n = sum(1 for cy in cycle_len if c in echo.get((cy, "C"), ()))
            cells.append(f"{n:>11}")
        print(f"    {'ctrl':>5}  " + "  ".join(cells))
    else:
        print("    none")

    # Pooled across lengths. The per-length verdict throws away most of the
    # power: with a 57% control rate, six informative cycles per length needs
    # a long run, while every test frame above the shortest length is evidence
    # about the same question. Denominators are frames sent, not paired
    # cycles, which is sound here because round-robin spreads the lengths
    # evenly over the run.
    shortest = lengths[0]
    ctrl_sent = len(cycle_len)
    ctrl_hit = sum(1 for cy in cycle_len if target in echo.get((cy, "C"), ()))
    long_sent = sum(1 for b in cycle_len.values() if b > shortest)
    long_hit = sum(1 for cy, b in cycle_len.items()
                   if b > shortest and target in echo.get((cy, "T"), ()))
    print()
    print(f"  pooled for {args.target}:")
    print(f"    {shortest}-byte controls   {ctrl_hit:>4} of {ctrl_sent:<4} "
          f"({100.0 * ctrl_hit / ctrl_sent if ctrl_sent else 0:.0f}%)")
    print(f"    tests above {shortest}B    {long_hit:>4} of {long_sent:<4} "
          f"({100.0 * long_hit / long_sent if long_sent else 0:.0f}%)")
    if ctrl_sent and long_sent and ctrl_hit and not long_hit:
        rate = ctrl_hit / ctrl_sent
        p_val = (1.0 - rate) ** long_sent
        print(f"    probability of {long_sent} straight misses at the control "
              f"rate: {p_val:.2g}")

    print()
    blocked = [L for L in lengths if verdicts[L] == "blocked"]
    repeated = [L for L in lengths if verdicts[L] == "repeated"]
    total_informative = sum(
        1 for cy in cycle_len if target in echo.get((cy, "C"), ()))
    if not repeated and not blocked and total_informative == 0:
        print(f"  VERDICT  no information: {args.target} repeated no control "
              f"frame, so no cycle was informative.")
    elif not repeated and not blocked:
        print(f"  VERDICT  no per-length verdict: {total_informative} "
              f"informative cycles across {len(lengths)} lengths, "
              f"{args.min_informative} needed each.")
        print(f"           Read the pooled block above instead. It answers the "
              f"same question with every frame rather than")
        print(f"           only the paired ones, which is where the power is "
              f"in a short run.")
    elif blocked and repeated and min(blocked) > max(repeated):
        print(f"  VERDICT  length matters. Repeated to {max(repeated)} bytes, "
              f"blocked from {min(blocked)}. A gated message is about 120.")
    elif not blocked:
        print(f"  VERDICT  length does not matter: repeated at every length "
              f"tested, {max(repeated)} included.")
    else:
        print(f"  VERDICT  mixed: repeated {repeated}, blocked {blocked}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
