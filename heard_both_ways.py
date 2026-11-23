#!/usr/bin/env python3
"""Who this station hears, and who hears this station -- four maps from one log.

The four questions are NOT four views of one dataset. They are two different
measurements with very different power, and the figure has to say so:

  1. Stations KD3CCO-10 copied DIRECTLY     -- rich: every station that
  2. Stations KD3CCO-10 copied AT ALL          transmits in range leaves a frame.
  3. Stations that copied KD3CCO-10 DIRECTLY -- sparse: a station only reveals
  4. Stations that copied KD3CCO-10 AT ALL      that it heard us if it ACTS on
                                                the frame.

Only two kinds of station act. A DIGIPEATER repeats the frame and we copy the
repeat, which names it in the path. An IGATE forwards the frame to APRS-IS and
the q-construct names it when the frame comes back down the server stream.
An ordinary station that copies our beacon and does nothing is invisible here
and always will be. So 3 and 4 are LOWER BOUNDS, and the bound is loose:
APRS-IS keeps only the FIRST copy of a frame, this station gates its own
repeats within a second, and every other igate's copy of that frame is then
discarded by the server. W3TM-10 shows 71 gatings and KB3AWQ-10 shows 11 not
because one hears us six times better but because of who won a race.

**Do not read 3 and 4 as a coverage map.** They are a map of who is listening
AND equipped to tell us. Read 1 and 2 for coverage.

Positions, and where they come from:

  * For 1 and 2, position comes from Direwolf's own decoded-position line under
    each RF frame, which gets MIC-E and the compressed formats for free. Only
    payloads carrying the SENDER'S position count: objects (`;`), items (`)`)
    and third-party (`}`) carry somebody else's coordinates, and counting them
    puts a repeater object 98 miles away into this station's receive map.
  * **Per frame, never per station.** Using a station's latest position for all
    of its frames is wrong for mobiles and it is wrong in the direction that
    flatters: it put N0XGA-9 at 253 miles into the directly-heard set off one
    frame copied while it was local. A mobile crossing cells belongs in each
    cell it transmitted from.
  * For 3 and 4 the station is a digi or an igate, so its position comes from
    its own beacon, by RF where we hear it and from the APRS-IS stream where we
    do not. APRS-IS positions are used ONLY to place these few stations, never
    to build a receive heat map.

Beacons lie. Null island, 23N/113E, 90N/180E and sign-flipped longitudes all
appear in real traffic, so positions outside a North American box are dropped
and the count of drops is reported rather than hidden.

Two stages, because the log is downloaded data and the plot needs libraries:

    python3 -I heard_both_ways.py analyze 'pi-frames/frames-*.log.gz' -o OUTDIR
    python    heard_both_ways.py draw OUTDIR          # conda env, NOT -I
"""
import argparse
import glob
import gzip
import math
import os
import re
import sys
from collections import defaultdict

# A frame received on RF. `[0L]` is this station TRANSMITTING; counting it as a
# reception puts the gateway's own beacons into its own coverage map.
RX = re.compile(r'^\[0\.[0-9]+(?: [^\]]*)?\]\s+([A-Z0-9][A-Z0-9-]*)>([^:]*):(.*)$')
# A frame handed down by the APRS-IS server. Never an RF reception.
IG = re.compile(r'^\[ig>tx\]\s+([A-Z0-9][A-Z0-9-]*)>([^:]*):(.*)$')
# The first line of Direwolf's decode block. Field 2 is the symbol
# description, which is the only evidence of mobility for a station heard once:
# a span-based test needs at least two widely separated copies.
SYMLINE = re.compile(r'^(?:Position|Position with time|MIC-E)[,.]\s')
# Direwolf's decoded position, printed under the frame it belongs to.
DECODED = re.compile(r'^([NS])\s+(\d+)\s+([\d.]+),\s*([EW])\s+(\d+)\s+([\d.]+)')
# Uncompressed APRS position in a raw payload, for the APRS-IS fallback.
RAW_POS = re.compile(r'(\d{4}\.\d{2})([NS])[\x21-\x7e](\d{5}\.\d{2})([EW])')
# Path entries that name no station.
ALIAS = re.compile(r'WIDE\d(-\d)?|TRACE\d?|RELAY|ECHO|GATE|TEMP\d?|LOCAL|ARISS')
# Data types carrying the sender's OWN position.
SELF_POS_DTI = set("!=/@`'")
# How far below its frame Direwolf's decoded position can sit.
LOOKAHEAD = 6
# Beacons lie; anything outside this box is a bad beacon, not a long path.
BOX = (24.0, 60.0, -130.0, -60.0)

ME = "KD3CCO-10"
GW = (40.0 + 46.25 / 60, -(77.0 + 47.50 / 60))   # FN10CS, from igate.conf


