"""Parse Direwolf logs into frame records. Shared by every report below.

Log line forms, with and without the -T timestamp:
    [0.5] SRC>DEST,path:info
    [0.5 2026-11-09T14:00:32-0500] SRC>DEST,path:info
    [0L] / [0H]   our own transmissions
    [rx>ig] [ig>tx]   APRS-IS sides, ignored for RF analysis
On-air length follows the project definition in digi_length_profile.py:
    7*(2+ndigis) + control + PID + infolen + flag + FCS + flag

BYTE COUNTING IS EXACT, AND TWO THINGS MAKE THAT NON-TRIVIAL
    Read the logs as LATIN-1, never UTF-8. 199 frames in the 2026-11 archive
    carry byte sequences that are valid multi-byte UTF-8, and a text decode
    counts two bytes as one character, undercounting those frames. Latin-1 maps
    every byte to exactly one character.

    Dire Wolf renders unprintable bytes as <0xNN>: six logged characters for
    one on-air byte. It does this for SPACES too -- <0x20> occurs 10,241 times
    in that archive -- so a naive len() of the logged text overcounts by 5 per
    escape. decode() handles it. Lines marked (Not AX.25) have no address field
    and are skipped; there were 11.
"""
import gzip, io, os, re

ON_AIR_OVERHEAD = 4
TAG = re.compile(r"^\[([^\]]*)\]\s(.*)$")
HEX = re.compile(r"<0x([0-9a-fA-F]{2})>")
ALIASES = re.compile(r"^(WIDE\d?|TRACE\d?|RELAY|ECHO|GATE|TEMP\d?|SAR\d?|ARISS|RS0ISS|NOGATE|RFONLY|TCPIP|TCPXX|qA[A-Z])",
                     re.I)

def decode(s):
    return HEX.sub(lambda m: chr(int(m.group(1), 16)), s)

class Frame:
    __slots__ = ("kind","slicer","ts","src","dest","digis","used_to","infolen",
                 "on_air","info","raw","file")
    def __repr__(self):
        return "<%s %s>%s via %s %dB>" % (self.kind,self.src,self.dest,
                                          ",".join(self.digis),self.on_air)

def parse_line(line, fname):
    m = TAG.match(line)
    if not m: return None
    tag, rest = m.group(1), m.group(2)
    parts = tag.split()
    chan = parts[0]
    ts = parts[1] if len(parts) > 1 else None
    if not chan or chan[0] != "0": return None          # skip rx>ig, ig>tx
    kind = "rx" if "." in chan else ("tx" if chan[1:2] in ("L","H") else None)
    if kind is None: return None
    if ">" not in rest or ":" not in rest: return None
    head, info = rest.split(":", 1)
    if ">" not in head: return None
    addr = head.split(",")
    sd = addr[0]
    if ">" not in sd: return None
    src, dest = sd.split(">", 1)
    digis = addr[1:]
    used_to = -1
    for i, d in enumerate(digis):
        if d.endswith("*"): used_to = i
    f = Frame()
    f.kind, f.slicer, f.ts = kind, chan, ts
    f.src, f.dest = src.strip(), dest.strip()
    f.digis = [d.rstrip("*") for d in digis]
    f.used_to = used_to
    f.info = decode(info)
    f.infolen = len(f.info)
    f.on_air = 7*(2+len(f.digis)) + 1 + 1 + f.infolen + ON_AIR_OVERHEAD
    f.raw = rest
    f.file = fname
    return f

def used_digis(f):
    return f.digis[:f.used_to+1] if f.used_to >= 0 else []

def real_calls(ds):
    """Path entries that are actual station callsigns, not routing aliases."""
    return [d for d in ds if not ALIASES.match(d)]

def iter_frames(paths):
    for p in paths:
        base = os.path.basename(p)
        op = gzip.open if p.endswith(".gz") else open
        with op(p, "rt", encoding="latin-1") as fh:
            for line in fh:
                f = parse_line(line.rstrip("\r\n"), base)
                if f is not None:
                    yield f

def logfiles(d):
    keep = []
    for n in sorted(os.listdir(d)):
        if not n.endswith(".log.gz"): continue
        if n.startswith("direwolf.log."): continue   # byte-identical to two archives
        keep.append(os.path.join(d, n))
    return keep


