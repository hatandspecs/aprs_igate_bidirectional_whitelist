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
| 2026-11-01 14:30–14:45 UTC | Radio off, one section re-extended | 15-minute outage |
| 2026-10-30 – 2026-11-05 | Nominally ~28 ft, **at least one section slipping** | Two joints let go inside this window. The height is unverified and may have crept downward through it |
| 2026-11-05 19:46 – 22:12 UTC | **Mast down**, antenna at ground level | 2 h 26 m. rx -89%, transmit reach zero |
| 2026-11-05 22:12 – 2026-11-06 18:00 UTC | Radio powered off, station off air | 19 h 48 m. No data of any kind |
| 2026-11-06 18:00 – 2026-11-13 17:31 UTC | Back on air, angle-iron base stake, **joints partly retained** | 6 d 23 h 31 m. Some stop collars were already fitted during this stretch; how many and on what date is not recorded |
| **2026-11-13 17:31 – 18:23 UTC** | **Radio powered off, Pi running, mast lowered and the collar set completed** | **52 m. No RF in either direction. Small enough to ignore in practice, recorded because an unwritten edge is worse than a small one** |
| 2026-11-13 18:23 UTC onward | Back on air, all 13 stop collars fitted | Current baseline. **Height still not confirmed** |

**That planned outage happened on 2026-11-13 and it was 52 minutes, as
predicted here.** Radio off 12:31 EST, back on 13:23 EST. **The whole cycle fits
inside that**: lowering the mast, wrapping and cutting the rubber, fabricating
the collars, fitting them, raising, sweeping. Nothing was prepared on another
day. It is the shortest recorded hole in November and the cleanest, because both
edges are operator-recorded rather than inferred from a degradation curve.

**Compensating November analysis for it, which is barely necessary.** The radio
was off and the Pi was up, which is the contaminating combination: Direwolf
prints `[0L]` for every frame it queues with no RF path. But at one beacon per
30 minutes, 52 minutes is about **2 queued beacons that never left the
antenna**, against 493 sent in the month. That is below the point where it moves
any rate, so the guidance is to record the window and not bother excising it.

**Size the correction to the hole.** Excising 2026-11-05 to 11-06, at 19 h 48 m
and roughly 40 phantom beacons, moves W3YA-1's 75-byte beacon rate from 32.5% to
about 36%. Excising this one moves it by a fraction of a point. **Neither
accounts for the clean 55% figure**, which needs about 202 beacons out of the
month's 493 removed and therefore also drops the 2026-10-30 to 11-05 stretch
where the mast was slipping and then down. See AGENTS.md, "The clean rate is
55%, not 32%", which has that arithmetic and the warning not to quote 55% as
"the month minus the outages".

**If a mid-day hole ever does need excising, the obvious command is wrong.**
`--since/--until` *selects* a window rather than excluding one:
`digi_length_profile.py` skips frames before `--since` and at or after
`--until`, so passing the outage's own edges keeps the outage and discards the
month. A hole in the middle of a UTC day takes one of these instead:

1. **Two runs, pooled by hand.** `--until 2026-11-13T17:31:00` for everything
   before the hole, `--since 2026-11-13T18:23:00` for everything after, and add
   the counts. This keeps all 23 h of good data from that day.
2. **Drop the UTC day**, by leaving `frames-2026-11-13.log.gz` out of the file
   list. For a 52-minute hole that discards 23 hours to save 2 beacons, so it
   is the wrong trade here and was the right one on 2026-11-06.

Note the seconds. `parse_bound` accepts `%Y-%m-%d`, `%Y-%m-%dT%H:%M:%S` and
`%Y-%m-%dT%H:%M:%S%z`, so a bare `2026-11-13T05:31` is rejected outright rather
than rounded.

**The window edges are checkable against the log and should be checked.** The
times above are from the operator's own note, not from the data, so they are
subject to a misremembered hour in a way the log is not. The dated packet log
carries a `utime` per packet and the console log is timestamped since 2026-11-08,
so the last decode before the hole and the first after it bracket the real
window. If the log's edges disagree with 17:31 and 18:23 UTC, **the log wins**
and this entry should be corrected to match it. The 12:31 figure was first
written down as 12:31 am and corrected to 12:31 pm the same day, which is
exactly the misremembered-hour failure this paragraph exists for.