def sane(lat, lon):
    return BOX[0] <= lat <= BOX[1] and BOX[2] <= lon <= BOX[3]


def miles(lat, lon, origin=GW):
    p1, p2 = math.radians(origin[0]), math.radians(lat)
    dp, dl = p2 - p1, math.radians(lon - origin[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h)) * 0.621371


def dms(ns, d, m, ew, d2, m2):
    lat = (int(d) + float(m) / 60) * (1 if ns == "N" else -1)
    lon = (int(d2) + float(m2) / 60) * (1 if ew == "E" else -1)
    return lat, lon


def raw_position(payload):
    """Parse an uncompressed APRS position out of a payload. APRS-IS fallback."""
    if payload[:1] not in SELF_POS_DTI:
        return None
    m = RAW_POS.search(payload[:30])
    if not m:
        return None
    la, ns, lo, ew = m.groups()
    lat = (int(la[:2]) + float(la[2:]) / 60) * (1 if ns == "N" else -1)
    lon = (int(lo[:3]) + float(lo[3:]) / 60) * (1 if ew == "E" else -1)
    return (lat, lon) if sane(lat, lon) else None


def digis(path):
    """Station callsigns in a path, in order, aliases dropped."""
    out = []
    for hop in path.split(",")[1:]:
        bare = hop.rstrip("*")
        if bare.startswith("q") or bare == "TCPIP" or bare == "TCPXX":
            break
        if not ALIAS.fullmatch(bare):
            out.append(bare)
    return out


def analyze(patterns, outdir):
    files = sorted(f for p in patterns for f in glob.glob(p))
    if not files:
        sys.exit("no log files matched")

    # 1 & 2: one row per position-bearing RF frame.
    frames = []                       # (call, lat, lon, direct, symbol)
    # 3 & 4: per station, how many of our frames it carried, and how many of
    # those it had copied directly rather than through another digi.
    hears = defaultdict(lambda: {"direct": 0, "any": 0, "how": set()})
    pos_rf, pos_is = {}, {}
    dropped = defaultdict(int)
    stats = defaultdict(int)

    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "rt", encoding="latin-1") as fh:
            lines = fh.readlines()
        for i, line in enumerate(lines):
            m = RX.match(line)
            if m:
                src, pth, info = m.groups()
                hops = pth.split(",")[1:]
                direct = not any(h.endswith("*") for h in hops)
                if src == ME:
                    # A repeat of our own frame. The first station in the path
                    # copied us off the air; later ones copied the repeat.
                    for j, d in enumerate(digis(pth)):
                        hears[d]["any"] += 1
                        hears[d]["how"].add("digipeated")
                        if j == 0:
                            hears[d]["direct"] += 1
                    stats["own_repeats"] += 1
                    continue
                stats["rx_frames"] += 1
                if info[:1] not in SELF_POS_DTI:
                    continue
                sym = ""
                for k in range(i + 1, min(i + 1 + LOOKAHEAD, len(lines))):
                    nxt = lines[k].strip()
                    if nxt.startswith("["):
                        break
                    if SYMLINE.match(nxt):
                        bits = nxt.split(", ")
                        if len(bits) > 1:
                            sym = bits[1].strip()
                        continue
                    d = DECODED.match(nxt)
                    if d:
                        lat, lon = dms(*d.groups())
                        if sane(lat, lon):
                            frames.append((src, lat, lon, direct, sym))
                            pos_rf[src] = (lat, lon)
                        else:
                            dropped[src] += 1
                        break
                continue

            m = IG.match(line)
            if m:
                src, pth, info = m.groups()
                p = raw_position(info)
                if p:
                    pos_is.setdefault(src, p)
                if src != ME:
                    continue
                # Our own frame, back down from the server. The q-construct
                # names the igate that fed it in.
                # ONLY qAR/qAO (and the indirect qAr/qAo) name an igate that
                # copied the frame off the air. qAC, qAS, qAU, qAX and qAZ name
                # a SERVER, and matching those puts a Tier-2 host on the map as
                # a station that hears this one.
                q = re.search(r'\bq(A[ROro]),([A-Z0-9][A-Z0-9-]*)', pth)
                if not q or q.group(2) == ME:
                    continue
                gater = q.group(2)
                before = pth.split(",q" + q.group(1))[0]
                hears[gater]["any"] += 1
                hears[gater]["how"].add("gated")
                if not any(h.endswith("*") for h in before.split(",")[1:]):
                    hears[gater]["direct"] += 1
                stats["own_gated_by_others"] += 1

    os.makedirs(outdir, exist_ok=True)
    fp = os.path.join(outdir, "rx_frames.tsv")
    with open(fp, "w") as fh:
        fh.write("call\tlat\tlon\tdirect\tsym\n")
        for call, lat, lon, direct, sym in frames:
            fh.write(f"{call}\t{lat:.5f}\t{lon:.5f}\t{int(direct)}\t"
                     f"{sym.replace(chr(9), ' ')}\n")

    hp = os.path.join(outdir, "hears_us.tsv")
    unplaced = []
    with open(hp, "w") as fh:
        fh.write("call\tlat\tlon\tdist_mi\tdirect\tany\thow\tpos_src\n")
        for call in sorted(hears, key=lambda c: -hears[c]["any"]):
            h = hears[call]
            p, src = pos_rf.get(call), "rf"
            if not p:
                p, src = pos_is.get(call), "aprs-is"
            # An unplaced station still belongs in the table. Dropping it
            # silently is how a figure comes to claim 8 stations when the
            # measurement found 9.
            if not p:
                unplaced.append(call)
                fh.write(f"{call}\t\t\t\t{h['direct']}\t{h['any']}\t"
                         f"{'+'.join(sorted(h['how']))}\tnone\n")
                continue
            fh.write(f"{call}\t{p[0]:.5f}\t{p[1]:.5f}\t{miles(*p):.2f}\t"
                     f"{h['direct']}\t{h['any']}\t{'+'.join(sorted(h['how']))}\t{src}\n")

    direct_calls = {c for c, _, _, d, _ in frames if d}
    all_calls = {c for c, _, _, _, _ in frames}
    print(f"files            {len(files)}")
    print(f"RF frames read   {stats['rx_frames']}  "
          f"({len(frames)} carried a usable position)")
    print(f"our own repeats  {stats['own_repeats']}  "
          f"gated by others {stats['own_gated_by_others']}")
    print(f"bad positions    {sum(dropped.values())} frames from "
          f"{len(dropped)} stations, dropped: "
          f"{', '.join(sorted(dropped)[:6])}{' ...' if len(dropped) > 6 else ''}")
    print()
    print(f"1) heard directly   {len(direct_calls):4d} stations")
    print(f"2) heard at all     {len(all_calls):4d} stations")
    nd = sum(1 for h in hears.values() if h["direct"])
    print(f"3) heard us direct  {nd:4d} stations")
    print(f"4) heard us at all  {len(hears):4d} stations"
          + (f"  ({len(unplaced)} unplaced: {', '.join(unplaced)})" if unplaced else ""))
    for label, s in (("direct", direct_calls), ("any", all_calls)):
        ds = sorted(miles(la, lo) for c, la, lo, d, _ in frames
                    if c in s and (d or label == "any"))
        if ds:
            print(f"   {label:6s} frame distance mi: median {ds[len(ds)//2]:.0f}  "
                  f"p90 {ds[int(len(ds)*.9)]:.0f}  max {ds[-1]:.0f}")
    print(f"\nwrote {fp}\n      {hp}")