# ---------------------------------------------------------------------------
# Report: who relayed what, at what length.
#
# Written 2026-11-15 to re-derive the frame-length findings against the whole
# archive rather than one day. It retired a published 97-byte "ceiling" at
# W3YA-1: over twelve logs that station emits to 102 bytes and decodes to 95.
#
#   ./digi_relay_audit.py <dir-of-direwolf-logs> [--me KD3CCO-10,KD3CCO-7]
#
# Pull the logs first; the archives are disjoint, contiguous windows, so no
# deduplication is needed, but direwolf.log.N.gz are byte-identical copies of
# two of the archives and logfiles() drops them.
#
#   scp 'igate@aprs-igate.local:/opt/aprs-igate/archive/frames/*.log.gz' ./logs/
# ---------------------------------------------------------------------------

def transmitter(f):
    """The station whose transmitter put this copy on the air."""
    rc = real_calls(used_digis(f))
    return rc[-1] if rc else f.src

def report(d, me):
    import collections
    frames = list(iter_frames(logfiles(d)))
    print("%d frames, %d logs" % (len(frames), len(logfiles(d))))
    ts = [f.ts for f in frames if f.ts]
    if ts:
        print("timestamped span %s .. %s" % (min(ts)[:19], max(ts)[:19]))

    # as-sent length for my own traffic: my TX log, or an undigipeated copy
    sent = {}
    for f in frames:
        if f.src in me and (f.kind == "tx" or not used_digis(f)):
            sent.setdefault((f.src, f.info), f.on_air)

    print("\n=== every digipeater that relayed my traffic ===")
    print("%-12s %8s %10s %10s" % ("digi", "copies", "max mine", "equipment"))
    rel = collections.defaultdict(lambda: [0, 0])
    tocall = {}
    for f in frames:
        if not used_digis(f):
            tocall.setdefault(f.src, f.dest)
        if f.src not in me:
            continue
        ur = real_calls(used_digis(f))
        if not ur:
            continue
        L = sent.get((f.src, f.info), f.on_air - 7 * len(ur))
        for dg in ur:
            rel[dg][0] += 1
            rel[dg][1] = max(rel[dg][1], L)
    for dg, (n, mx) in sorted(rel.items(), key=lambda kv: -kv[1][1]):
        print("%-12s %8d %10d %10s" % (dg, n, mx, tocall.get(dg, "?")))

    print("\n=== per digipeater: my relay rate below vs above its own cutoff ===")
    for dg in sorted(rel):
        got = set()
        for f in frames:
            if f.src in me and dg in real_calls(used_digis(f)):
                got.add(f.info)
        lo = [0, 0]; hi = [0, 0]
        for (s, info), L in sent.items():
            bucket = lo if L <= 82 else hi
            bucket[0] += 1
            if info in got:
                bucket[1] += 1
        print("  %-12s <=82B %4d/%-4d   >82B %4d/%-4d"
              % (dg, lo[1], lo[0], hi[1], hi[0]))

    print("\n=== what each digipeater emits for OTHER stations ===")
    print("%-12s %8s %6s %8s %12s" % ("digi", "copies", "min", "median", "max emitted"))
    em = collections.defaultdict(list)
    for f in frames:
        if f.src in me:
            continue
        t = transmitter(f)
        if t != f.src:
            em[t].append(f.on_air)
    for dg, v in sorted(em.items(), key=lambda kv: -len(kv[1])):
        if len(v) < 50:
            continue
        v.sort()
        print("%-12s %8d %6d %8d %12d" % (dg, len(v), v[0], v[len(v)//2], v[-1]))

def hop_report(d, digi="W3YA-1", me=("KD3CCO-10","KD3CCO-7")):
    """Emitted length by the digipeater's own hop position.

    At hop k the frame already carries k-1 inserted callsigns, so for the same
    information field a later hop is a longer frame. That separates a limit on
    the whole frame (max info field falls 7 bytes per extra path entry, max
    emitted stays flat) from a PACLEN-style field limit (the reverse).
    """
    import collections
    frames = [f for f in iter_frames(logfiles(d))
              if real_calls(used_digis(f))
              and real_calls(used_digis(f))[-1] == digi
              and f.src not in me]
    byhop = collections.defaultdict(list)
    for f in frames:
        byhop[len(real_calls(used_digis(f)))].append(f)
    print("%s emitted length by hop position" % digi)
    print("%4s %8s %8s %12s %12s %14s"
          % ("hop", "copies", "median", "max emitted", "max decoded", "max info field"))
    for k in sorted(byhop):
        g = byhop[k]
        e = sorted(f.on_air for f in g)
        print("%4d %8d %8d %12d %12d %14d"
              % (k, len(g), e[len(e)//2], e[-1], e[-1]-7,
                 max(f.infolen for f in g)))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("logdir")
    ap.add_argument("--me", default="KD3CCO-10,KD3CCO-7")
    a = ap.parse_args()
    report(a.logdir, tuple(a.me.split(",")))
    print()
    hop_report(a.logdir, me=tuple(a.me.split(",")))