**November is not a clean month, but every edge in it is now known to the
minute.** A contaminated window with a hard start and a hard end is excisable;
that is the difference between slicing November and discarding it.

---

## Station reference — the 10 m mast and its joints

The mast is a **Spiderbeam 10 m "mini"** fiberglass telescoping pole, catalog
33 ft, erected with the thinnest top segments left out. Published figures:
17 segments, 60 mm base diameter without the cap and 66 mm with it, 6 mm tip,
0.72 m collapsed, 2.22 kg, wall tapering 1.4 mm at the bottom to 0.7 mm at the
top.

### Joint retention: stop collars at the rim

The decision that matters, taken 2026-11-06 after two taped joints failed.

A clamp belongs on the **inner (upper) section, snug against the rim of the
outer one**. The rim takes the load.

**It is friction that holds the collar on the section, and the taper works
against it (corrected 2026-11-13).** Earlier versions of this entry, and of both
articles, said the load path was compression into the rim with no friction
involved. That is wrong. If the collar slips it slips *upward*, toward
decreasing diameter, so the band gets looser as it goes and a collar that starts
to migrate keeps getting looser. The same entries then said a stop collar "only
has to resist migrating up a 1 to 3 degree taper", which contradicts the
no-friction claim outright. **Do not restore it.**

The practical consequences, which are all this needs:

- **A hand-snug finger key on a rubber pad is enough.** The load is small and
  rubber on epoxy grips far better than steel would, and the inability to
  over-tighten a 0.7 mm wall by hand is a feature.
- **Check the collars rather than trusting them.** Because migration is a
  runaway rather than self-arresting, a witness mark at each rim is the cheap
  instrument, not more torque.
- **Clamping the outer section's top lip instead is the wrong orientation.** It
  adds no mechanical stop, so it is the same squeeze-and-hope that failed twice,
  and it puts hoop stress on a 0.7 to 1.4 mm wall where a split is a scrap pole.
- A clamp at a joint also stops a guy ring from creeping down the taper past it.

### How many clamps, and roughly what size

Thirteen joints carry load on the erected mast and that is how many collars were
built, counted rather than estimated. The pole ships with seventeen segments;
the three thinnest are not in it, leaving fourteen, and fourteen segments meet
at thirteen joints. They run from roughly 57 mm at joint 1, the lowest, down to
16 mm at joint 13, which is the range the clamps have to cover.

That range is all the sizing information this job needs. **There is no table of
per-joint diameters here any more, and one should not be added** (removed
2026-11-13, along with its blank `Measured OD` and `Clamp fitted` columns). The
pads are not sized from a number, so the number was never used.

Number the clamps to their joints. Every one is a different size, and
re-erection should not require sorting them out again.

### Building the padded stop collars

**Wrap the rubber around the mast at the position it is going to sit, cut it
there, and number it. Cut heat shrink to fit, leaving a gap under the worm
housing so the band can still feed. Heat gun. Done.**

Each collar comes out one reusable piece with no loose pad to drop in the grass,
and the shrink covers the band edges so no steel touches fiberglass anywhere.
Nothing about this is difficult or interesting, and it does not need more
explanation than the paragraph above. It is also Spiderbeam's own recommended
method for securing the joints, so it is not an invention here.

**The pole comes down and goes back up in a few minutes.** Wrapping, marking and
fitting are all ground-level work on a lowered pole, with no ladder in any of
it, which is why lowering the mast ahead of a forecast is realistic and why a
planned outage for mast work costs minutes of data rather than a day.

The collars stay removable on purpose. Radial clearance between nesting
sections is a few tenths of a millimeter, so anything permanent added to the
outside of a section would cost the ability to collapse the pole, which is the
ability wanted on the day a storm is forecast.

---

## The log

### 2026-11-13 — the collar set completed, 13 of them; 52-minute planned outage

**Radio off 12:31 EST (17:31 UTC), back on 13:23 EST (18:23 UTC). 52 minutes off
the air, by plan, and the hole is in the middle of the 2026-11-13 UTC day.** The
Pi ran throughout, so about 2 of the day's beacons are queued `[0L]` lines with no
RF behind them, which is small enough to leave in. See "Data-quality windows at a
glance" above.

