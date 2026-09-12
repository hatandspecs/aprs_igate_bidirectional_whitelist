# Bidirectional APRS iGate

A strict-whitelist APRS iGate: packets heard on 144.390 are gated up to
APRS-IS, and **only** APRS *messages* addressed to whitelisted callsigns are
ever transmitted back onto RF. Everything else — positions, telemetry, other
people's traffic — is silently dropped.

Runs as a locked-down Docker container, or bare-metal, driven by one editable
config file. `build_pi_image.sh` also builds a headless Raspberry Pi SD card
image that boots straight into it — see "Running it on a Raspberry Pi".

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

`IS GATED` and `IS DROP` are decided by pairing each `[ig>tx]` with the `[0L]`
that transmits it. Direwolf accepts packets from APRS-IS as they arrive but
transmits under `IGTXLIMIT`, so those lines interleave rather than alternate —
`monitor` matches them on the packet payload, not on position. A drop is only
knowable by the *absence* of a transmission, so `IS DROP` is reported about 15
seconds after the packet arrived. That delay is deliberate: report it sooner and
a packet merely waiting in the transmit queue gets labelled as dropped.

`monitor` requires **gawk** — it uses `strftime()`, which mawk (the default
`awk` on Debian-family systems) does not have. Bare-metal installs pull it in;
if it is missing, `monitor` says so rather than showing an empty screen.

`./deploy_igate.sh status` also writes `run/status.html` — a static page showing
state, whitelist, and resolved filter. Open with `xdg-open run/status.html`.

## Testing it

**1. Is it receiving?** Run `monitor` and wait for `RF RX` lines. If none appear
while there's audible activity, the RX gain is too low (see Audio levels below).

**2. Is it transmitting a decodable signal?** The best test needs no second
radio — send a packet with a digipeat path and see if a digipeater repeats it
back to you:

```bash
# Requires KISS_PORT = 8001 in igate.conf (default 0 = off), then a restart.
echo 'KD3CCO-10>APDW18,WIDE1-1,WIDE2-1::KD3CCO-7 :test{01' \
  | docker exec -i aprs-igate kissutil      # docker mode
echo 'KD3CCO-10>APDW18,WIDE1-1,WIDE2-1::KD3CCO-7 :test{01' \
  | kissutil                                # bare-metal mode
```

Set `KISS_PORT` back to `0` when you're done — see Local control ports below.

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

### Forcing a digipeat path

`TX_VIA` sets the AX.25 digipeat path on everything this station transmits —
gated messages and the RF beacon alike. Blank means direct, with no digipeater.

```
TX_VIA =                  # direct
TX_VIA = W3YA-1           # force every transmission through that digipeater
TX_VIA = WIDE2-1          # generic single hop via whatever answers WIDE2
TX_VIA = W3YA-1,WIDE2-1   # that digi first, then one more generic hop
```

Naming a digipeater explicitly is the deterministic choice: a digipeater repeats
any packet carrying its own callsign in the path, regardless of which `WIDEn-N`
aliases it answers to. The generic form depends on that digi's configuration —
W3YA-1 answers `WIDE2`, not `WIDE1`, so `WIDE1-1` would never reach it.

`RX_VIA` is the mirror image, and it is a **test knob**. There is no way to
force how other stations route *to* you — that's their path setting — but you
can restrict what you gate up:

```
RX_VIA =                  # gate everything heard (normal operation)
RX_VIA = W3YA-1           # gate ONLY packets W3YA-1 actually repeated
RX_VIA = W3YA-1,N3KTX-4   # either of them
```

This renders `FILTER 0 IG d/W3YA-1` — the RF→APRS-IS direction, the reverse of
the whitelist's `FILTER IG 0`. Direwolf's `d/` checks the AX.25 has-been-used
bit, so it matches packets genuinely relayed by that station rather than ones
merely listing it in the path.

It also makes your station less useful to the network, since everything else you
hear stops being gated. Set it back to blank when the test is done. Both settings
are reported by `./deploy_igate.sh config`:

```
TX_VIA = via W3YA-1
RX_VIA = ONLY gate packets digipeated by W3YA-1
```

### Station beacon

Off by default — a beacon is the only thing besides a whitelisted message this
station ever transmits, so it's a deliberate choice.

