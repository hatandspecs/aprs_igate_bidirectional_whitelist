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

**The web monitor's long-standing stall is solved** (2026-09-30). A Latin-1
degree sign in another station's APRS comment raised `UnicodeDecodeError` in a
strictly-decoded pipeline; that is a `ValueError`, so the read loop caught it
and exited *without killing the pipeline*, orphaning a `tail -f | gawk` each
time until the Pi ran out of threads. Fixed by `errors="replace"` and by
killing the process group whenever the reader stops. If something like this
recurs, note that `rc=None` means the pipeline is still running, and that
`stderr` was being discarded — which is why it took four days.

**Power and time, added 2026-10-02.** A PiShop UPS HAT is fitted: GPIO17 is
mains-fail, GPIO27 a heartbeat the HAT toggles, GPIO18 held low to mean "the Pi
is running" — the kernel releasing it at power-off is what tells the HAT to cut
output. `igate-ups.service` powers off 30 s after mains loss. It uses the
deprecated sysfs GPIO interface **deliberately**: a libgpiod line is released
when its process exits, which would drop GPIO18 and cut power to a healthy Pi
whenever the service stopped. The HAT's DS3231 is set up via `PI_RTC = ds3231`.

**The antenna moved on 2026-10-03**, from the porch roof to a telescoping mast
about fifteen feet up in the yard, and the station moved to a side table in a
spare room. **The feedline was replaced on 2026-10-04**, 50 feet of RG316 for
RG-8X — roughly 5 dB of loss at 2 m down to under 2 dB, in both directions.

Both reach measurements above were taken from the porch, on RG316. Nothing
measured before 10-04 is comparable with anything measured after it: antenna
height, station location and feedline have all changed since, on top of a
37%-versus-60% difference that was never explained. The 37/60 question is now
unanswerable and should be left alone. What is needed is two fresh `reach` runs
on different days from the finished station, as a new baseline — not a
comparison with the old numbers.

Note that SWR read at the radio end will be **higher** on RG-8X than it was on
RG316, and that is the masking going away rather than a fault: at 5 dB of line
loss even a disconnected antenna reads about 1.9:1, while at 1.8 dB it reads
about 5:1.

**Untested:** delivery to a handheld genuinely outside the gateway's own
footprint, and the UPS shutdown against a real power cut — simulated GPIO is
not a plug being pulled.

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
