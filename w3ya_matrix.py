#!/usr/bin/env python3
"""Two-transmitter, many-length matrix against W3YA-1, byte-exact over KISS.

The question
------------
Does W3YA-1 repeat KD3CCO-10 (FT-2900R + Digirig + Pi) and KD3CCO-9 (FTX-1 +
laptop, single USB, 20 W) differently, and up to what frame length?

Both are driven the SAME way -- byte-exact AX.25 handed to each station's
Direwolf over KISS -- so the only differences left are the radio, the sound
card and the antenna. They are interleaved within every cycle, so each length
is tried by both stations under the same propagation minute. Station order
flips each cycle so neither one always goes first.

Why not `w3ya_length_sweep.py`
------------------------------
That injects into APRS-IS and lets the Pi's iGate logic build the frame, so
the on-air length is whatever the gateway produces and it needs TX_VIA
changed. This builds the frame itself. Nothing touches APRS-IS.

What "on-air bytes" means
-------------------------
The same accounting the rest of this repository uses, so numbers are directly
comparable with `digi_length_profile.onair()` and the published sweeps:

    7 * (2 + ndigis) + 1 (control) + 1 (PID) + len(info) + 4 (flags/FCS)

KISS carries the frame WITHOUT flags or FCS -- Direwolf adds those -- so the
+4 is accounting, not bytes we send.

Reading the result
------------------
`--score LOG` reads a Direwolf log and prints relayed/sent per station per
length. Not from aprs.fi. A digipeated copy arrives about a second after the direct
copy with an identical source and information field, lands inside APRS-IS's
dedupe window, and is silently dropped; measured 2026-11-26, nine relayed
copies offered and zero stored. Grep the Pi's own Direwolf log for
`KD3CCO-9>` and count `W3YA-1*` against the direct copies.

Safety
------
A KISS frame is one bounded transmission and Direwolf owns PTT, so there is
no path here that can leave the radio keyed -- unlike a tone or a voice-over
helper. `--max-frames` still bounds a run, and Ctrl-C closes the socket
between frames rather than mid-frame.
"""

import argparse
import datetime
import socket
import sys
import time

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
UI, PID_NONE = 0x03, 0xF0

#: Flags + FCS, counted but not transmitted over KISS. Matches
#: digi_length_profile.ON_AIR_OVERHEAD so the two agree.
ON_AIR_OVERHEAD = 4


def encode_address(call, last=False, cbit=False, repeated=False):
    """One AX.25 address field: 6 shifted characters plus an SSID byte.

    Bit 7 of the SSID byte is the command/response bit on the destination and
    source, and the has-been-repeated (H) bit on a digipeater -- same bit,
    different meaning. We always send a digipeater unrepeated; W3YA-1 setting
    that bit on its echo is exactly the thing being measured.
    """
    base, _, ssid = call.upper().partition("-")
    ssid = int(ssid) if ssid else 0
    if len(base) > 6 or not base:
        raise ValueError(f"{call!r}: callsign must be 1 to 6 characters")
    if not 0 <= ssid <= 15:
        raise ValueError(f"{call!r}: SSID must be 0 to 15")
    out = bytes(((ord(c) << 1) & 0xFE) for c in base.ljust(6))
    b = 0x60 | (ssid << 1)
    if cbit or repeated:
        b |= 0x80
    if last:
        b |= 0x01
    return out + bytes([b])


def build_frame(src, dest, digis, info):
    """A UI frame, ready for KISS. `info` is bytes."""
    addrs = [encode_address(dest, cbit=True), encode_address(src)]
    addrs += [encode_address(d) for d in digis]
    tail = addrs[-1]
    addrs[-1] = tail[:6] + bytes([tail[6] | 0x01])
    return b"".join(addrs) + bytes([UI, PID_NONE]) + info


def on_air_len(ndigis, infolen):
    return 7 * (2 + ndigis) + 1 + 1 + infolen + ON_AIR_OVERHEAD


def kiss_wrap(frame):
    body = bytearray([FEND, 0x00])
    for b in frame:
        if b == FEND:
            body += bytes([FESC, TFEND])
        elif b == FESC:
            body += bytes([FESC, TFESC])
        else:
            body.append(b)
    body.append(FEND)
    return bytes(body)


def ruler(n):
    """Self-counting padding: every 0 closes a group of ten."""
    return "".join(str((i + 1) % 10) for i in range(n))