```
BEACON_TO      = off        # off | IG | RF | BOTH
BEACON_EVERY   = 30:00
BEACON_GRID    = FN10cs     # Maidenhead locator, 4 or 6 characters
BEACON_LAT     =            # decimal degrees, used only if GRID is blank
BEACON_LON     =
BEACON_OVERLAY = R
BEACON_COMMENT = RX iGate | TX whitelist only
```

`IG` sends the beacon to APRS-IS over the internet and **never keys the radio** —
the station appears on aprs.fi with a position at no cost in airtime. `RF`
transmits it on 144.390, which is what local operators see on their own radios;
it only reaches aprs.fi if a neighbouring iGate hears and gates it. `BOTH` does
each, which is the combination to use if you want local visibility *and*
guaranteed presence on the map.

**Give the position as a grid square.** A 6-character Maidenhead locator is
about 4 km by 6 km, so it is rounded by construction rather than by remembering
to round; `deploy_igate.sh` converts it to the centre of the square. Four
characters (`FN10`) is coarser still, roughly 111 km by 156 km. `BEACON_LAT` and
`BEACON_LON` remain available for a precise position and are used only when
`BEACON_GRID` is blank — round them yourself if you use them, because the
position goes into a permanent, public, worldwide database with no delete
button.

`BEACON_OVERLAY` is the character APRS puts on the `&` gateway symbol to say
what kind of gate this is: `R` receive-only, `I` generic, `T` transmitting with
a 1-hop path, `2` with a 2-hop path. **`R` is the honest default for this
project.** The transmit path is whitelist-only, so from any other operator's
point of view this gate receives and never relays to them; advertising `T` or
`2` invites someone to rely on delivery that will not happen.

`./deploy_igate.sh config` reports the resolved setting, including the
coordinates a grid square resolved to, and says plainly when a beacon will be
transmitted on RF:

```
BEACON = every 30:00 TRANSMITTED ON RF and to APRS-IS at FN10CS (40.7708, -77.7917), overlay R
```

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
`IGATE_MODE=bare-metal`. **Bare-metal mode has not yet been run on the air** —
the Raspberry Pi build below is its first intended deployment.

Docker mode is locked down to the minimum that still works — all capabilities
dropped (verified: `CapEff` and `CapBnd` both zero), `no-new-privileges`,
Docker's seccomp profile active, read-only root filesystem with only `/tmp`
writable, non-root, 64 PIDs, 512 MB, and no published ports.

Device access is **only this radio's nodes** — its two serial ports and its
single ALSA card (`controlC1`, `pcmC1D0c`, `pcmC1D0p`, `timer`). Notably it does
*not* get the whole `/dev/snd` directory, which the common recipe passes and
which would include the laptop's built-in microphone.

### Local control ports

Direwolf can listen for AGW clients (Xastir, APRSIS32) and KISS TCP clients
(`kissutil`). It binds both to `0.0.0.0` and authenticates nothing, so anything
that can reach the KISS port can transmit arbitrary packets under `MYCALL`.
Direwolf has no bind-address setting, so off is the only way to make them
unreachable.

```
AGW_PORT  = 0    # 0 disables; 8000 is Direwolf's default
KISS_PORT = 0    # 0 disables; 8001 is Direwolf's default
```

Both default to `0`. Docker mode concealed the exposure by publishing no ports;
bare-metal mode has no such boundary, which matters most for a headless station
on a shared network. Enable `KISS_PORT` only while injecting test packets, and
check with `ss -tln | grep -E '8000|8001'`.

Outbound traffic is restricted to DNS and the APRS-IS port via a dedicated
docker network filtered in the `DOCKER-USER` iptables chain. This needs `sudo`;
without it `up` warns and continues with unrestricted egress rather than
refusing to start. Disable with `RESTRICT_EGRESS = no`.

Note that `monitor` redacts the APRS-IS passcode, which Direwolf echoes in its
login line. Raw `logs` does not — prefer `monitor` when sharing output.

Full detail and remaining gaps are in §13.4 of the design doc.

## Running it on a Raspberry Pi

`build_pi_image.sh` produces a Raspberry Pi OS SD card image that boots
straight into this gateway: joins WiFi, enables SSH, installs Direwolf and
hamlib, and starts the iGate — no keyboard or monitor needed at any point.