**The set is 13 collars, and 2026-11-13 completed it rather than starting it.**
Some were already fitted during the 11-06 to 11-13 stretch. **How many, and on
what date, is not recorded**, so that stretch is "joints partly retained" in the
window table rather than unretained, and no analysis should treat 11-06 to 11-13
as a fully taped mast or a fully collared one. Worth reconstructing from the
photographs if it ever matters.

**52 minutes covered the entire measure-fabricate-fit cycle, and that is the
operational finding.** Nothing was pre-built on another day. The time came from
planning each move before the radio went off, which is why this log's original
"minutes, not hours" prediction held for a session that included fabrication.
**Plan the sequence, not the parts**: the pole is down for the duration either
way, so the saving is in not deciding anything while it is down.

**Work done inside the window:** mast lowered, rubber wrapped at each position
and cut there, collars fabricated, collars fitted on the inner section against
the rim of the outer one, mast raised, 2 m sweep taken, radio reconnected and
powered on. **Thirteen collars now in total**, each a key-type worm clamp with a
rubber strip inside the band and heat shrink over the band, numbered to its
joint.

The two key colors in the photographs are the two band-size ranges the taper
needs, blue on the low large-diameter joints and yellow on the high small ones,
with the joint number inked on each key.

**The pads were sized by wrapping, not measuring.** A strip of rubber wrapped
around the mast at the position it was going to sit, cut there, numbered; heat
shrink cut to fit with a gap under the worm housing; heat gun. No calipers, and
the old per-joint diameter table was not used, so it has been deleted. Nothing
here is outstanding work.

**Not done, although the pole was in hand:** segment count, erected height, and
witness marks at each rim. Those three were queued against exactly this takedown
and all three were missed, so the erected height is still "about twenty-eight
feet" by estimate and still carrying the two-ray figure and the
November-against-October comparison. They need another takedown.

**SWR after the work, at the station end, four markers over 144 to 148 MHz:**

| Frequency | R | X | SWR |
|---|---|---|---|
| 144.000 MHz | 36.1 Ω | −17.4 Ω | 1.68 |
| 145.413 MHz | 28.6 Ω | −2.2 Ω | 1.75 |
| 146.787 MHz | 32.1 Ω | +19.9 Ω | 1.93 |
| 148.000 MHz | 58.4 Ω | +44.2 Ω | 2.25 |

All four displayed SWR values reproduce from the R and X on the same screen, so
the instrument is at least self-consistent. Interpolated to 144.390 MHz: R 34 Ω,
X −13 Ω, SWR 1.64.

**The match is resistance-limited, not reactance-limited.** Reactance crosses
zero at 145.55 MHz and R there is 28.9 Ω, which is a 1.73 mismatch on its own.
Trimming an element would move the crossing frequency and leave the depth of the
minimum alone, so this is a matching question at the slim jim's stub tap and not
a length question. **Do not log a future "retune" against this number.**

**Unresolved: 2026-10-30 recorded "SWR under 1.3" and this reads 1.70.** Three
candidates, not separated by one sweep. The reference plane, since 50 ft of
RG-8X at 146 MHz puts 3.6 dB on the reflection and a reading at the feedpoint is
not a reading at the station end. The instrument, since the October figure is
not sourced to a VNA in this log. And the collars themselves, since the slim jim
is taped along the mast and the upper bands are steel within a few millimeters of
a radiating element. **The resonant-parasitic version of that third one is
dead:** at 146 MHz a resonant loop needs 654 mm across and the largest collar is
57 mm, one eleventh of a wavelength in circumference. Local capacitance is the
mechanism left. Whichever it is, the whole 1.30-to-2.29 span across both planes
is 0.07 dB to 0.73 dB of mismatch loss, which this station cannot resolve in
packet counts.

**The test that separates them, unrun:** with the pole down, sweep untouched,
sweep again with the top three or four collars off, refit and sweep a third
time. Ground-level work, no ladder. Pair it with the takedown that still owes
segment count, height and witness marks.

**The collars are untested by weather.** The 2026-11-05 failure took a 24 mph
gust on top of two prior joint slips. A collar fitted in still air is not yet
evidence of anything.

**Off-station: the W3YA-1 administrator was told about the frame-length
behavior** and finds it unexpected, and is looking at it from the site end. That
matters because two of the three surviving explanations, the receive audio level
into that TNC and co-channel noise at the ridge site, are not observable from
this yard at all.

### 2026-11-06 — back on the air at 13:00 EST; the tape is the root cause

