#!/usr/bin/env python3
"""Where this station copies other stations directly, as a map of cells.

The question this answers is "how much better does the antenna hear", and the
honest denominator is not another station. aprs.fi's heard-directly map cannot
answer it: APRS-IS keeps the FIRST copy of a packet and drops the rest, so a
neighbouring iGate's map moves DOWN when this one improves, and any ratio
against it overstates the gain by an amount the data does not recover.

Every packet this station logs arrives one of two ways: copied directly off
the air, or copied as a digipeated repeat. A repeat is proof that something
transmitted in that cell and this receiver had the chance. So per cell:

    direct share = packets copied directly / packets that originated there

That normalises away how much any cell happened to transmit, uses only this
station's own log, and is untouched by aprs.fi, by duplicate suppression and
by what any other station does. Difference it cell by cell between two
captures and the result is a map of where a change to this station helped.

The denominator does lean slightly optimistic: repeats arrive via digipeaters
whose path to this station also improves with height. Those are 49 W from
640 ft and near-certain decodes either way, so the lean is small -- but it is
a lean, not a neutrality.

Two capture formats are read, and WHICH ONE MATTERS. The console log is the
only format that exists for captures made before LOG_DIR was turned on, so a
before/after comparison has to use it on both sides: measuring one month by
console log and the other by dated CSV puts the method difference on top of
the effect being measured, and nothing separates them afterwards. Use the CSV
as a cross-check, not as one half of a comparison.

    ./heard_spatial.py archive-2026-10/frames-15ft-2026-10-30.log.gz --label oct
    ./heard_spatial.py archive/frames/frames-2026-11-*.log.gz --label nov
    ./heard_spatial.py --compare cells-oct.tsv cells-nov.tsv
    ./heard_spatial.py logs/2026-11-24.log --label check     # dated CSV
"""
import argparse
import csv
import gzip
import io
import math
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

# A received RF frame. `[0L]` is this station TRANSMITTING and must not be
# counted as a reception -- it is the gateway's own beacons and the traffic it
# gates down from APRS-IS, and including it put this station's own position
# into its own coverage map on the first attempt.
RX_FRAME = re.compile(r'^\[0\.[0-9]+\]\s+([A-Z0-9][A-Z0-9-]*)>([^:]*):(.*)$')

# Direwolf prints its own decoded position under the frame. Reading that rather
# than hand-parsing the payload gets MIC-E and the compressed formats for free.
DECODED_POS = re.compile(r'^([NS])\s+(\d+)\s+([\d.]+),\s*([EW])\s+(\d+)\s+([\d.]+)')

# APRS data types that carry the SENDER'S OWN position. Objects (`;`), items
# (`)`) and third-party payloads (`}`) carry somebody else's coordinates, and
# counting those placed a repeater object 98 miles away into this station's
# receive map. Status, messages and telemetry carry none at all.
SELF_POSITION_DTI = set("!=/@`'")

# How far Direwolf's decoded position line can sit below its frame.
POS_LOOKAHEAD = 6

CELL_KM = 2.005          # aprs.fi's heard-map square, to three figures
KM_PER_MILE = 1.609344


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
def grid_square_center(locator):
    """Centre of a 6-character Maidenhead locator, in degrees.

    The station beacons a grid square rather than a position, so the centre of
    that square is where aprs.fi draws it and the only position this file can
    know. It is also the right reference: a comparison between two captures
    uses the same origin on both sides, so any offset from the real antenna
    cancels out of the difference.
    """
    loc = locator.strip()
    if len(loc) < 6:
        raise ValueError(f"need a 6-character locator, got {locator!r}")
    lon = (ord(loc[0].upper()) - 65) * 20 - 180
    lat = (ord(loc[1].upper()) - 65) * 10 - 90
    lon += int(loc[2]) * 2
    lat += int(loc[3]) * 1
    lon += (ord(loc[4].lower()) - 97) * (2 / 24) + (2 / 24) / 2
    lat += (ord(loc[5].lower()) - 97) * (1 / 24) + (1 / 24) / 2
    return lat, lon