# ---------------------------------------------------------------- drawing ----
# Azimuthal equidistant about the gateway. A flat lat/lon projection is wrong
# by about 10% at the 500-mile frame and it is wrong in the worst place: the
# range rings stop meaning what they say. Projecting by great-circle distance
# and bearing makes every ring exactly right by construction.
SURFACE = "#fdfdfd"
INK, INK2, MUTED, RING = "#0b0b0b", "#52514e", "#8a8a84", "#dcdbd6"
# One hue, light to dark: a sequential ramp for a magnitude. The light end is
# #dce8f8 rather than white so a one-station cell is still visible on #fdfdfd.
RAMP = ["#dce8f8", "#a8c8ee", "#6fa3e0", "#2a78d6", "#1d5099", "#10335f"]
NEAR, FAR = 80.0, 500.0
NEAR_RINGS, FAR_RINGS = (20, 40, 60, 80), (100, 200, 300, 400, 500)
# Marker shape carries HOW a station revealed itself, so that distinction never
# rests on colour alone.
SHAPE = {"digipeated": "^", "gated": "s", "digipeated+gated": "D"}


def project(lat, lon, origin=GW):
    """Miles east and north of the origin, azimuthal equidistant."""
    p1, p2 = math.radians(origin[0]), math.radians(lat)
    dl = math.radians(lon - origin[1])
    d = miles(lat, lon, origin)
    brg = math.atan2(math.sin(dl) * math.cos(p2),
                     math.cos(p1) * math.sin(p2)
                     - math.sin(p1) * math.cos(p2) * math.cos(dl))
    return d * math.sin(brg), d * math.cos(brg)