def build_status(target_onair, ndigis, seq, marker):
    """A status report of an exact on-air size.

    Status rather than a message, for the multi-digipeater ceiling sweep:
    no addressee means no whitelist interaction, and nothing acks a status,
    so there are no retries to unpick when counting. A message addressed to
    a station that does not exist retries about five times, which is free
    data but makes per-transmission accounting mandatory.
    """
    overhead = 7 * (2 + ndigis) + 1 + 1 + ON_AIR_OVERHEAD
    pad = target_onair - overhead - 1 - 1 - 3 - 1
    if pad < 0:
        raise SystemExit(f"ftx1/w3ya matrix: {target_onair} B is below this path's floor")
    info = f">{marker}{seq} " + ruler(pad)
    got = on_air_len(ndigis, len(info))
    if got != target_onair:
        raise SystemExit(f"built {got} B, wanted {target_onair} B")
    return info.encode("latin-1")


def build_info(target_onair, ndigis, addressee, seq, marker):
    """An APRS message whose frame lands on exactly `target_onair` bytes.

    info = ':' + 9-char addressee + ':' + text + '{' + seq
    """
    target_info = target_onair - (7 * (2 + ndigis) + 1 + 1 + ON_AIR_OVERHEAD)
    #: Neutral text. An earlier revision said "ftx1" here, which was wrong the
    #: moment this script drove two stations: KD3CCO-10 frames also carried it
    #: and read on a handheld as if the FTX-1 had sent them. The AX.25 source
    #: field is the only thing that identifies the transmitter; the text is
    #: padding and must not imply otherwise.
    head = f"{marker} w3ya "
    pad = target_info - 12 - len(seq) - len(head)
    if pad < 0:
        raise SystemExit(
            f"ftx1_length_test: {target_onair} B is below the floor for this "
            f"path and addressee (need {len(head) + 12 + len(seq) + 27} B minimum)")
    text = head + ruler(pad)
    info = f":{addressee.upper().ljust(9)[:9]}:{text}{{{seq}"
    got = on_air_len(ndigis, len(info))
    if got != target_onair:
        raise SystemExit(f"ftx1_length_test: built {got} B, wanted {target_onair} B")
    return info.encode("latin-1")


def parse_station(spec):
    call, _, hostport = spec.partition("=")
    host, _, port = hostport.partition(":")
    if not call or not host or not port:
        raise SystemExit(f"--station {spec!r}: want CALL=HOST:PORT")
    return call.upper(), host, int(port)


