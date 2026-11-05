# OPERATIONS.md — station log for KD3CCO-10 (the pi-gate)

What happened to the physical station, and when. One entry per event, newest
first.

This file exists because the measurements in this repository are only
interpretable against the configuration that produced them, and the
configuration changes by hand. A fifteen-minute hole in the packet log is an
outage, a fault, or somebody carrying the mast across the yard, and nothing in
the data distinguishes them. This file is where that distinction is written
down.

## What belongs here, and what belongs in AGENTS.md

| This file | AGENTS.md |
|---|---|
| Events: what was done to the station, at what time | Findings: what is now known to be true |
| Outages, moves, antenna and mast changes, hardware swaps | Gotchas, the measured baselines, what to do next |
| Observations of state, dated, even when nothing was learned | Conclusions drawn from them |

An event that produced a finding goes in both: the event here with its
timestamp, the finding in AGENTS.md, each pointing at the other. When the two
disagree, this file is the authority on *when* and AGENTS.md on *what it
means*.

## Conventions

- **UTC is the reference.** Direwolf's dated packet log and
  `igate-archive.sh`'s summary are per UTC day, so any window that has to line
  up with the data is written in UTC. Local time is given alongside it for
  anything observed by a human, with the zone named — EDT and EST both occur
  in this log and the difference has already been a trap once (see 2026-11-01).
- **Record when it was noticed, separately from when it happened**, and do not
  collapse the two. Most physical faults here are found by looking, not by an
  alarm, so the discovery time is an upper bound and nothing more.
- **An entry with no measurement is still worth writing.** "Checked, normal" on
  a dated line is what makes the next anomaly datable.
- **Standing practice when something physical fails: radio power off, Pi left
  running.** This is deliberate and it is worth knowing when reading the data,
  because it puts a **second, sharper marker** in the log. The fault itself is a
  partial degradation; the radio going off is total. A dated log that goes to
  zero decodes is the moment of the intervention, not the moment of the fault,
  and the fault is somewhere in the degraded stretch before it.
- **The dated packet log is kept for `ARCHIVE_KEEP_DAYS`, which is 400.**
  Nothing since 2026-10-30 has been pruned. It is on the card at
  `/opt/aprs-igate/logs/YYYY-MM-DD.log`, one CSV per UTC day, and unlike the
  console log it carries a `utime` per packet -- so it is the only source that
  can time anything.
- **The live packet log holds about eight days** and `run/` is tmpfs, so
  anything not written down here or archived off the Pi is gone at the next
  reboot. See AGENTS.md, "The log holds about eight days, not the uptime".

## Data-quality windows at a glance

What to exclude, and why, before comparing anything to anything.

| Window | State | Why it matters |
|---|---|---|
| through 2026-10-03 | Station and antenna moved repeatedly | Not comparable to anything later |
| 2026-10-04 16:25 EDT – 2026-10-12 | 15 ft mast, unbroken, 8 d 3 h | The original baseline |
| 2026-10-13 22:00 – 2026-10-18 15:56 EDT | `BEACON_VIA` two-hop experiment | Inflates TX-side reach; biases aprs.fi's October page **against** November |
| through 2026-10-29 | No dated packet log | `rx_*` columns read 0 for these days; absence of data, not absence of traffic |
| 2026-10-22 – 2026-10-30 | 15 ft mast, clean | The measured baseline. Archived to `archive-2026-10/` |
| 2026-10-30 | Mast 15 ft → ~28 ft mid-day | Split day; belongs to neither baseline |
| 2026-10-31 | First full UTC day at ~28 ft | 48 beacons, no outage |
| 2026-11-01 14:30–14:45 UTC | Radio off, mast re-erected | 15-minute outage. Height between 10-30 and this is **unverified** |
| 2026-11-05 19:46 UTC | **Mast down**, base stake failed | Antenna at ground level. rx -89%, transmit reach zero |
| 2026-11-05 22:12 UTC onward | Radio powered off, station off air | Open at the time of writing. **November is not a clean month** |

---

## The log

### 2026-11-05 — the mast came down; the base stake failed