def read_tsv(path):
    import csv
    with open(path) as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _frame(ax, extent, rings):
    for r in rings:
        ax.add_patch(plt.Circle((0, 0), r, fill=False, ec=RING, lw=0.9, zorder=1))
        ax.text(0, r, f"{r}", color=MUTED, fontsize=7.5, ha="center",
                va="bottom", zorder=2,
                bbox=dict(boxstyle="round,pad=0.08", fc=SURFACE, ec="none"))
    ax.set_xlim(-extent, extent)
    ax.set_ylim(-extent, extent)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.plot(0, 0, marker="D", ms=5.5, color=INK, mec=SURFACE, mew=1.2, zorder=9)


def _heat(ax, rows, want_direct, extent, cell, rings):
    """Cells coloured by how many DISTINCT stations were copied from them."""
    import numpy as np
    n = int(math.ceil(extent / cell)) * 2
    grid = [[set() for _ in range(n)] for _ in range(n)]
    inside, outside = set(), set()
    for r in rows:
        if want_direct and r["direct"] != "1":
            continue
        x, y = project(float(r["lat"]), float(r["lon"]))
        ix, iy = int((x + extent) // cell), int((y + extent) // cell)
        if 0 <= ix < n and 0 <= iy < n:
            grid[iy][ix].add(r["call"])
            inside.add(r["call"])
        else:
            outside.add(r["call"])
    counts = np.array([[len(c) for c in row] for row in grid], dtype=float)
    counts[counts == 0] = np.nan
    cmap = mcolors.LinearSegmentedColormap.from_list("heard", RAMP)
    im = ax.pcolormesh(np.linspace(-extent, extent, n + 1),
                       np.linspace(-extent, extent, n + 1),
                       counts, cmap=cmap, vmin=1, vmax=np.nanmax(counts),
                       shading="flat", zorder=3)
    _frame(ax, extent, rings)
    cb = ax.figure.colorbar(im, ax=ax, orientation="horizontal", fraction=0.042,
                            pad=0.015, aspect=30)
    cb.set_label(f"distinct stations per {cell:g}-mile cell", fontsize=8,
                 color=INK2)
    cb.ax.tick_params(labelsize=7.5, colors=INK2, length=2)
    cb.outline.set_visible(False)
    # A station with frames on both sides of the edge is INSIDE. Only a
    # station never once heard from within the frame counts as outside.
    only_out = outside - inside
    note = f"{len(inside | outside)} stations"
    if only_out:
        note += f"; {len(only_out)} heard only from beyond this frame"
    ax.text(0.5, -0.215, note, transform=ax.transAxes, ha="center",
            va="top", fontsize=8.5, color=INK2)


def _points(ax, rows, want_direct, extent, rings, labels=None, extra=None,
            ref_ring=None):
    key = "direct" if want_direct else "any"
    live = [r for r in rows if int(r[key]) > 0]
    placed = [r for r in live if r["lat"]]
    missing = [r["call"] for r in live if not r["lat"]]
    top = max(int(r[key]) for r in placed)
    _frame(ax, extent, rings)
    if ref_ring:
        ax.add_patch(plt.Circle((0, 0), ref_ring, fill=False, ec=MUTED, lw=1.0,
                                ls=(0, (4, 3)), zorder=2))
        ax.text(-ref_ring * 1.3, -ref_ring * 1.1,
                f"{ref_ring:g} mi, the frame at left", color=MUTED,
                fontsize=7.8, ha="right", va="top", zorder=2)
    for r in placed:
        x, y = project(float(r["lat"]), float(r["lon"]))
        n = int(r[key])
        ax.plot(x, y, marker=SHAPE.get(r["how"], "o"), ms=7 + 9 * math.sqrt(n / top),
                color=RAMP[3], mec=SURFACE, mew=1.1, zorder=6)
        off = (labels or {}).get(r["call"])
        if off:
            dx, dy, ha = off
            ax.annotate(f"{r['call']}  {n:,}", (x, y), textcoords="offset points",
                        xytext=(dx, dy), ha=ha, fontsize=8.2, color=INK, zorder=10,
                        arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7,
                                        shrinkA=0, shrinkB=8)
                        if abs(dx) + abs(dy) > 34 else None,
                        bbox=dict(boxstyle="round,pad=0.14", fc=SURFACE,
                                  ec="none", alpha=0.9))
    if extra:
        ax.text(extra[0], extra[1], extra[2], transform=ax.transAxes,
                fontsize=7.6, color=INK2, va="top", ha="left",
                linespacing=1.4,
                zorder=10, family="DejaVu Sans Mono",
                bbox=dict(boxstyle="round,pad=0.45", fc=SURFACE, ec=RING))
    note = f"{len(live)} stations"
    if missing:
        note += (f"; {len(missing)} not drawn: "
                 f"{', '.join(missing)} never beaconed a position "
                 f"in this log")
    ax.text(0.5, -0.045, note, transform=ax.transAxes, ha="center", va="top",
            fontsize=8.5, color=INK2)


