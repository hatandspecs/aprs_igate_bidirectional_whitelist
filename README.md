# Bidirectional APRS iGate

A strict-whitelist APRS iGate: packets heard on 144.390 are gated up to
APRS-IS, and **only** APRS *messages* addressed to whitelisted callsigns are
ever transmitted back onto RF. Everything else — positions, telemetry, other
people's traffic — is silently dropped.

Runs as a locked-down Docker container driven by one editable config file.
The design rationale is in [aprs-igate-prototype-test.md](aprs-igate-prototype-test.md);
sections 13–15 there cover what was actually built, the problems hit along the
way, and known limitations.

## Quickstart

```bash
cp igate.secrets.example igate.secrets
$EDITOR igate.secrets     # your APRS-IS passcode
$EDITOR igate.conf        # MYCALL, ADEVICE, CAT_DEVICE, PTT_DEVICE, WHITELIST_CALLS

./deploy_igate.sh config  # validate; prints resolved settings
./deploy_igate.sh up      # builds image on first run, then starts
./deploy_igate.sh monitor # watch packets: what's heard, gated, dropped
```

Tear down with `./deploy_igate.sh down`, or `./deploy_igate.sh uninstall` to
return to a freshly-cloned state.

## Commands

| Command | Does |
|---|---|
| `config` | Parse and validate `igate.conf`, print resolved settings (passcode masked) |
| `build` | Build the container image |
| `up` | Render `direwolf.conf`, apply audio levels, start |
| `down` | Stop and remove the container |
| `restart` | `down` then `up` |
| `status` | Running or not; also writes `run/status.html` |
| `logs` | Follow the raw Direwolf log |
| `monitor` | Follow the log **annotated** — recommended |
| `uninstall` | Tear down to a zero state |

All take an optional config-file argument: `./deploy_igate.sh up field.conf`.

## Monitoring

`./deploy_igate.sh monitor` is the one to use. It annotates Direwolf's output
into plain language:

```
16:17:34  INFO       Now connected to IGate server noam.aprs2.net
16:17:34  IS SERVER  # logresp KD3CCO verified, server T2BC
16:18:02  RF RX      KQ4BBR-9>T0PV3T,W3YA-1,WIDE2*:`i3$r67>/`"6G}Len - Mobile
16:18:11  IS DROP    QRX>APQRX,TCPIP*,qAC::KC3WRY-14:not whitelisted{1
16:18:40  IS GATED   SMS>APOSMS,TCPIP*,qAC,WA7BF::KD3CCO-7 :@4848324995 hello{99
16:19:05  TX LOCAL   KD3CCO-10>APDW18,WIDE1-1,WIDE2-1::KD3CCO-7 :test{06
```

| Label | Meaning |
|---|---|
| `RF RX` | Heard on the air and decoded |
| `IS GATED` | Came from APRS-IS, matched the whitelist, **was transmitted** |
| `IS DROP` | Came from APRS-IS, did **not** match the whitelist, dropped |
| `TX LOCAL` | Transmitted by this station (beacon or injected packet) |
| `IS SERVER` | APRS-IS server chatter |
| `WARN` / `INFO` | Problems and connection state |

**Why this exists:** Direwolf's raw log prints `[ig>tx]` when a packet *arrives
from APRS-IS* — before the whitelist runs — not when it transmits. The line that
means *actually transmitted* is `[0L]`. Reading `[ig>tx]` as "transmitted" makes
a correctly-working whitelist look broken. `monitor` pairs the two lines and
reports the real outcome. If you use raw `logs` instead, remember: **an
`[ig>tx]` with no following `[0L]` was dropped, not sent.**

`./deploy_igate.sh status` also writes `run/status.html` — a static page showing
state, whitelist, and resolved filter. Open with `xdg-open run/status.html`.

## Testing it

**1. Is it receiving?** Run `monitor` and wait for `RF RX` lines. If none appear
while there's audible activity, the RX gain is too low (see Audio levels below).

**2. Is it transmitting a decodable signal?** The best test needs no second
radio — send a packet with a digipeat path and see if a digipeater repeats it
back to you:

```bash
echo 'KD3CCO-10>APDW18,WIDE1-1,WIDE2-1::KD3CCO-7 :test{01' \
  | docker exec -i aprs-igate kissutil
```

Watch `monitor`. You'll see `TX LOCAL` immediately. If within ~30 seconds you
also see the same message come back as `RF RX` or `IS GATED` with a digipeater
in the path (e.g. `W3YA-1,WIDE1*`), **your signal is good** — a real station
decoded and repeated it. That is the strongest single proof available.

> Use your own callsign or SSID in test packets. Never put another operator's
> callsign in a packet you transmit.

**3. Is the whitelist working?** Watch for `IS DROP` lines — those are packets
that arrived and were correctly refused. Seeing them is the whitelist working.

**4. Full round trip.** Text `@KD3CCO-7 <message>` to the aprs.wiki SMS gateway
at `866-352-4096`. It should appear as `IS GATED`. From RF, address a message to
`SMS` with body `@<your-number> <message>`.

## Configuration

`igate.conf` is plain `key = value`. Key settings:

```
MYCALL = KD3CCO-10                 # this station's callsign
WHITELIST_CALLS = KD3CCO*          # comma-separated; * covers all SSIDs
ADEVICE = plughw:1,0               # from `arecord -l`
RIG_MODEL = 1035                   # hamlib model (1035 = FT-991, works for FTX-1)
CAT_DEVICE = /dev/ttyUSB0          # CAT control port
PTT_DEVICE = /dev/ttyACM0          # PTT port (same as CAT on single-port radios)
```

Multiple whitelisted calls: `WHITELIST_CALLS = KD3CCO*, W3XYZ*, N0CALL-9`.
Only *messages* addressed to these are ever transmitted.

### The passcode

`igate.conf` is meant to be committed, so the passcode goes elsewhere. Resolved
in priority order: `IGATE_PASSCODE` env var → `igate.secrets` (gitignored) →
`IGLOGIN_PASSCODE` in the config. The APRS-IS passcode is a checksum of your
callsign, not a chosen password — but keep it out of version control anyway.
`config` prints it masked.

### Audio levels — important

```
TX_AUDIO_LEVEL = 10    # too high over-deviates: audible but decodes NOWHERE
RX_AUDIO_LEVEL = 35    # too low decodes nothing
DISABLE_AGC = yes
```

These reset to (wrong) device defaults whenever the radio's USB re-enumerates,
and both failure modes are **silent**. `up` re-applies them every start. If you
change radios, recalibrate: raise TX until the digipeat test in §2 stops
working, then back off.

## Safety notes

- **Enable your radio's TOT (time-out timer).** If USB drops mid-transmission
  the unkey can't get through and the radio sticks in transmit. No software can
  fix that — the control path is what died. The radio's own timer is the only
  backstop. This happened twice at 5 W.
- **Watch for RFI on the USB cable.** At 5 W with a whip near the laptop, RF
  crashed the USB link. A ferrite choke and antenna separation fixed it; stable
  at 1 W.
- **`up` refuses to start if the audio device is missing**, since PTT would
  still key the radio and transmit an unmodulated carrier.

## Tearing it down

```bash
./deploy_igate.sh down       # stop and remove the container
./deploy_igate.sh uninstall  # also remove the image and run/ — back to a fresh clone
```

In bare-metal mode `uninstall` also removes the `direwolf` package, but
deliberately leaves `hamlib` and `alsa-utils` alone — other ham radio software
(WSJT-X among them) depends on hamlib. It prints the command if you want them
gone.

## Deployment modes

`DEPLOY_MODE = docker` (default) or `bare-metal`; override per-run with
`IGATE_MODE=bare-metal`. Docker mode runs with all capabilities dropped, a
read-only root filesystem, no exposed ports, and access to only the two serial
devices and `/dev/snd`. **Bare-metal mode is implemented but untested.**

## Known limitation

Messages relayed from APRS-IS go out in APRS third-party format (`}`), which is
correct and standard. **The Yaesu FT-5DR does not display them** — so SMS
messages won't show on that radio, though direct messages will. This affects any
iGate, not just this one. See §15 of the prototype doc for options.