**The mast came down at about 14:46 EST (19:46 UTC) on 2026-11-05.** The
light-duty wooden ground stake at the base failed -- broken or bent. The mast
itself and all three guy lines are undamaged. Found some hours later; radio
power switched off at **17:12 EST (22:12 UTC)**, Pi left running, per standing
practice (above). The antenna lay on the ground working badly for **2 h 26 m**,
and the station has been off the air since.

  **The time was recovered from the dated packet log, not observed.** Method,
  because it will be wanted again:

  * **Bin the log by `utime` and watch `direct`** -- packets copied with no
    digipeater hop. At 30 minutes the edge was between the 14:30 and 15:00
    buckets; at 5 minutes it is inside 14:45-14:50. Normal was 24 packets per
    5 minutes, grounded was 3.0, and the separation is clean: the lowest
    pre-fall bucket is 14 and the highest post-fall bucket is 4, with no
    overlap. Interpolating inside the edge bucket gives **14:46:25**.
  * **Compare like-for-like clock hours**, because APRS traffic has a strong
    daily cycle. Against 15:00-17:00 on 11-04 -- a period when traffic was
    *rising* -- 11-05 was **rx -89.4%, direct -82.8%**.
  * **The method checked itself.** The same interpolation applied to the
    radio-off bucket predicted 17:16. The log file's mtime, which is the last
    decoded packet, is 17:12. Four minutes apart, from a number that was not
    used to build the estimate.

  **A fallen antenna is not an outage, and the shape says so.** Receive fell
  to 17-28 packets per half hour from 1-2 stations rather than to zero -- the
  nearest and strongest digipeaters, still audible from ground level. Transmit
  died outright: beacon echoes went to zero at 14:00 and stayed there for eight
  consecutive buckets, where the longest run of zeros earlier that day was two.
  Echoes are the most height-sensitive indicator because they are a round trip,
  but at one or two beacons per bucket they are far too sparse to time
  anything. **`direct` finds the edge; echoes only confirm it.**

**This is a different failure from 11-01.** That one was a telescoping section
dropping with slack guys. This one is the foundation. The mast and guys came
through it fine, which points at the base and not at the structure above it.

  **Working hypothesis, not a conclusion: tightening the guys on 11-01 may have
  loaded the stake that failed on 11-05.** Guy lines compress a mast into its
  base, by roughly 3 x tension x cos(angle from vertical) for a three-way set.
  Guys at 36 degrees tensioned to 50 lb each put about 120 lb of down-force on
  the base before any wind or the mast's own weight. The 11-01 repair was
  re-tensioning slack guys. Nothing here is measured -- the guy tension was
  never recorded -- so this is a chain worth testing on the rebuild, not a
  finding.

  **"Bent" is the informative word.** Pure compression crushes or splits a
  stake; bending means the load had a sideways component, which means the base
  was not acting as a pin. A guyed mast base should see compression only. An
  eccentric foot, a guy anchored to the mast foot rather than independently, or
  a stake acting as a lever would all produce bending.

  **Three-way guying is not itself the fault.** Three guys at 120 degrees is
  standard and statically determinate; four-way is indeterminate and arguably
  worse. What three-way is, is **unforgiving of uneven tension** -- let one guy
  go slack and the remaining two form a hinge rather than a tripod. Slack guys
  were the 11-01 symptom, so that much is worth taking seriously even though
  the base is what actually failed this time.

  **Not recorded, and needed for the rebuild:** the guy tension, whether the
  guys were at one level or two on a 28 ft mast, and the weather around the
  time of the fall. Soft ground after rain removes most of a stake's holding.

  **The fall was an accidental height experiment, and its results are in
  AGENTS.md** under "Measure height per station, not in aggregate": same
  antenna, radio and feedline at 28 feet and then at ground level, with the
  change timed to the minute. Two fixed stations lost every direct packet
  (WO3T-1 35 -> 0, KE4MHE-10 17 -> 0) while two others lost nothing
  measurable (W3YA-1 11 -> 7, W3TM-1 15 -> 15). Non-moving stations together:
  78 -> 22 direct packets.

  **Three station-classification methods were tried before one worked**, and
  the first two produced confident wrong answers: position span (destroyed by
  one bad GPS fix) and largest-cluster-share (destroyed by rounding
  boundaries). The method that works, and every other log-analysis command
  from this investigation, is in **README.md, "Reading the dated packet log
  directly"** -- kept there rather than here so there is one copy to maintain.

  **Do not write station positions, distances or bearings into this
  repository.** Beacon coordinates here are rounded to a 6-character grid
  square on purpose, and a precise range and bearing from this station to a
  known one undoes that rounding. Both commands above report counts and
  scatter rather than position, deliberately.

