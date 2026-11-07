#!/usr/bin/env python3
"""Find the frame length at which a digipeater stops repeating this station.

WHY THIS EXISTS
---------------
W3YA-1 repeats this station's beacons (113 of 306 echoes on 2026-10-30, 18 in
the November window) and has never repeated one of its gated IS-to-RF messages
(0 of 28). Two explanations were ruled out from the archive: not the `TCPIP` in
the third-party payload, which a digipeater never sees, and not a three-hop
ceiling, because W3YA-1 repeated `KG4DQH>APT311,W3YA-1,WIDE1*,WIDE2-2`. The
leading remaining candidate is frame length against a thin margin: gated
messages are the longest frames this station sends, about 120 bytes on air
against a 75-byte beacon.

DESIGN
------
Each cycle sends a CONTROL frame at the known-good beacon length, waits a few
seconds, then a TEST frame at the length under examination. The control answers
"was the path working at all in this cycle", so a missing test echo cannot be
confused with a dead minute. A cycle whose control was not echoed carries no
information and is excluded rather than counted as a failure.

Lengths are visited round-robin, not as a rising staircase. A monotonic sweep
confounds length with time: if conditions sag over the run, the longest frames
are the ones sent last and a false cliff appears. Each pass covers every
length, so drift hits all of them equally.

Echoes are read live from the same KISS socket, so the run stops as soon as the
answer is unambiguous and at the deadline regardless. `linktest_report.py`
recomputes the same table offline from Direwolf's text log, which is the
independent check if anything here misbehaves.

Frames are status reports (`>`), not positions, so the run leaves this
station's position history on aprs.fi alone. Every frame carries a readable
`LT` marker, its cycle number and its own byte count. Source is this station's
own callsign; nothing transmits under anyone else's call.

CHANNEL LOAD
------------
At the 30-second default this sends 4 frames a minute, up to 240 in the hour.
That is a real load on a shared channel. It is bounded by --max-minutes and by
the early stop, and it is the reason the deadline is not optional.

SAFETY AND TEARDOWN
-------------------
Needs `KISS_PORT = 8001` in **igate.conf**, not igate.local.conf: the local
layer accepts only DEPLOY_MODE, RADIO, WEB_*, DEVICE_WAIT, RF_QUIET_MINUTES and
hardware keys, and silently ignores anything else after warning about it. The
port is unauthenticated and on 0.0.0.0, so set it back to 0 and restart when
finished. The full teardown is in README.md under "Link length sweep".
"""
import argparse
import datetime
import select
import socket
import sys
import time

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
CTRL_UI, PID_NONE = 0x03, 0xF0
MARKER = "LT"
PAD_PATTERN = "linktest."
ON_AIR_OVERHEAD = 4           # flag + FCS + flag, added by the TNC
ALIAS_PREFIXES = ("WIDE", "TRACE", "RELAY", "ECHO", "GATE", "ARISS", "SGATE")


# ---------------------------------------------------------------- AX.25 / KISS

def split_call(text):
    if "-" in text:
        base, _, ssid = text.rpartition("-")
        try:
            return base.upper(), int(ssid)
        except ValueError:
            raise SystemExit(f"linktest: {text!r} has a non-numeric SSID")
    return text.upper(), 0


def join_call(call, ssid):
    return call if ssid == 0 else f"{call}-{ssid}"


def encode_address(call, ssid, last=False, command=False, repeated=False):
    if len(call) > 6:
        raise SystemExit(f"linktest: callsign {call!r} is over 6 characters")
    if not 0 <= ssid <= 15:
        raise SystemExit(f"linktest: SSID {ssid} is outside 0-15")
    out = bytearray((ord(c) << 1) & 0xFE for c in call.ljust(6))
    # bit0 extension (set on the final address), bits1-4 SSID, bits5-6 reserved,
    # bit7 the C bit on dest or the H bit on a digipeater.
    byte = 0x60 | (ssid << 1)
    if command or repeated:
        byte |= 0x80
    if last:
        byte |= 0x01
    out.append(byte)
    return bytes(out)


def build_ax25(source, dest, via, info):
    frame = bytearray()
    call, ssid = split_call(dest)
    frame += encode_address(call, ssid, command=True)
    call, ssid = split_call(source)
    frame += encode_address(call, ssid, last=not via)
    for n, hop in enumerate(via):
        call, ssid = split_call(hop)
        frame += encode_address(call, ssid, last=(n == len(via) - 1))
    frame.append(CTRL_UI)
    frame.append(PID_NONE)
    frame += info.encode("ascii", "replace")
    return bytes(frame)


