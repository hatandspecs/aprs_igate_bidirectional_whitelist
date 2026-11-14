# AGENTS.md — bidirectional APRS iGate with a strict whitelist

A two-way APRS iGate that transmits **only** messages addressed to a callsign on
a whitelist. It runs on a Raspberry Pi 3B+ (`igate@aprs-igate.local`) driving an
FT-2900R through a Digirig Lite, and on a laptop in Docker.

## Start here

| Read | For |
|---|---|
| `README.md` | What it is, quickstarts, how a message travels |
| `aprs-igate-prototype-test.md` | Design doc: numbered FR/NFR with honest status, faults found, §15 open items |
| `PI-SETUP.md` | Building and deploying the Pi image |
| `igate.conf` | Every setting, documented inline with the evidence behind it |
| `OPERATIONS.md` | Station log: what was done to the hardware and when, and which date windows are contaminated |

The design doc separates **Specification** (what it must do) from **Findings**
(what was observed). Keep that separation when editing it.

## Things that cost a lot to rediscover

- **Read back `run/direwolf.conf` after changing any path setting.** Direwolf
  splits config lines on whitespace, so `IGTXVIA 0 WIDE1-1 WIDE2-2` silently
  means `WIDE1-1` and throws the rest away. Nothing downstream complains.
- **`TX_VIA` and `BEACON_VIA` are separate.** `TX_VIA` is the path on gated
  messages (`IGTXVIA`); `BEACON_VIA` is the beacon's (`PBEACON via=`). Blank
  `BEACON_VIA` inherits `TX_VIA`. They differ because the beacon goes out ~1,400
  times a month and a gated message a handful of times.
- **Never send test traffic as `MYCALL`** — Direwolf drops frames it originated.
  `selftest` sends as `IGLOGIN_CALL-1` and refuses if that equals `MYCALL`.
- **`IS GATED` in the monitor is not proof of transmission.** `[0L]` means the
  frame reached the modem, not that the radio keyed. Only an acknowledgement
  proves delivery, which is what `selftest` measures.
- **Powering the radio off is invisible to every layer of monitoring**
  (2026-11-01). On the Digirig profile the C-Media codec is in the dongle and
  powered from the Pi's USB, so the sound card and the CM108 PTT node stay
  present with the radio dead. `radio_devices_missing` finds nothing missing,
  the watchdog takes no action, Direwolf keeps running, and every frame handed
  to the modem is still logged `[0L]`. The station looks healthy in the log
  and on the web monitor while transmitting into an unpowered radio -- the
  same signature as the backwards PTT cable in `radios/digirig.conf`, reached
  from the other direction. **Only an acknowledgement proves the air path**,
  which is what `selftest` is for, and at four hours it can be that long
  before anything notices. Write deliberate radio-off windows into
  `OPERATIONS.md`; nothing else will record them.
- **A mast that has dropped a section leaves no trace in any data**
  (2026-11-01). It degrades marginal paths and nothing else, which is
  indistinguishable from weather. Guy tension is a scheduled visual check, not
  a troubleshooting step, and the height belongs in `OPERATIONS.md` whenever
  it is confirmed -- a reach figure is only as good as the last time somebody
  counted the segments.
- **Taped telescoping joints are the root cause of both mast failures**
  (established by inspection 2026-11-06). The chain is: a taped joint slips,
  the mast shortens, the three guys go slack, the mast becomes an unguyed
  cantilever, and the base takes a bending moment it was never meant to see.
  11-01 was caught at step 2; 11-05 ran to the end and bent the stake.
  **Guy tension drives the slip rather than merely suffering from it**: a guy
  pulls down along the mast axis, which is the direction a section telescopes,
  so re-tensioning reloads the joint that just moved. This repeats until stop
  collars are fitted, and a height written down after a repair is only good
  until the next joint slips.
- **The weather at the time of a past event is recoverable from the station's
  own packet log** (2026-11-06). Several of the WX stations in the gated
  traffic -- N3DXC, N3TUQ-2 among them -- put wind, gust and temperature in
  the comment field of every beacon, and those beacons are in the dated log
  like any other packet. They are tens of miles out, so this is a **regional
  proxy and not site wind**, but it separates "windy afternoon" from "dead
  calm", which is enough to test whether wind was the thing that bent the
  11-05 base stake. Command in README.md.
- **`reach` counts beacons**, so it measures `BEACON_VIA`, not `TX_VIA`.
- **"The web monitor is down" is usually the client, not the Pi** (2026-10-14).
  The service was `active (running)`, listening on 8080, one `tail -F`, `/`
  returning 200 with 10 KB of HTML, `/api/status` valid JSON and `/events`
  streaming live packets — and the page still would not load, on an Android
  phone, until the browser was granted Android's **local network devices**
  permission. Recent Android gates that separately from internet access, so the
  browser reaches the whole web while every LAN address fails. Check the client
  before the gateway: `curl -sS -o /dev/null -w '%{http_code}\n'
  http://127.0.0.1:8080/` on the Pi settles the server side in one command, and
  it must then be retested **from the device that is actually failing** — a
  laptop answering 200 by name proves nothing about a phone. Nothing is logged
  per request, so a journal holding only the startup lines is not evidence that
  requests are arriving. Full list of client-side causes in README.md, "When the
  page will not load at all".
- **A digipeater missing from a path on aprs.fi proves nothing.** APRS-IS
  deduplicates on source, destination and payload over ~30 s and ignores the
  path, and a digipeated copy arrives 1.5-3 s late, so it loses to any iGate that
  heard the original directly — and W3SWL-2 at 4 mi and W3TM-10 at 3.5 mi always
  do. The error is one-sided: a digi *appearing* in a path is real evidence, a
  digi *absent* is no evidence. For "did anything repeat this station," the only
  authority is the gateway's own receiver — `reach`, or `grep
  'KD3CCO-10>APDW17' run/direwolf.log | grep -i W3YA-1`. §14.9 of the design doc
  has the worked example: one transmission recorded two ways, supporting opposite
  conclusions.
- **`systemctl start` on an active oneshot does nothing** — use `restart`.
  `systemctl is-active` reports the unit, not the gateway; `./deploy_igate.sh
  status` reports what is actually running.
- **`igate.local.conf` and `igate.secrets` exist only on the Pi** and are
  excluded from the deployment rsync. Do not expect them in the repository.
- **Never decode the packet log strictly.** It carries other stations' APRS
  comment text verbatim and plenty of it is Latin-1, not UTF-8 — a degree sign
  in a bearing is `0xB0`. `UnicodeDecodeError` is a `ValueError`, so a broad
  `except ValueError` will swallow it and look like a clean shutdown.
- **In the monitor's logs, `rc=None` means the pipeline is still running**, not
  that it ended. A reader that stops must kill its pipeline or it orphans one.
- **Card-durability settings live outside the repo's own config.**
  `rootflags=data=journal` is on the kernel command line, because `data=` cannot
  be changed by the remount `fstab` drives. The swap writeback file is disabled
  by `[Main]` / `Mechanism=zram` in `/etc/rpi/swap.conf.d/` — stock Pi OS
  defaults to `zram+file` and keeps a `/var/swap` file that `swapon --show` does
  not reveal; check `/sys/block/zram0/backing_dev` instead. Both are written by
  `build_pi_image.sh`, so a rebuilt card inherits them and a hand-built one
  does not.
- **Transmit deviation is measured with Carson's rule, not a meter.**
  `dF = BW/2 - Fmax`, with `Fmax` 2200 Hz for APRS: 10 kHz occupied on a band
  scope is 2.8 kHz deviation, which is where this station measured on
  2026-10-03, good to about 50 Hz against 4 kHz gridlines. Target is 2.5-3.0 kHz.
  `direwolf -x m` generates the tone, the gateway service has to be stopped
  first, and it goes into a dummy load, never onto 144.390. The full procedure,
  the Bessel-null alternative and the pre-emphasis caveat are in
  `./deploy_igate.sh audio` and §14.8 of the design doc. A handheld transmitting
  in the same room reads a meaningless bandwidth — that is front-end
  compression, not its deviation.
- **A test that builds its own fixture proves nothing about the machine.** The
  UPS shutdown was "verified" against a simulated `/sys/class/gpio` tree that
  the test created, so it could only confirm the arithmetic it already used. On
  the real Pi the service crash-looped 11,735 times without arming, for two
  days, while three documents called it verified.
- **BCM numbers are not sysfs numbers, and the two kernel interfaces number
  lines differently.** Both of these are true at once on
  6.18.34+rpt-rpi-v7 (checked 2026-10-12), and mixing them up wastes an
  evening:

  * `/sys/class/gpio` — **global** numbering, base allocated dynamically by
    gpiolib. It is **512** here, so BCM17/18/27 are **529/530/539**. This is
    what a script exporting a pin must use, and `ups-shutdown.sh` computes it
    at runtime rather than hard-coding it: `ups: sysfs GPIO base is 512; BCM
    17/27 are 529/539`.
  * `/sys/kernel/debug/gpio` — prints lines **chip-relative** in the current
    format (`gpiochip0: 54 GPIOs` then `gpio-0`…`gpio-53`), so BCM18 appears
    there as plain **`gpio-18`**. That is the right number for reading state
    back:

        sudo grep -E "gpio-18 " /sys/kernel/debug/gpio
        #  gpio-18  (GPIO18   |power_ctrl   ) out lo

  Never hard-code either; read the base from the kernel for sysfs, and use
  chip-relative offsets for debugfs. A verification command greping the wrong
  namespace reports a working overlay as a broken one.
- **`bash -n deploy_igate.sh`** after any edit. It is 2,800 lines of bash and
  there is no test suite.

## Commands

```
./deploy_igate.sh config      # resolved settings, with which layer set each
./deploy_igate.sh restart     # render direwolf.conf and restart
./deploy_igate.sh selftest    # end-to-end: inject, gate, transmit, acknowledge
./deploy_igate.sh reach       # which digipeaters repeat this station, from logs
./deploy_igate.sh monitor     # live packet flow