**Back on the air and gating packets at exactly 13:00 EST (18:00 UTC) on
2026-11-06.** Off the air for 19 h 48 m from the radio-off at 22:12 UTC the
previous day, and 22 h 14 m from the fall itself.

**Inspection found the actual cause, and it is not the base.** One of the taped
telescoping joints came untaped and the section collapsed into the one below it.
That shortened the mast, which took the tension out of all three guy lines,
which left the mast standing as an unguyed cantilever on a light-duty wooden
stake. The stake then bent and split along the grain, which is what the shape
of the failure was pointing at all along: a guyed base sees compression, and a
bending failure means the guys had stopped doing their job before the stake
did.

**Guy tension is part of the cause, not just a casualty.** A guy running from
a point up the mast to a ground anchor pulls down along the mast axis as well
as sideways, and down the mast axis is the direction a telescoping section
slides. Tensioning the guys loads the joints in the direction they fail, so
re-tensioning after a slip is not a neutral act -- it reloads the joint that
just proved it would move. **This recurs until stop collars are fitted.** It is
not a puzzle about why the guys went slack; the pole semi-collapsed, which is
obvious on sight every time it has happened.

The chain, in order:

1. A taped joint lost its grip and the section telescoped down.
2. The mast got shorter, so the guys went slack.
3. With slack guys the mast became a free cantilever, and wind put a bending
   moment into the base.
4. The light-duty wooden ground stake bent, and the mast came down.

**This is the same failure as 11-01, carried one step further.** On 11-01 slack
guys were noticed and the section was re-extended within fifteen minutes, before
anything downstream of step 2 happened. On 11-05 nobody was home to catch it.
Two joints letting go in five days is a pattern, not bad luck, and the
conclusion is that **friction plus tape is not a joint retention method for a
semi-permanent deployment**.

**Fixed:** the ground stake is now **angle iron** in place of the light-duty
wooden one. That hardens step 4, where angle iron resists the bending moment
that broke the wooden stake.

**Fixed 2026-11-13:** the padded stop collars are built and fitted. Radio off
00:31, mast lowered and joints measured, collars built on the bench, fitted,
mast raised, swept, radio back on 13:23. Each collar is a key-type worm clamp
with a rubber strip inside the band and heat shrink over the band to capture it,
numbered in marker to the joint it was measured for, and fitted **on the inner
section against the rim of the outer one** as a positive stop. That hardens
steps 1 and 2. The sizing table, the orientation rule and why the heat shrink
stays off the tail are in "Station reference -- the 10 m mast and its joints"
above.

**Still open after that takedown.** The pole was in hand and three of the
measurements queued against that moment were *not* taken: **segment count,
erected height, and witness marks**. The erected height is still "about
twenty-eight feet" by estimate, and the two-ray figure and the whole
November-against-October comparison still rest on it. The witness mark remains
the cheapest unclosed item and the one that would have caught both failures
days early. Expect to need another takedown for them, and do not record this
one as having closed them.

**The collars are untested by weather.** The November 5 failure took a 24 mph
gust on top of two prior joint slips. Nothing about a collar fitted in still air
is evidence yet.

**Spot check, 13:05-13:15 EST (18:05-18:15 UTC).** 46 RF receptions in
9 m 32 s from 26 distinct sources, essentially all gated. That is **24.1
packets per five minutes against a pre-fall normal of 24.0**, where the
grounded antenna gave 3.0. One short window at a slightly different hour of
the day, so this is a sign of life and not a measurement -- but it is the
right order of magnitude and it is not 3.

  **The two height-sensitive instrument stations are back as direct copies.**
  WO3T-1 at 13:06:24 and 13:10:24 with no digipeater in the path at all, and
  KE4MHE-10 at 13:12:00 with its `WIDE1-1` unused. Those two went 35 -> 0 and
  17 -> 0 when the mast fell, and they are being copied directly again within
  ten minutes of the restoration. W3YA-1, the control that never went away, is
  also direct (13:09:05). This is the fastest available confirmation that the
  rebuild restored the paths the fall took away. It says the paths exist, not
  how far they reach -- that needs matched clock-hour windows against 11-04.

  **Nothing is known yet about transmit.** No beacon fell inside the watch
  window, which is expected at a 30-minute interval.

  **Service state, from `/api/status`:** `direwolf pid 6461`, CAT none, PTT
  `cm108` on `/dev/hidraw0`, beacon every 30:00 on RF, IS filter
  `IG 0 g/KD3CCO*/KD3CCP*/W3EDP*/W3TM*`, today's dated log present and five
  seconds old. The monitor answered at `http://192.168.68.66:8080/` from the
  LAN; that address is DHCP and `http://aprs-igate.local:8080/` is the stable
  name.

  **Two documented traps turned up live in the same ten minutes**, which is
  worth recording as evidence that neither is rare:

  * A **third-party wrapper arriving on RF** at 13:07:43 -- `KB2WAC-1`
    carrying an inner station and a repeater object in central New York.
    Direwolf unwrapped it and declined to gate it, correctly, on the grounds
    of `TCPIP` in the path. This is the mechanism behind the inflated
    distinct-station count in AGENTS.md, no longer a hypothesis.
  * A **null MIC-E position** at 13:12:51 from KA3FMO-7, decoding to 0,0.
    This is exactly the single bad fix that destroyed the span-based mobility
    classification on 11-05, observed in the wild inside a ten-minute sample.