def decode_ax25(frame):
    """-> (source, [(call, repeated_bool), ...], info). Raises ValueError."""
    if len(frame) < 16:
        raise ValueError("frame too short")
    addrs, pos = [], 0
    while True:
        if pos + 7 > len(frame):
            raise ValueError("address field ran off the end")
        chunk = frame[pos:pos + 7]
        call = "".join(chr(b >> 1) for b in chunk[:6]).rstrip()
        ssid = (chunk[6] >> 1) & 0x0F
        addrs.append((join_call(call, ssid), bool(chunk[6] & 0x80)))
        pos += 7
        if chunk[6] & 0x01:
            break
        if len(addrs) > 10:
            raise ValueError("too many addresses")
    if len(addrs) < 2:
        raise ValueError("no source address")
    if pos + 2 > len(frame):
        raise ValueError("no control/pid")
    pos += 2
    info = frame[pos:].decode("ascii", "replace")
    return addrs[1][0], addrs[2:], info


def repeaters(digis):
    """Real callsigns, in path order, whose H bit is set."""
    out = []
    for call, used in digis:
        if not used:
            continue
        if any(call.upper().startswith(p) for p in ALIAS_PREFIXES):
            continue
        out.append(call)
    return out


def kiss_wrap(frame):
    out = bytearray([FEND, 0x00])
    for b in frame:
        if b == FEND:
            out += bytes([FESC, TFEND])
        elif b == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(b)
    out.append(FEND)
    return bytes(out)


def kiss_unwrap(blob):
    out, esc = bytearray(), False
    for b in blob:
        if esc:
            out.append(FEND if b == TFEND else FESC if b == TFESC else b)
            esc = False
        elif b == FESC:
            esc = True
        else:
            out.append(b)
    return bytes(out)


# ------------------------------------------------------------------ test frames

def frame_bytes(source, dest, via, info):
    return len(build_ax25(source, dest, via, info)) + ON_AIR_OVERHEAD