def metres_per_degree(lat):
    """Local scale of the ellipsoid. Good to centimetres over this range."""
    p = math.radians(lat)
    mlat = 111132.92 - 559.82 * math.cos(2 * p) + 1.175 * math.cos(4 * p)
    mlon = 111412.84 * math.cos(p) - 93.5 * math.cos(3 * p) + 0.118 * math.cos(5 * p)
    return mlat, mlon


class Origin:
    """Station position and the flat-earth projection hung off it."""

    def __init__(self, lat, lon, cell_km=CELL_KM):
        self.lat, self.lon, self.cell_km = lat, lon, cell_km
        self.mlat, self.mlon = metres_per_degree(lat)

    def offset_km(self, lat, lon):
        """East, north in km from the station."""
        return ((lon - self.lon) * self.mlon / 1000.0,
                (lat - self.lat) * self.mlat / 1000.0)

    def cell(self, lat, lon):
        e, n = self.offset_km(lat, lon)
        return (math.floor(e / self.cell_km), math.floor(n / self.cell_km))

    def distance_miles(self, lat, lon):
        e, n = self.offset_km(lat, lon)
        return math.hypot(e, n) / KM_PER_MILE


def cell_center_miles(ix, iy, cell_km=CELL_KM):
    half = cell_km / 2.0
    return ((ix * cell_km + half) / KM_PER_MILE, (iy * cell_km + half) / KM_PER_MILE)


