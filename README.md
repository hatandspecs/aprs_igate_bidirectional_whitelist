# Bidirectional APRS iGate

Runs the strict-whitelist APRS iGate described in
[aprs-igate-prototype-test.md](aprs-igate-prototype-test.md) as a locked-down
Docker container, driven by one editable config file. Read that document
first for *why* the design looks like this (the whitelist rule, the PTT
ladder, the bench test procedure); this file is the *how to actually run it*.

## Quickstart

```bash
cp igate.secrets.example igate.secrets
$EDITOR igate.secrets        # set IGLOGIN_PASSCODE to your real APRS-IS passcode
$EDITOR igate.conf           # set MYCALL, ADEVICE, CAT_DEVICE, PTT_DEVICE, WHITELIST_CALLS

./deploy_igate.sh config     # validate everything resolves correctly
./deploy_igate.sh up         # builds the image on first run, then starts the container
./deploy_igate.sh logs       # watch for [rf>ig] / [ig>tx]
```

Stop it with `./deploy_igate.sh down`. The sections below explain what each
setting means, how to find your `ADEVICE`/`CAT_DEVICE`/`PTT_DEVICE` values,
what APRS-IS and the passcode actually are, and the full command reference.

## Contents

- `igate.conf` — the settings you edit (station call, audio device, radio
  ports, APRS-IS login, whitelisted calls).
- `igate.secrets` — your real APRS-IS passcode. **Not committed** (gitignored).
- `Dockerfile` / `entrypoint.sh` — builds the container image.
- `deploy_igate.sh` — the only script you run. Subcommands: `config`,
  `build`, `up`, `down`, `restart`, `status`, `logs`.

## 1. What APRS-IS is, and the "passcode"

**APRS-IS** is the internet backbone that ties every APRS iGate in the world
together — when your iGate hears a packet on RF, it forwards it to APRS-IS;
when someone sends an APRS message from a phone or a website, it comes
*from* APRS-IS and your iGate is what puts it on the air. `aprs.fi` and the
NA7Q SMS gateway both sit on this network. Your iGate is just one more
client connecting to it over plain TCP (port 14580).

To log in, APRS-IS wants your callsign and a **passcode** — but this isn't a
password you choose. It's a small numeric checksum computed from your
callsign by a public, well-known algorithm (the same one every APRS client —
APRSdroid, Xastir, Direwolf itself — uses). Anyone who knows your callsign
can (re)compute it; it exists to keep accidents and casual misconfiguration
off the network, not to authenticate you cryptographically. That said, it's
still not something to publish in a git repo: treat it like any other
account credential and keep it out of version control. Get yours from your
club, an existing APRS client's settings, or any APRS-IS passcode
calculator — search "APRS-IS passcode calculator" or generate it locally
with an `aprs-passcode`/`callpass`-style tool if you have one installed.

### Keeping it out of `igate.conf`

`igate.conf` is meant to be committed and shared; `IGLOGIN_PASSCODE` in it is
intentionally left **blank**. `deploy_igate.sh` resolves the real value from,
in priority order:

1. The `IGATE_PASSCODE` environment variable — good for a one-off run:
   ```bash
   IGATE_PASSCODE=12345 ./deploy_igate.sh up
   ```
2. `igate.secrets`, a small file next to the script, listed in `.gitignore`:
   ```bash
   cp igate.secrets.example igate.secrets
   $EDITOR igate.secrets   # set IGLOGIN_PASSCODE = <your real passcode>
   ```
3. `IGLOGIN_PASSCODE` in `igate.conf` itself, if you really want it there.

`./deploy_igate.sh config` prints the resolved passcode masked
(`IGLOGIN_PASSCODE = ***45`) so you can confirm it loaded without echoing it
in full.

## 2. Your radio: the FTX-1 over CAT

Hamlib (4.6.5, the version this image uses) has no dedicated FTX-1 model
yet — `rigctl --list | grep -i ftx` comes back empty. But you've already
confirmed, both from WSJT-X and from the `ftx1-tuner-sweep` project, that
the FTX-1 CAT-controls cleanly as a **Yaesu FT-991** (hamlib model `1035`),
and that it exposes CAT control and PTT as **two separate serial ports**
over its one USB-C connection:

| Function | Device (yours) | Notes |
|---|---|---|
| CAT control | `/dev/ttyUSB0` | 38400 baud, frequency/mode |
| PTT | `/dev/ttyACM0` | keyed via a CAT command (hamlib calls this `RIG`; WSJT-X calls it "CAT") |

Direwolf's own `PTT RIG model port` config line only accepts **one** serial
port, so it can't drive this two-port setup by itself. The fix — and the
reason there's an `entrypoint.sh` in this repo — is `rigctld`, hamlib's own
daemon, which *does* support a separate `--ptt-file`. The container runs:

```
rigctld -m 1035 -r /dev/ttyUSB0 -s 38400 -p /dev/ttyACM0 -P RIG -t 4532 -T 127.0.0.1
```

bound to loopback only (unreachable from outside the container), and
Direwolf connects to it as a network rig: `PTT RIG 2 localhost:4532`. Both
processes run in the same container; `entrypoint.sh` starts `rigctld` in the
background, waits for it to come up, then `exec`s Direwolf so Direwolf
becomes PID 1 and receives `docker stop` directly.

If your radio only has one port, or you swap radios later, set
`CAT_DEVICE` and `PTT_DEVICE` to the same path in `igate.conf` — the script
detects that and only passes one `--device` flag through.

Your exact settings, in `igate.conf`, become:

```
RIG_MODEL = 1035
CAT_DEVICE = /dev/ttyUSB0
CAT_BAUD = 38400
PTT_DEVICE = /dev/ttyACM0
PTT_TYPE = RIG
```