### 2026-11-01 — one mast section collapsed; 15-minute outage

**Noticed at about 09:30 EST (14:30 UTC) on Sunday 2026-11-01: one section of
the Spiderbeam mini mast had telescoped back down, and the guy lines were
slack.** Radio power to the gateway was switched off at that point and the Pi
was left running. The mast was re-erected and the station was back on the air
by about 09:45 EST (14:45 UTC). Outage: **14:30–14:45 UTC, fifteen minutes**,
transmitter and receiver both down, everything else up.

**When it dropped is not known.** 09:30 is when it was seen, not when it
happened; it could have been minutes or days earlier. The last confirmed
~28 ft observation is 2026-10-30, so **every figure from 10-30 through 11-01
rests on a height that was not checked**, and the collapsed height was never
measured.

**The height after the repair has not been confirmed either.** The mast went
back up by hand without counting segments. AGENTS.md's +5.4 dB two-ray figure
is for 15 → 28 ft specifically, and November's comparison against October
assumes the 10-30 configuration. Until the erected height is checked against
it, that is an assumption and not an observation.

**What the data will and will not show.** The Pi stayed up, so `uptime -s` is
unchanged and there is no restart to find. The event appears as:

- a fifteen-minute hole in both directions in the dated packet log;
- **one missing beacon, or none** — the interval is 30 minutes, so a
  2026-11-01 beacon count of 47 against the usual 48 is this event and not a
  second outage;
- possibly one failed self-test. `igate-selftest.timer` fires every four
  hours, so the window had roughly a one-in-sixteen chance of catching one. The
  journal is persistent (`PI_PERSISTENT_JOURNAL = yes`), so if a self-test
  failed at 14:30–14:45 UTC it is still recoverable, and it is explained by
  this and is not a transmitter fault.

A lowered mast leaves no mark at all. It degrades marginal paths and nothing
else, which is the same signature as weather.

**The watchdog did not fire and could not have.** The Digirig Lite's C-Media
codec is in the dongle and powered from the Pi's USB, so powering the radio
off does not remove the sound card or the CM108 PTT node.
`radio_devices_missing` saw nothing missing, Direwolf kept running, and the
watchdog took no action. For the same reason **Direwolf logged every frame it
handed to the modem as `[0L]` throughout the outage** — the gateway looked
healthy in the log and on the web monitor while transmitting into a radio with
no power. This is the same failure signature as the backwards PTT cable
documented in `radios/digirig.conf`, arrived at from the other direction.

**Station state at 09:45 EST, from the web monitor:** `state running`, call
`KD3CCO-10`, whitelist `KD3CCO*, KD3CCP*, W3EDP*, W3TM*`, feed live and
gating — `RF->IS UP` on traffic from N3SNN-3 at 09:45:14, 09:45:15 and
09:45:18, and `IS DROP` on non-whitelisted APRS-IS traffic. Receive, transmit
and the whitelist were all confirmed working within the minute after the
repair.

**Logging and archive panel, same observation** (the live log is tmpfs, so
these numbers are recorded here because they do not survive a reboot):

| UTC day | rx | direct | stns | direct stns | win beacons | win repeats | chunks |
|---|---|---|---|---|---|---|---|
| 2026-10-31 | 5543 | 785 | 211 | 9 | 455 | 379 | 0 |
| 2026-10-30 | 1020 | 152 | 84 | 6 | 407 | 318 | 0 |
| 2026-10-29 | 0 | 0 | 0 | 0 | 397 | 308 | 0 |

