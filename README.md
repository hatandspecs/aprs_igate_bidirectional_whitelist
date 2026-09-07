# Bidirectional APRS iGate

A strict-whitelist APRS iGate: packets heard on 144.390 are gated up to
APRS-IS, and **only** APRS *messages* addressed to whitelisted callsigns are
ever transmitted back onto RF. Everything else — positions, telemetry, other
people's traffic — is silently dropped.

Runs as a locked-down Docker container driven by one editable config file.
The design rationale is in [aprs-igate-prototype-test.md](aprs-igate-prototype-test.md);
sections 13–15 there cover what was actually built, the problems hit along the
way, and the corrections made when earlier conclusions turned out to be wrong.

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
| `up` | Render `direwolf.conf`, apply audio levels, set radio freq/mode, start |
| `down` | Stop and remove the container |
| `restart` | `down` then `up` |
| `status` | Running or not; also writes `run/status.html` |
| `logs` | Follow the raw Direwolf log |
| `monitor` | Follow the log **annotated** — recommended. `monitor raw` omits decode detail |
| `uninstall` | Tear down to a zero state |

All take an optional config-file argument: `./deploy_igate.sh up field.conf`.

## Monitoring

`./deploy_igate.sh monitor` is the one to use. It annotates Direwolf's output
into plain language, and shows Direwolf's decode of each frame indented beneath:

```
17:23:49  RF RX      AA3BR>SYRV6V,N3KTX-1,WIDE1,W3YA-1,WIDE2*:`h@dl#GYY`"5+}_0
17:23:49  RF->IS UP  AA3BR>SYRV6V,...,qAR,KD3CCO-10:`h@dl#GYY`"5+}_0
                       MIC-E, Yaesu/Standard*, Yaesu FT3D, Off Duty
                       N 39 26.6600, W 076 36.7200, 0 km/h, course 343, alt 111 m