def draw(outdir):
    global plt, mcolors
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    rx = read_tsv(os.path.join(outdir, "rx_frames.tsv"))
    hu = read_tsv(os.path.join(outdir, "hears_us.tsv"))

    fig, axes = plt.subplots(2, 2, figsize=(11.6, 11.6), dpi=110)
    fig.patch.set_facecolor(SURFACE)
    for ax in axes.flat:
        ax.set_facecolor(SURFACE)

    _heat(axes[0][0], rx, True, NEAR, 4.0, NEAR_RINGS)
    _heat(axes[0][1], rx, False, FAR, 25.0, FAR_RINGS)
    _points(axes[1][0], hu, True, NEAR, NEAR_RINGS, labels={
        "KE4MHE-10": (13, 7, "left"),
        "N3SNN-3":   (8, 10, "left"),
        "W3TM-10":   (-62, 52, "right"),
        "W3YA-1":    (-64, -10, "right"),
        "WO3T-1":    (-12, -13, "right"),
        "W3VO-1":    (-11, -4, "right"),
        "K3DNA":     (13, -7, "left"),
        "N3DXC-2":   (-9, 9, "right"),
    })
    only4 = [r for r in hu if int(r["any"]) > 0 and int(r["direct"]) == 0]
    only4.sort(key=lambda r: -int(r["any"]))
    box = ("carried our frames, but never\nas the first to copy them:\n" +
           "\n".join(f" {r['call']:<10}{int(r['any']):>3}" for r in only4))
    _points(axes[1][1], hu, False, FAR, FAR_RINGS, ref_ring=NEAR,
            extra=(0.0, 0.92, box))

    titles = [
        ("1.  Stations KD3CCO-10 copied DIRECTLY",
         "copied off the air with no digipeater involved", NEAR),
        ("2.  Stations KD3CCO-10 copied AT ALL",
         "direct, or repeated by somebody else's digipeater", FAR),
        ("3.  Stations that copied KD3CCO-10 DIRECTLY",
         "they repeated or gated our beacon as the first station to touch it", NEAR),
        ("4.  Stations that copied KD3CCO-10 AT ALL",
         "they repeated or gated our beacon at any hop", FAR),
    ]
    for ax, (t, sub, ext) in zip(axes.flat, titles):
        ax.text(0.0, 1.085, t, transform=ax.transAxes, fontsize=11.5,
                fontweight="600", color=INK, va="bottom", ha="left")
        ax.text(0.0, 1.032, sub, transform=ax.transAxes, fontsize=8.5,
                color=INK2, va="bottom", ha="left")
        ax.text(0.995, 0.995, f"frame spans {ext:g} miles",
                transform=ax.transAxes, fontsize=8.2, color=MUTED,
                va="top", ha="right", zorder=11)

    h = [plt.Line2D([], [], ls="", marker="^", ms=8, color=RAMP[3], mec=SURFACE,
                    label="revealed itself by DIGIPEATING our frame"),
         plt.Line2D([], [], ls="", marker="s", ms=8, color=RAMP[3], mec=SURFACE,
                    label="revealed itself by GATING our frame to APRS-IS"),
         plt.Line2D([], [], ls="", marker="D", ms=8, color=RAMP[3], mec=SURFACE,
                    label="both"),
         plt.Line2D([], [], ls="", marker="D", ms=6, color=INK, mec=SURFACE,
                    label="KD3CCO-10, the gateway"),
         plt.Line2D([], [], ls="", marker="o", ms=8, mfc="none", mec=MUTED,
                    label="marker area is how many frames it carried")]
    lg = axes[1][0].legend(handles=h, loc="lower left", fontsize=7.6,
                           frameon=True, facecolor=SURFACE, edgecolor=RING,
                           borderpad=0.55, labelspacing=0.45,
                           bbox_to_anchor=(-0.005, 0.0))
    lg.set_zorder(11)

    axes[1][1].text(0.015, 0.015,
                    "Only a digipeater or an iGate can reveal that it heard us.\n"
                    "An ordinary station that copies the beacon and does nothing\n"
                    "is invisible here, and APRS-IS keeps only the first copy of\n"
                    "a frame, so panels 3 and 4 are LOWER BOUNDS. Panels 1 and 2\n"
                    "are the coverage measurement; panels 3 and 4 are not.",
                    transform=axes[1][1].transAxes, fontsize=8.1, color=INK2,
                    va="bottom", ha="left", linespacing=1.5, zorder=11,
                    bbox=dict(boxstyle="round,pad=0.45", fc=SURFACE, ec=RING))

    fig.text(0.008, 0.990, "What KD3CCO-10 hears, and what hears KD3CCO-10",
             fontsize=16, fontweight="600", color=INK, ha="left", va="top")
    fig.text(0.008, 0.967,
             "16 days of this station's own console log, 2026-10-29 to "
             "2026-11-20. Rings are miles from the gateway. The two columns "
             "are drawn to different scales.",
             fontsize=9.2, color=INK2, ha="left", va="top")
    fig.text(0.008, 0.008,
             "Positions are each station's own beacon, taken per frame rather "
             "than per station, so a mobile counts in every cell it transmitted "
             "from. Beacons outside North America are dropped as bad data.\n"
             "The top row counts distinct stations, not frames, so one chatty "
             "weather station cannot colour a cell.",
             fontsize=7.5, color=MUTED, ha="left", va="bottom", linespacing=1.5)

    fig.subplots_adjust(left=0.035, right=0.975, top=0.895, bottom=0.075,
                        hspace=0.42, wspace=0.10)
    png = os.path.join(outdir, "heard-both-ways.png")
    fig.savefig(png, facecolor=SURFACE)
    print(f"wrote {png}")
    interactive(outdir, rx, hu)


