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
- **`reach` counts beacons**, so it measures `BEACON_VIA`, not `TX_VIA`.
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
```

`selftest` and `reach` transmit or read for real, on my hardware — give me the
command rather than running it.

## Where it stands (2026-10-04)

All functional requirements met.

**The baseline window ran unbroken from 2026-10-04 16:25 EDT to 2026-10-12,
eight days and three hours**, on the final configuration: slim jim on the
fifteen-foot mast, 50 ft of RG-8X, station in the spare room, no UPS. `run/` is
tmpfs, so any reboot restarts it — check `uptime -s` against that timestamp
before trusting a sample. Everything below was measured in that one window and
supersedes the 09-27/09-28 pair, which was a different antenna and position and
was never comparable.

**The whitelist, over eight days: APRS-IS offered 73,066 packets. 95 messages
reached the air and every one was addressed to a whitelisted call** — 85 to
KD3CCO-7, 7 to KD3CCO-10, 3 to KD3CCO-1. Plus 392 beacons, which is 47.8 a day
and confirms the 30-minute interval. Nothing else was transmitted.

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

**Gated messages are repeated far less often than beacons, and the cause is not
established (2026-10-13).** Measured on this station: beacons 12 of 52 (23%),
gated messages 1 of 36 (2.8%), z = 2.64, p = 0.008. A deliberate run of twenty
self-tests produced 0 of 20 while W3YA-1 was repeating beacons in the same
window, so the digipeaters were awake and the result is interpretable.

  **Three explanations fit it equally and the experiment cannot separate them.**
  Third-party filtering (digipeaters dropping `}` frames, which is a reasonable
  thing for an operator to configure, since internet-sourced frames put back on
  RF are how APRS loops form); path length (`TX_VIA = WIDE1-1,WIDE2-2` asks for
  three hops where `BEACON_VIA = WIDE1-1` asks for one, and plenty of
  digipeaters refuse a path above a hop budget); and frame length (a gated
  third-party frame is roughly 30% longer, and at 1200 baud longer frames fail
  more). Do not write up any one of them as the cause.

  **The experiment that separates them** changes one variable using traffic
  already being sent: set `BEACON_VIA = WIDE1-1,WIDE2-2` so beacons request the
  same path, leave it a few days, and compare the beacon repeat rate against
  23%. A drop toward the gated rate means it is the path; no change means
  filtering or length, and a long beacon on a short path isolates length next.
  48 beacons a day gives a usable sample in two or three days, against six
  gated messages a day needing a fortnight.

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
times between attempts with no change in behaviour. Five attempts in total.

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
already published, add a dated update section rather than rewriting the
narrative — the wrong turns are part of why it is worth reading. Do not repeat
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