# --------------------------------------------------------------------------
# readers -- both yield (source, direct, lat, lon)
# --------------------------------------------------------------------------
def _open(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", errors="replace")
    return open(path, "rt", errors="replace")


def read_console(fh):
    """Direwolf console log: the format every capture has, old ones included."""
    pending, look = None, 0
    for line in fh:
        m = RX_FRAME.match(line)
        if m:
            source, path, payload = m.groups()
            if payload[:1] not in SELF_POSITION_DTI:
                pending = None
                continue
            # A USED digipeater anywhere in the path means this copy came
            # through somebody else. `*` marks the last one used.
            pending = (source, "*" not in path)
            look = POS_LOOKAHEAD
            continue
        if pending and look > 0:
            look -= 1
            p = DECODED_POS.match(line.strip())
            if not p:
                continue
            ns, dd, mm, ew, DD, MM = p.groups()
            lat = (int(dd) + float(mm) / 60) * (1 if ns == "N" else -1)
            lon = (int(DD) + float(MM) / 60) * (1 if ew == "E" else -1)
            source, direct = pending
            pending = None
            yield source, direct, lat, lon


def read_dated_csv(fh):
    """Direwolf's LOG_DIR CSV. Authoritative on `heard`, but only exists for
    captures made after the dated log was switched on."""
    for row in csv.DictReader(fh):
        src = (row.get("source") or "").strip()
        heard = (row.get("heard") or "").strip()
        lat, lon = (row.get("latitude") or "").strip(), (row.get("longitude") or "").strip()
        if not src or not lat or not lon:
            continue
        # Direwolf appends `?` when it is not certain which station it copied.
        # Comparing the raw strings counts every uncertain direct copy as
        # digipeated, understating exactly the number being measured.
        src_c = src[:-1] if src.endswith("?") else src
        heard_c = heard[:-1] if heard.endswith("?") else heard
        try:
            flat, flon = float(lat), float(lon)
        except ValueError:
            continue
        yield src_c, (heard_c == src_c or heard_c == ""), flat, flon


def sniff(path):
    with _open(path) as fh:
        head = fh.read(4096)
    return read_dated_csv if "chan,utime" in head or head.startswith("chan,") else read_console


# --------------------------------------------------------------------------
# accumulation
# --------------------------------------------------------------------------
class Coverage:
    def __init__(self, origin, exclude=()):
        self.origin = origin
        self.exclude = set(exclude)
        self.cells = defaultdict(lambda: [0, 0])    # (ix, iy) -> [direct, total]
        self.stations = defaultdict(lambda: {"direct": 0, "total": 0, "dist": []})
        self.placed = 0
        self.excluded = 0

    def add(self, source, direct, lat, lon):
        # This station's OWN beacons come back off the air after a digipeater
        # repeats them. They are receptions, but they are not evidence about
        # hearing anyone else, and they land in the cell holding this station's
        # own beaconed position: 306 of that cell's 311 packets in the October
        # capture, reading the home cell as 1.3% direct instead of 80.0%.
        # Worse for the purpose, how many of them come back is itself a
        # function of antenna height, so leaving them in puts the effect being
        # measured into the denominator that is supposed to control for it.
        if source in self.exclude:
            self.excluded += 1
            return
        e, n = self.origin.offset_km(lat, lon)
        if abs(e) > 400 or abs(n) > 400:            # a busted position, not a path
            return
        self.placed += 1
        c = self.cells[self.origin.cell(lat, lon)]
        c[1] += 1
        s = self.stations[source]
        s["total"] += 1
        if direct:
            c[0] += 1
            s["direct"] += 1
            # Distance belongs to the DIRECT packets only. A mobile heard
            # directly nearby and digipeated from far away is not a distant
            # reception, and taking its last known position said 126 miles.
            s["dist"].append(math.hypot(e, n) / KM_PER_MILE)

    def solid_stations(self, min_direct=5, min_share=0.05):
        """Stations with a real direct path, and the ones that only look like it.

        One frame in 1742 is a lucky decode of a corrupt path, not a 98-mile
        reception. This project has already published one retraction built on
        a handful of frames; the screen is here so it does not publish another.
        """
        solid, artefact = {}, {}
        for call, s in self.stations.items():
            if not s["direct"]:
                continue
            ok = s["direct"] >= min_direct and s["direct"] / s["total"] >= min_share
            (solid if ok else artefact)[call] = s
        return solid, artefact


def conf_get(conf_path, key):
    """One setting, last definition wins, local overrides base."""
    out = ""
    for f in (conf_path, os.path.join(HERE, "igate.local.conf")):
        if not os.path.isfile(f):
            continue
        with open(f, errors="replace") as fh:
            for line in fh:
                m = re.match(rf'^\s*{key}\s*=\s*(.*)$', line)
                if m:
                    v = re.sub(r'\s*#.*$', '', m.group(1)).strip()
                    if v:
                        out = v
    return out


def load_config(conf_path):
    """BEACON_LAT/LON if set, else the centre of BEACON_GRID."""
    def get(key):
        return conf_get(conf_path, key)
    lat, lon = get("BEACON_LAT"), get("BEACON_LON")
    if lat and lon:
        return float(lat), float(lon)
    grid = get("BEACON_GRID")
    if not grid:
        raise SystemExit("no BEACON_LAT/BEACON_LON and no BEACON_GRID in the config")
    return grid_square_center(grid)


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------
def write_cells(cov, path, label, sources):
    with open(path, "w") as fh:
        fh.write("# heard_spatial.py -- packets per cell, and how many were copied directly.\n")
        fh.write("# Compare two of these with --compare. The origin and cell size MUST match.\n")
        fh.write(f"#label\t{label}\n")
        fh.write(f"#origin_lat\t{cov.origin.lat:.6f}\n")
        fh.write(f"#origin_lon\t{cov.origin.lon:.6f}\n")
        fh.write(f"#cell_km\t{cov.origin.cell_km}\n")
        for s in sources:
            fh.write(f"#source\t{os.path.basename(s)}\n")
        fh.write("ix\tiy\tdirect\ttotal\n")
        for (ix, iy), (d, t) in sorted(cov.cells.items()):
            fh.write(f"{ix}\t{iy}\t{d}\t{t}\n")


def read_cells(path):
    meta, cells = {}, {}
    with open(path) as fh:
        for line in fh:
            if line.startswith("#"):
                parts = line[1:].rstrip("\n").split("\t")
                if len(parts) == 2:
                    meta.setdefault(parts[0], parts[1])
                continue
            if line.startswith("ix\t"):
                continue
            ix, iy, d, t = line.split()
            cells[(int(ix), int(iy))] = [int(d), int(t)]
    return meta, cells


def summarise(cov, min_total=5):
    out = []
    solid, artefact = cov.solid_stations()
    out.append(f"own-position packets placed : {cov.placed:,}")
    if cov.excluded:
        out.append(f"  own beacons heard back     : {cov.excluded:,} excluded "
                   f"({', '.join(sorted(cov.exclude))})")
    tot_d = sum(c[0] for c in cov.cells.values())
    tot_a = sum(c[1] for c in cov.cells.values())
    share = 100 * tot_d / tot_a if tot_a else 0.0
    out.append(f"  copied directly           : {tot_d:,}  ({share:.1f}%)")
    out.append(f"cells with traffic          : {len(cov.cells)}")
    out.append(f"  with a direct copy        : {sum(1 for c in cov.cells.values() if c[0])}")
    out.append(f"stations copied directly    : {len(solid)}"
               f"   ({len(artefact)} screened out as decode artefacts)")
    out.append("")
    out.append(f"{'ring':>9s} {'cells':>6s} {'direct':>7s} {'':>5s} {'packet share':>13s}")
    for lo, hi in ((0, 5), (5, 10), (10, 15), (15, 25), (25, 40), (40, 1e9)):
        sel = [(d, t) for (ix, iy), (d, t) in cov.cells.items()
               if t >= min_total and lo <= math.hypot(*cell_center_miles(ix, iy, cov.origin.cell_km)) < hi]
        if not sel:
            continue
        nd = sum(1 for d, _ in sel if d)
        ps = 100 * sum(d for d, _ in sel) / sum(t for _, t in sel)
        rng = f"{lo}-{hi:.0f}" if hi < 1e9 else f"{lo}+"
        out.append(f"{rng:>9s} {len(sel):6d} {nd:7d} {100*nd/len(sel):4.0f}% {ps:12.1f}%")
    return "\n".join(out)


def compare(a_path, b_path, min_total=5):
    ma, ca = read_cells(a_path)
    mb, cb = read_cells(b_path)
    for key in ("origin_lat", "origin_lon", "cell_km"):
        if ma.get(key) != mb.get(key):
            raise SystemExit(f"refusing to compare: {key} differs "
                             f"({ma.get(key)} vs {mb.get(key)}). Same origin or nothing.")
    la, lb = ma.get("label", a_path), mb.get("label", b_path)
    cell_km = float(ma.get("cell_km", CELL_KM))
    lines = [f"{la} -> {lb}, per cell, cells with {min_total}+ packets in both", ""]
    both = [k for k in set(ca) & set(cb) if ca[k][1] >= min_total and cb[k][1] >= min_total]
    if not both:
        lines.append("  no cell carries enough traffic in both captures.")
        return "\n".join(lines)
    lines.append(f"{'ring':>9s} {'cells':>6s} {la[:8]:>9s} {lb[:8]:>9s} {'change':>9s}")
    for lo, hi in ((0, 5), (5, 10), (10, 15), (15, 25), (25, 1e9)):
        sel = [k for k in both
               if lo <= math.hypot(*cell_center_miles(k[0], k[1], cell_km)) < hi]
        if not sel:
            continue
        sa = sum(ca[k][0] for k in sel) / sum(ca[k][1] for k in sel)
        sb = sum(cb[k][0] for k in sel) / sum(cb[k][1] for k in sel)
        rng = f"{lo}-{hi:.0f}" if hi < 1e9 else f"{lo}+"
        lines.append(f"{rng:>9s} {len(sel):6d} {100*sa:8.1f}% {100*sb:8.1f}% {100*(sb-sa):+8.1f}%")
    gained = [k for k in set(cb) - set(ca) if cb[k][0] > 0]
    lost = [k for k in set(ca) - set(cb) if ca[k][0] > 0]
    lines.append("")
    lines.append(f"cells with a direct copy only in {lb}: {len(gained)}")
    lines.append(f"cells with a direct copy only in {la}: {len(lost)}")
    lines.append("")
    lines.append("A cell present in one capture and absent from the other is a change in")
    lines.append("who transmitted, not in what this station can hear. The ring table above")
    lines.append("is the comparable part: same cells, both captures, share of the same traffic.")
    return "\n".join(lines)


def draw(cov, path, extent=30.0, min_total=5):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.colors import LogNorm, Normalize

    cell_mi = cov.origin.cell_km / KM_PER_MILE
    fig, axes = plt.subplots(1, 2, figsize=(14.5, 7.4))
    for ax in axes:
        for r in (5, 10, 15, 20, 25):
            ax.add_patch(plt.Circle((0, 0), r, fill=False, ec="0.75", lw=.8, ls=":"))
            ax.text(r * .707, r * .707, f"{r} mi", color="0.55", fontsize=8)
        ax.plot(0, 0, marker="*", ms=17, color="black", zorder=6)
        ax.set_xlim(-extent, extent); ax.set_ylim(-extent, extent)
        ax.set_aspect("equal")
        ax.set_xlabel("miles east"); ax.set_ylabel("miles north")

    for (ix, iy), (d, t) in cov.cells.items():
        x, y = ix * cell_mi, iy * cell_mi
        if abs(x) > extent or abs(y) > extent or d:
            continue
        for ax in axes:
            ax.add_patch(Rectangle((x, y), cell_mi, cell_mi, fc="0.90", ec="0.80", lw=.3, zorder=2))

    dmax = max((d for d, _ in cov.cells.values()), default=1)
    norm, cmap = LogNorm(1, max(dmax, 2)), plt.get_cmap("plasma")
    cmap2 = plt.get_cmap("viridis")
    shown = 0
    for (ix, iy), (d, t) in cov.cells.items():
        x, y = ix * cell_mi, iy * cell_mi
        if abs(x) > extent or abs(y) > extent:
            continue
        if d:
            axes[0].add_patch(Rectangle((x, y), cell_mi, cell_mi, fc=cmap(norm(d)), ec="none", zorder=3))
        if t >= min_total:
            axes[1].add_patch(Rectangle((x, y), cell_mi, cell_mi, fc=cmap2(d / t), ec="none", zorder=3))
            shown += 1
    axes[0].set_title("Packets copied directly, per cell\n"
                      "(gray = traffic from that cell, never copied directly)", fontsize=11)
    axes[1].set_title(f"Direct share: copied directly / originated there\n"
                      f"(cells with {min_total}+ packets, n={shown})", fontsize=11)
    fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), ax=axes[0], shrink=.8,
                 label="direct packets")
    fig.colorbar(plt.cm.ScalarMappable(norm=Normalize(0, 1), cmap=cmap2), ax=axes[1], shrink=.8,
                 label="share copied directly")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", help="console logs (.log/.gz) or dated CSVs")
    ap.add_argument("--conf", default=os.path.join(HERE, "igate.conf"))
    ap.add_argument("--label", default="capture")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--lat", type=float), ap.add_argument("--lon", type=float)
    ap.add_argument("--cell-km", type=float, default=CELL_KM)
    ap.add_argument("--min-total", type=int, default=5,
                    help="packets a cell needs before its share is reported")
    ap.add_argument("--include-own", action="store_true",
                    help="keep this station's own beacons heard back off the air "
                         "(they are excluded by default, and should be)")
    ap.add_argument("--map", metavar="PNG", nargs="?", const="auto")
    ap.add_argument("--compare", nargs=2, metavar=("A.tsv", "B.tsv"))
    args = ap.parse_args()

    if args.compare:
        print(compare(*args.compare, min_total=args.min_total))
        return 0
    if not args.inputs:
        ap.error("give at least one capture, or --compare two cell files")

    if args.lat is not None and args.lon is not None:
        lat, lon = args.lat, args.lon
    else:
        lat, lon = load_config(args.conf)
    mycall = conf_get(args.conf, "MYCALL")
    exclude = () if args.include_own or not mycall else (mycall,)
    cov = Coverage(Origin(lat, lon, args.cell_km), exclude=exclude)
    print(f"origin {lat:.6f}, {lon:.6f}   cell {args.cell_km} km"
          + (f"   excluding own traffic from {mycall}" if exclude else ""))
    for path in args.inputs:
        reader = sniff(path)
        with _open(path) as fh:
            n = 0
            for rec in reader(fh):
                cov.add(*rec)
                n += 1
        print(f"  {os.path.basename(path)}: {n:,} positioned packets ({reader.__name__})")
    print()
    print(summarise(cov, min_total=args.min_total))

    os.makedirs(args.out_dir, exist_ok=True)
    out = os.path.join(args.out_dir, f"cells-{args.label}.tsv")
    write_cells(cov, out, args.label, args.inputs)
    print(f"\nwrote {out}")
    if args.map:
        png = args.map if args.map != "auto" else os.path.join(args.out_dir, f"heard-{args.label}.png")
        try:
            print(f"wrote {draw(cov, png, min_total=args.min_total)}")
        except ImportError:
            print("matplotlib not installed; skipping the map (the TSV is the data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