def interactive(outdir, rx, hu):
    import folium
    from folium.plugins import HeatMap

    # CARTO's positron basemap started demanding an API key: without one every
    # tile renders as an "API KEY REQUIRED" watermark and the map has no
    # geography at all. OpenStreetMap needs no key. Busier-looking, but the
    # roads show, which for a map of where mobiles travel is the whole point.
    m = folium.Map(location=GW, zoom_start=8, tiles="OpenStreetMap",
                   control_scale=True)
    for r in (20, 40, 80, 250, 500):
        folium.Circle(GW, radius=r * 1609.344, color=RING, weight=1, fill=False,
                      interactive=False, tooltip=f"{r} mi").add_to(m)

    # The heat layer is weighted by DISTINCT STATIONS, not frames: weighting by
    # frame count lets one chatty weather station outshine a whole town.
    seen_d, seen_a = {}, {}
    for r in rx:
        k = (round(float(r["lat"]), 2), round(float(r["lon"]), 2))
        seen_a.setdefault(k, set()).add(r["call"])
        if r["direct"] == "1":
            seen_d.setdefault(k, set()).add(r["call"])
    for name, seen, show in (("2. KD3CCO-10 heard these, at all", seen_a, True),
                             ("1. KD3CCO-10 heard these directly", seen_d, False)):
        g = folium.FeatureGroup(name=name, show=show)
        HeatMap([[la, lo, len(c)] for (la, lo), c in seen.items()],
                radius=14, blur=18, min_opacity=0.25).add_to(g)
        g.add_to(m)

    for name, key, show in (("4. These heard KD3CCO-10, at all", "any", True),
                            ("3. These heard KD3CCO-10 directly", "direct", False)):
        g = folium.FeatureGroup(name=name, show=show)
        for r in hu:
            n = int(r[key])
            if n == 0 or not r["lat"]:
                continue
            body = (f"<b>{r['call']}</b><br>{float(r['dist_mi']):.1f} mi from the "
                    f"gateway<br>carried {int(r['direct']):,} of our frames as the "
                    f"first station to copy them<br>{int(r['any']):,} at any hop"
                    f"<br>revealed itself by: {r['how']}"
                    f"<br><small>position from {r['pos_src']}</small>")
            folium.CircleMarker([float(r["lat"]), float(r["lon"])],
                                radius=6 + 10 * math.sqrt(n / 4248),
                                color="#fdfdfd", weight=2, fill=True,
                                fill_color=RAMP[3], fill_opacity=1,
                                tooltip=f"{r['call']}  {n:,}",
                                popup=folium.Popup(body, max_width=270)).add_to(g)
        g.add_to(m)

    folium.Marker(GW, tooltip="KD3CCO-10 gateway",
                  icon=folium.Icon(color="black", icon="tower-broadcast",
                                   prefix="fa")).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    out = os.path.join(outdir, "heard-both-ways.html")
    m.save(out)
    print(f"wrote {out}")


# ----------------------------------------------------------------- mobiles ---
# "Mobile" is decided two ways, because each alone is wrong in a known
# direction. The SYMBOL is what the operator says the station is, and it is the
# only evidence available for a station heard once -- but operators mis-set it,
# and 29 stations here moved more than a mile while claiming a fixed symbol.
# The SPAN is what the station actually did, and it is undersampled: a mobile
# copied twice in the same parking lot looks fixed. A station is mobile if
# either test says so.
VEHICLE = {
    "normal car (side view)", "truck", "Truck (18 wheeler)", "JEEP", "VAN",
    "OVERLAID Van", "FIRE TRUCK", "Farm Vehicle (tractor)", "REC. VEHICLE",
    "BUS", "BIKE", "motorcycle", "Wheelchair (handicapped)",
    "HORSE (equestrian)", "Police", "Police/Sheriff",
    "Overlay Special vehicles.  A = ATV for example",
    'Overlay Trucks.  "Tu" is a tanker. "Gu" is a gas truck',
    "Top view and symbol POINTS in direction of travel",
}
PERSON = {"Human", "Kenwood HT (W)",
          "Yaesu/Standard* <= Recommend special symbol"}