If you ever need to change radios: `rigctl --list` shows all model numbers,
and `PTT_TYPE` can also be `RTS` or `DTR` for radios keyed by a serial
control line instead of a CAT command (see Section 8 of the prototype doc
for that fallback ladder).

## 3. Audio device

Section 5 of the prototype doc has you find this with `arecord -l` — same
here. Plug in the FTX-1, then either run it on the host (if `alsa-utils` is
installed) or through the image you're about to build, which already has it:

```bash
./deploy_igate.sh build   # first time only
docker run --rm --device /dev/snd:/dev/snd --entrypoint arecord aprs-igate:latest -l
```

Look for "USB Audio CODEC", note the card number, and set it in
`igate.conf`:

```
ADEVICE = plughw:2,0    # card 2 from the arecord -l output
```

## 4. Editing `igate.conf`

Plain `key = value`, `#` comments, no shell syntax — safe to hand-edit.

**Whitelist, one call:**
```
WHITELIST_CALLS = KD3CCO*
```

**Whitelist, multiple calls** (comma-separated; the script chains them into
Direwolf's `g/KD3CCO*/W3XYZ*` filter syntax for you):
```
WHITELIST_CALLS = KD3CCO*, W3XYZ*, N0CALL-9
```
The `*` wildcard covers all SSIDs of a call (`KD3CCO*` matches `KD3CCO-7`,
`KD3CCO-10`, etc.); drop it to whitelist one specific SSID only. Remember
the rule from the prototype doc: this is addressee-only and OR'd, so only
*messages* to one of these calls will ever transmit — positions, telemetry,
and messages to anyone else are dropped regardless of what else you add.

Everything else in the file (`MYCALL`, `IGTXVIA`, `IGTXLIMIT`, `IGSERVER`)
maps directly to the Direwolf directives explained in Section 7 of the
prototype doc.

## 5. Commands

```bash
./deploy_igate.sh config          # parse + validate igate.conf, print resolved values
./deploy_igate.sh build           # build the aprs-igate image
./deploy_igate.sh up              # render direwolf.conf, start the container
./deploy_igate.sh down            # stop and remove the container
./deploy_igate.sh restart         # down, then up
./deploy_igate.sh status          # is it running
./deploy_igate.sh logs            # follow the log — watch for [rf>ig] / [ig>tx]
```

Every subcommand except `down`/`status`/`logs` takes an optional config
file path, e.g. `./deploy_igate.sh up field.conf`, if you keep more than one
profile (bench vs. field, per Section 12 of the prototype doc).

`up` refuses to start if any required setting is still blank — it tells you
exactly which one. It also warns (but doesn't refuse) if `CAT_DEVICE`,
`PTT_DEVICE`, or `/dev/snd` don't exist yet, in case the radio is unplugged.

## 6. What the container can and can't touch

Least privilege, matched to exactly what Direwolf + rigctld need:

- **No `--privileged`, all Linux capabilities dropped** (`--cap-drop=ALL`),
  `no-new-privileges` set, process count capped (`--pids-limit=64`).
- **Read-only root filesystem** — only `/tmp` and `/var/lock` are writable
  (both anonymous tmpfs, gone on container removal).
- **Device access limited to exactly two things**: the specific serial
  device node(s) for CAT/PTT, and `/dev/snd` for the USB audio codec. No
  other host devices are reachable.
- **Runs as a dedicated non-root user** inside the image; it can only open
  those device nodes because the container is launched with `--group-add`
  for the host's `dialout` and `audio` group GIDs — the same groups your own
  user account needs to be in to use the radio directly.
- **No exposed ports.** `rigctld` binds to `127.0.0.1` inside the container
  only, reachable by Direwolf in the same container and nothing outside it.
  The only network traffic leaving the container is the outbound APRS-IS
  connection.
- **`--restart unless-stopped`** so it survives a reboot, matching the
  systemd suggestion in Section 12 of the prototype doc, without needing a
  systemd unit on the host.

Podman (installed on this machine) is a drop-in alternative if you'd rather
avoid a root-owned daemon entirely — `podman build`/`podman run` accept the
same flags used here.

## 7. Bench test procedure

Once `up` reports the container is running, follow Section 9 of the
prototype doc using `./deploy_igate.sh logs` in place of watching a bare
`direwolf -c` terminal — the `[rf>ig]`, `[ig>tx]`, and periodic `IGATE`
statistics lines all show up the same way. Test E (the negative test — a
non-whitelisted message must **not** transmit) is the one that actually
proves the whitelist works; don't skip it.

## 8. Troubleshooting

- **`up` warns a device doesn't exist**: check the radio is plugged in and
  re-run `ls /dev/ttyUSB* /dev/ttyACM*` — Linux can renumber these if other
  USB-serial devices are attached in a different order.
- **rigctld can't open the serial port / PTT doesn't key**: test it standalone
  first, outside Direwolf, using the same image:
  ```bash
  docker run --rm -it \
    --device /dev/ttyUSB0:/dev/ttyUSB0 --device /dev/ttyACM0:/dev/ttyACM0 \
    --entrypoint rigctl aprs-igate:latest \
    -m 1035 -r /dev/ttyUSB0 -s 38400 T 1   # key
  ```
  (add a second run with `T 0` to unkey). If this doesn't key the radio,
  the problem is in the CAT/PTT wiring, not Direwolf or Docker.
- **No audio decodes**: confirm `ADEVICE` matches the card number from
  `arecord -l` and that the FTX-1's internal APRS/TNC decode is switched off
  (Section 6 of the prototype doc) so it isn't fighting Direwolf for the
  audio path.