./igate-archive.sh            # daily archive + summary row; safe to run by hand
./heard_spatial.py CAPTURE    # receive coverage per 2 km cell; safe, reads only
./test_archive.sh             # 29 checks, incl. replay against archive-2026-10/
./test_web_logging.py         # 21 checks: the monitor panel's bounds
./test_heard_spatial.py       # 48 checks, incl. replay against archive-2026-10/
```

**`igate-archive.sh` is a separate script, not a subcommand**, run by
`igate-archive.timer` at 00:20 UTC. It copies each rotated generation of the
packet log onto the card by content hash, appends one row to
`archive/daily-summary.tsv`, and prunes both to `ARCHIVE_KEEP_DAYS`. It
transmits nothing and restarts nothing, so unlike `selftest` and `reach` it is
safe for an assistant to run.

  **Receive columns are exact for the named day; transmit columns are a
  ROLLING WINDOW** over whatever the console log still holds, because that log
  has no per-line timestamp. Difference consecutive rows for a rate. The
  `chunks_saved` column is the health signal: above 1 on a routine day means
  rotation is outpacing the timer.

**`heard_spatial.py` answers "how much better does the antenna hear", and the
answer does NOT come from comparing against another station.** APRS-IS keeps
the first copy of a packet and drops the rest, so a neighboring iGate's
heard-directly map moves DOWN when this station improves; a ratio against it
overstates the gain and nothing in the data recovers by how much. The
denominator that works is this station's own log: packets copied directly
divided by packets that originated in the same 2 km cell, where the
denominator comes from digipeated repeats of traffic this receiver had the
chance to copy.

  **Use the console log on both sides of a before/after.** It is the only
  format old captures have. Measuring one month by console log and the other
  by `LOG_DIR` CSV puts the method difference on top of the effect. The CSV is
  a cross-check, not one half of a comparison.

  Four ways this tool gave a confident wrong answer before it was fixed, all
  now covered by tests: `[0L]` lines are this station TRANSMITTING; object,
  item and third-party payloads carry somebody else's coordinates; a mobile's
  last known position is not where it was copied from; and one lucky frame in
  1742 is not a 98-mile path. The fifth is subtler and worth keeping in mind
  anywhere else it might apply — **this station's own beacons come back off
  the air after a digipeater repeats them**, 306 of 311 packets in the home
  cell, reading it as 1.3% direct instead of 80.0%. They are excluded by
  default, because how many come back is itself a function of antenna height.

**15 ft baseline, from `archive-2026-10/frames-15ft-2026-10-30.log.gz`** (~8
days to 2026-10-30): 21,406 placed packets, 4,430 copied directly, 1,236 cells
with traffic, 46 with a direct copy, 14 stations with a real direct path.
Direct capture by ring: 100% of active cells inside 5 miles, 75% at 5-10, then
**38% at 10-15**. That fall is where the mast should act, and it is the
prediction to test against a Nov 22-30 capture.

`selftest` and `reach` transmit or read for real, on my hardware — give me the
command rather than running it.

## Where it stands (2026-11-01)

All functional requirements met.

**The baseline window ran unbroken from 2026-10-04 16:25 EDT to 2026-10-12,
eight days and three hours**, on the configuration of the time: slim jim on the
**fifteen-foot** mast, 50 ft of RG-8X, station in the spare room, no UPS. The
mast is about twenty-eight feet as of 2026-10-30 (below), so every figure here
is a fifteen-foot measurement and is not comparable to anything taken after
that date. `run/` is tmpfs, so any reboot restarts it — check `uptime -s`
against that timestamp before trusting a sample. Everything below was measured in that one window and
supersedes the 09-27/09-28 pair, which was a different antenna and position and
was never comparable.

**The mast is about twenty-eight feet as of 2026-10-30**, up from fifteen. A
33 ft Spiderbeam telescoping mast with the thinnest top segments left out --
so the figure is the erected height, not the catalog one, and it is
approximate. Nothing else changed: same N9TAX monoband 2 m slim jim, same
coax, same configuration. By the two-ray model 15 -> 28 ft is worth about
**+5.4 dB**, and the radio horizon goes from 5.5 to about 7.5 miles -- but the
ridges here set the limit more often than the horizon does, so the gain should
show on marginal paths rather than on stations already solid.

  **Do not write "doubled" or "thirty feet".** Both were said in passing on
  10-30 before the segments were counted, and both are in this file's history.

  **The figure is weaker than "approximate" as of 2026-11-06.** Two taped
  joints slipped between 10-30 and 11-05, so the erected height inside that
  window was not a constant. The 15-to-28-foot contrast is large enough that
  its direction survives; the number itself should be treated as an upper
  bound until somebody measures the rebuilt mast.

  **November 2026 is the first clean twenty-eight-foot month**, and aprs.fi resets
  its per-station statistics on the 1st. October is therefore the fifteen-foot
  baseline, with three caveats to carry into any comparison:

  * **Oct 30-31 are already at twenty-eight feet** -- two of thirty-one.
  * **Oct 13-18 ran the `BEACON_VIA` two-hop experiment**, about 15% of the
    month's ~1,490 beacons. Two-hop beacons are digipeated further, so more
    stations heard and gated them, which inflates October's "stations which
    heard KD3CCO-10" list and its TX-side packet counts specifically. **This
    biases the aprs.fi month-to-month comparison AGAINST November**: the list
    can shrink while transmit genuinely improved. The window cannot be sliced
    out of the aprs.fi page, and the frame log does not reach back that far
    (see "The log holds about eight days"), so it cannot be corrected for
    either -- **use the measured baseline below instead of the monthly page**
    for anything about reach.
  * **November has holes, but they have known edges, so slice it rather than
    discarding it.** Two mast failures from one cause: a taped joint slipped on
    11-01 and was caught in fifteen minutes, and another slipped on 11-05 and
    took the mast down. Off the air 11-05 22:12 UTC to 11-06 18:00 UTC, back
    up on an angle-iron base stake. Every edge is timestamped to the minute in
    `OPERATIONS.md`, recovered from the dated packet log -- which is the point.
    What the edges do **not** fix is the height inside the 10-30 to 11-05
    window: two joints are known to have let go in it, so the mast was
    somewhere at or below its nominal twenty-eight feet and drifting. **A contaminated window with a known start and end is
    excisable; a contaminated month is not.** Use `OPERATIONS.md`'s
    data-quality table as the exclusion list and compare matched windows
    between the holes. What cannot be sliced is the aprs.fi monthly page,
    which is one more reason to prefer the station's own log to it.

  **Change nothing in the configuration during November.** A change made
  mid-month confounds the comparison. If something is worth changing, change it
  before 11-01 or wait for 12-01. (Written when November was expected to be one
  clean month. It is not, but the rule still holds for a different reason: the
  windows between the mast failures are only comparable to each other if
  nothing else moved.)
  `igate-selftest.timer` was confirmed active on 2026-10-30, so `BEACON_TO`
  stays `RF` -- the condition under which this file calls for `BOTH` does not
  hold.

### Measure height per station, not in aggregate (2026-11-05)

The mast falling on 11-05 ran the experiment the whole 15-to-28-foot exercise
has been trying to run: the same antenna, same radio, same feedline, at 28 feet
and then at ground level, with the change timed to the minute. Direct packets
over matched 2 h 26 m windows either side of it, by station:

| Station | Class | Before | After | p | |
|---|---|---|---|---|---|
| WO3T-1 | fixed, max scatter 0.00 mi | 35 | 0 | 6e-16 | **lost** |
| KE4MHE-10 | fixed, max scatter 0.02 mi | 17 | 0 | 4e-8 | **lost** |
| W3YA-1 | fixed, max scatter 0.00 mi | 11 | 7 | 0.14 | **no detectable change** |
| W3TM-1 | parked all day, max 0.01 mi | 15 | 15 | 0.57 | no change |
| W3TM-10 | fixed; objects faked movement | 5 | 0 | 0.007 | lost |
| W3TM-9 | MOBILE, max 3.98 mi | 11 | 0 | -- | discarded |
| KF0JZM-7 | MOBILE, max 2.22 mi | 2 | 0 | -- | discarded |

Non-moving stations: **78 -> 22 direct packets, -72%.**

**Paths split in two; they do not taper.** Some stations lost everything and
others lost nothing measurable.

  **What this ranks is LINK MARGIN, not terrain.** The first reading of it was
  "terrain -- some paths need height to clear a ridge". That does not survive
  the station list: **W3TM-1 and W3TM-10 are 194 ft apart at Roger's QTH**, and
  W3TM-1 was unchanged (15 -> 15) while W3TM-10 went 5 -> 0. Identical path,
  opposite outcome, because the transmitters differ -- a 50 W Kenwood mobile
  against a CSN iGateMini that beacons mostly over TCPIP and rarely on RF.
  Terrain is one cause of thin margin; the far station's power and antenna are
  others, and this measurement cannot separate them. Do not read the table as a
  terrain map.

**So compare per station, with a control, and the aggregate stops mattering.**

- **Instruments: WO3T-1 and KE4MHE-10.** Both fixed, both went to zero. A mast
  change shows up here or nowhere.
- **Null channel: W3YA-1.** Fixed, and unmoved by the antenna going from 28
  feet to the ground. **If W3YA-1's direct count changes between two windows,
  height is not the explanation** -- look at the radio, feedline, audio levels
  or receiver.
- **Not W3TM-1**, despite its flat 15 -> 15. It is one of Roger's mobiles, and
  it was parked for this event only. A control has to still be there next
  month.

This needs no clean month -- two matched windows at confirmed heights are
enough, which is what makes the November holes survivable.

  **Check mobility first, every time. Classifying by SSID or by eye does not
  work, and two obvious tests do not either.** Three methods were tried on
  2026-11-05 and the first two gave wrong answers:

  | Test | Why it fails |
  |---|---|
  | Position **span** (max minus min) | One bad GPS fix destroys it. Called W3TM-10 a mobile that moved 1.4 mi, on 9 packets |
  | Largest **cluster share** of rounded positions | Bin edges. A fixed GPS station straddling a 3-decimal boundary scores ~32%, indistinguishable from a mobile |
  | **Distance from the modal position** | Works -- but only after objects are excluded |

  The working test: round positions to 3 decimals, take the most common cell
  as the center, and measure each packet's distance from it. Fixed if
  essentially everything is within 0.25 mi. GPS jitter is hundredths of a
  mile; driving is miles.

  **And exclude packets carrying a NAME, because objects and items are not the
  station's own position.** W3TM-10 originates `K3CR-U`, `K3CR-V` and
  `NARC mtg`, which travel under its callsign with the OBJECT's coordinates.
  That made a fixed home iGate score 70.7% with 1.71 mi of apparent movement;
  filtering named packets returns it to 100% and 0.00 mi. Any station that
  originates objects will look mobile without this filter.

  **Scores cluster in three groups, and the middle one is the trap.** Exactly
  100.0% means the position is hard-coded in the config with no GPS at all --
  unambiguously fixed. Intermediate scores mean the station has a GPS, and
  say nothing by themselves. Low scores are real movement.

  **All the log-analysis commands live in README.md, "Reading the dated packet
  log directly"** -- the time-binned profile that dates an event, the matched
  per-station before/after, this mobility test, and the repeater breakdown.
  One copy, so they cannot drift.

  **The control's power scales with window length**, so p = 0.14 on W3YA-1
  means "no detectable change", not "identical". At this sample size a real
  55% loss could hide. A one-day window resolves 17%, an 8-day window 6% --
  which is the right direction for matched October and November windows.

  **Open: the "448 distinct stations heard via somebody's digipeater" figure
  below may not mean what it says.** The 11-05 log contains HI8MSB, TA5FW,
  PY1IP and a dozen more at similar distances, which is not 2 m propagation.
  Third-party traffic that a local iGate transmitted onto RF, the same wrapping
  this station uses for whitelisted messages. `heard_spatial.py` is unaffected
  -- it discards anything beyond 400 km as a busted position -- but a raw count
  of distinct stations is not. Check before quoting it again.

    **The mechanism is confirmed live, 2026-11-06 13:07:43 EST.** A single
    ten-minute watch of the web monitor caught
    `KB2WAC-1>APJI43,...:}KB2WAC-13>APJIO4,TCPIP,KB2WAC-1*:;443.150/R*...`
    -- a third-party wrapper arriving on RF, carrying an inner station and a
    repeater object in central New York. Direwolf unwrapped it and correctly
    declined to gate it ("Do not relay with TCPIP in path"), so the whitelist
    and the relay logic are behaving. What it means for counting is that
    inflation of a distinct-station count is not a hypothesis. Filter on the
    third-party DTI (`}`) when counting stations; confirm what the `dti` column
    actually holds for these on the 11-05 log first, since Direwolf unwraps
    before logging.

    **Three days of summary rows point the same way, and are not a trend.**
    From `/api/status` on 2026-11-06, `rx_stations` ran 240 -> 315 -> 434 over
    11-03, 11-04 and 11-05 while `rx_packets` ran 9,420 -> 11,654 -> 6,791.
    The highest distinct-station count falls on the day with the fewest packets
    and a four-hour hole in it. A count that moves against traffic is not being
    driven by propagation. Three days decides nothing on its own; it agrees
    with the live capture above, which is a different kind of evidence.

    **Do not try to read the 11-05 outage out of daily totals.** Two intact
    days differ by 24% (9,420 against 11,654), and the outage removed about
    17% of a UTC day. The noise is larger than the signal, which is the whole
    reason for matched clock-hour windows.

### The fifteen-foot baseline: 2026-10-22 to 10-30, measured not estimated

Archived off the Pi on 2026-10-30 before the mast went up:
`archive-2026-10/` (gitignored -- it is large and every position in it is a
real station's coordinates). 378,997 lines, 30,247 frames.

**Transmit**, from `./deploy_igate.sh reach`:

| frame type | sent | repeats heard | rate |
|---|---|---|---|
| beacons (`!`) | 394 | 306 | 77% |
| gated messages (`}`) | 50 | 13 | 26% |

  Heard directly by W3TM-10 (182), W3YA-1 (113), K3DNA (15), WO3T-1 (9).
  Relayed on a second hop by WO3T-1 (9), all via the chain `K3DNA then
  WO3T-1` -- the same asymmetry the 10-18 experiment found, on a clean window.

**Receive**, computed from the archived frames: **22 distinct stations heard
directly**, 4,731 direct packets, and 448 distinct stations heard via
somebody's digipeater. Direct, by packet count: WO3T-1 2918, W3YA-1 691,
K3DNA 403, W3TM-10 300, N3DXC-2 113, KB3EWY-9 85, W3TM-9 83, N3SE-9 43,
KF0JZM-7 22, W3AKU-7 16, the rest in single figures.

  **"Direct" means no USED hop before `qAR`** -- no `*` anywhere in the path.
  Testing for a bare `SOURCE>DEST,qAR` path instead counts 6 stations, because
  it throws away everything heard direct that happened to request `WIDE1-1`.

**394 beacons over 8.2 days is 48 a day**, which was the 30-minute interval
exactly, so the window has no outage in it. **`BEACON_EVERY` became `120:00` on
2026-11-07**, so the same check against a later window expects **12 a day**, not
48. A November capture showing ~12 a day is healthy, not an outage.

### An absolute path in igate.conf is readable by the station and invisible to its monitor

`~/aprs-igate` is a **symlink to `/opt/aprs-igate`**. `deploy_igate.sh` run
from the symlink resolves `SCRIPT_DIR` to the `/home` path, so anything it
writes down is a `/home/...` path. `igate-web.service` runs with
`WorkingDirectory=/opt/aprs-igate` and **`ProtectHome=yes`**, so `/home` does
not exist for it at all.

An absolute `/home/igate/aprs-igate/logs` in `igate.conf` is therefore written
to correctly by the station and read as *absent* by its own web monitor. Found
2026-10-30: every logging count on the panel read zero while the dated log was
filling normally, and only `live_log_bytes` was right -- because that one
derives from the script's own directory rather than from the config.

  **`LOG_DIR` and `ARCHIVE_DIR` are RELATIVE** (`logs`, `archive`) and each
  program resolves them against its own install directory, which is the same
  directory from either side of the symlink. An absolute path still works and
  is still a trap; prefer relative for anything both the station and the
  monitor must read.

  The same applies to any future setting naming a path. The test for it is not
  "can I read this at a shell" -- it is "can a unit with `ProtectHome=yes`
  read it".

### Direwolf 1.7 ignores unknown config keywords in silence

**Verified on the station 2026-10-30.** A config file containing
`NOTAREALSETTING 42` drew no complaint of any kind -- not a warning, not a
line number, nothing. Direwolf read the file, reported "Reading config file",
and carried on.

So **a directive appearing in `run/direwolf.conf` is not evidence that Direwolf
honors it.** Anything added to that file has to be confirmed by its effect, not
by the absence of an error. This nearly shipped a `LOGDIR` line that might have
been discarded every day until the day the data was wanted, which is the one
day it could not be recovered.

  **The dated packet log is passed as `-l`**, the documented command-line flag
  (`direwolf -h`: `-l logdir   Directory name for log files.`), built by
  `build_logdir_flag` and spliced into the launch in `cmd_up`. The rendered
  conf is unchanged by the feature -- byte-identical to the one that ran
  before it -- which is also what makes it safe to deploy mid-experiment.

  The same caution applies to `IGMSP`, `IGFILTER`, `IGTXVIA` and `IGTXLIMIT`:
  `PI-SETUP.md` says no complaint about them means the version parsed them.
  That reasoning is wrong for 1.7. They are believed because their EFFECTS
  have been observed, not because the parser stayed quiet.

### The web monitor cannot answer a path question (2026-11-07)

`/api/status` is station state plus log row counts, with no packet-level data.
`/events` holds `HISTORY = 300` events, which at this station's traffic is
about **nine minutes**. Neither can reconstruct a path. The file that carries
full digipeater paths is `run/direwolf.log`, and it is not served over HTTP on
purpose, because it contains the APRS-IS passcode (`igate_web.py:19`). A path
question means either the Pi's own logs or aprs.fi.

**aprs.fi is now usable from the workstation.** The key is in `aprsfi.secrets`,
gitignored, separate from `igate.secrets` by design: `apply_secrets` in
`deploy_igate.sh` reports any key there other than `IGLOGIN_PASSCODE` as a
layer warning, and `build_pi_image.sh` copies that file to the Pi, which has no
use for it. Usage and the limits of the API are in README.md under "The aprs.fi
API, for paths this station never heard". The short version: last position
only, so one path sample per station; `what=msg` filters on `dst` only; ten
entries per query.

**aprs.fi's `path` is one duplicate, not a coverage statement.** A `loc` entry's
`path` comes from the packet at `lasttime` and is whichever copy APRS-IS kept.
On 2026-11-07 it read `W3YA-1,WIDE1,N3DXC-2,WIDE2*,qAS,K3MJW-10` for KD3CCO-7
while this station's own dated log had that handheld copied DIRECT at 09:53:35,
09:54:35 and 10:48:01. Reading that path as "this station did not hear it" is
wrong, and this session made exactly that mistake before checking the log. The
APRS-IS side does answer what the local logs cannot, which is whether another
gateway carried something this station missed, but it takes both sources to
tell that case apart from a discarded duplicate.

**W3YA-1 is the only first hop this handheld gets.** Every digipeated copy of
KD3CCO-7 in the 2026-11-07 window begins `W3YA-1,WIDE1`, with WO3T-1 or
KE4MHE-10 taking the second hop afterward. Those two never heard the handheld
directly, they heard W3YA-1's repeat. Treat second-hop digipeaters in this
log as evidence about W3YA-1's coverage, not the handheld's.

### W3YA-1 repeats the beacons and none of the gated traffic (2026-11-07)

Counted over the text log's window, from echoes of this station's own
transmissions. A path containing `WIDE2` is gated traffic, since
`TX_VIA = WIDE1-1,WIDE2-2`; a path with only `WIDE1*` is a beacon, since
`BEACON_VIA = WIDE1-1`.

| Digipeater | Gated, first hop | Beacons |
|---|---|---|
| WO3T-1 | 26 | 21 |
| KE4MHE-10 | 15 | 19 |
| W3VO-1 | 2 | 1 |
| **W3YA-1** | **0** | **18** |

**This matters because W3YA-1 is the handheld's only first hop.** Every
digipeated copy of KD3CCO-7 in the 2026-11-07 window begins `W3YA-1,WIDE1`.
WO3T-1 and KE4MHE-10 heard W3YA-1's repeat, not the handheld. So the one
digipeater the handheld demonstrably reaches is the one that will not repeat
what this station gates down, and the measured downlink that morning was **4 of
10 messages reaching the radio** against **4 of 4** on the uplink.

**The SMS gateway's retry count is not a delivery signal.** It measures the ack
round trip. On 2026-11-07 two messages (luna, nittany) reached the handheld and
were retried the full three times because no ack came back, and neither
`ack2915` nor `ack2919` appears anywhere in this station's logs. Retries give a
lower bound on failure and nothing more. Delivery was established by reading the
handheld's message list, and that step is not optional: skipping it undercounted
the downlink by half. By site: South Atherton corridor 3 of 4, Elixer Cafe
downtown 0 of 6, Fraser garage fifth floor 1 of 1.

**The handheld's antenna does not explain that split, and the reason is worth
keeping.** Stock rubber duck on every message. In a moving vehicle for the South
Atherton sites and the Fraser garage, **on foot for both Elixer sites** (four
from inside the building, two from the sidewalk). The messages that arrived went
to a rubber duck shielded inside a car; the six that failed went to the same
radio in open air. Taking the handheld out of the vehicle improved its receive
situation and the downlink went to zero, so the limitation sits elsewhere in the
chain. A mag-mount rerun would still be worth doing, to find out whether it
lifts the overall level rather than the ranking.

The three easy explanations are ruled out: the station copies W3YA-1 direct 90
times in the same log, so it is not a hearing problem; 28 gated transmissions
with zero repeats against a beacon rate matching WO3T-1's is not sample size;
and where two digipeaters repeat the same gated packet the log holds both, so
nothing is being suppressed.

**Confirmed in a second capture.** `archive-2026-10/frames-15ft-2026-10-30.log.gz`
holds 306 echoes of this station's beacons, 113 of them W3YA-1, against 13
echoes of its gated traffic, none W3YA-1. Different mast height, same split.

**Two explanations are ruled out. Do not re-propose either.**

*Not the `TCPIP` in the payload.* On the protocol, `TCPIP` is never in the AX.25
digipeater path of an RF frame; a gated message is third-party wrapped so the
string lives in the information field after `:}`, and the address fields a
digipeater acts on read `WIDE1-1,WIDE2-2`. A KPC-3 cannot filter on payload
content. On the measurement, W3YA-1 repeated **39 third-party frames** in the
October day, including `KC3ZLD-10>APDW17,NJ3T-4,W3YA-1,WIDE2*` and
`N3TJJ-13>APMI06,W3YA-1,WIDE1*`.

*Not a three-hop ceiling.* The same archive has
`KG4DQH>APT311,W3YA-1,WIDE1*,WIDE2-2:!4052.49N/07746.52W>234/052`. W3YA-1 took
the first hop of a three-hop request from a mobile tracker.

**What is left.** No single factor survives: W3YA-1 accepts third-party packets,
accepts three-hop paths, and accepts this station's beacons, while refusing this
station's gated traffic, which is the combination of the first two. The leading
candidate is **frame length against a thin margin**. Gated messages are the
longest frames this station sends, roughly twice a beacon; KG4DQH's accepted
three-hop frame is a bare position report; W3YA-1 is the most distant of the
three digipeaters involved. That is a candidate, not a conclusion.

The reproduction commands are in README.md under "The aprs.fi API"; the
per-digipeater third-party tabulation runs against the archive with awk and
needs no Pi access.

**Two tests, neither yet run. Do the first one first.**

1. *Length, on the beacon.* Pad the beacon comment until the frame matches a
   gated message in length, leave `BEACON_VIA = WIDE1-1` alone, run an hour.
   W3YA-1 repeats those beacons reliably now. If the padded ones stop appearing
   while WO3T-1 and KE4MHE-10 keep repeating them, frame length is the variable
   and the fix is on the transmit side rather than in the path. The beacon fires
   on a timer, so it builds a sample without waiting for message traffic, and the
   other two digipeaters are a built-in control.
2. *Hop count, on the gated path.* `TX_VIA = WIDE1-1,WIDE2-1`, then watch for
   `KD3CCO-10>APDW17,W3YA-1,WIDE1*,WIDE2-1`. The surviving `WIDE2-1` keeps the
   echo distinguishable from a beacon echo, which bare `WIDE1-1` would not.
   KG4DQH's frame rules out a hard ceiling, but a rule treating two hops
   differently from three is still possible. The current two-hop reach carries
   gated traffic to Cortland, NY, which is of no use to a handheld four miles
   away.

**Do not assume the handheld can hear W3YA-1.** Digipeating is one-way
evidence. W3YA-1 hears the handheld; the reverse is untested and the whole
proposed fix depends on it.

### What this station's channel share actually is (2026-11-07)

Measured from `archive-2026-10/frames-15ft-2026-10-30.log.gz`, counting
originals (no `*` in the path) per source over the 8.2-day window:

- **29,800 RF frames heard, only 4,785 of them originals.** 84% of what this
  station hears is digipeater repeats of other people's packets. Local
  congestion is set by infrastructure relaying, not by anyone's originals.
- **3,634 frames a day** heard at this location.
- 22 stations heard directly. Median **1.3 a day**, mean 26.5. The median is
  dominated by mobiles heard once or twice in eight days, so it is not a
  useful yardstick; the resident infrastructure is what to compare against:
  WO3T-1 **356/day**, W3YA-1 84, K3DNA 49, W3TM-10 37, N3DXC-2 19.5.

This station's own share, originals plus the repeats its one-hop beacon and
three-hop gated path provoke (0.78 and 0.26 repeats each, from the same
window):

| beacon interval | own TX/day | with repeats | share of frames heard | local percentile |
|---|---|---|---|---|
| 30:00 (through 2026-11-07) | 54 | ~93 | 2.6% | 91st |
| **120:00 (set 2026-11-07)** | **18** | **~29** | **0.8%** | **77th** |
| 240:00 (considered, rejected) | 12 | ~18 | 0.5% | 77th |

**Going to 4 hours was rejected because it changes nothing measurable.** There
is a gap in the local distribution between 10.4 and 19.5 packets a day, so 18
and 12 land on the same percentile. It would cost 0.3 points of channel
occupancy and halve the `reach` sample and the W3YA-1 beacon reference again.
Do not revisit this without new data.

**Caveats on the numbers.** "Originals heard directly" misses the 448 stations
heard only via digipeaters, so the population is stations in direct range,
which is the right one for local courtesy but is not "the region." The window
is also the 15-foot/indoor era; the mast hears more now, which grows the
denominator and makes this station's share smaller than the table says. The
estimate is conservative in the safe direction.

### W3YA-1 has a frame length cliff between 75 and 83 bytes (2026-11-07, cause corrected 2026-11-08)

**Measured, and it answers the question this session opened with.** 30 cycles
of `linktest_sweep.py`, stopped early for channel courtesy, with every frame
identical but for padding length:

| on-air bytes | KE4MHE-10 | W3YA-1 | WO3T-1 |
|---|---|---|---|
| 75 (test) | 2 | **2** | 2 |
| 83 | 1 | **0** | 3 |
| 91 | 2 | **0** | 0 |
| 99 | 2 | **0** | 1 |
| 107 | 1 | **0** | 1 |
| 115 | 4 | **0** | 1 |
| 123 | 2 | **0** | 1 |
| 131 | 1 | **0** | 0 |
| 75 (control) | 19 | **17** | 13 |

Pooled: at 75 bytes W3YA-1 repeated **19 of 34**, 56%. At 83 bytes and above,
**0 of 26**. The probability of 26 straight misses at the 56% control rate is
about 3e-10.

**It does not kill the transmit-chain hypothesis, which the 2026-11-07 version
of this entry claimed.** KE4MHE-10 and WO3T-1 repeated frames at every length
up to 131, which shows the audio, deviation and power are good enough for
those two paths. It does not show the signal is clean, and a length-dependent
transmit defect would surface first at the thinnest-margin digipeater. The behavior is
specific to W3YA-1.

**It is not this station's receiver failing on long echoes either.** A W3YA-1
repeat of an 83-byte frame is 90 bytes, because the digipeater inserts its own
callsign. W3YA-1's own beacons are about 86 bytes bare, 93 with one unused
`WIDE2-2` and 100 after a hop, and this station decodes all three shapes dozens
of times in the same window. The receive path handles 90 to 100 byte frames
from W3YA-1 fine.

**Why the original mystery existed.** The beacon is 75 bytes, just under the
cliff, so W3YA-1 repeats it. A gated message is about 120 bytes, far over, so
W3YA-1 never does. That is the whole of it: not `TCPIP`, not hop count, not
power.

**A configured length limit is ruled out (2026-11-08).** In
`archive-2026-10/frames-15ft-2026-10-30.log.gz`,
counting only frames where W3YA-1 was the first hop, so it heard them off air
directly, it repeated **2,269 frames with a median decoded length of 84 bytes,
1,139 of them over 82 bytes and 600 over 90**. Decoded length is the
retransmitted length less 7 bytes where W3YA-1 inserted its own callsign, since
that address was not in the frame it had to decode.

| station | longest frame repeated | frames over 82 bytes |
|---|---|---|
| NJ3T-4 | **97** | 622 |
| WV8MT-4 | 94 | 22 |
| K3DNA | 91 | 188 |
| K3ARL-6 | 91 | 1 |
| WO3T-1 | 88 | 50 |
| N3KTX-1 | 88 | 14 |
| W3VLG-14 | 87 | 2 |
| N3DXC-2 | 85 | 57 |
| N3SNN-3 | 84 | 396 |
| **KD3CCO-10** | **75** | **0 of 113** |

**The station's own traffic measures the cliff, and the sweep was not needed.**
Same tool, `--mine` block, over the Pi's `archive/frames/frames-2026-11-*.log.gz`:

| on-air bytes | sent | repeated by W3YA-1 | rate |
|---|---|---|---|
| 75 | 493 | 160 | 32% |
| 79 | 10 | 0 | 0% |
| 83 | 4 | 0 | 0% |
| 91 | 63 | 0 | 0% |
| 99 to 150 | 61 | 0 | 0% |

0 of 128 over 82 bytes at the 32% beacon rate is 1e-22, but 105 of the 128 are
the sweep's own frames. **Dropping every sweep length and adding October's 50
gated frames at 91 bytes leaves 0 of 73 ordinary transmissions over 82 bytes at
a pooled 31% beacon rate, 2e-12.** The archive alone settles it, which is the
case for never running the sweep again.

**The matched-length comparison, which is the whole argument in one line.** On
October 30, at exactly 91 bytes on the air, W3YA-1 repeated **96 frames from
K3DNA and 1 from K3ARL-6, and 0 of the 50 KD3CCO-10 sent**. Same digipeater,
same day, same length.

**Height is not the knob.** October ran on the 15 ft mount, November with the
mast up. W3YA-1's own behavior is identical, median 84 bytes and max 97 in both,
and so is the outcome: 178 frames over 82 bytes from this station across the two
captures, 0 repeated.

**The 79-byte row is the open edge.** 0 of 10 is p = 0.02, which puts the cliff
below 83 rather than at it. Do not write that down as established; 10 frames do
not settle a rate.

**The gated length is 91 to 150 bytes, not "about 120".** It varies with the
message text. The selftest frame is 91 bytes, which is why 91 carries the most
ordinary-traffic evidence.

`digi_length_profile.py` recomputes all of this from any Direwolf text log, with
`--digi` naming the digipeater and `--mine` the own callsign that is always
reported. It reads `archive/frames/frames-*.log.gz` or `run/direwolf.log`, not
`LOG_DIR`, whose dated CSV carries no digipeater path.

So the cliff is a property of this station's signal at W3YA-1, not of W3YA-1's
configuration. The 9 stations that get long frames through are fixed digipeater
sites, which is what a margin explanation predicts.

**W3YA-1 is a Kantronics KPC-3, and K3DNA is the matched control (2026-11-08).**
Both send with tocall `APN391`; the aprs.org tocall registry maps `APN3xx` to
Kantronics KPC-3 ROM versions, and aprs.fi lists W3YA-1's device as a KPC-3. An
identical tocall means the same model and ROM. **K3DNA repeated 12 of the same
50 gated 91-byte frames that W3YA-1 took 0 of**, so identical hardware gave the
opposite result on the same day from the same transmitter. A device explanation
is therefore dead along with a configuration one.

**The KPC-3 cannot do what a per-type or per-length rule would need.** Its UI
digipeat parameters are `DIGIPEAT`, `MYCALL`, `MYALIAS`, `UIDIGI`, `UIFLOOD`,
`UITRACE` and `UIDWAIT`, all matching on a call sign or alias in the AX.25
address field. `PACLEN` (0 to 255) cuts data arriving from the serial port and
does not touch repeated frames. `BUDLIST`/`SUPLIST` select terminal monitoring
only. Nothing reads the information field, so the TNC cannot know a frame's APRS
data type, and nothing rejects a frame by length. Confirmed in the KPC-3 Plus
v8.2 user guide. The data agree: on 2026-10-30 W3YA-1 repeated frames over 82
bytes in **11 APRS data types**, including 320 status reports to 96 bytes (the
sweep's type) and 24 third-party frames to 90 bytes (a gated message's type).

**Three surviving explanations, all of them unreachable from this end:** receive
audio level into that TNC; site noise and co-channel traffic at a 2065 ft ridge;
and a marginal signal from this station at that receiver, since 29% of 75-byte
beacons in October and 32% in November is poor in absolute terms for 5.4 miles
to a mountaintop.

**A transmit fault is not a candidate, structurally.** Anything wrong at this
end reaches every receiver identically, so it cannot single out one digipeater.
A corruption-type fault would also cost every receiver the same frames, and it
does not: in the sweep KE4MHE-10 went 21 of 34 at 75 bytes to 13 of 26 at 83
through 131 (62% to 50%, not a real difference at that sample), WO3T-1 15 of 34
to 7 of 26 (44% to 27%, in the range frame duration predicts from collisions).
**Deviation was already measured at 2.8 kHz on 2026-10-03** (see the Carson's
rule entry above); do not write that it is unmeasured. Raising power remains
worth doing as a margin test, not as a fault test.

**Height insensitivity is geometry, not evidence about margin (2026-11-08).**
This was got wrong once in the same session and then corrected, so do not
re-derive it. W3YA-1's pickup barely moves with this station's antenna height:
29% at 15 ft, 32% at 28 ft, and 11 to 7 across the mast fall while WO3T-1 and
KE4MHE-10 both went to zero. The tempting reading is "the link is not
margin-limited". **It does not follow.** Height buys clearance, the path to a
mountaintop is already clear line of sight, and a ridge station can still see
an antenna lying on the ground where valley stations cannot. The observation
says W3YA-1 has the most robust geometry of any digipeater here, and nothing
about its margin.

**The handheld is the matched transmitter, and it gets through (2026-11-08).**
W3YA-1 repeats KD3CCO-7 at 68 and 77 bytes on air, from downtown, on a rubber
duck, and the Nov 7 run found every digipeated copy of the handheld beginning
`W3YA-1,WIDE1`. So W3YA-1 is not failing on frames of that length, only on
frames from the igate above it. **The handheld never transmits above 79 bytes
on air**, its longest being "angelos 2", so it has never been tested above the
cliff and cannot locate it.

**What the two transmitters differ in is the modulation chain.** The handheld
modulates from the FT5D's own modem; the igate modulates from Direwolf through
the Digirig into the radio's audio input. If a level-independent defect exists,
frequency offset, deviation asymmetry, clipping or sound-card bit timing, that
chain is the only place left for it. An occupied-bandwidth deviation check
cannot see any of those, which is why the 2.8 kHz measurement does not close
it.

**The selftest ack loop already measures the 91-byte path, and it works.**
aprs.fi's 2-day raw window held 8 selftests and 4 acks from KD3CCO-7, which is
4 of 5 in daylight hours with the overnight ones unanswered. The handheld
decodes the igate's 91-byte gated frames at the house. That rules out frame
corruption and nothing else: at that range it has enormous margin.

**W3SWL-2 is the best unused instrument.** A Direwolf igate at FN10BS, online
since 2026-10-31, which heard KD3CCO-10 242 times in 2026-11, more than it
heard any other station. Direwolf logs which slicer decoded each frame and
whether bit-error correction was needed. **Asking its operator for that is the
cheapest remaining test of this station's signal quality**, and it needs no
transmissions. For scale on the decoder asymmetry: this station's own receiver
runs at least six slicers, visible as `[0.4]` and `[0.5]` in the log, against
W3YA-1's single hardware DPLL with no error correction.

**Per-digipeater beacon rates are not clean margin proxies.** W3TM-10 takes 46%
of this station's beacons and W3YA-1 29%, which looks like a ranking and is
not one. A one-hop beacon echo is only logged if this station hears that
digipeater directly, while a two-hop gated frame gets a second chance to be
seen through the next hop, so the denominators differ per digipeater. Use them
for existence, not for ordering.

**Published distances come from the beaconed position, not the antenna.** The
beacon reports the center of its grid square by design, so every distance in
the articles is measured from there and is deliberately coarse. If one looks
slightly off against a map, that is the rounding and not an error. **Do not
recompute these from the true antenna location, and do not put that location,
or any distance or bearing derived from it, into this repository or an
article.** Two precise distances to known stations locate a house.

**The sharpness needs no separate explanation now.** Fitting an independent
per-bit error rate to 56% at 75 bytes predicts 53% at 83 bytes, so 0 of 26 is
not a simple bit-error-rate effect (p ~ 3e-9). Independent bit errors are the
wrong model for a hardware modem that recovers its bit clock in a DPLL and
corrects nothing.

**Practical consequence: W3YA-1 is unavailable for message downlink at the
power and antenna of that run.** "No setting on this end changes that" was the
2026-11-07 wording and does not survive the archive check above: if the cause
is margin, power, antenna and transmit audio are all in play, and the HIGH
power arm is still unmeasured. A third-party wrapper alone is about 41
bytes, so even a one-character gated message runs about 75 bytes with a one-hop
path and 82 with two. Shortening `TX_VIA` buys 7 bytes, nowhere near enough for
real message text. The downlink has to rely on WO3T-1, KE4MHE-10 and direct
copy, none of which the handheld reached from downtown.

**The free confirmation, if ever wanted:** grep the text log for
`[ig>tx] KD3CCO-10>APDW17,W3YA-1` with an `LT ` marker. If some other gateway
had heard W3YA-1 repeating a long test frame, it would have come back from
APRS-IS. That check does not depend on this station's receiver at all.

**The per-length verdict in both scripts is too conservative for a short run.**
It needs 6 informative cycles per length and got 1 to 4, so it reported
"undecided" everywhere while the pooled numbers were already decisive. Both
scripts now print a pooled block; read that first.

### The HIGH arm is measured and the 79-byte edge is closed (2026-11-14)

**The HIGH power arm the 2026-11-08 entry asked for is done, and it is a null.**
The radio is an FT-2900R and the two settings are Low3 and High, **30 W and
75 W, a 4.0 dB step**, not 5 W and 75 W. Above the cliff, meaning 86 on-air bytes and up:
0 of 24 at 30 W and 0 of 18 at 75 W. Below it: 15 of 29 at 30 W against 3 of 12
at 75 W, one-tailed
Fisher p = 0.11, which is no change in either direction. High is the radio's
maximum, so no larger power step exists at this station. This sits beside the
8 dB step in 2026-09 that also bought nothing measurable.

**The 79-byte edge is closed, and it went the other way.** The old reading was
0 of 10 at 79 bytes, flagged here as not established. New numbers, all on-air:
**81 bytes 4 of 9, 82 bytes 6 of 15, 86 bytes 0 of 12.** 81 and 82 are
repeated, so the cliff sits **between 82 and 86 on-air bytes**, not below 83.
The old 0 of 10 at 79 was a fluke and the warning against writing it down was
right.

**New control: W3YA-1 answers its own callsign.** Every earlier run used
`WIDE1-1`. With `TX_VIA = W3YA-1`, frames below the threshold were repeated
16 of 35. That removes an alias-only digipeat configuration, which nothing had
tested.

**New control: the information field is not what is counted.** Holding the
information field at exactly 55 bytes and changing only the digipeater count,
which costs 7 bytes of address field and nothing in the field itself:

| Information field | Digipeaters | On-air bytes | Repeated |
|---|---|---|---|
| 55 | 1 | 82 | 4 of 9 |
| 55 | 2 | 89 | 0 of 10 |

Same message, same message length, 7 more header bytes, and it stops. So the
whole frame is the variable. At 1200 baud that is also airtime, and the two
cannot be separated from this end.

**Every figure in this entry is an on-air length**, the arithmetic
`digi_length_profile.py` and `linktest_sweep.py` already use: 7 per address,
control, PID, information, flag, FCS, flag. `w3ya_length_sweep.py` counted
2 bytes less until 2026-11-14, so any figure taken off that tool before then is
low by 2 and does not line up with the tables above.

**Do not ask W3YA-1's manager about `PACLEN`.** This session drafted that email
before reading this file, and it is wrong twice over for reasons already
recorded above: `PACLEN` cuts data arriving from the serial port and does not
touch repeated frames, and W3YA-1 demonstrably repeats 84 to 97 byte frames
from nine other stations, including K3DNA which is another KPC-3 on the same
`APN391` tocall and took 12 of the same 50 frames W3YA-1 took 0 of. A
configuration or device cause is dead. The three surviving explanations are the
ones listed above: receive audio into that TNC, site noise at the ridge, and
this station's margin at that receiver.

**The sweep was re-run against the instruction below, and cost about 141 frames
over three hours.** It produced the three controls above, which the archive
cannot produce because the archive holds no callsign-path frames and no
matched-information-field pairs. It produced nothing about the cause. The
instruction stands: read this file before transmitting.

### Direwolf's -T timestamp broke twelve log-parsing sites (regression 2026-11-08, found 2026-11-14)

Commit `6073541` added `-T "%Y-%m-%dT%H:%M:%S%z"`, which puts the timestamp
**inside the existing bracket**: `[0L]` became `[0L 2026-11-14T04:31:52-0500]`.
Everything anchored on `^\[0L\]` or `^\[0\.[0-9]+\]` stopped matching that
day, and so did every `sed 's/:.*//'`, which then truncated at the timestamp's
own colon and left no path to parse.

What was silently dead for six days:

- `cmd_selftest` step 3 reported `FAIL at step 3: the gateway had the message
  and did not transmit it` every four hours, while the log showed the `[0L]`
  transmission one second after each report.
- `cmd_selftest` step 4 never fired, so the echo proof was unavailable.
- `_selftest_repeats` returned no repeater attribution at all.
- `cmd_monitor` printed no `IS GATED`, `TX LOCAL` or `RF RX` lines, which is
  the tool the step 3 failure message tells the operator to use.
- `cmd_reach`'s floor was zero.
- `heard_spatial.py` parsed zero RX frames from any console log after 11-08.

Fixed in `deploy_igate.sh` (patterns given `[^]]*`; the three attribution
`sed`s given a leading-bracket strip; monitor's `[0L]` branch given a
`sub()`-based body extraction in place of `substr($0, 6)`) and in
`heard_spatial.py`'s `RX_FRAME`. **`test_archive.sh`'s fixtures still emit the
pre-`-T` format, which is why the test suite passed throughout.** Fix the
fixtures or the next log format change does this again.

Two more faults surfaced once steps 3 and 4 fired again:

- **Step 5's ack match had no boundary.** `ack${msgid}` with msgid 36 matched a
  genuine `ack3686` from twelve minutes earlier and reported a delivery that
  never happened. Now `ack${msgid}([^0-9]|$)`.
- **Step 4 printed the starred path entry**, which is usually a routing alias
  after callsign substitution, so it credited `WIDE1` and `WIDE2` with repeats
  that KE4MHE-10 and WO3T-1 made. Now skips aliases the way
  `_selftest_repeats` and `cmd_reach` do.

**None of it is deployed or verified.** The fixes are in the working tree with
`bash -n` clean and nothing more. Deploying `deploy_igate.sh` to the Pi and
running one `selftest` is the verification, and it has not been done.

### The downlink has at least five transmitting gateways, not one (2026-11-14)

The README's downlink section says "there is no redundancy here" and "only this
one has these callsigns whitelisted". Both are wrong. Frames addressed to
KD3CCO-7 were observed being gated to RF independently by **KB2WAC-1, N3TJJ-7,
N3DXC-2 and N3TJJ-13**, each identifiable by its own callsign before the `*`
inside the third-party header. Whitelisting is not what another igate needs:
having heard the addressee is enough.

Also wrong in that section: condition 1, that the gateway "must have heard the
addressed station on RF recently". This station gated messages to KD3CCO-7
through six days of that radio being switched off, and gated test frames to
KD3CCO-9, an SSID it has never heard at all.

### The length sweep exists, was paused for channel courtesy (2026-11-07)

`linktest_sweep.py` and `linktest_report.py` are built, self-tested and proven
to transmit (`[0L] KD3CCO-10>APDW17,WIDE1-1:>LT C001 b075` confirmed in the
log). The full sweep was **stopped early on purpose**: at 4 frames a minute it
adds real congestion to 144.390, and that cost is not worth paying for a
question the station's own traffic answers outright. **Do not run it again for
this question.** `digi_length_profile.py` over the archive gives a larger sample
at zero channel cost, which is recorded above. If some later question does need
it, prefer `--cycle-seconds 300` and two or three lengths over the defaults.

**The smoke test's one data point:** a 75-byte control frame was repeated by
W3YA-1, WO3T-1 **and** KE4MHE-10; the 131-byte test frame by none of them. One
sample each, so it is a hint, not a result. The archive has since confirmed the
shape it suggested: every digipeater has its own length cliff set by its own
margin to this station, W3YA-1's falling below the gated length and the other
two above it, and the cause is on this station's side rather than in W3YA-1's
configuration.

**The LOW3 arm is already measured, for free.** The November archive holds 128
transmissions over 82 bytes with zero W3YA-1 echoes against 160 of 493 beacons
repeated, all at the power the radio was already set to. So only the HIGH arm is missing, and
`./deploy_igate.sh selftest` produces exactly one gated RF frame at the real
length per invocation. Ten or fifteen of those at HIGH is the whole remaining
experiment, against 28 already banked at LOW3, and it adds about a dozen frames
rather than hundreds. Raising power costs no extra airtime: a 120-byte frame
occupies the same time slot at 5 W or 75 W.

**Confirm the power setting did not change during the November window** before
treating those 28 as a clean arm. There is no CAT port, so nothing in the
software can read or log it, which is the whole weakness of the method.

**`radios/digirig.conf` carries an older power note that is stale context, not
a current finding.** It recorded W3YA-1 repeating 2 of 8 beacons and concluded
"power is not the knob; antenna height is." That was measured on an **indoor
antenna**, and W3YA-1 repeated 18 beacons in the November window on the mast,
so the relationship changed. The note's metric was also insensitive: success
rate at one frame length cannot see a cliff move. Do not cite it as settling
the power question.

### Elevation is not why the downlink failed (2026-11-07)

Checked because it is the obvious next idea, and it does not hold. Every test
site that morning sits inside a 93 ft band of ground elevation and the station
is above all of them. The **highest** site delivered, the second highest failed
all six times, and the lowest failed too. Distance does not separate them
either: the farthest site delivered and nearer ones both delivered and failed. The table is in the blog article; it is not repeated here
because it would put the station's position back into this repository by
arithmetic.

**The argument that settles it:** the handheld's own message from inside that
cafe was copied **direct** by this station at 09:54:35, street level, no
digipeater. A path blocked by terrain is blocked both ways. The path exists, so
elevation is not the variable.

**Mobility is ruled out too.** The tempting explanation was that a moving car
samples new ground between retries while a stationary operator retries the same
spot. The Fraser garage message was sent to a **parked** car on the fifth floor,
went out once, and was acknowledged in 7 seconds. A stationary receiver did
fine.

**What is still open.** All six failures came from one address, so they are six
samples of one place, not six samples of a condition. Elixer differs from the
working sites in two surviving ways: on foot rather than in a vehicle, and
ground level in a downtown block. **Only one of those points the right way.** A
rubber duck in a car body sitting in the car's noise is a worse receive setup
than the same antenna in open air, so if being out of the vehicle were the cause
the on-foot site should have done better, not gone 0 of 6. Height is the
survivor that explains the direction.

The separating test: three messages from the Fraser garage ground floor against
three from its fifth floor, **parked in the car both times**, which holds
location, posture, mobility and the antenna's surroundings fixed and moves only
height. Note the condition to match is in-vehicle, not on-foot, because
in-vehicle is the one already known to work.

### Another gateway carried a message this station never heard (2026-11-07)

The SMS gateway acknowledged handheld message `{17}`, which appears nowhere in
this station's logs in any form. Positive proof that the local logs are a
partial view, alongside the Cortland echo in the next section, which arrived
only because APRS-IS handed it back.

### A corrupted echo defeated third-party loop detection (2026-11-07)

One gated message came back around. KD3CCO-10's own RF transmission was
digipeated by KE4MHE-10, then N3TJJ-7 and KC2ANT-1, gated back to APRS-IS by
KB2LUV-1, and arrived at this station as `[ig>tx]`, which re-transmitted it
wrapped in a second layer of third-party encapsulation:

```
[ig>tx] KD3CCO-10>APDW17,KE4MHE-10,WIDE1,N3TJJ-7,KC2ANT-1,WIDE2*,qAR,KB2LUV-1:}SMS>APOSMS,TCPYP,KD3CCO-1p*::KD3CCO-7 :...
[0L] KD3CCO-10>APDW17,WIDE1-1,WIDE2-2:}KD3CCO-10>APDW17,TCPIP,KD3CCO-10*:}SMS>APOSMS,TCPYP,KD3CCO-1p*::KD3CCO-7 :...
```

Note `TCPYP` for `TCPIP` and `KD3CCO-1p*` for `KD3CCO-10*`. Loop detection
drops a third-party packet whose inner path carries this station's own
callsign, and RF bit errors corrupted that callsign past recognition, so the
check passed it. The `g/KD3CCO*` whitelist term then matched the outer source,
which is KD3CCO-10 itself.

**Observed once**, so this is a recorded mechanism rather than a measured rate.
It self-limited here. Each pass adds an encapsulation layer, so a sustained
loop would be visible as growing `}` nesting in `[0L]` lines:

```
grep -c '}.*}.*}' /opt/aprs-igate/run/direwolf.log
```


**Retry count reads as an ack signal.** The SMS gateway resends until acked, so
a message appearing once in `what=msg` was acknowledged and one appearing six
times was not. Note that SMS-gateway message bodies carry phone numbers;
redact before writing output anywhere.

### Frames in the archive are chunks, not days (2026-11-08)

**`archive/frames/frames-YYYY-MM-DD-<hash>.log.gz` is named for the day the
archiver first saw the chunk, not the day it covers.** `igate-archive.sh`
copies logrotate generations deduplicated by content; its own comment says
"whatever day it was first seen on". A chunk is 8 MB of text log, which at this
station takes about a day and a half to fill, so five files named 11-01 to
11-08 covered roughly 10-30 to 11-08. **A day with no file is a day no chunk
finished, not a day with no data.** 11-03 and 11-07 have no file and are
ordinary days of 9,420 and 9,047 packets.

**For anything per-day, use `logs/YYYY-MM-DD.log`**, Direwolf's dated CSV,
named with `gmtime_r` and therefore genuinely one UTC day. It has no
digipeater path column, so it cannot do length work, but it is the right
record for outages and daily counts:

```
cd ~/aprs-igate && for f in logs/*.log; do printf '%s  %6d rows\n' \
  "$(basename "$f" .log)" "$(( $(wc -l < "$f") - 1 ))"; done
```

**Transmit lines are logged whether or not RF left the antenna.** Direwolf
prints `[0L]` for a queued frame with the radio off or the antenna down, so
frames from an outage inflate the denominator of any rate computed over that
window. **2026-11-06 is the known outage**, 1,913 packets against a 9,047
median. Exclude it with `digi_length_profile.py --since/--until` rather than
leaving it in: it moves W3YA-1's beacon rate from 32% to about 36%.

**The clean rate is 55%, not 32%, and the 32% is not simply the 55% with the
outages left in (2026-11-13).** W3YA-1 repeats **55%** of this station's 75-byte
beacons over the clean stretch, which agrees with the **56%** the sweep's own
controls measured in a 15-minute window with the mast up. Two independent
measurements in the same era landing within a point of each other is what makes
55% the figure to quote. **29%, 32% and 36% are superseded and should not be
used**, including where they still appear in the entries above and in
`aprs-downlink-paths-2026-11.md`, which has 29% and 32% in its "what is left"
section. The direction matters: a higher control rate makes the long-frame
absence *more* improbable, not less. Against 55%, the 23 consecutive misses on
ordinary (non-sweep) frames over 82 bytes come out at about 8.5e-9, roughly one
in a hundred million.

**Do not describe the correction as "the month minus the radio-off windows." The
arithmetic rules that out.** The recorded base is 160 repeats of 493 beacons,
32.5%. November's four no-RF windows total about 23 h, which is roughly 47
queued `[0L]` beacons at one beacon per 30 minutes, and removing only those
gives 160/446 = **35.9%**. Reaching 55% with the numerator fixed needs the
denominator down to 291, so about **202 beacons excluded, 41% of the month**.
That can only come from also dropping the 2026-10-30 to 11-05 stretch, where the
mast was slipping and then on the ground and beacons went out from a degraded
antenna. **So 55% is the rate over the post-rebuild era, not the whole month with
holes punched out**, which is also why it matches a sweep control taken in that
same era.

**This composition is inferred from the counts, not read off the run that
produced 55%.** That run came from a pasted Pi session and its exact `--since`
bounds are not recorded here. **Re-derive it from
`archive/frames/frames-2026-11-*.log.gz` before quoting 55% in anything new**,
and write the bounds down when doing so.

**The four windows with no RF path** are 2026-11-01 14:30-14:45 UTC (15 m),
2026-11-05 19:46-22:12 UTC (mast on the ground, 2 h 26 m), 2026-11-05 22:12 to
2026-11-06 18:00 UTC (19 h 48 m), and 2026-11-13 17:31-18:23 UTC (52 m, the
stop-collar fitting). The full table with the reasons is `OPERATIONS.md`,
"Data-quality windows at a glance". **Size the correction to the hole**: the
19 h 48 m one is worth excising and the 52 m one puts about 2 phantom beacons in
and is not.

**Two traps in the exclusion itself.** `--since/--until` *selects* a window
rather than excluding one, so passing an outage's own edges keeps the outage and
discards the month; a hole in the middle of a UTC day therefore needs two runs
pooled by hand, or the whole UTC day dropped. And `parse_bound` accepts only
`%Y-%m-%d`, `%Y-%m-%dT%H:%M:%S` and `%Y-%m-%dT%H:%M:%S%z`, so a bound written
without seconds is rejected rather than rounded.

**Since 2026-11-08 the text log is timestamped.** Both direwolf invocations,
`deploy_igate.sh` bare-metal and `entrypoint.sh` container, pass
`-T "%Y-%m-%dT%H:%M:%S%z"`. Direwolf puts the stamp **inside** the existing
bracket, on received and transmitted frames alike: `[0.3 2026-11-08T21:56:02-0500]`
and `[0L 2026-11-08T21:56:02-0500]`. It uses `localtime_r` while the daily CSV
uses `gmtime_r`, which is why `%z` is not optional. **Any new parser of this
log must accept the timestamp**; `digi_length_profile.py` and
`linktest_report.py` were updated in the same commit and still read older
undated logs, reporting how many lines were undated.

**`rotate 2` plus a once-daily archiver tolerates at most two new chunks per
day.** Safe at current traffic, where a chunk takes a day and a half. If
traffic triples, chunks will age out before the archiver sees them.

### The log holds about eight days, not the uptime

`uptime -s` was 2026-10-12 and the log reached back only to 10-22. `run/` is a
32 MB tmpfs rotated hourly at 8 MB with two generations, so **at this
station's traffic the readable history is roughly eight days** however long
the station has been up. `reach` and any retrospective question are bounded by
that, not by the uptime.

  **This makes the November comparison easy rather than hard, with one
  caveat added 2026-11-07.** Run `reach`, `config` and the frame-log archive
  again on 2026-11-30 and compare against the table above: Nov 22-30 against
  Oct 22-30 is the same days of the month, the same ~8-day length and the same
  one-hop `BEACON_VIA`. **The mast is no longer the only difference:**
  `BEACON_EVERY` went from `30:00` to `120:00` on 2026-11-07 to cut this
  station's channel footprint, so an 8-day November window holds roughly 98
  beacons where October held 394. **Compare the rates, never the counts.** The
  table above is already in rates for exactly this reason, and quartering n
  widens the 95% interval on the 77% figure from about +/-4 to +/-8 points. The
  gated-message row is unaffected, because it comes from the four-hourly
  selftest timer and gated traffic, not the beacon timer.

  **Transmit-fault detection does not depend on the beacon.**
  `igate-selftest.timer` fires every 4 hours and is the thing that catches a
  dead transmitter, so a longer beacon interval costs measurement precision
  rather than safety. Commands are in "Commands";
  the archive one-liner is in the 10-30 session.

  **The Oct 13-18 two-hop experiment is NOT in this window** -- zero two-hop
  beacons in the archived log -- so none of the figures above need correcting
  for it. The caveat still applies to **aprs.fi's monthly page**, which covers
  all of October and cannot be sliced by date; its October TX-side figures are
  inflated by five days of two-hop beacons. Prefer the table above to the
  monthly page for anything about reach.

**The whitelist, over eight days: APRS-IS offered 73,066 packets. 95 messages
reached the air and every one was addressed to a whitelisted call** — 85 to
KD3CCO-7, 7 to KD3CCO-10, 3 to KD3CCO-1. Plus 392 beacons, which is 47.8 a day
and confirms the 30-minute interval. Nothing else was transmitted.

  **95 is not operator traffic and should not be quoted as though it were.**
  Roughly 49 of it is the self-test, which fired 49 times in that window and
  transmits one message per run; 3 are acknowledgements that went up to APRS-IS
  and came back down addressed to the self-test's own sender, which matches
  `KD3CCO*` and so was legitimately re-transmitted; 7 are the same round trip
  aimed at the gateway. The balance is hand-sent phone-to-radio testing. What
  the figure measures is the *gate*, not demand on it.

  Measure it on the addressee, never on the whole line. Dire Wolf gates IS→RF
  in APRS third-party format, re-originated under this station's own callsign,
  so `KD3CCO-10` appears in every transmitted line by construction and
  `grep -v` against the whitelist can never match. A leak shows up only in the
  `::ADDRESSEE` field:

      grep -E '^\[0L\] ' "$log" | grep -oE '::[A-Z0-9-]{1,9}' | sort | uniq -c

**Reach: 483 transmissions, 194 repeats heard.** Between 141 and 194 of those
transmissions came back off a digipeater — **29% to 40%**. It is a range and not
a figure because repeats are counted as log lines: one frame repeated by two
digipeaters is two lines. Any single path element appears at most once per
frame, so the busiest one is a floor and the total is a ceiling; the beacons are
byte-identical and APRS carries no sequence number, so the log cannot narrow it.
`reach` prints both bounds. **Do not quote the ratio alone as "the fraction of
transmissions repeated"** — that is the ceiling.

  W3YA-1 123, W3TM-10 53, N3SNN-3 10, K3DNA 8 heard this station directly, and
  WO3T-1 relayed 7 on a second hop via K3DNA.

**`TX_VIA`'s documented rationale is now backwards.** It says the near
digipeater W3TM-10 hears this station far more often than the mountain-top
W3YA-1, so a second slot is what reaches the mountain. On this antenna and
position it is the reverse — W3YA-1 123 against W3TM-10 53. `WIDE1-1,WIDE2-2`
may still be the right setting, but not for that reason, and the reason has not
been re-established.

**The self-test's witness is other people's digipeaters, not a radio of ours
(2026-10-12).** The addressee here is a mobile radio, on only when the car is,
and with it off every run failed at the final step: 46 of 49 unattended runs
over the eight days, each accurate and none useful. A test that cries wolf five
times a day is how a real alarm gets missed.

Steps 1-3 were never the problem — they passed 49 times out of 49. Step 4 now
takes evidence in three tiers: an acknowledgement from `SELFTEST_TO` if one
comes, else a digipeated copy of the test frame itself, else any repeat of this
station within the last `SELFTEST_REPEAT_FRAMES` **beacons**. The fallback is
what makes it work unattended: at a 29-40% repeat rate a single test frame goes
unrepeated most of the time, so a bare "was this frame repeated" check would
fail more often than the acknowledgement test it replaced.

  **Beacons, not transmissions, and the difference is a false-alarm mode.**
  Beacons are the instrument -- fixed interval, short, repeated about a quarter
  of the time. Gated messages are what is being measured and come back far less
  often (below). Counting them as evidence dilutes it: twenty self-tests in
  forty minutes on 2026-10-13 pushed the beacons out of a
  sixteen-TRANSMISSION window and walked the repeat count down 3, 3, 2, 1. It
  bottomed out at 1, so nothing failed; at 0 the self-test would have reported
  "nothing is repeating this station" during a run where the transmitter was
  demonstrably fine.

  Beacons, not hours, because the log has no per-line timestamps and a time
  window holds a different number of beacons depending on traffic. At p = 0.40,
  P(none of 16 came back) = 0.028%, about one false alarm every three years at
  six checks a day. Verified on the Pi: PASS in 43 s with the addressee off
  (2026-10-12), and after the beacon-window fix `5 repeats heard over the last
  16 beacons / by W3YA-1(4) W3TM-10(1)` (2026-10-13).

  **A used hop is an asterisk in the PATH, which is everything before the first
  **The whitelist matches a message's addressee, not its source (2026-11-08).**
  `build_filter()` renders `WHITELIST_CALLS` as the APRS-IS `g/`
  group-message filter, and `g/` is addressee-matched. The 2026-10 loop packet
  was addressed to `KD3CCO-7`, which is on the whitelist and has to be, so
  **narrowing the whitelist to explicit SSIDs would not have stopped it** and
  the whitelist is not the control that failed. The loop check is. Two guards
  would close it, neither implemented: the returning packet's outer source was
  `KD3CCO-10` and uncorrupted, and this station never needs to retransmit a
  frame it sourced, so `FILTER IG 0 ! b/KD3CCO-10` rejects it on a field the
  bit errors did not touch; and a gated message carries one layer of
  encapsulation, so refusing two is a depth check no corrupted callsign
  defeats.

  colon.** A third-party frame's payload contains `TCPIP,KD3CCO-10*`, which a
  whole-line search cannot tell from a used hop — so the gateway's own callsign
  appeared in a list of digipeaters that had repeated it. The patterns are
  `^\[0\.N\] MYCALL>[^:]*\*[^:]*:` and extraction strips at the first colon.
  `reach` and the self-test both shipped the whole-line version; it never showed
  until gated frames were counted separately, because beacons have no decoy in
  their payload.

  Attribution must also skip routing aliases. A digipeater consuming a WIDE1-1
  hop may insert its own callsign and mark the ALIAS used, so the asterisk sits
  on `WIDE1` and the station is elsewhere in the path. Both `reach` and the
  self-test have shipped a version that printed `WIDE1` as though it were a
  station. The `floor` in `reach` is the deliberate exception: it counts every
  used path element, aliases included, because any one element appears at most
  once per frame and that makes a tighter bound.

**Gated messages are repeated far less often than beacons, and PATH LENGTH IS
NOT THE CAUSE (settled 2026-10-18).** Measured on this station over 22,587
frames of log, with beacons and messages requesting the SAME two-hop path:

| frame type | sent | repeats heard | rate |
|---|---|---|---|
| beacons (`!`) | 278 | 163 | 58% |
| gated messages (`}`) | 78 | 8 | 10% |

A 48-point gap at 10.8 standard errors. The experiment below matched the
paths so the two differ in nothing but frame type and length; the beacon
rate did not fall toward the gated rate, so **the hop count is eliminated**
and third-party format is not innocent.

  **The experiment is CLOSED and `BEACON_VIA` is back to `WIDE1-1`
  (2026-10-18).** It ran 2026-10-13 22:00 to 2026-10-18, and separately
  answered its own secondary question: two-hop beacons were repeated 131 of
  223 (58.7%) against one-hop 32 of 53 (60.4%) -- a difference of 1.6 points
  with a standard error of 7.5, which is nothing. **The second hop does not
  help beacons**, and at ~1,400 beacons a month it is a real cost to a
  shared channel.

  **But the second hop IS what delivers messages, and `TX_VIA` must stay at
  two hops.** Of the 8 gated messages repeated, 5 were carried by `WO3T-1` --
  which never hears this station directly. `reach` places it only under
  "relayed onward on a second hop", 40 times, dominated by the chain
  `K3DNA then WO3T-1` (25). The downlink that works is a second-hop
  delivery. This asymmetry -- the second hop useless for beacons and load
  bearing for messages -- is the most useful thing the experiment produced.

  **Two explanations remain and the data cannot separate them**: third-party
  filtering (digipeaters dropping `}` frames, which is a reasonable thing for
  an operator to configure, since internet-sourced frames put back on RF are
  how APRS loops form) and frame length (a gated frame is ~30% longer, and at
  1200 baud longer frames fail more). Do not write up either as the cause.

  **`W3TM-10` is not the lever it looks like.** It is this station's best
  direct listener by a distance -- 114 direct receptions -- and it repeated 0
  of 78 gated messages, which reads as filtering. **It runs about 1 watt.**
  At that power it would not deliver a message to a mobile HT several miles
  out whether it repeated one or not, so persuading its operator to carry
  third-party frames would raise the repeat count and might not move
  delivery at all. Its zero is also equally consistent with failing to
  decode the longer frames, which its power says nothing about either way.

  **What the downlink actually rests on is the direct path.** On 2026-10-17 a
  message reached `KD3CCO-7` at Rural King on Benner Pike and was
  acknowledged -- `KD3CCO-7>APY05D,WIDE1-1,WIDE2-1::SMS :ack20550`, the path
  unused, so the gateway heard the HT direct with no digipeater involved.

  **The next experiment is designed and NOT running: a long beacon on a short
  path.** Pad the `PBEACON` comment until the frame matches a gated frame's
  length, leave `BEACON_VIA = WIDE1-1`, and read it after a few days. A long
  beacon still near 58% means length is innocent and it is filtering; a long
  beacon falling toward 10% means it is length and nobody is filtering. It
  carries the same cost to the channel as the last one, so it wants the same
  `TEMPORARY, set <date>` comment and revert instruction. **Let the reverted
  one-hop beacon build a better baseline first** -- the one-hop sample is
  only 53 beacons.

  **Reading it: `reach` is fine now, per type.** The warning that applied
  while the experiment ran -- that `reach` blends one-hop and two-hop beacons
  from either side of the change into a meaningless rate -- expires once the
  log no longer spans the switch. Its per-type rows are plain ratios; the
  floor/ceiling range it prints applies to the AGGREGATE line only, and
  quoting that ratio alone as "the fraction of transmissions repeated" is
  still wrong.

      cd ~/aprs-igate
      ALL=$( { [ -f run/direwolf.log.2.gz ] && zcat run/direwolf.log.2.gz
               [ -f run/direwolf.log.1.gz ] && zcat run/direwolf.log.1.gz
               [ -f run/direwolf.log.1 ] && cat run/direwolf.log.1
               cat run/direwolf.log; } 2>/dev/null )
      R='^\[0\.[0-9]+\] KD3CCO-10>[^:]*\*[^:]*:!'
      echo "ONE-HOP beacons (before the change)"
      echo "  sent:    $(grep -cE '^\[0L\] KD3CCO-10>APDW17,WIDE1-1:!' <<<"$ALL")"
      echo "  repeats: $(grep -E "$R" <<<"$ALL" | sed 's/:.*//' | grep -vc WIDE2)"
      echo "TWO-HOP beacons (the experiment)"
      echo "  sent:    $(grep -cE '^\[0L\] KD3CCO-10>APDW17,WIDE1-1,WIDE2-2:!' <<<"$ALL")"
      echo "  repeats: $(grep -E "$R" <<<"$ALL" | sed 's/:.*//' | grep -c WIDE2)"
      echo "GATED messages (unchanged, the comparison)"
      echo "  sent:    $(grep -cE '^\[0L\] KD3CCO-10>[^:]*:\}' <<<"$ALL")"
      echo "  repeats: $(grep -cE '^\[0\.[0-9]+\] KD3CCO-10>[^:]*\*[^:]*:\}' <<<"$ALL")"

  Needs ~50 two-hop beacons to be worth computing, which is about a day; two is
  better. Reading it: the two-hop beacon rate **falling toward the gated 2%**
  means the hop count explains the gap and third-party format is innocent;
  **staying near the one-hop 23%** means filtering or frame length, and the next
  test is a long beacon on a short path to isolate length.

  Record the outcome here and revert `BEACON_VIA` either way. A negative result
  is as worth writing down as a positive one.