# Balloons, aircraft and boats move, but they do not trace roads, and a balloon
# drifting 80 miles would paint a line through country no car can reach. They
# get their own layer rather than being silently folded in or silently dropped.
AIRSEA = {
    "Small Aircraft (original primary symbol)", "LARGE Aircraft", "HELO",
    "Original Balloon (think Ham balloon)", "ROCKET (amateur)(2007)",
    "SHUTTLE", "Yacht  <= the original primary symbol", "Canoe",
    "Power boat (ship) side view",
}
MOVED_MI = 1.0          # a span under this is GPS jitter, not travel
CELL_MI = 2.0


def classify(rows):
    """Split stations into mobile, air/sea and fixed."""
    from collections import defaultdict
    pts, syms = defaultdict(list), defaultdict(set)
    for r in rows:
        pts[r["call"]].append((float(r["lat"]), float(r["lon"])))
        if r.get("sym"):
            syms[r["call"]].add(r["sym"])
    span = {}
    for c, p in pts.items():
        la = [x[0] for x in p]
        lo = [x[1] for x in p]
        span[c] = miles(min(la), min(lo), (max(la), max(lo)))
    by_sym = {c for c in syms if syms[c] & (VEHICLE | PERSON)}
    by_span = {c for c in pts if span[c] > MOVED_MI}
    airsea = {c for c in syms if syms[c] & AIRSEA and c not in by_sym}
    mobile = (by_sym | by_span) - airsea
    fixed = set(pts) - mobile - airsea
    return mobile, airsea, fixed, span, syms, by_sym, by_span


def cells_of(rows, calls, cell_mi):
    """Distinct stations per square cell, keyed on a latitude-banded grid.

    A constant degree step in longitude makes cells that are square at one
    latitude and wrong everywhere else. The longitude step is recomputed for
    each latitude band, so every cell is genuinely cell_mi on a side.
    """
    from collections import defaultdict
    dlat = cell_mi / 69.093
    out = defaultdict(lambda: {"calls": set(), "frames": 0, "direct": 0})
    for r in rows:
        if r["call"] not in calls:
            continue
        lat, lon = float(r["lat"]), float(r["lon"])
        iy = math.floor(lat / dlat)
        dlon = cell_mi / (69.093 * math.cos(math.radians((iy + 0.5) * dlat)))
        ix = math.floor(lon / dlon)
        c = out[(iy, ix)]
        c["calls"].add(r["call"])
        c["frames"] += 1
        c["direct"] += r["direct"] == "1"
        c["bounds"] = [[iy * dlat, ix * dlon], [(iy + 1) * dlat, (ix + 1) * dlon]]
    return out


