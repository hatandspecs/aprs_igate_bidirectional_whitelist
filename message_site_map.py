#!/usr/bin/env python3
"""Map the 2026-11-21 SMSGTE test sites: a static figure and a folium page.

The figure's job is to show that the downlink outcome does NOT sort by
distance. Alpha and Garage are the same distance from the gateway, 4.0 miles
within the geocoding error, and one received its message while the other did
not. The variable is the hour: the handheld was worked direct for the first
four minutes and was reachable only through W3YA-1 afterwards.

Color: blue #2a78d6 against critical red #d03b3b. Deliberately NOT
good-green against critical-red, which measures a deuteranope-simulated OKLab
dE of 4.3, under the 8 floor; blue/red measures 26.5 and both clear 3:1 on the
blog's #fdfdfd surface. Outcome is also carried by marker shape and by the
label text, so color never carries it alone.

Positions: Luna, Alpha, Third and Garage are geocoded street addresses.
Sheetz is the radio's own position beacon at 09:46:55, which is the better
source where it exists. The Luna geocode falls 230 m from the 09:48:46
beacon, which is what validates the other three.

    conda run -n repeater_monitoring python message_site_map.py OUTDIR
"""
import math
import os
import sys

GW = (40.7708, -77.7917)                      # KD3CCO-10, FN10CS, igate.conf
W3YA = (40 + 43.04 / 60, -(77 + 53.53 / 60))  # W3YA-1, from its own beacon

# name, lat, lon, downlink keyed, in the radio's list after, beacon heard here,
# where the position came from, what the operator was doing
SITES = [
    ("Sheetz", 40.784667, -77.829167, "09:47", True, True,
     "radio's own beacon, 09:46:55", "moving car"),
    ("Luna", 40.7848025, -77.8473916, "09:49", True, True,
     "1229 S Atherton St", "moving car"),
    ("Alpha", 40.7904126, -77.8639476, "09:51", True, False,
     "400 W Beaver Ave", "moving car"),
    ("Third", 40.7921821, -77.8585038, "10:21", False, False,
     "300 S Allen St", "inside the building"),
    ("Garage", 40.7930785, -77.8619759, "10:49", False, False,
     "135 S Fraser St", "3rd floor, inside"),
]

YES, NO, INK, INK2, MUTED, RING = (
    "#2a78d6", "#d03b3b", "#0b0b0b", "#52514e", "#8a8a84", "#dcdbd6")
MI_LAT = 69.09
MI_LON = MI_LAT * math.cos(math.radians(GW[0]))


def en(lat, lon):
    """Miles east and north of the gateway. Flat is exact enough at 6 miles."""
    return (lon - GW[1]) * MI_LON, (lat - GW[0]) * MI_LAT


def miles(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(h)) * 0.621371