**Wanted later: a blacklist, starting with CX2JO-10** (noted 2026-10-14).
Nothing is to be built or configured for this yet — it is recorded so the next
session does not have to rediscover the idea, and **no setting was changed when
it was written down.** The gateway today has only a whitelist, `WHITELIST_CALLS`,
which decides what may be transmitted from APRS-IS to RF. A blacklist is the
other direction and a different mechanism: suppressing a station that is noisy
on the *receive* side, which the whitelist cannot express because it is not
about transmitting.

CX2JO-10 is the station that prompted it. It appears in third-party messages
relayed through other digipeaters — `CX2JO-10>APV209,TCPIP,N3DXC*::KC3ZLD-10:`
and the like — which arrive repeatedly and fill the monitor. Worth knowing
before designing anything: those are already dropped rather than transmitted
(Direwolf logs `Rx IGate: Do not relay with TCPIP in path`), so this is about
noise in what gets *shown and gated up*, not about a transmit hazard. Decide
deliberately whether the blacklist applies to what is gated up to APRS-IS, what
the monitor displays, or both — they are three different features and only one
of them touches the air.

**The web monitor's long-standing stall is solved** (2026-09-30). A Latin-1
degree sign in another station's APRS comment raised `UnicodeDecodeError` in a
strictly-decoded pipeline; that is a `ValueError`, so the read loop caught it
and exited *without killing the pipeline*, orphaning a `tail -f | gawk` each
time until the Pi ran out of threads. Fixed by `errors="replace"` and by
killing the process group whenever the reader stops. If something like this
recurs, note that `rc=None` means the pipeline is still running, and that
`stderr` was being discarded — which is why it took four days.