Today (UTC) 1.1 MB / 7138 rows at 09:45; yesterday 855 kB / 5543 rows; live
packet log 3.3 MB with 2 rotated generations; 2 archived chunks, 1.9 MB,
newest 44 h old — which is the 10-30 pre-mast archive, consistent.

  **The transmit columns are a rolling window, not a daily count.**
  `igate-archive.sh` says so in the summary header: receive columns are exact
  for the named UTC day, transmit columns cover whatever the packet log still
  holds. Difference consecutive rows for a rate. Doing that gives **48 beacons
  on 10-31**, which is the 30-minute interval exactly and confirms 10-31 ran
  without an outage, and 10 on 10-30, which is a part day.

  **The 10-29 zeros are absence of the log, not absence of traffic.** The
  dated packet log was first deployed on 2026-10-30.

  **Nothing is claimed from the 10-30 → 10-31 jump.** Differenced repeats are
  61 against 48 beacons on 10-31, or 1.27 per beacon, where the 15 ft baseline
  over 8.2 days was 306 against 394, or 0.78. That is one day and 48 beacons,
  on a Saturday, with the rolling window making the difference only
  approximately a daily rate. This repository has already produced one
  retraction from reading a change out of a sample this size. Record it, wait
  for the month.

  **The archive timer did not move with the clocks.** The panel shows last run
  Sat 2026-10-31 20:21:40 EDT and next Sun 2026-11-01 19:20:30 EST, which looks
  like an hour's drift and is not: `igate-archive.timer` is
  `OnCalendar=*-*-* 00:20:00 UTC` with a 3-minute randomized delay, so both are
  00:20–00:22 UTC. The display is local and the clocks changed; the schedule
  did not.

**Daylight saving time ended at 02:00 EDT this morning**, 7.5 hours before the
mast was found. The packet log and the archive summary are per UTC day and are
unaffected. Anything read in local time on 2026-11-01 — the journal, the web
monitor's own labels — has a repeated 01:00 hour.

**Guy tension is now a scheduled check, not a troubleshooting step.** Slack
guys were the visible symptom. A telescoping mast not held in tension can drop
a section under its own weight, and nothing in the monitoring would say so.
This is the first such event; whether it recurs is not yet known.

Otherwise the station is up and running normally.

### 2026-10-31 — first full UTC day at ~28 ft

No intervention. 48 beacons, 5543 received packets, 211 stations, 9 heard
directly. Recorded because it is the only clean twenty-eight-foot day that
precedes the 11-01 event.

### 2026-10-30 — mast raised 15 ft → ~28 ft, and the logging work

The large change day. In order, as far as the repository records it:

- **Baseline archived off the Pi before anything was touched** —
  `archive-2026-10/`, 378,997 lines, 30,247 frames, the 10-22 → 10-30 window.
  Gitignored: large, and every position in it is a real station's coordinates.
- **Mast raised from 15 ft to about 28 ft.** A 33 ft Spiderbeam telescoping
  mast with the thinnest top segments left out, so the figure is the erected
  height and is approximate. Nothing else changed — same N9TAX slim jim, same
  coax, same configuration.