def static(path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.7, 4.75), dpi=110)
    fig.patch.set_facecolor("#fdfdfd")
    ax.set_facecolor("#fdfdfd")

    # Ring labels march west along the y=0 radial, which is the one band of
    # the frame with nothing in it: the sites all sit above y=0.9, the legend
    # below y=-0.6, and the W3YA-1 arrow stops short of x=-1.
    for r in (1, 2, 3, 4):
        ax.add_patch(plt.Circle((0, 0), r, fill=False, ec=RING, lw=1, zorder=1))
        ax.text(-r, 0.07, f"{r} mi", color=MUTED, fontsize=8,
                ha="center", va="bottom", zorder=2)

    wx, wy = en(*W3YA)
    L = math.hypot(wx, wy)
    ax.annotate("", xy=(wx / L * 1.15, wy / L * 1.15), xytext=(0, 0),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.3,
                                linestyle=(0, (5, 4))), zorder=2)
    ax.text(wx / L * 1.25, wy / L * 1.25 - 0.06,
            f"W3YA-1 digipeater, {miles(GW, W3YA):.1f} mi",
            color=MUTED, fontsize=9, ha="center", va="top", zorder=3)

    ax.plot(0, 0, marker="D", ms=9, color=INK, zorder=6)
    ax.text(0, 0.13, "KD3CCO-10 gateway", fontsize=9.5, fontweight="bold",
            ha="center", va="bottom", color=INK, zorder=6)

    # Third, Garage and Alpha sit within 0.2 mi of each other downtown, so
    # their labels are pushed out on leaders rather than placed in situ.
    offs = {"Sheetz": (14, -16, "left"), "Luna": (0, -34, "center"),
            "Alpha": (-6, -40, "center"), "Third": (92, -18, "left"),
            "Garage": (92, 18, "left")}
    for name, lat, lon, when, ok, bea, _src, _ctx in SITES:
        x, y = en(lat, lon)
        col = YES if ok else NO
        if ok:
            ax.plot(x, y, marker="o", ms=9, color=col, mec="#fdfdfd",
                    mew=1.6, zorder=6)
        else:
            ax.plot(x, y, marker="o", ms=10, mfc="#fdfdfd", mec=col, mew=2,
                    zorder=6)
            ax.plot(x, y, marker="x", ms=6, color=col, mew=2, zorder=7)
        if bea:
            ax.plot(x, y, marker="o", ms=17, mfc="none", mec=MUTED, mew=1,
                    zorder=5)
        dx, dy, ha = offs[name]
        lead = dict(arrowstyle="-", color=MUTED, lw=0.9,
                    shrinkA=0, shrinkB=9) if abs(dx) + abs(dy) > 56 else None
        ax.annotate(f"{name}\n{miles(GW, (lat, lon)):.1f} mi, {when}",
                    (x, y), textcoords="offset points", xytext=(dx, dy),
                    ha=ha, fontsize=9.5, color=INK, zorder=8,
                    arrowprops=lead,
                    bbox=dict(boxstyle="round,pad=0.18", fc="#fdfdfd",
                              ec="none", alpha=0.85))

    ax.text(-1.00, 2.15,
            "Alpha and Garage sit the same\n"
            "distance out, 4.0 miles. Alpha's\n"
            "message arrived at 09:51;\n"
            "Garage's, an hour later, did not.",
            fontsize=9.5, color=INK, va="top", zorder=8)

    h = [plt.Line2D([], [], ls="", marker="o", ms=9, color=YES,
                    mec="#fdfdfd", mew=1.6, label="In the radio's message list"),
         plt.Line2D([], [], ls="", marker="x", ms=8, color=NO, mew=2,
                    label="Not in the radio's message list"),
         plt.Line2D([], [], ls="", marker="o", ms=13, mfc="none", mec=MUTED,
                    label="Position beacon heard here"),
         plt.Line2D([], [], ls="", marker="D", ms=8, color=INK,
                    label="Gateway")]
    lg = ax.legend(handles=h, loc="lower left", fontsize=9, frameon=True,
                   facecolor="#fdfdfd", edgecolor=RING, borderpad=0.7,
                   labelspacing=0.65, handletextpad=0.8)
    lg.set_zorder(9)

    ax.set_xlim(-4.70, 0.95)
    ax.set_ylim(-1.30, 2.30)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.text(0.013, 0.975, "Where the gated messages reached the handheld",
             fontsize=14, fontweight="600", color=INK, ha="left", va="top")
    fig.text(0.013, 0.912,
             "SMSGTE downlinks, 2026-11-21. Rings are miles from the gateway.",
             fontsize=9.5, color=INK2, ha="left", va="top")
    fig.text(0.013, 0.018,
             "Luna, Alpha, Third and Garage geocoded from street addresses; "
             "Sheetz from the radio's own beacon at 09:46:55.\nDelivery from "
             "the radio's message list, photographed after the session.",
             fontsize=7.6, color=MUTED, ha="left", va="bottom")
    fig.subplots_adjust(left=0.01, right=0.99, top=0.875, bottom=0.115)
    fig.savefig(path, facecolor="#fdfdfd")
    print(f"wrote {path}")


def interactive(path):
    import folium

    m = folium.Map(location=[40.783, -77.828], zoom_start=13,
                   tiles="CartoDB positron", control_scale=True)
    for r in (1, 2, 3, 4):
        folium.Circle(GW, radius=r * 1609.344, color=RING, weight=1,
                      fill=False, interactive=False).add_to(m)
    folium.PolyLine([GW, W3YA], color=MUTED, weight=2, dash_array="6 5",
                    tooltip=f"W3YA-1, {miles(GW, W3YA):.1f} mi").add_to(m)
    folium.Marker(GW, tooltip="KD3CCO-10 gateway",
                  icon=folium.Icon(color="black", icon="tower-broadcast",
                                   prefix="fa")).add_to(m)
    folium.Marker(W3YA, tooltip="W3YA-1, Pine Grove Mountain",
                  icon=folium.Icon(color="gray", icon="tower-cell",
                                   prefix="fa")).add_to(m)
    for name, lat, lon, when, ok, bea, src, ctx in SITES:
        col = YES if ok else NO
        body = (f"<b>{name}</b><br>{miles(GW, (lat, lon)):.1f} mi from the "
                f"gateway<br>downlink keyed {when} EST<br>"
                f"<b>{'in' if ok else 'not in'}</b> the radio's message list"
                f"<br>position beacon: {'heard' if bea else 'none heard'}"
                f"<br><i>{ctx}</i><br><small>{src}</small>")
        if bea:
            folium.CircleMarker([lat, lon], radius=14, color=MUTED, weight=1,
                                fill=False, interactive=False).add_to(m)
        folium.CircleMarker([lat, lon], radius=8, color="#fdfdfd", weight=2,
                            fill=True, fill_color=col, fill_opacity=1,
                            tooltip=name,
                            popup=folium.Popup(body, max_width=260)).add_to(m)
    m.save(path)
    print(f"wrote {path}")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    static(os.path.join(out, "message-sites-2026-11-21.png"))
    interactive(os.path.join(out, "message_sites_2026-11-21.html"))