**Power and time — state as of 2026-10-12.** There is still no working UPS on
the running station. The HAT is physically on the header with its **cell
disconnected**, and `dmesg` confirms the consequence: `rtc-ds1307 1-0068: SET
TIME!` at every boot is the oscillator-stopped flag, so the clock holds nothing
across a power cut and NTP sets it on the way up. Mains loss is an instant cut;
mains return is an unattended boot.

**The power cut works, confirmed on hardware 2026-10-12 after five failures.**
The answer is the kernel's `gpio-poweroff` driver rather than anything in
userspace:

    dtoverlay=gpio-poweroff,gpiopin=18,timeout_ms=60000

in `/boot/firmware/config.txt` — not `/boot/config.txt`, which on this install
is a stub saying so. After a reboot the pin reads

    gpio-18  (GPIO18   |power_ctrl   ) out lo

and that `out lo` is the HAT armed. Check it with `sudo grep -E "gpio-18 "
/sys/kernel/debug/gpio`; the driver logs nothing to `dmesg`, so the debugfs
claim is the evidence. **An unclaimed `gpio-18` means the overlay did not
probe** and there is no point pulling the mains.

Measured: mains unplugged, the cell carried the Pi; `sudo poweroff` on battery,
and the red PWR LED went dark in **about fifteen seconds**.