The image is customised offline, on this machine, by loop-mounting the
downloaded Raspberry Pi OS image and writing into its two partitions. No Pi is
involved in the build.

```bash
cp pi.secrets.example pi.secrets
$EDITOR pi.secrets            # Pi login password + WiFi SSIDs and PSKs
$EDITOR pi.conf               # hostname, user, country, SSH key

./build_pi_image.sh check     # validate before downloading ~500 MB
./build_pi_image.sh build     # download, customise, write pi-build/aprs-igate-pi.img
./build_pi_image.sh flash /dev/sdX
```

`build` needs `sudo` for the loop mount. `flash` refuses anything that is not a
whole removable/hotplug disk or that has mounted partitions, and asks you to
type the device name a second time. Raspberry Pi Imager works too — choose
"Use custom" and pick `pi-build/aprs-igate-pi.img`.

Then, after a few minutes on first boot:

```bash
ssh igate@aprs-igate.local
cd aprs-igate && ./deploy_igate.sh monitor
```

Step-by-step, from blank SD card to a gateway on the air, is in
[PI-SETUP.md](PI-SETUP.md).

### What ends up on the card

| Written | Purpose |
|---|---|
| `ssh`, `userconf.txt` on the boot partition | Enables sshd and creates `PI_USER` with a SHA-512 password hash |
| `/etc/igate/authorized_keys` | Key-based login, if `PI_SSH_PUBKEY` is set; moved into the home directory on first boot |
| One `.nmconnection` per WiFi network, mode 600 | NetworkManager profiles; network 1 has the highest autoconnect priority |
| `/etc/modprobe.d/cfg80211-regdom.conf`, `/etc/default/crda`, `igate-wifi-country.service` | WiFi regulatory domain, three ways (see below) |
| The whole project in `/opt/aprs-igate` | With `DEPLOY_MODE = bare-metal` rewritten in the installed copy; symlinked to `~/aprs-igate` on first boot |
| `igate-firstboot.service` | Installs `direwolf libhamlib-utils alsa-utils avahi-daemon`, adds the user to `dialout` and `audio` |
| `aprs-igate.service` | `deploy_igate.sh up` at boot, if `PI_AUTOSTART = yes` |

`pi.secrets` is **not** copied to the card — the WiFi keys it holds are already
in the NetworkManager profiles. `igate.secrets` **is** copied, mode 600: the Pi
cannot log in to APRS-IS without the passcode. `build` refuses to run if
`igate.secrets` is missing, since a headless Pi gives no easy way to notice.

### Why the WiFi country is set three ways

Raspberry Pi OS keeps the WiFi radio rfkill-blocked until a regulatory domain
is set — but first boot needs the network to install Direwolf. So the country
has to be in place *before* NetworkManager starts, and the mechanism that does
that has moved between OS releases. The builder sets the `cfg80211` kernel
module parameter (applied as the driver loads), the legacy `REGDOMAIN` default,
and an early oneshot ordered `Before=NetworkManager.service` that runs `rfkill
unblock wifi` and `raspi-config nonint do_wifi_country`. Any one of them
suffices; together they survive an OS release changing its mind.

### Before trusting it on the air

`ADEVICE`, `CAT_DEVICE` and `PTT_DEVICE` are copied from the build host, and the
Pi enumerates its own hardware. The installed `igate.conf` carries a comment
saying so. Check on the Pi with `arecord -l` and `ls /dev/ttyUSB* /dev/ttyACM*`,
then `./deploy_igate.sh restart`.

Two other things worth knowing about the 3A+ specifically: it has one USB-A
port, so a radio and anything else need a hub, and 512 MB of RAM, which is why
`pi.conf` defaults to the 32-bit (`armhf`) Lite image and why the Pi runs
bare-metal rather than under Docker.

### Settings

`pi.conf` (committed) holds hostname, user, image variant, locale, install
directory and autostart. `pi.secrets` (gitignored) holds `PI_USER_PASSWORD` and
`WIFI_<n>_SSID` / `WIFI_<n>_PSK` / `WIFI_<n>_HIDDEN`, numbered from 1 — the
builder reads until a number is missing. `PI_IMAGE_PATH` points at an image
already on disk to skip the download.
