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
  days, while three documents called it verified. BCM numbers are not sysfs
  numbers: gpiolib allocates dynamically and the base is 512 on this kernel.
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

All functional requirements met. Self-test runs every four hours unattended and
is believed by default: three consecutive step-4 failures on 2026-09-30 were a
true alarm — the witness radio was switched off. Whitelist, measured over 30
hours: APRS-IS offered 10,151 packets and 8 reached the air, all of them
messages to a whitelisted call.

Reach was measured twice from the porch position, on RG316: 42 of 112 (37%) on
09-27, then 41 of 68 (60%) on 09-28. The difference is significant (z = 2.97,
p = 0.003) and was never explained; see the 10-03/10-04 note below for why it
can no longer be. `TX_VIA` is `WIDE1-1,WIDE2-2` because the near digipeater
(W3TM-10) hears this station far more often than the mountain-top one (W3YA-1),
so a second slot is what reaches the mountain.

**A new baseline window opened 2026-10-04 16:25 EDT**, on the final
configuration: slim jim on the fifteen-foot mast, 50 ft of RG-8X, station in the
spare room, no UPS. `run/` is tmpfs, so any reboot restarts the window — check
`uptime -s` against that timestamp before trusting a sample. At ~48 beacons a
day, a run on 10-05 and another on 10-06 gives samples comparable to the old 112
and 68. **Two runs on different days is the baseline; one is an anecdote**, and
this project has already produced a retraction from concluding a change from a
small sample. Nothing from this window is comparable with the 37/60 pair.

**The web monitor's long-standing stall is solved** (2026-09-30). A Latin-1
degree sign in another station's APRS comment raised `UnicodeDecodeError` in a
strictly-decoded pipeline; that is a `ValueError`, so the read loop caught it
and exited *without killing the pipeline*, orphaning a `tail -f | gawk` each
time until the Pi ran out of threads. Fixed by `errors="replace"` and by
killing the process group whenever the reader stops. If something like this
recurs, note that `rc=None` means the pipeline is still running, and that
`stderr` was being discarded — which is why it took four days.

**Power and time — state as of 2026-10-04.** There is no UPS. The HAT is still
physically on the header with its **cell disconnected**, pending the swap below,
but every trace of it is gone from the software: no `PI_UPS` setting, no
`igate-ups.service`, no shutdown script, nothing in `build_pi_image.sh`. Mains
loss is an instant cut; mains return is an unattended boot.

**Pending:** an **Adafruit PiRTC** (product 4282) with an **Energizer CR1220**
has been ordered to replace it. The swap is physical only — same DS3231, same
I2C address `0x68`, same `i2c-rtc` overlay — so `PI_RTC = ds3231` and everything
`build_pi_image.sh` writes stay exactly as they are. Afterwards, confirm with
`i2cdetect -y 1` showing `68` and `sudo hwclock -r` returning a sane time.

The card protections are what do the real work and are independent of all this:
`run/` on tmpfs, journal in RAM and capped, zram with no writeback file, root
mounted `data=journal`.

**Do not turn the UPS service back on with this HAT (settled 2026-10-04).** The
script is correct — it detects mains loss and shuts down cleanly, both verified
against real plug-pulls. The hardware cannot finish the job. A Pi halted by
`poweroff` keeps its 5 V rail up, the HAT back-feeds it indefinitely, and mains
returning makes the HAT *charge* rather than let the cell flatten — so the
station stays halted until somebody unplugs the battery by hand. That happened
three times in one afternoon.

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
re-derive that theory; it was tested and it is wrong. The momentary button was
also ruled out — their script was run again with the button toggled and failed
identically, and the HAT has been fully de-powered, cell disconnected, several
times between attempts with no change in behaviour. Five attempts in total.

**PiShop support was asked about this on 2026-10-04** — documentation wrong,
usage error, or faulty board, and specifically whether GPIO18 is meant to be
pulled up on the HAT. No reply yet; record it here if one comes.

**If a Pi is ever stranded halted-on-battery, press the HAT's momentary button.**
It cuts the output immediately; plug mains back in and the Pi boots. Far easier
than unplugging the cell, which is how the first several recoveries were done.

Also note mains reaches the Pi's own micro-USB jack and only gets to the HAT
through the GPIO header, so the HAT never had to "restore" anything; and the
pull registers are write-only on a BCM2837, so none of these pin states can be
verified by reading them back. The only instrument is the red PWR LED.

**Untested:** delivery to a handheld genuinely outside the gateway's own
footprint.

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