**`timeout_ms` is 60000 and not 15000 for that reason.** It is how long the
driver holds GPIO18 high waiting for the cut, and the observed delay was at the
15 s ceiling. If the HAT ever takes longer — a colder or flatter cell — the
driver gives up, the kernel halts, the pin stops being driven, and BCM18's
pull-DOWN takes it low again: the HAT re-arms and never cuts, which is the
stranded-halted-on-battery failure that cost three recoveries in one afternoon.
The timeout costs nothing when power is cut before it expires, so there is no
reason to run without margin.

**`PI_UPS = yes` is in `pi.conf` and `build_pi_image.sh`, uncommitted.** Do not
enable it without the overlay — a clean shutdown with no power cut leaves the
station dark until somebody presses the HAT's button, which is strictly worse
than no UPS at all. `ups-shutdown.sh` checks for the `gpio-poweroff` node at
start and warns if it is missing.

**The overlay line in the running Pi's `config.txt` was added by hand and must
stay there.** `build_pi_image.sh` writes it only when building a card, into the
mounted image — it does not touch a live `/boot/firmware/config.txt`. Deleting
the hand-added line on the running station would disarm the HAT and take the
power cut away. A rebuilt card gets the line from the script, on a fresh
`config.txt`, so there is no duplicate to avoid.