def mobiles(outdir, cell_mi=CELL_MI):
    import folium

    rows = read_tsv(os.path.join(outdir, "rx_frames.tsv"))
    mobile, airsea, fixed, span, syms, by_sym, by_span = classify(rows)
    print(f"stations  mobile {len(mobile)}  fixed {len(fixed)}  "
          f"air/sea {len(airsea)}")
    print(f"  by symbol only {len(by_sym - by_span)}, by movement only "
          f"{len(by_span - by_sym - airsea)}, by both {len(by_sym & by_span)}")

    layers = [("Mobiles per cell", mobile, True),
              ("Fixed stations per cell", fixed, False),
              ("Balloons, aircraft and boats", airsea, False)]
    grids = {name: cells_of(rows, calls, cell_mi) for name, calls, _ in layers}
    mob = grids["Mobiles per cell"]
    print(f"mobile frames {sum(c['frames'] for c in mob.values()):,} in "
          f"{len(mob):,} occupied {cell_mi:g}-mile cells")
    top = sorted(mob.values(), key=lambda c: -len(c["calls"]))[:8]
    print("busiest cells by distinct mobiles:")
    for c in top:
        la = (c["bounds"][0][0] + c["bounds"][1][0]) / 2
        lo = (c["bounds"][0][1] + c["bounds"][1][1]) / 2
        print(f"   {len(c['calls']):3d} mobiles  {c['frames']:5d} frames  "
              f"{la:.3f},{lo:.3f}  {miles(la, lo):5.1f} mi out")

    # One hue, light to dark. The bins are fixed across every layer so the
    # mobile grid and the fixed grid are read against the same scale.
    # A value-proportional colorbar crushes bins 1, 2 and 3 into the left
    # eighth of its width and the labels collide, so the legend is drawn as
    # equal-width swatches instead.
    EDGES = [1, 2, 3, 5, 8, 13]
    NAMES = ["1", "2", "3-4", "5-7", "8-12", "13+"]

    def band(n):
        i = 0
        while i + 1 < len(EDGES) and n >= EDGES[i + 1]:
            i += 1
        return RAMP[i]

    m = folium.Map(location=GW, zoom_start=9, tiles="OpenStreetMap",
                   control_scale=True)
    for r in (20, 40, 80, 150):
        folium.Circle(GW, radius=r * 1609.344, color="#444444", weight=1.3,
                      dash_array="6 7", fill=False, interactive=False,
                      opacity=0.55, tooltip=f"{r} mi from KD3CCO-10").add_to(m)

    for name, calls, show in layers:
        grid = grids[name]
        feats = []
        for c in grid.values():
            (s_lat, w_lon), (n_lat, e_lon) = c["bounds"]
            names = sorted(c["calls"])
            feats.append({
                "type": "Feature",
                "properties": {
                    "n": len(names),
                    "Stations": len(names),
                    "Frames": c["frames"],
                    "Copied directly": c["direct"],
                    "Who": ", ".join(names[:12])
                           + (f" and {len(names) - 12} more" if len(names) > 12 else ""),
                },
                "geometry": {"type": "Polygon", "coordinates": [[
                    [w_lon, s_lat], [e_lon, s_lat], [e_lon, n_lat],
                    [w_lon, n_lat], [w_lon, s_lat]]]},
            })
        folium.GeoJson(
            {"type": "FeatureCollection", "features": feats},
            name=f"{name}  ({len(calls)} stations)", show=show,
            style_function=lambda f: {
                "fillColor": band(f["properties"]["n"]), "color": "#ffffff",
                "weight": 0.3, "fillOpacity": 0.78},
            highlight_function=lambda f: {"weight": 2, "color": INK},
            tooltip=folium.GeoJsonTooltip(
                fields=["Stations", "Frames", "Copied directly", "Who"],
                sticky=True),
        ).add_to(m)

    folium.Marker(GW, tooltip="KD3CCO-10 gateway",
                  icon=folium.Icon(color="black", icon="tower-broadcast",
                                   prefix="fa")).add_to(m)
    m.get_root().html.add_child(folium.Element(
        f'<div style="position:fixed;bottom:22px;left:12px;z-index:9999;'
        f'background:{SURFACE};border:1px solid {RING};border-radius:5px;'
        f'padding:8px 10px;font:12px sans-serif;color:{INK};'
        f'box-shadow:0 1px 4px rgba(0,0,0,.18)">'
        f'<b>distinct stations per {cell_mi:g}-mile cell</b><br>'
        f'<div style="margin-top:4px;line-height:0">'
        + "".join(f'<div style="display:inline-block;width:34px;height:12px;'
                  f'background:{c}"></div>' for c in RAMP)
        + '</div><div style="line-height:1.4">'
        + "".join(f'<span style="display:inline-block;width:34px;'
                  f'text-align:center;font-size:10px">{t}</span>'
                  for t in NAMES)
        + '</div>'
        f'<div style="margin-top:5px;font-size:11px;color:{INK2}">'
        f'Mobile = a vehicle or person symbol, or a station seen to move '
        f'more than {MOVED_MI:g} mile.</div></div>'))
    folium.LayerControl(collapsed=False).add_to(m)
    out = os.path.join(outdir, "mobiles.html")
    m.save(out)
    print(f"wrote {out}")

    tsv = os.path.join(outdir, "mobiles.tsv")
    with open(tsv, "w") as fh:
        fh.write("call\tclass\tspan_mi\tby_symbol\tby_movement\tsymbols\n")
        for c in sorted(span, key=lambda c: -span[c]):
            kind = ("mobile" if c in mobile else
                    "air/sea" if c in airsea else "fixed")
            fh.write(f"{c}\t{kind}\t{span[c]:.2f}\t{int(c in by_sym)}\t"
                     f"{int(c in by_span)}\t{'; '.join(sorted(syms.get(c, [])))}\n")
    print(f"wrote {tsv}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("logs", nargs="+")
    a.add_argument("-o", "--outdir", default=".")
    d = sub.add_parser("draw")
    d.add_argument("dir")
    mo = sub.add_parser("mobiles")
    mo.add_argument("dir")
    mo.add_argument("--cell", type=float, default=CELL_MI,
                    help="cell size in miles (default %(default)s)")
    args = ap.parse_args()
    if args.cmd == "analyze":
        analyze(args.logs, args.outdir)
    elif args.cmd == "mobiles":
        mobiles(args.dir, args.cell)
    else:
        draw(args.dir)


if __name__ == "__main__":
    main()