def make_info(role, cycle, total, source, dest, via):
    header = f"{MARKER} {role}{cycle:03d} b{total:03d} "
    floor = frame_bytes(source, dest, via, ">" + header)
    if total < floor:
        raise SystemExit(f"linktest: {total} bytes is below the {floor}-byte "
                         f"floor for this path and marker")
    pad_len = total - floor
    pad = (PAD_PATTERN * (pad_len // len(PAD_PATTERN) + 1))[:pad_len]
    info = ">" + header + pad
    assert frame_bytes(source, dest, via, info) == total
    return info


def parse_marker(info):
    """'>LT T007 b099 pad...' -> ('T', 7, 99), or None."""
    if not info.startswith(">" + MARKER + " "):
        return None
    parts = info[len(MARKER) + 2:].split()
    if len(parts) < 2:
        return None
    role_cycle, nbytes = parts[0], parts[1]
    if len(role_cycle) < 2 or not nbytes.startswith("b"):
        return None
    try:
        return role_cycle[0], int(role_cycle[1:]), int(nbytes[1:])
    except ValueError:
        return None


def tnc2(source, dest, via, info):
    return f"{source}>{dest}" + "".join("," + v for v in via) + f":{info}"


# ------------------------------------------------------------------------- run

class Tally:
    def __init__(self, lengths, target, min_informative):
        self.lengths = lengths
        self.target = target.upper()
        self.min_informative = min_informative
        self.cycle_length = {}                  # cycle -> test length
        self.echo = {}                          # (cycle, role) -> set(callsigns)
        self.others = {}                        # callsign -> echo count

    def note_send(self, cycle, role, nbytes):
        if role == "T":
            self.cycle_length[cycle] = nbytes

    def note_echo(self, cycle, role, reps):
        self.echo.setdefault((cycle, role), set()).update(r.upper() for r in reps)
        for r in reps:
            self.others[r.upper()] = self.others.get(r.upper(), 0) + 1

    def counts(self, length, upto_cycle):
        informative = echoed = 0
        for cycle, L in self.cycle_length.items():
            if L != length or cycle > upto_cycle:
                continue
            if self.target in self.echo.get((cycle, "C"), ()):
                informative += 1
                if self.target in self.echo.get((cycle, "T"), ()):
                    echoed += 1
        return informative, echoed

    def verdict(self, length, upto_cycle):
        informative, echoed = self.counts(length, upto_cycle)
        if echoed >= 2:
            return "repeated", informative, echoed
        if informative >= self.min_informative and echoed == 0:
            return "blocked", informative, echoed
        return "undecided", informative, echoed

    def settled(self, upto_cycle):
        """True once every length has a verdict."""
        return all(self.verdict(L, upto_cycle)[0] != "undecided"
                   for L in self.lengths)

    def matrix(self, upto_cycle):
        """Rows per length, columns per digipeater, counting TEST echoes,
        with the control row for reference."""
        calls = sorted(self.others)
        if not calls:
            return "  no echoes from anyone"
        out = ["    " + "bytes".rjust(6)
               + "".join(c.rjust(12) for c in calls)]
        for L in self.lengths:
            cells = []
            for c in calls:
                n = sum(1 for cy, LL in self.cycle_length.items()
                        if LL == L and cy <= upto_cycle
                        and c in self.echo.get((cy, "T"), ()))
                cells.append(str(n).rjust(12))
            out.append("    " + str(L).rjust(6) + "".join(cells))
        cells = []
        for c in calls:
            n = sum(1 for cy in self.cycle_length
                    if cy <= upto_cycle and c in self.echo.get((cy, "C"), ()))
            cells.append(str(n).rjust(12))
        out.append("    " + "ctrl".rjust(6) + "".join(cells))
        return "\n".join(out)

    def table(self, upto_cycle):
        rows = [f"  {'bytes':>6} {'informative':>12} {'repeated':>9}  verdict"]
        for L in self.lengths:
            v, informative, echoed = self.verdict(L, upto_cycle)
            rows.append(f"  {L:>6} {informative:>12} {echoed:>9}  {v}")
        return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--call", default="KD3CCO-10", help="source; your own call")
    ap.add_argument("--dest", default="APDW17")
    ap.add_argument("--via", default="WIDE1-1",
                    help="comma separated; match BEACON_VIA to reuse its history")
    ap.add_argument("--target", default="W3YA-1",
                    help="the digipeater under test")
    ap.add_argument("--control-bytes", type=int, default=75)
    ap.add_argument("--min-bytes", type=int, default=75)
    ap.add_argument("--max-bytes", type=int, default=131)
    ap.add_argument("--step", type=int, default=8)
    ap.add_argument("--cycle-seconds", type=float, default=30.0)
    ap.add_argument("--gap-seconds", type=float, default=8.0)
    ap.add_argument("--settle-seconds", type=float, default=12.0,
                    help="grace after a test frame before its cycle is scored")
    ap.add_argument("--max-minutes", type=float, default=60.0)
    ap.add_argument("--min-informative", type=int, default=6)
    ap.add_argument("--no-early-stop", action="store_true")
    ap.add_argument("--log", default="linktest-sent.tsv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    via = [v.strip() for v in args.via.split(",") if v.strip()]
    if args.self_test:
        return self_test(args, via)

    lengths = list(range(args.min_bytes, args.max_bytes + 1, args.step))
    if not lengths:
        raise SystemExit("linktest: empty length range")
    max_cycles = int(args.max_minutes * 60 / args.cycle_seconds)

    print(f"source       {args.call} > {args.dest} via "
          f"{','.join(via) or '(direct)'}")
    print(f"target       {args.target}")
    print(f"control      {args.control_bytes} bytes, every cycle")
    print(f"test         {lengths}")
    print(f"schedule     a cycle every {args.cycle_seconds:g}s, "
          f"{max_cycles} max, stopping at {args.max_minutes:g} min")
    print(f"early stop   when every length has >= {args.min_informative} "
          f"informative cycles or 2 echoes"
          + (" (disabled)" if args.no_early_stop else ""))
    print(f"log          {args.log}")
    print()

    if args.dry_run:
        for role, total in (("C", args.control_bytes), ("T", lengths[-1])):
            info = make_info(role, 1, total, args.call, args.dest, via)
            frame = build_ax25(args.call, args.dest, via, info)
            print(tnc2(args.call, args.dest, via, info))
            print(f"  on-air {frame_bytes(args.call, args.dest, via, info)} "
                  f"bytes, KISS payload {len(frame)}")
            print(f"  {kiss_wrap(frame).hex()}")
            print()
        print("dry run: nothing transmitted")
        return 0

    try:
        sock = socket.create_connection((args.host, args.port), timeout=10)
    except OSError as exc:
        raise SystemExit(
            f"linktest: cannot reach KISS at {args.host}:{args.port} ({exc}).\n"
            f"           sed -i 's/^KISS_PORT *=.*/KISS_PORT = 8001/' "
            f"igate.conf\n"
            f"           ./deploy_igate.sh restart\n"
            f"           (igate.conf, not igate.local.conf, which rejects it)")

    tally = Tally(lengths, args.target, args.min_informative)
    rx = bytearray()
    started = time.monotonic()
    deadline = started + args.max_minutes * 60
    schedule = []                               # (when, cycle, role, nbytes)
    for rep in range(max_cycles // len(lengths) + 1):
        for L in lengths:
            cycle = len(schedule) // 2 + 1
            if cycle > max_cycles:
                break
            t0 = started + (cycle - 1) * args.cycle_seconds
            schedule.append((t0, cycle, "C", args.control_bytes))
            schedule.append((t0 + args.gap_seconds, cycle, "T", L))
    next_idx = 0
    reason = "deadline"

    sock.setblocking(False)
    with sock, open(args.log, "a", buffering=1) as log:
        begun = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        log.write("# run started %s target=%s\n" % (begun, args.target))
        log.write("# utc\tcycle\trole\tbytes\tinfo\n")
        try:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    reason = "deadline"
                    break
                if next_idx >= len(schedule):
                    reason = "schedule exhausted"
                    break

                # transmit anything due
                while next_idx < len(schedule) and schedule[next_idx][0] <= now:
                    _, cycle, role, nbytes = schedule[next_idx]
                    info = make_info(role, cycle, nbytes,
                                     args.call, args.dest, via)
                    try:
                        sock.sendall(kiss_wrap(
                            build_ax25(args.call, args.dest, via, info)))
                    except OSError as exc:
                        print(f"\nlinktest: KISS write failed ({exc})")
                        reason = "socket error"
                        next_idx = len(schedule)
                        break
                    tally.note_send(cycle, role, nbytes)
                    stamp = datetime.datetime.now(datetime.timezone.utc)\
                        .strftime("%Y-%m-%dT%H:%M:%SZ")
                    log.write(f"{stamp}\t{cycle}\t{role}\t{nbytes}\t{info}\n")
                    print(f"[{cycle:3d}/{max_cycles}] tx {role} {nbytes:3d}B")
                    next_idx += 1

                # drain echoes
                ready, _, _ = select.select([sock], [], [], 0.5)
                if ready:
                    try:
                        chunk = sock.recv(8192)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        print("\nlinktest: KISS connection closed by Direwolf")
                        reason = "connection closed"
                        break
                    rx += chunk
                    while FEND in rx:
                        end = rx.index(FEND)
                        blob, rx = bytes(rx[:end]), rx[end + 1:]
                        if len(blob) < 2:
                            continue
                        try:
                            src, digis, info = decode_ax25(
                                kiss_unwrap(blob[1:]))
                        except (ValueError, IndexError):
                            continue
                        mark = parse_marker(info)
                        if not mark or src.upper() != args.call.upper():
                            continue
                        role, cycle, nbytes = mark
                        reps = repeaters(digis)
                        if reps:
                            tally.note_echo(cycle, role, reps)
                            print(f"          echo {role} {nbytes:3d}B "
                                  f"c{cycle} via {','.join(reps)}")

                # early stop, scoring only cycles that have had time to echo
                if not args.no_early_stop:
                    cutoff = 0
                    for t0, cycle, role, _ in schedule[:next_idx]:
                        if role == "T" and now - t0 >= args.settle_seconds:
                            cutoff = max(cutoff, cycle)
                    if cutoff and tally.settled(cutoff):
                        reason = "answer settled"
                        break
        except KeyboardInterrupt:
            reason = "interrupted"

    elapsed = (time.monotonic() - started) / 60
    print()
    print(f"stopped: {reason} after {elapsed:.1f} min")
    print()
    print(tally.table(max_cycles))
    print()
    print("  test-frame echoes per length, per digipeater:")
    print(tally.matrix(max_cycles))
    print()
    if tally.others:
        print("  echoes by digipeater (all frames, all lengths):")
        for call, n in sorted(tally.others.items(), key=lambda kv: -kv[1]):
            print(f"    {call:<12} {n}")
        print()

    blocked = [L for L in lengths if tally.verdict(L, max_cycles)[0] == "blocked"]
    repeated = [L for L in lengths
                if tally.verdict(L, max_cycles)[0] == "repeated"]
    total_informative = sum(tally.counts(L, max_cycles)[0] for L in lengths)
    if not repeated and not blocked and total_informative == 0:
        print(f"  VERDICT  no information. {args.target} echoed no control "
              f"frame, so no cycle was informative.")
        print(f"           Either it is not hearing this station at all right "
              f"now, or the KISS frames did not go out.")
        print(f"           Check the log for '[0L] {args.call}' lines before "
              f"rerunning.")
    elif not repeated and not blocked:
        print(f"  VERDICT  not enough cycles yet. {total_informative} "
              f"informative across {len(lengths)} lengths; each needs "
              f"{args.min_informative}.")
        print(f"           The control frames are getting through, so the "
              f"method works. Run it longer,")
        print(f"           or narrow the range to spend the cycles where they "
              f"count, e.g.")
        print(f"           --min-bytes 107 --max-bytes 131 --step 8")
    elif blocked and repeated and min(blocked) > max(repeated):
        print(f"  VERDICT  length matters. {args.target} repeated up to "
              f"{max(repeated)} bytes and none at or above {min(blocked)}.")
        print(f"           A gated message is about 120 bytes, which lands "
              f"on the blocked side.")
    elif not blocked:
        print(f"  VERDICT  length does not matter. {args.target} repeated "
              f"frames at every length tested, {max(repeated)} bytes included.")
        print(f"           Look elsewhere for why gated traffic is refused.")
    else:
        print(f"  VERDICT  mixed: repeated {repeated}, blocked {blocked}. "
              f"No clean threshold.")
    print()
    print(f"  Cross-check offline:  ./linktest_report.py")
    print("  TEARDOWN:  sed -i 's/^KISS_PORT *=.*/KISS_PORT = 0/' "
          "igate.conf && ./deploy_igate.sh restart")
    return 0


def self_test(args, via):
    ok = True

    got = encode_address("WIDE1", 1, last=True).hex()
    want = "ae92888a624063"
    print(f"WIDE1-1 final address     {got}  "
          f"{'ok' if got == want else 'FAIL ' + want}")
    ok &= got == want

    #   A=0x41<<1=0x82 P=0x50<<1=0xa0 D=0x44<<1=0x88
    #   W=0x57<<1=0xae 1=0x31<<1=0x62 7=0x37<<1=0x6e, then 0x60|0x80=0xe0
    got = encode_address("APDW17", 0, command=True).hex()
    want = "82a088ae626ee0"
    print(f"APDW17 dest address       {got}  "
          f"{'ok' if got == want else 'FAIL ' + want}")
    ok &= got == want

    got = kiss_wrap(bytes([0xC0, 0xDB, 0x41])).hex()
    want = "c000dbdcdbdd41c0"
    print(f"KISS escaping             {got}  "
          f"{'ok' if got == want else 'FAIL ' + want}")
    ok &= got == want

    got = kiss_unwrap(bytes.fromhex("dbdcdbdd41")).hex()
    want = "c0db41"
    print(f"KISS unescaping           {got}  "
          f"{'ok' if got == want else 'FAIL ' + want}")
    ok &= got == want

    lengths_ok = True
    for total in range(41, 220):
        try:
            info = make_info("T", 1, total, args.call, args.dest, via)
        except SystemExit:
            continue
        if frame_bytes(args.call, args.dest, via, info) != total:
            print(f"length {total} FAIL")
            lengths_ok = False
    print(f"length builder exact 41..219            "
          f"{'ok' if lengths_ok else 'FAIL'}")
    ok &= lengths_ok

    info = make_info("T", 7, 99, args.call, args.dest, via)
    src, digis, body = decode_ax25(build_ax25(args.call, args.dest, via, info))
    rt = (src == args.call.upper() and body == info
          and [d for d, _ in digis] == [v.upper() for v in via])
    print(f"encode/decode round trip                "
          f"{'ok' if rt else 'FAIL'}")
    ok &= rt

    mk = parse_marker(info)
    print(f"marker parse  {mk}            "
          f"{'ok' if mk == ('T', 7, 99) else 'FAIL'}")
    ok &= mk == ("T", 7, 99)

    # W3YA-1 repeated it (H set), WIDE1 remnant also marked used.
    hop = bytearray(build_ax25(args.call, args.dest, ["W3YA-1", "WIDE1"], info))
    hop[20] |= 0x80
    hop[27] |= 0x80
    _, digis, _ = decode_ax25(bytes(hop))
    reps = repeaters(digis)
    print(f"H-bit repeater detection  {reps}  "
          f"{'ok' if reps == ['W3YA-1'] else 'FAIL expected [W3YA-1]'}")
    ok &= reps == ["W3YA-1"]

    print()
    print("self-test", "passed" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