**Full unattended run, 2026-10-12:** mains unplugged with nobody typing, and
the station powered itself down and the LED went dark in **1 min 14 s** — the
HAT asserting GPIO17, the 60 s ride-through, the shutdown, and the HAT's own
cut delay. Mains return boots it.

**Pending:** an **Adafruit PiRTC** (product 4282) with an **Energizer CR1220**
has been ordered to replace it. The swap is physical only — same DS3231, same
I2C address `0x68`, same `i2c-rtc` overlay — so `PI_RTC = ds3231` and everything
`build_pi_image.sh` writes stay exactly as they are. Afterwards, confirm with
`i2cdetect -y 1` showing `68` and `sudo hwclock -r` returning a sane time.

The card protections are what do the real work and are independent of all this:
`run/` on tmpfs, journal in RAM and capped, zram with no writeback file, root
mounted `data=journal`.

**The UPS service needs the `gpio-poweroff` overlay, and is safe with it
(settled 2026-10-12).** The monitor script was always correct — it detects
mains loss and shuts down cleanly, verified against real plug-pulls. What was
missing was the power cut at the end.

Without the overlay, a Pi halted by `poweroff` keeps its 5 V rail up, the HAT
goes on supplying it, and mains returning makes the HAT *charge* rather than let
the cell flatten — so the station stays halted until somebody intervenes. That
happened three times in one afternoon. **That is the failure mode to avoid, and
the overlay is what avoids it:** see "Power and time" above for the line, the
`out lo` check, and the measured timings.