def score(logpath, via, lengths):
    """Relayed/sent per station per length, from a Direwolf log.

    A frame is 'sent' when its direct copy appears with the digipeater
    unused, and 'relayed' when a copy of the SAME (source, information
    field) appears with the used bit set. Pairing on the information field
    is safe here only because every test frame carries a distinct {nn.
    """
    import collections, gzip, re
    HEX = re.compile(r"<0x[0-9a-fA-F]{2}>")
    RF = re.compile(r"^\[0\.\d+[^\]]*\]\s+(?P<src>KD3CCO-\d+)>(?P<path>\S+?):(?P<info>.*)$")
    seen = {}
    op = gzip.open if logpath.endswith(".gz") else open
    with op(logpath, "rt", encoding="latin-1") as fh:
        for line in fh:
            m = RF.match(line.rstrip("\n"))
            # "ftx1" is the 2026-11-26 run's wording, kept so those logs still
            # score; "w3ya" is what this script writes now.
            if not m or not any(t in m.group("info") for t in (" w3ya ", " ftx1 ", " test ")):
                continue
            info = HEX.sub(".", m.group("info"))
            mid = re.search(r"\{(\d+)", info)
            if not mid:
                continue
            key = (m.group("src"), mid.group(1), len(info.encode("latin-1")))
            ndigis = len([t for t in m.group("path").split(",") if t])
            d = seen.setdefault(key, {"len": on_air_len(ndigis, len(info.encode("latin-1"))),
                                      "rep": False})
            if f"{via}*" in m.group("path"):
                d["rep"] = True
    grid = collections.defaultdict(lambda: [0, 0])
    for (src, _mid, _il), d in seen.items():
        grid[(src, d["len"])][0] += 1
        grid[(src, d["len"])][1] += d["rep"]
    calls = sorted({k[0] for k in grid})
    lens = sorted({k[1] for k in grid})
    if not calls:
        print("no matching frames found in", logpath)
        return
    print(f"W3YA-1 relays, from {logpath}\n")
    print("  on-air  " + "  ".join(f"{c:>14s}" for c in calls))
    for L in lens:
        row = f"  {L:3d} B   "
        for c in calls:
            n, r = grid[(c, L)]
            row += f"{(str(r) + '/' + str(n)):>8s}" + (f" {100*r/n:3.0f}%" if n else "     ") + "  "
        print(row)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lengths", default="76,83,89,95",
                   help="on-air frame sizes, flags and FCS included")
    p.add_argument("--control", type=int, default=76,
                   help="known-good length, reported but not treated specially")
    p.add_argument("--cycles", type=int, default=8)
    p.add_argument("--interval", type=float, default=14.0, help="seconds between frames")
    p.add_argument("--station", action="append", metavar="CALL=HOST:PORT",
                   help="repeatable; e.g. KD3CCO-9=127.0.0.1:8001")
    p.add_argument("--score", metavar="LOG", help="score a Direwolf log instead of transmitting")
    #: Deliberately NOT a whitelisted callsign. With an addressee matching
    #: WHITELIST_CALLS (KD3CCO*, KD3CCP*, W3EDP*, W3TM*) the loop is:
    #: we transmit -> W3SWL-2 gates it to APRS-IS -> the Pi's IGFILTER g/KD3CCO*
    #: pulls it straight back -> the iGate retransmits it on WIDE1-1,WIDE2-2.
    #: Measured 2026-11-26: one injected frame produced a second, unwanted
    #: transmission two seconds later on the wrong path. An addressee no igate
    #: has ever heard is also one no igate will try to deliver.
    p.add_argument("--to", default="TESTLEN", help="APRS message addressee (keep it off the whitelist)")
    p.add_argument("--dest", default="APDW18", help="AX.25 destination (tocall)")
    p.add_argument("--via", action="append", metavar="PATH",
                   help="digipeater path; repeat the flag to sweep several, "
                        "each as its own explicit one-hop path")
    p.add_argument("--format", choices=("message", "status"), default="message",
                   help="status has no addressee and is never acked, so it does not retry")
    p.add_argument("--marker", default="T", help="leading character, so runs are separable")
    p.add_argument("--max-frames", type=int, default=120)
    p.add_argument("--dry-run", action="store_true", help="build and print, transmit nothing")
    a = p.parse_args()

    lengths = [int(x) for x in a.lengths.replace(",", " ").split()]
    if a.score:
        return score(a.score, (a.via or ["W3YA-1"])[0].split(",")[0], lengths)
    if not a.station:
        raise SystemExit("need at least one --station CALL=HOST:PORT (or --score LOG)")

    stations = [parse_station(s) for s in a.station]
    #: Each --via is its own path. Several of them sweep several digipeaters,
    #: each addressed explicitly so exactly one can act on a given frame and
    #: a relay is unambiguous.
    paths = [[d for d in v.replace(",", " ").split() if d]
             for v in (a.via or ["W3YA-1"])]

    #: Order flips each cycle so no station or digipeater is always first at a
    #: given length -- otherwise the later ones always meet a channel that a
    #: moment ago carried a transmission.
    plan = []
    for c in range(a.cycles):
        sts = stations if c % 2 == 0 else list(reversed(stations))
        pth = paths if c % 2 == 0 else list(reversed(paths))
        for L in lengths:
            for digis in pth:
                for st in sts:
                    plan.append((st, digis, L))
    if len(plan) > a.max_frames:
        raise SystemExit(f"plan is {len(plan)} frames, over --max-frames {a.max_frames}")

    mins = len(plan) * a.interval / 60
    kind = "status" if a.format == "status" else f"message to {a.to}"
    print(f"{len(plan)} {kind} frames, {a.interval}s apart (~{mins:.0f} min)")
    for call, host, port in stations:
        print(f"   station  {call:11s} -> {host}:{port}")
    for d in paths:
        print(f"   via      {','.join(d)}")
    print(f"   lengths  {', '.join(str(x) + ' B' for x in lengths)}"
          f"   ({a.cycles} per digipeater per station)")

    socks = {}
    if not a.dry_run:
        for call, host, port in stations:
            socks[call] = socket.create_connection((host, port), timeout=10)
            print(f"connected {call} at {host}:{port}")
    counts = {c: 0 for c, _, _ in stations}
    try:
        for i, ((call, _h, _p), digis, L) in enumerate(plan):
            seq = f"{i % 1000:03d}"
            if a.format == "status":
                info = build_status(L, len(digis), seq, a.marker)
            else:
                info = build_info(L, len(digis), a.to, seq, a.marker)
            frame = build_frame(call, a.dest, digis, info)
            built = on_air_len(len(digis), len(info))
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"  {stamp}  {call:11s} {','.join(digis):11s} {built:3d} B  {a.marker}{seq}")
            if socks:
                socks[call].sendall(kiss_wrap(frame))
                counts[call] += 1
            if i < len(plan) - 1:
                time.sleep(0 if a.dry_run else a.interval)
    except KeyboardInterrupt:
        print("\nstopped by operator between frames", file=sys.stderr)
    finally:
        for s_ in socks.values():
            s_.close()
        print("transmitted: " + ", ".join(f"{c} {n}" for c, n in counts.items()))
    print("\nscore it from the Pi log (NOT aprs.fi -- dedupe hides relayed copies):")
    print("  python3 w3ya_matrix.py --score pulled.log")


if __name__ == "__main__":
    main()