**The wind on 11-05 was checked, and it does not explain the failure on its
own.** Regional ASOS, hourly, the afternoon of the fall: 10 kt gusting 21 at
13:53 EST, 12 kt gusting 18 at 14:53, both from 220 degrees, 72 F. The mast
came down at 14:46, between those two observations. **Gusts of about 24 mph
are nowhere near enough to bring down a correctly guyed 28 ft fiberglass
mast, and entirely enough to topple an unguyed one.** This is the test the
chain needed and it passes: wind is why the failure finished when it did, not
why it happened. It also closes out "the weather around the time of the fall"
from the 11-05 not-recorded list. Command in README.md.

**Confirm and write down the erected height.** It has not been stated for this
rebuild, and the whole point of the height work in AGENTS.md is that the number
has to be known. Two further things are worth recording this time: the guy
tension, which has never been measured, and a **witness mark on each section at
the rim of the one below it**. A painted or taped witness mark turns "has a
section crept?" into something answerable from the ground with a glance, and
both of these failures would have been visible days early.

### 2026-11-05 — the mast came down; the base stake bent

**The mast came down at about 14:46 EST (19:46 UTC) on 2026-11-05.** The
light-duty wooden ground stake at the base failed -- bent into a pronounced
curve and split lengthwise, without coming apart. The mast
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

**This is the same failure as 11-01, carried further.** A taped telescoping
joint let go, the mast shortened, the guys went slack, the mast became an
unguyed cantilever, and the base bent. Inspection on 11-06 established that
order; see the entry above for the chain and the fix. The mast and the guy lines
themselves came through undamaged.

  **The break says sideways load.** The stake bent into a curve and split
  lengthwise along the grain at the knee without coming apart, and the knee
  sits where the stake stops being strapped to the mast, which is where a
  cantilever's bending moment is largest. A mast with three tight guys on it
  does not deliver a sideways load to its base, so the guys had stopped working
  before the stake did. That is all the stake has to tell; it was the last link
  in the chain and the analysis belongs upstream at the joints.

  **The guy-compression hypothesis is dead**, recorded so it is not raised
  again: the guys were slack when the stake failed, so they were not loading
  the base at all. Guy tension matters to this failure at the joints, not at
  the stake. **Do not turn it into a measurement task.** Nobody tensions a
  lightweight portable mast to a specified figure, and the fix is mechanical
  stops rather than a number.

  **Three-way guying is not the fault either.** Three guys at 120 degrees is
  standard and statically determinate; four-way is indeterminate and arguably
  worse. What three-way is, is **unforgiving of a slack guy** -- let one go and
  the remaining two form a hinge rather than a tripod. That sensitivity is what
  turned a slipped joint into a fallen mast, which makes joint retention the
  thing to fix rather than the guying pattern.

  **Three-way guying is not itself the fault.** Three guys at 120 degrees is
  standard and statically determinate; four-way is indeterminate and arguably
  worse. What three-way is, is **unforgiving of uneven tension** -- let one guy
  go slack and the remaining two form a hinge rather than a tripod. Slack guys
  were the 11-01 symptom, so that much is worth taking seriously even though
  the base is what actually failed this time.

  **Not recorded, and still wanted:** whether the guys were at one level or two
  on a 28 ft mast. The weather is settled above.

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