17:24:02  IS GATED   SMS>APOSMS,TCPIP*,qAC,WA7BF::KD3CCO-7 :@4848324995 hello{99
17:24:11  IS DROP    QRX>APQRX,TCPIP*,qAC::KC3WRY-14:not whitelisted{1
```

| Label | Meaning |
|---|---|
| `RF RX` | Heard on the air and decoded |
| `RF->IS UP` | Heard on RF and gated **up** to APRS-IS by this station |
| `IS GATED` | Came from APRS-IS, matched the whitelist, **was transmitted** |
| `IS DROP` | Came from APRS-IS, did **not** match the whitelist, dropped |
| `TX LOCAL` | Transmitted by this station (beacon or injected packet) |
| `IS SERVER` | APRS-IS server chatter |
| `WARN` / `INFO` | Problems and connection state |

Indented grey lines are Direwolf's decode — packet type, position, radio model,
comment text. `./deploy_igate.sh monitor raw` hides them if you want it terse.

**Why this exists:** Direwolf's raw log prints `[ig>tx]` when a packet *arrives
from APRS-IS* — before the whitelist runs — not when it transmits. The line that
means *actually transmitted* is `[0L]`. Reading `[ig>tx]` as "transmitted" makes
a correctly-working whitelist look broken. `monitor` pairs the two and reports
the real outcome. If you use raw `logs` instead, remember: **an `[ig>tx]` with
no following `[0L]` was dropped, not sent.**

`RF->IS UP` only appears because `entrypoint.sh` runs Direwolf with `-d i`.
Without that flag the entire uplink direction is invisible in the log — note it
is `-d i`, not `-d g`, which is the unrelated GPS debug option.

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

## Radio setup — the one that matters

Set the FTX-1 to **D-FM (data FM)**, not plain FM. `deploy_igate.sh up` now
enforces this over CAT on every start, and warns if the radio reports plain FM.

This is worth understanding rather than just trusting: in plain FM the radio
modulates from the **microphone input**, not the USB codec. Direwolf keys the
radio and transmits a clean carrier with nothing in it. PTT works, SWR is fine,
a signal shows on the waterfall — and no receiver on earth can decode it. It
cost most of a bring-up session to find, because every obvious diagnostic comes
back healthy.

Verified via CAT: `rigctl M FM` -> `FM`, `rigctl M PKTFM` -> `FM-D`. The
relevant `igate.conf` settings:

```
RADIO_SET_ON_UP = yes
RADIO_FREQ = 144390000
RADIO_MODE = PKTFM        # hamlib's name for the radio's FM-D
RADIO_PASSBAND = 16000
```

Also confirm the radio's **USB MOD GAIN** (under its data-mode settings) is
sane, since that governs transmit deviation from the USB audio.

## Configuration

`igate.conf` is plain `key = value`. Key settings:

```
MYCALL = KD3CCO-10                 # this station's callsign
WHITELIST_CALLS = KD3CCO*          # comma-separated; * covers all SSIDs
ADEVICE = plughw:1,0               # from `arecord -l`
RIG_MODEL = 1035                   # hamlib model (1035 = FT-991, works for FTX-1)
CAT_DEVICE = /dev/ttyUSB0          # CAT control port
CAT_BAUD = 38400                   # CAT serial speed
PTT_DEVICE = /dev/ttyACM0          # PTT port (same as CAT on single-port radios)
PTT_TYPE = RIG                     # RIG = PTT via CAT command; also RTS, DTR
IGLOGIN_CALL = KD3CCO              # APRS-IS login (base call, no SSID)
```

The FTX-1 exposes CAT and PTT as **two separate serial ports**, which Direwolf's
single-port `PTT RIG` directive can't drive — so `rigctld` bridges them and
Direwolf talks to it over loopback. Single-port radios: set `PTT_DEVICE` the
same as `CAT_DEVICE`.

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
change radios, recalibrate: raise TX until the digipeat test above stops
working, then back off.

## Safety notes

- **Enable your radio's TOT (time-out timer).** If USB drops mid-transmission
  the unkey can't get through and the radio sticks in transmit. No software can
  fix that — the control path is what died. The radio's own timer is the only
  backstop. This happened twice at 5 W. **Set to 3 minutes here** — note it is
  global on the FTX-1, applying to voice as well as data, so a long SSB over
  could be cut. Invisible to APRS, where bursts are milliseconds.
- **Watch for RFI on the USB cable.** At 5 W, RF crashed the USB link and stuck
  the radio in transmit. Root cause was a quarter-wave whip with no ground
  plane — poorly matched, radiating into the shack. A half-wave on a tripod
  (SWR under 1.2:1 to 5 W) plus a ferrite choke fixed it properly.
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
`IGATE_MODE=bare-metal`. **Bare-metal mode is implemented but untested.**

Docker mode is locked down to the minimum that still works — all capabilities
dropped (verified: `CapEff` and `CapBnd` both zero), `no-new-privileges`,
Docker's seccomp profile active, read-only root filesystem with only `/tmp`
writable, non-root, 64 PIDs, 512 MB, and no published ports.

Device access is **only this radio's nodes** — its two serial ports and its
single ALSA card (`controlC1`, `pcmC1D0c`, `pcmC1D0p`, `timer`). Notably it does
*not* get the whole `/dev/snd` directory, which the common recipe passes and
which would include the laptop's built-in microphone.

Outbound traffic is restricted to DNS and the APRS-IS port via a dedicated
docker network filtered in the `DOCKER-USER` iptables chain. This needs `sudo`;
without it `up` warns and continues with unrestricted egress rather than
refusing to start. Disable with `RESTRICT_EGRESS = no`.

Note that `monitor` redacts the APRS-IS passcode, which Direwolf echoes in its
login line. Raw `logs` does not — prefer `monitor` when sharing output.

Full detail and remaining gaps are in §13.4 of the design doc.
