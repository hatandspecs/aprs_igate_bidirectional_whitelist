#!/usr/bin/env python3
"""Frame-length sweep against one digipeater, everything else held fixed.

The question is where W3YA-1 stops repeating this station. It repeats about a
quarter of the 71-73 byte beacons and none of 33 gated frames at 89-126 bytes,
so a threshold sits somewhere between. Length is the only thing this varies.

HELD CONSTANT, by construction
    AX.25 source and dest  KD3CCO-10>APDW17
    digipeater path        whatever TX_VIA names, unconsumed. The on-air
                           arithmetic reads the entry count from TX_VIA, so a
                           one-entry and a two-entry path are both counted
                           right.
    frame type             third-party wrap, dti `}`, exactly as a gated
                           message from APRS-IS to RF
    gating station         KD3CCO-10, named inside the wrap as the real
                           downlink names it
    addressee              one 9-character field for every frame
    injection route        APRS-IS, so Direwolf builds the RF frame the same
                           way it builds a real one. Nothing here touches KISS.

VARIED
    the message text, padded to set the total frame length.

    on-air bytes = 7 * addresses + control + PID + information
                 + flag + FCS + flag
                 = 7*(2+1) + 1 + 1 + (46 + len(text)) + 4
                 = 73 + len(text)

    This is the same arithmetic digi_length_profile.py and linktest_sweep.py
    use, which is the only reason the numbers can be compared with the tables
    already banked in AGENTS.md. An earlier version of this script counted
    2 bytes less by leaving out the flags and FCS, which silently put its
    results on a scale of their own.

WHY ROUND-ROBIN AND NOT A STAIRCASE
    A rising sweep confounds length with time: if conditions sag during the
    run, the longest frames are the ones sent last and a false cliff appears.
    Each cycle visits every length in a fresh random order, so drift spreads
    across all of them. This is the same reason linktest_sweep.py does it.

WHY A CONTROL LENGTH IS MANDATORY
    A digipeater that is not answering this path at all produces the identical
    result to one that is refusing long frames: zero. Every cycle therefore
    carries a frame at `--control` bytes, at or near beacon length, which is
    known to come back. A cycle whose control frame is not repeated carries no
    information about its test frames and is discarded in analysis.

RATE
    Gated frames are subject to Direwolf's IGTXLIMIT, 6 per minute and 10 per
    5 minutes on this station. Over that and Direwolf DROPS the frame rather
    than queueing it, which would read as a digipeater that did not repeat.
    The default interval keeps the 5-minute window at 7.5, and analysis counts
    only frames confirmed transmitted in the log.
"""
import argparse
import os
import random
import subprocess
import sys
import time

WRAP_OVERHEAD = 46          # }SRC>APRS,TCPIP,GATE*::ADDRESSEE_:  plus {nn

# 7 bytes per address, plus control, PID, opening flag, FCS, closing flag.
# The address count is 2 (dest + source) plus however many entries TX_VIA
# names, so it MUST be read from the config rather than assumed: a hardcoded
# 27 is right only for a single-entry path. With TX_VIA = WIDE1-1,WIDE2-2 it
# understates every frame by 7 bytes, which silently puts a whole run on a
# scale of its own. That happened on 2026-11-15.
def ax25_overhead(tx_via):
    ndigis = len([p for p in tx_via.replace(","," ").split() if p])
    return 7 * (2 + ndigis) + 1 + 1 + 4


def config(key, *files):
    for name in files:
        try:
            with open(name) as fh:
                for line in fh:
                    k, sep, v = line.strip().partition("=")
                    if sep and k.strip() == key and v.strip():
                        return v.strip()
        except IOError:
            pass
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lengths", default="74,82,90,97,105",
                    help="on-air frame sizes in bytes, flags and FCS included")
    ap.add_argument("--control", type=int, default=74,
                    help="the known-good length sent in every cycle")
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--interval", type=float, default=40.0, help="seconds")
    ap.add_argument("--to", default="KD3CCO-7")
    ap.add_argument("--from", dest="src", default="KD3CCO-1")
    ap.add_argument("--dir", default=os.path.join(os.path.expanduser("~"), "aprs-igate"))
    ap.add_argument("--marker", default="L",
                    help="first character of the text, so runs at different\n                         power or antenna are separable in the log without\n                         changing any frame length")
    ap.add_argument("--tx-via", default=None,
                    help="path to assume for the on-air arithmetic. Defaults "
                         "to TX_VIA from igate.conf. Set it when the station "
                         "being driven is configured differently from the "
                         "config this script can see, e.g. when running from "
                         "a workstation against the Pi.")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    os.chdir(a.dir)
    passcode = config("IGLOGIN_PASSCODE", "igate.secrets", "igate.conf")
    if not passcode and not a.dry_run:
        sys.exit("cannot read IGLOGIN_PASSCODE")

    tx_via = a.tx_via if a.tx_via is not None else config("TX_VIA", "igate.conf")
    OVERHEAD = ax25_overhead(tx_via)
    FLOOR = WRAP_OVERHEAD + OVERHEAD
    print("# TX_VIA = %s  ->  %d address fields, %d bytes of overhead, "
          "floor %d on-air bytes" % (tx_via or "(unset)",
          2 + len([p for p in tx_via.replace(","," ").split() if p]),
          OVERHEAD, FLOOR + 1), file=sys.stderr)

    lengths = [int(x) for x in a.lengths.split(",")]
    for n in lengths + [a.control]:
        if n - FLOOR < 1:
            sys.exit("%d on-air bytes is below the %d-byte floor of this frame "
                     "type on TX_VIA = %s" % (n, FLOOR + 1, tx_via))

    env = dict(os.environ)
    env.update({"SELFTEST_SERVER": config("IGSERVER", "igate.conf"),
                "SELFTEST_PORT": config("IGSERVER_PORT", "igate.conf") or "14580",
                "SELFTEST_LOGIN": a.src, "SELFTEST_PASS": passcode,
                "SELFTEST_FROM": a.src, "SELFTEST_TO": a.to})

    ids = random.sample(range(10, 100), len(lengths) * a.cycles + a.cycles)
    print("id\tbytes\trole\tsent_at")
    k = 0
    for cycle in range(a.cycles):
        order = [a.control] + lengths[:]
        random.shuffle(order)
        for n in order:
            text = a.marker + "." * (n - FLOOR - 1)
            env["SELFTEST_TEXT"] = text
            env["SELFTEST_ID"] = "%02d" % ids[k]
            role = "control" if n == a.control else "test"
            if a.dry_run:
                info = "}%s>APRS,TCPIP,KD3CCO-10*::%-9s:%s{%02d" % (
                    a.src, a.to, text, ids[k])
                print("%02d\t%d\t%s\tDRY len(info)=%d total=%d" % (
                    ids[k], n, role, len(info), len(info) + OVERHEAD))
            else:
                r = subprocess.run([sys.executable, "selftest_inject.py"],
                                   env=env, capture_output=True, text=True)
                stamp = time.strftime("%H:%M:%S")
                if r.returncode:
                    print("%02d\t%d\t%s\tFAILED" % (ids[k], n, role), flush=True)
                else:
                    print("%02d\t%d\t%s\t%s" % (ids[k], n, role, stamp), flush=True)
            k += 1
            if not a.dry_run and k < len(ids):
                time.sleep(a.interval)


if __name__ == "__main__":
    main()