- **Dated packet log deployed** (`LOG_DIR`, Direwolf's `-l` flag), the daily
  archive timer installed, and NFR-10's tmpfs rule given its one deliberate
  exception at ~1 MB/day to the card.
- **Found and fixed: an absolute path in `igate.conf` is readable by the
  station and invisible to its own web monitor**, because `igate-web.service`
  runs `ProtectHome=yes`. Every logging count on the panel read zero while the
  log filled normally.
- **Found: Direwolf 1.7 ignores unknown config keywords in silence.**
- `igate-selftest.timer` confirmed active, so `BEACON_TO` stays `RF`.

Findings in AGENTS.md under their own headings.

  **"Doubled" and "thirty feet" were both said on this date before the
  segments were counted.** Both are wrong and both are in the git history.

### 2026-10-22 to 10-30 — the measured 15 ft baseline window

No intervention. The window the fifteen-foot numbers come from: 394 beacons
over 8.2 days (48/day, so no outage in it), 306 repeats, 22 stations heard
directly, 4,731 direct packets, 448 stations via somebody's digipeater.
73,066 packets offered by APRS-IS and 95 transmitted, every one to a
whitelisted call.

### 2026-10-18 — two-hop beacon experiment closed

`BEACON_VIA` reverted to `WIDE1-1` at 15:56 EDT. The experiment answered its
secondary question — two-hop beacons repeated 131 of 223 against one-hop 32 of
53, a 1.6-point difference on a 7.5-point standard error, so the second hop
does not help beacons — and at ~1,400 beacons a month it was a real cost to a
shared channel. Hop count eliminated as the cause of the uplink/downlink
asymmetry.

  **The start time is recorded inconsistently.** AGENTS.md says
  "2026-10-13 22:22" in one place and "2026-10-13 22:00 EDT" in two others.
  Unresolved; the frame log no longer reaches back that far. Use 22:00 as the
  conservative start if the window has to be excluded.

### 2026-10-17 — downlink rests on the direct path

Measurement, no configuration change. See AGENTS.md.

### 2026-10-14 — web monitor "down" was the phone, not the Pi

The service was healthy on every check from the Pi and the page still would
not load on an Android phone until the browser was granted Android's **local
network devices** permission. No change to the station.

### 2026-10-13 — `W3TM*` added to the whitelist; beacon-window fix; experiment begins

Twenty self-tests in forty minutes walked the repeat count down 3, 3, 2, 1 by
pushing beacons out of a sixteen-transmission window — the fix counts beacons
rather than transmissions. `BEACON_VIA` two-hop experiment started at
22:00 EDT (see 10-18).

### 2026-10-12 — UPS power cut confirmed on hardware; baseline window ends

Mains unplugged with nobody typing; clean shutdown on battery, at the sixth
attempt after five failures. Mobile delivery tested at four locations 3–4 mi
out, all inside the footprint. `uptime -s` dates from this day. Evidence kept
at `~/evidence/mobile-2026-10-12.txt` on the Pi, because `run/` is tmpfs.

### 2026-10-06 — PiShop replied on the UPS HAT

Martin at PiShop confirmed the documentation was wrong and the hypothesis
about the mechanism was right.

### 2026-10-04 — coax replaced; baseline run begins 16:25 EDT

50 ft of RG316 replaced with RG-8X, recovering roughly 3.5 dB each way. Clean
shutdown verified against a real plug-pull; the HAT carried the Pi for 63
seconds on battery. The unbroken 8 d 3 h baseline starts at 16:25 EDT.

### 2026-10-03 — station moved from the yard to the spare room

Deviation checked, good to about 50 Hz against 4 kHz gridlines against a
2.5–3.0 kHz target.

### 2026-10-02 — UPS HAT and DS3231 fitted

PiShop UPS HAT with a LiPo cell; `rootflags=data=journal` added. RTC verified
reading correctly — before it, every gated packet was timestamped wrong until
the network came back.

  **It did not work at all between 2026-10-02 and 2026-10-04.**

### 2026-09-30 — web monitor stall solved; persistent journal earns its cost

A Latin-1 decode fault. Found only because the journal was persistent;
`Storage=volatile` would have lost it.

### 2026-09-28 — beacon repeat rate remeasured at 60%

41 of 68, W3TM-10 (32) and W3YA-1 (9). Against 09-27's 42 of 112 this is
z = 2.97, p = 0.003 — not sampling noise. **The cause is unknown.** Nothing on
the station changed between the two runs except a reboot at 16:20 on the 27th.

### 2026-09-27 — beacon repeat rate measured at 37%

112 beacons `via="WIDE1-1"` over 3406 frames, 42 repeated, W3TM-10 (28) and
W3YA-1 (14). Reboot at 16:20.

### 2026-09-26 — self-test passes end to end

All four steps, with delivery confirmed by the addressee's acknowledgement
rather than inferred from the gateway having transmitted.

### 2026-09-19 — a day on why the gateway could not reach W3YA-1

Mostly negative conclusions, which is why §15.3 of
`aprs-igate-prototype-test.md` records them at length.

---

## Where the retroactive entries came from

Everything dated before 2026-11-01 was **reconstructed on 2026-11-01** from
the repository itself: `AGENTS.md`, `aprs-igate-prototype-test.md`, the commit
dates, `archive-2026-10/`, and the unit files. It is not a contemporaneous
log and should not be read as one.

What that means in practice:

- **Times of day are absent except where a document happened to record one.**
  Most entries are good to the day and no better.
- **Only events that left a trace in a document are here.** A reboot, a knob
  turned, or a cable reseated that nobody wrote down is missing, and its
  absence is not evidence it did not happen.
- **The entries are summaries and the documents are the authority.** Where
  this file and AGENTS.md differ on what a measurement means, AGENTS.md wins.

From 2026-11-01 forward this file is written as things happen and the above
does not apply.