Driving GPIO18 **high** does start the HAT's disconnect timer; confirmed by
doing it on battery with the Pi left running, which went dark within the minute.
But the pin must stay high and only a running Pi can hold it there. Four ways of
combining it with a shutdown failed: letting the pin go Hi-Z at power-off (what
the vendor's own script relies on), driving it high then leaving an internal
pull-up (~50k is too weak), driving it high as a real output then halting — from
the monitor loop and from a `/usr/lib/systemd/system-shutdown` hook running after
every filesystem was unmounted — and **the vendor's own `ups-gpiod.sh` run
unmodified**, which uses libgpiod rather than sysfs and failed identically.

That last one rules out this project's implementation. sysfs looked like the
culprit, because an exported pin survives process exit by design where libgpiod
releases a line when its holder dies — so their method should have handed the
line over early in shutdown while ours never let go. It does not. Do not
re-derive that theory; it was tested and it is wrong.

The reason all five failed is the polarity, not the method: see the PiShop reply
below. Every one of them tried to hold GPIO18 high through shutdown, when what
the HAT needs is to have been armed low beforehand and then released. The momentary button was
also ruled out — their script was run again with the button toggled and failed
identically, and the HAT has been fully de-powered, cell disconnected, several
times between attempts with no change in behavior. Five attempts in total.

**PiShop support replied on 2026-10-06 (Martin), and the mechanism is the
opposite way round from what all five attempts assumed.** His words: *"check
that GPIO pin goes LOW once the Pi/script starts — the hat needs this to get in
the proper mode"*, and *"once GPIO18 goes back high, the UPS hat waits a moment
before cutting out power to the Pi's GPIO power pins. The delay is normal."*

So the HAT **arms while GPIO18 is held LOW** and cuts power a short delay after
the pin goes **HIGH** — measured here at about fourteen seconds, and **the pin
must still be high when that delay expires.**

**The arming was never what failed.** This project's sysfs monitor held GPIO18
low throughout normal running, which is the state the board waits for. What none
of the five attempts could do is hold the pin high for those fourteen seconds,
because each depended on something in userspace that the halt had already
killed. The pad is released, BCM18's default pull-DOWN takes the line low, the
board reads a live Pi and cancels — which is also why letting it go Hi-Z failed,
including in the vendor's own script.

Do not record this as a polarity mistake. It is a **duration** mistake: the
signal is a state that has to be sustained across the halt, not an edge to hit
at the right moment, and no userspace holder can sustain it by construction.

That is what `gpio-poweroff` fixes: a kernel driver holds GPIO18 low from boot,
arming the HAT, asserts it high as the final act of the power-off path after
every filesystem is unmounted, and **keeps driving it** for `timeout_ms`.
Nothing in userspace has to survive. Martin also noted the RTC half of the vendor guide is
out of date and pointed at Adafruit's DS3231 instructions instead.

He assumed Trixie and Step 1 option A of the newer guide
(pishop.us/raspberry-pi-ups-hat-guide/); this Pi runs Bullseye and used the
GitHub `ups-gpiod.sh`, which may matter.

**Untested.** Nothing above has been on hardware. The cell is disconnected and
has to be reconnected before any of it can be tried.

**If a Pi is ever stranded halted-on-battery, press the HAT's momentary button.**
It cuts the output immediately; plug mains back in and the Pi boots. Far easier
than unplugging the cell, which is how the first several recoveries were done.

Also note mains reaches the Pi's own micro-USB jack and only gets to the HAT
through the GPIO header, so the HAT never had to "restore" anything; and the
pull registers are write-only on a BCM2837, so none of these pin states can be
verified by reading them back. The only instrument is the red PWR LED.

**Mobile delivery, tested 2026-10-12 — in the footprint, not beyond it.** Phone
to radio and radio to phone, from a vehicle, at the corner of Atherton and
College Ave, the South Atherton Sheetz, and South Hills Business School. Four
messages acknowledged, both directions confirmed.

Every frame the gateway heard from KD3CCO-7 arrived **three times** by three
routes — direct, via W3YA-1, and via W3YA-1 then WO3T-1 — and **the direct copy
carried no asterisk in its path**, which is what settles it: the gateway and the
mobile hear each other without help at those distances (3-4 mi, the same range
as W3TM-10 and W3SWL-2). So this does not test the one thing still untested.

**Still untested:** delivery to a handheld genuinely outside the gateway's own
footprint — somewhere it cannot hear the radio directly. The same logs show a
two-hop return path exists (W3YA-1 then WO3T-1), so there is a plausible route
for it when it is tried.

**One message failed, and not for the reason it looked like.** Message 19905 was
transmitted three times — the SMS gateway retried, and this station relayed each
retry, all three confirmed on the air with `[0L]`. No acknowledgement came. It
is tempting to read that as a coverage hole, and the log says otherwise: the
gateway heard **nothing at all** from KD3CCO-7 between the frame before those
attempts and the frame after. Radio off, indoors, or in a dead spot. The
transmissions were fine.

  The check that distinguishes the two is whether `KD3CCO-7>` frames appear in
  the log either side of the failure or only outside it. A delivery failure with
  the radio demonstrably on the air is a coverage finding; one with silence
  around it is not evidence about the gateway at all. Evidence preserved at
  `~/evidence/mobile-2026-10-12.txt` on the Pi, because `run/` is tmpfs.

## What to do next (written 2026-10-15)

In rough order of what is blocking what.

**1. ~~Read the `BEACON_VIA` experiment and revert it.~~ DONE 2026-10-18.**
The experiment ran 2026-10-13 22:22 to 2026-10-18 15:56, `BEACON_VIA` is back
to `WIDE1-1`, and the result is written up under "Where it stands": the hop
count is eliminated, the second hop does not help beacons, and `TX_VIA` must
stay at two hops because the second hop is what delivers messages. The
original text follows for the reasoning only.

**1a. (closed) Read the `BEACON_VIA` experiment and revert it.** Written
2026-10-15 while it was still running; kept for the reasoning. It was the only
thing on the station deliberately not in its normal configuration, and it had
been that way since 2026-10-13 22:00 EDT — long enough. `BEACON_VIA` is
`WIDE1-1,WIDE2-2` in `igate.conf`; the question is whether the two-hop beacon
repeat rate falls toward the gated 2% (so hop count explains the gap and
third-party format is innocent) or stays near the one-hop 23% (so it is
filtering or frame length, and the next test is a long beacon on a short path).

  **Do not read it with plain `reach`.** It counts all beacons together and the
  window now holds both one-hop and two-hop ones, so the blended figure answers
  nothing. The split readout is recorded under "Commands" above.

  **Put `BEACON_VIA` back to `WIDE1-1` either way**, and write the outcome into
  "Where it stands" — a negative result is worth as much as a positive one and
  costs the same to record. At ~1,400 beacons a month this is not a setting to
  leave on an experiment.

**1b. Confirm the erected mast height, and write it down.** NEW 2026-11-01,
and more pressing after the 2026-11-06 rebuild: the mast has now been
re-erected by hand twice without the segments being counted
(`OPERATIONS.md`). Until somebody counts them, "about twenty-eight feet" is an
assumption carried over from 10-30 through two joint failures, and the +5.4 dB
two-ray figure and the whole November-against-October comparison rest on it.
**No ladder is needed** -- the pole lowers and redeploys in a few minutes, so
this is ground-level work. It blocks the only clean twenty-eight-foot month
there will be, and it costs minutes of data.

  **The takedown happened on 2026-11-13 and this did not get done.** The pole
  came down, the collars were fitted and the mast went back up
  (`OPERATIONS.md`), but the segments were not counted, the erected height was
  not written down and no witness marks were painted. So the erected height is
  still an estimate and still load-bearing, and all of this needs another
  takedown. When that happens: count the segments, write the erected height
  down, and put a paint or tape **witness mark** on each section at the
  rim of the one below it. **Not joint diameters.** The per-joint diameter table
  was deleted on 2026-11-13 because the pads are wrapped on the mast rather than
  cut to a measurement; do not reinstate measuring the joints as a task.
  **The segment count is counted, not derived.** The pole ships with seventeen
  segments, the three thinnest are not in it, fourteen go up, and fourteen
  segments meet at thirteen joints, which is why there are thirteen collars.
  Drafts that described the thin top segments as "left unclamped" were wrong:
  they are out of the pole. Nothing here was interpolated from the taper. The witness mark is the cheapest item on that list
  and the one that would have caught both failures days early, by turning "has
  a joint crept?" into a question answerable from the ground. **Also check the 2026-11-01 journal for a failed `igate-selftest` between
14:30 and 14:45 UTC** -- the journal is persistent, and a failure in that
window is the radio-off period, not a fault.

**2. The open coverage question, unchanged.** Whether a message gated by this
station reaches a handheld genuinely outside its own footprint is still
untested. The four mobile locations on 2026-10-12 were all 3–4 mi out with
direct copies in the log, so they measured the radio and not the digipeaters.
What is needed is a location where `KD3CCO-7>` frames do NOT appear direct in
the gateway's log, with a message sent to it and the result noted. Until then
the honest claim is the 29–40% repeat rate, which is about beacons.

**3. A blacklist, if it still seems worth it.** Recorded above under "Wanted
later" with the three different features it could mean. Nothing to build yet.

**4. Rebuild `slides/igate-talk.pdf`.** The markdown is one edit ahead of the
PDF — the whitelist statistics slide was updated and the PDF was not. Build
instructions are in `slides/README.md`.

**5. A UPS cell, when one is ordered.** The power-cut path is confirmed working
on hardware (2026-10-12) with the `gpio-poweroff` overlay, but the HAT's cell
is still disconnected, so the running station has no UPS and no clock across a
power cut. Nothing to debug — the fix is a battery.

**Not a bug, and do not go looking for one:** "the web monitor is down" on
2026-10-14 was Android withholding the browser's local-network permission. The
service, the port, the page, the status endpoint and the event stream were all
healthy. The gotcha and the one-command server-side check are above.

## How I work — standing preferences

These are the same in every repository of mine. They are restated in each one
so that any assistant reads them, not only the one configured on my machine.

**Git is mine.** Never run `git commit` or `git push`, in any repository, for
any reason. Reading history is encouraged — `log`, `diff`, `status`, `show` —
and so is telling me when a good commit point has been reached, or drafting a
commit message for me to use. Finish the work, leave it uncommitted, and say
what changed and where.

**Hardware is mine.** Do not build SD-card images, `rsync` to a device, open an
`ssh` session to one, or run anything on a Raspberry Pi or the cyberdeck unless
I ask in that message. Hand me the exact commands to copy and paste — one block
per step, in order — say what each should print, and stop. I will run them and
paste the output back. Local work in the repository needs no such restraint.

**Writing.** No British spellings; US throughout. Design documents are
declarative: no hero's-journey narrative, no second-person "you", and never
state something as fact and then refute it a few lines later. For an article
already published, ask whether I want a dated update section or an in-place
rewrite. A dated update is the better default, because the wrong turns are part
of why a piece is worth reading, but it is my call rather than a rule. Do not repeat
a warning I have already acknowledged.

**Images.** Look at any photograph or screenshot before adding it to an
article, a slide deck, or a repository. Phone numbers show up in radio screens
and log captures, coordinates show up in beacon lines and station pages, and
backgrounds show rooms. Say what you found and redact it rather than guess.

**Destructive commands.** `/dev/sdX` stays a placeholder in any flashing or
disk-writing instructions. Never substitute a real device node.

**Amateur radio.** Test traffic uses my own callsign and its SSIDs — never
another operator's call, unless I explicitly ask for one.

**Working style.** I start fresh sessions often rather than carrying one for
weeks, so assume no memory of previous conversations. Everything you need
should be in this file or in the documents it points at.

**Keeping this file true is part of the work.** Anything dated here records
what was true on that date, not what is true now — check it against the
repository before relying on it, and correct it when it is wrong. When a
session has changed how the project works, turned up a gotcha worth the next
session not rediscovering, or outdated something in a "where it stands"
section, propose the edit to this file before the session ends. Do not wait to
be asked, and do not save it for a tidy-up later: the next session starts cold,
and this file is most of what it gets.
