# Bidirectional APRS iGate: Bench Prototype

**Strict Internet-to-RF whitelist, KD3CCO**
Prototype platform: Fedora laptop + Yaesu FTX-1 Optima (USB-C)

---

## 1. Objective

Stand up a working two-way APRS iGate on prototype hardware and prove three things before committing to the permanent Raspberry Pi + TM-V71A build:

1. **Uplink (RF to Internet):** packets heard on 144.390 are gated to APRS-IS. **Confirmed working:** a message sent from RF to the `SMS` gateway reaches the cell phone, so this leg is proven end to end.
2. **Downlink (Internet to RF):** messages arriving from APRS-IS are transmitted on RF, so an SMS reply reaches a field station. This is the leg still to close, because the nearby igate that hears you best (W3SWL-2) is receive-only (`qAO`) and can never deliver a reply back to you.
3. **Strict whitelist:** the only thing ever keyed onto the air is an APRS *message* addressed to your own callsign, or another call on your whitelist. Everything else (positions, telemetry, messages to anyone else) is silently dropped.

This is a bench rig, so the document errs toward low power, a nearby witness receiver, and validating each leg in isolation before going for the full loop.

---

## 2. Equipment

| Role | Item | Notes |
|------|------|-------|
| iGate host | Fedora laptop | Runs Direwolf + hamlib |
| iGate radio | Yaesu FTX-1 Optima | HF/50/70/144/430, one USB-C cable carries CAT + TX control + audio codec |
| Field / witness station | Any 2 m radio (HT is fine) as KD3CCO-7 | Originates the uplink test and receives the downlink test |
| Internet | Home network | APRS-IS reachable outbound on TCP 14580 |
| SMS bridge | NA7Q SMS gateway | Already confirmed working for your call |

Two points about the FTX-1 Optima that shape everything below:

- It exposes **CAT, transmit control, and an audio codec over the single USB connection**, so you do not need an external sound-card interface. Direwolf talks to the built-in codec for audio and keys the radio over USB.
- It has an **internal 1200/9600 APRS TNC**, but you are *not* using it here. Direwolf is the modem. Operate the radio as plain **FM with data audio routed to USB**, and leave the internal APRS/decode function off so the two do not fight over the same audio.

---

## 3. System Architecture

```mermaid
flowchart LR
    Phone["Your phone<br/>2 whitelisted numbers"] <--> Bot["NA7Q SMS Gateway<br/>SMS to/from APRS-IS"]
    Bot <--> IS(("APRS-IS<br/>Internet backbone"))
    IS <--> IG["Fedora laptop<br/>Direwolf iGate<br/>KD3CCO-10"]
    IG <--> Radio["FTX-1 Optima<br/>144.390 MHz FM"]
    Radio <-. direct RF .-> Field["KD3CCO-7<br/>field / witness"]
    Radio <-. RF via digi .-> Digi["W3YA-1 digipeater<br/>Pine Grove Mtn"]
    Digi <-. RF .-> Field
```

Every link is bidirectional. On the bench you will lean on the **direct RF** path between the FTX-1 and a nearby witness radio; the **digi** path (W3YA-1) is what carries the real over-the-air downlink to a distant field station once the bench test passes.

---

## 4. How the Strict Whitelist Works

The rule is one line: a *message* addressed to a whitelisted call gets transmitted, and nothing else does.

```mermaid
flowchart TD
    A["Packet arrives from APRS-IS"] --> B{"Is it an APRS<br/>message packet?"}
    B -->|No| X["DROP<br/>positions, telemetry, and<br/>others' traffic never transmit"]
    B -->|Yes| C{"Addressee on the<br/>whitelist? g/KD3CCO*"}
    C -->|No| X
    C -->|Yes| F{"Within IGTXLIMIT<br/>rate cap?"}
    F -->|No| X
    F -->|Yes| T["TRANSMIT on 144.390"]
```

The whole thing is one config line, `FILTER IG 0 g/KD3CCO*`. Two properties make it strict *and* simple:

- **It is addressee-only, and only messages have an addressee.** Positions, telemetry, and status are broadcasts with no recipient, so they can never match and can never be transmitted. You do not have to enumerate packet types to exclude them.
- **It removes the heard-recently dependency.** Specifying your own IS-to-RF filter replaces Direwolf's default "messages only to stations heard nearby recently" behavior. So a message to a whitelisted call transmits **unconditionally**, subject only to the rate cap. No hop-count reasoning, no `LOC_CNT` dependence. That is exactly the return leg that was missing when your `{26` and `{31` messages kept retrying unacked.

Add more calls by chaining with slashes: `g/KD3CCO*/W3XYZ*`. The wildcard covers all SSIDs of each call.

One trap to avoid: APRS filter tokens are **OR'd**, not AND'd. Adding a source token like `b/NA7Q*` would *widen* the gate (pass anything from that source too), not narrow it. Keep the single `g/` addressee filter, and enforce the "only two phone numbers" rule where it belongs, at the NA7Q gateway registration.

---

## 5. Software Install (Fedora)

```bash
sudo dnf install direwolf hamlib alsa-utils
```

Confirm Direwolf was built with hamlib (needed for CAT PTT):

```bash
direwolf -h 2>&1 | grep -i hamlib   # or: ldd $(which direwolf) | grep -i hamlib
```

Identify the FTX-1's audio codec and serial port after plugging in the USB-C cable:

```bash
arecord -l                # look for "USB Audio CODEC"; note the card number
dmesg | tail -20          # look for the new tty (often /dev/ttyACM0 or /dev/ttyUSB0)
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

Add yourself to the `dialout` group so Direwolf can open the serial port without root, then log out and back in:

```bash
sudo usermod -aG dialout $USER
```

---

## 6. Radio Setup (FTX-1 Optima)

Exact menu labels on this radio are new, so treat these as the settings to locate rather than fixed paths:

1. Band/frequency: **144.390 MHz**, mode **FM** (data/packet FM if offered).
2. Route **TX and RX audio to the USB codec** (the data-in / data-out source set to USB), so Direwolf sends and receives through the USB connection rather than the mic/speaker jacks.
3. Set the radio's **PTT source** to match whichever PTT method you pick in Section 7 (CAT/USB PTT is cleanest).
4. **Disable the internal APRS/TNC decode** so it does not contend with Direwolf for the audio.
5. Start at **low power** (QRP, a few watts) for bench work, ideally into a dummy load or a short antenna, and only raise power once decode and gating are confirmed.

---

## 7. Direwolf Configuration

Save as `~/direwolf.conf`. Replace the three placeholders (card number, PTT model, passcode).

```ini
ACHANNELS 1
ADEVICE  plughw:2,0        # your card # from `arecord -l`

CHANNEL 0
MYCALL   KD3CCO-10
MODEM    1200

PTT RIG XXXX /dev/ttyACM0  # model # from `rigctl --list` (see Section 8)

IGSERVER noam.aprs2.net
IGLOGIN  KD3CCO 123456     # your passcode

FILTER   IG 0 g/KD3CCO*    # web -> RF: ONLY messages to whitelisted calls
IGTXVIA  0                 # bench: direct. Field: IGTXVIA 0 WIDE2-1 (W3YA-1)
IGTXLIMIT 6 10
```

That is the entire gateway. The `FILTER IG 0` line is the only thing between the Internet and your transmitter, so it is the one line to get right; whitelist more calls by chaining, `g/KD3CCO*/W3XYZ*`.

Two intentional omissions for brevity: presence beacons (`PBEACON`/`IBEACON`) and the optional server-side `IGFILTER g/KD3CCO*` prefilter. Neither changes what transmits. The server-side filter only trims inbound bandwidth; add it back if you want a quieter feed. On the path line, `IGTXVIA 0` transmits direct for bench work, and `IGTXVIA 0 WIDE2-1` is the field path, since your testing showed W3YA-1 on Pine Grove Mountain answers WIDE2, not WIDE1.

---

## 8. PTT Ladder

PTT is the single most fiddly part on a radio this new, because hamlib may or may not yet ship a dedicated FTX-1 model. Work down this ladder:

```mermaid
flowchart TD
    S["Direwolf must key the FTX-1"] --> H{"Does hamlib list an<br/>FTX-1 model?<br/>rigctl --list | grep -i ftx"}
    H -->|Yes| H1["PTT RIG model# /dev/ttyACM0<br/>CAT PTT over USB"]
    H -->|No| R{"Radio menu offers<br/>RTS or DTR as PTT?"}
    R -->|Yes| R1["PTT /dev/ttyACM0 RTS"]
    R -->|No| V["Radio data-mode PTT / VOX<br/>last resort only"]
```

Quick CAT test before trusting Direwolf, once you have a model number and port:

```bash
rigctl -m XXXX -r /dev/ttyACM0 T 1   # key TX
rigctl -m XXXX -r /dev/ttyACM0 T 0   # unkey
```

If the radio keys and unkeys cleanly, CAT PTT is your method.

---

## 9. Bench Test Procedure

Run Direwolf in a terminal so you can watch the tag lines:

```bash
direwolf -c ~/direwolf.conf
```

The three tag lines to watch for are `[rx>ig]` (something you heard went up to the Internet), `[ig>rf]` or `[ig>tx]` (something from the Internet was transmitted), and the periodic `IGATE` statistics line that reports `LOC_CNT`.

```mermaid
sequenceDiagram
    participant H as HT field KD3CCO-7
    participant R as FTX-1 iGate radio
    participant D as Direwolf laptop
    participant I as APRS-IS
    participant N as NA7Q Gateway
    participant P as Phone SMS

    Note over H,P: Uplink test (RF to SMS)
    H->>R: APRS message on 144.390
    R->>D: decode
    D->>I: gate up [rx>ig]
    I->>N: deliver to gateway
    N->>P: SMS to phone

    Note over P,H: Downlink test (SMS to RF, strict whitelist)
    P->>N: SMS reply
    N->>I: inject message to KD3CCO-7
    I->>D: routed to your iGate
    D->>D: whitelist check passes
    D->>R: key PTT, transmit [ig>tx]
    R->>H: message received on RF
```

**Test A, decode only.** With the HT, send a beacon or message on 144.390. Confirm Direwolf prints the decoded frame. If nothing decodes, fix RX audio level before anything else.

**Test B, uplink gating.** Confirm the decoded frame produces an `[rx>ig]` line, then check your iGate and KD3CCO-7 appear on aprs.fi. This proves RF to Internet.

**Test C, PTT.** Trigger a transmit (Test D will do it naturally, or use the `rigctl` keying test). Confirm the FTX-1 actually keys and the witness radio hears carrier.

**Test D, downlink with the whitelist.** From one of your registered phone numbers, text the NA7Q gateway a message to KD3CCO-7. Watch for the `[ig>tx]` line and confirm the witness radio receives the message. This proves Internet to RF.

**Test E, strictness (the important negative test).** Arrange or wait for an APRS message addressed to *someone else*, or send yourself a position rather than a message. Confirm Direwolf **does not** transmit it. Passing the negative test is what tells you the whitelist is real and not just permissive-by-luck.

---

## 10. Success Criteria

- [ ] Frames from KD3CCO-7 decode in the Direwolf console.
- [ ] `[rx>ig]` appears and both stations show on aprs.fi. (The full RF-to-SMS path is already confirmed over the air; this just verifies the bench igate does the gating.)
- [ ] The FTX-1 keys under Direwolf control.
- [ ] An SMS to your call comes back out on RF (`[ig>tx]`) and is received. **This is the leg you are here to close.**
- [ ] A non-message, or a message to a non-whitelisted call, is **not** transmitted.

When these hold, the prototype has proven the full bidirectional design with a strict downlink whitelist. Note that with the explicit `FILTER IG 0` in place, delivery no longer depends on `LOC_CNT` or heard-recently state, so it is informational only.

---

## 11. Operating Notes and Cautions

- **Keep bench power low and prefer a dummy load.** A software TNC under test can key unexpectedly while you tune settings; low power and a load protect the band and the radio's finals.
- **Acknowledgments ride the same rails.** When the witness radio receives the message it emits an ack addressed to the gateway; your iGate gates that up automatically, and gateway retries to your call come back down through the same whitelist. No extra config.
- **The two-number limit is not in this file.** It lives at the NA7Q registration. Direwolf only reasons about callsigns.
- **Watch out for double audio paths.** If the FTX-1's internal TNC is left enabled, you can get odd decodes or self-triggered transmits. Confirm it is off.

---

## 12. From Prototype to Production

Once the six criteria pass, the same `direwolf.conf` moves almost verbatim to the permanent build. The differences to expect:

- Swap the Fedora laptop for the Raspberry Pi and the FTX-1 Optima for the Kenwood TM-V71A. The `ADEVICE` and `PTT` lines change to match the Pi's sound-card interface and the TM-V71A's data connector; everything from `IGSERVER` down stays the same.
- Uncomment `IGTXVIA 0 WIDE2-1` as the standing transmit path, since production downlink always goes out through W3YA-1 to reach you in the field.
- Consider a fixed, deliberately rounded beacon position for the permanent site, and wrap Direwolf in a `systemd` service so it restarts on boot.

---

## 13. What Was Actually Built

The prototype above was implemented as a containerised, config-driven deployment
rather than a hand-edited `direwolf.conf`. Files in this repository:

| File | Purpose |
|------|---------|
| `igate.conf` | The only file you edit. Plain `key = value`, no shell syntax. |
| `igate.secrets` | APRS-IS passcode. Gitignored, never committed. |
| `deploy_igate.sh` | The single entry point: `config`, `build`, `up`, `down`, `restart`, `status`, `logs`, `monitor`, `uninstall`. Applies audio levels and sets radio frequency/mode on every `up`. |
| `Dockerfile` / `entrypoint.sh` | Builds and runs the container. |
| `run/` | Generated at runtime: rendered `direwolf.conf`, status page, logs. Gitignored. |

`deploy_igate.sh` renders `direwolf.conf` from `igate.conf` on every start, so the
Direwolf config is a build artefact and never edited by hand.

### 13.1 The two-serial-port problem

Section 8's PTT ladder assumed one serial port. The FTX-1 needs **two**: CAT
control on `/dev/ttyUSB0` (38400 baud) and PTT on `/dev/ttyACM0`. Hamlib has no
native FTX-1 model, but the radio CAT-controls correctly as a **Yaesu FT-991
(rigctl model 1035)**, confirmed independently via WSJT-X.

Direwolf's `PTT RIG model port` directive accepts only **one** port, so it cannot
drive this arrangement directly. The solution is `rigctld` as a bridge:

```
rigctld -m 1035 -r /dev/ttyUSB0 -s 38400 -p /dev/ttyACM0 -P RIG -t 4532 -T 127.0.0.1
```

Direwolf then talks to it as a network rig with `PTT RIG 2 localhost:4532`.
`entrypoint.sh` starts `rigctld` bound to loopback only, then `exec`s Direwolf so
Direwolf becomes PID 1 and receives `docker stop` directly.

### 13.2 Whitelist corrections found in testing

Two config lines were added beyond Section 7, both discovered by live testing:

- **`IGFILTER g/KD3CCO*`** — without a server-side subscription filter, APRS-IS
  falls back to sending only traffic involving stations heard recently on RF.
  On a fresh iGate that set is empty, so *nothing arrives at all* and the
  downlink silently never fires. `FILTER IG 0` alone is not sufficient: it
  governs what may be transmitted, not what the server sends you.
- **`IGMSP 0`** — Direwolf's "Message Sender Position" feature transmits a
  position report from any message's sender *"regardless of any other filtering
  rules"* (its own documentation). This was observed live: after gating SMS
  messages, the SMS gateway's own position beacon was transmitted, bypassing the
  whitelist. `IGMSP 0` disables it. For a project whose entire premise is that
  nothing but whitelisted messages is ever keyed, this is mandatory.

### 13.3 Container security model

The container runs with `--cap-drop=ALL`, `no-new-privileges`, a read-only root
filesystem, a PID limit, no exposed ports, and device access restricted to
exactly the two serial nodes and `/dev/snd`. It runs as the invoking user rather
than root. `rigctld` binds to `127.0.0.1` inside the container only.

---

## 14. Problems Encountered and Their Root Causes

Recorded because most of these were slow to find and none are obvious.

### 14.1 Audio levels — the biggest time sink

The ALSA mixer defaults are wrong in **both** directions, and both fail silently:

- **TX level too high (default 23/37) over-deviates.** The signal is audible and
  visible on a waterfall but decodes nowhere. Calibrated value: **10**.
- **RX capture gain too low (default 1/35)** to decode reliably. Use **35**.

Symptoms look identical to a dozen unrelated faults. Hours went into chasing
modulation type, timing, and receiver settings before the level was the answer.
`deploy_igate.sh up` now re-applies both on every start (§13), because these
reset to defaults whenever the radio's USB re-enumerates.

### 14.2 `[ig>tx]` does not mean "transmitted"

Direwolf prints `[ig>tx]` when a packet **arrives from APRS-IS**, before the
whitelist runs (`igate.c:1837`, ~30 lines before the filter at `igate.c:2261`).
The line that means *actually transmitted* is **`[0L]`**.

Misreading this produced two false "whitelist bypass" alarms and two unnecessary
shutdowns. Verified empirically: during a broken-filter incident, 25 `[ig>tx]`
lines produced **zero** `[0L]` lines — nothing was transmitted. `pfilter()`
returns `-1` on a parse error and igate.c drops anything that is not exactly
`1`, so a malformed filter **fails closed**. `deploy_igate.sh monitor` exists
specifically to present this correctly.

### 14.3 RF getting into the USB cable

At **5 W** with a whip indoors near the laptop, RF crashed the USB link. Because
PTT is asserted over that same link, the unkey never arrived and **the radio
stuck in transmit** — twice, recoverable only by physically power-cycling the
radio. No software watchdog can fix this: when USB dies, the CAT control path
dies with it. Mitigations: a ferrite choke on the USB cable, antenna separation,
lower power, and **the radio's own TOT (time-out timer)**, which is the only
backstop independent of the failed control path. Stable at **1 W** with a choke.

### 14.4 Docker specifics

- **Read-only rootfs + bind-mounted config**: mounting the rendered
  `direwolf.conf` under `/home/igate` failed because that directory is mode
  `700` owned by a different UID. Moved to `/etc/direwolf/` (mode 755).
- **Output buffering**: under `docker run -d`, stdout is a pipe, so Direwolf's
  C stdio block-buffers and `docker logs` appears dead. `stdbuf -oL -eL` in
  `entrypoint.sh` fixes it and must not be removed. `gawk` needs `fflush()` for
  the same reason in the monitor.

### 14.5 The radio mode — the single root cause

The FTX-1 must be in **D-FM (data FM)**, not plain FM. In plain FM the radio
modulates from the **microphone input**, not the USB codec — so Direwolf keys
the radio and transmits a clean carrier with no data on it. Every symptom
follows from this:

- The transmission is audible and visible on a waterfall, but decodes nowhere.
- Sweeping the transmit audio level changes nothing (the audio never reaches
  the modulator regardless of level).
- No digipeater repeats it; no receiver acks it.
- PTT, SWR, frequency, and audio levels all check out, so the obvious
  diagnostics all come back clean.

Verified via CAT: `rigctl M FM` reports `FM`, `rigctl M PKTFM` reports `FM-D`.
`deploy_igate.sh up` now sets frequency and mode on every start and warns
loudly if the radio reports plain `FM`.

This one setting accounted for essentially the entire downlink investigation.
Several elaborate theories were built on top of it — over-deviation, error
correction suppressing uplink gating, and the third-party theory below — and
all of them dissolved once the mode was corrected.

### 14.5.1 A wrong conclusion worth recording: third-party format

For a long stretch this document asserted that the FT5DR could not display
third-party (`}`) wrapped messages, and a whole compatibility bridge was built
around that belief. **That was wrong.**

The evidence looked strong but was confounded twice over:

- Every "working" test carried a `WIDE1-1,WIDE2-1` digipeat path; every failing
  third-party test had none (`IGTXVIA 0`). Format and path were perfectly
  correlated, so neither was isolated.
- All of it ran while the radio was in plain FM, i.e. while *nothing* this
  station transmitted was decodable by anyone.

Retested properly — D-FM, stock third-party gating, no path — the result was
unambiguous:

```
[0.4] KD3CCO-7>APY05D,WIDE1-1,WIDE2-1::SMS      :ack15961
"KD3CCO-7" ACKnowledged message number "15961" from "SMS", Yaesu FT5D
```

The radio received it, unwrapped the third-party header, identified the real
sender, and acked **to `SMS`** — the original sender, per spec. That ack was
then gated back up to APRS-IS, closing the loop properly.

Lesson: when two variables change together, the experiment proves nothing about
either. Confirm the transmit path is known-good *before* drawing conclusions
about packet formats.

### 14.6 The uplink was invisible, not broken

Direwolf prints `[rx>ig]` when it gates an RF packet up to APRS-IS — but only
when the iGate debug level is >= 1 (`igate.c:1604`, fed from `d_i_opt` at
`direwolf.c:1129`). The flag for that is **`-d i`**, not `-d g`.

`-d g` is the **GPS** debug option (`direwolf.c:222`). Enabling it produces no
iGate output whatsoever. Because of that mix-up the uplink direction produced no
log lines at all, which led to a confident but wrong conclusion that RF-to-IS
gating was broken, and then to an elaborate theory about error-corrected packets
being refused. Neither was true — aprs.fi showed `qAR,KD3CCO-10` on the very
packets believed ungated.

Lesson: absence of a log line is evidence about *logging*, not about behaviour.
Confirm against an external source (aprs.fi shows which station gated a packet
via the `qAR`/`qAO` construct) before concluding a subsystem is broken.
`entrypoint.sh` now passes `-d i`, and `deploy_igate.sh monitor` labels these
`RF->IS UP`.

---

## 15. Known Limitations and Future Work

- **A plain-format downlink bridge was built and then removed.** It relayed
  IS->RF messages without the third-party wrapper, for radios believed unable
  to display third-party packets. The FT5DR turned out to handle them correctly
  (§14.5.1), so the bridge solved a problem that did not exist — and would have
  made things worse, since the receiving radio would then ack to `MYCALL`
  instead of the original sender and the SMS gateway would retry forever.
  Deleted. Recorded here so the idea is not re-invented without first checking
  §14.5.
- **Bare-metal mode is untested.** `deploy_igate.sh` supports
  `DEPLOY_MODE = bare-metal`, but only docker mode has been exercised.
- **Audio levels are not auto-calibrated.** The values in `igate.conf` were
  found empirically for one radio at one power level. A calibration helper that
  transmits and checks for a digipeat would be a real improvement.
- **Mixer control names are assumed.** `apply_audio_levels` looks for
  `Speaker Playback Volume` / `Mic Capture Volume`; a different codec will need
  different names. It warns rather than failing.
- **No RF-side watchdog.** Consider enabling the radio's TOT permanently.

### 15.1 What is verified working

| Leg | Evidence |
|-----|----------|
| RF → APRS-IS | Live `[rx>ig] ... qAR,KD3CCO-10` for KQ4BBR-9, NJ3T-4, KF0JZM-7, W3YA-1; messages 34-37 confirmed on aprs.fi |
| APRS-IS → RF | Transmission digipeated by W3YA-1, gated by KB3KPP-1 and W3SWL-2 |
| Message delivery to RF | SMS gateway message displayed on the FT5DR **and auto-acked to `SMS`**, ack relayed back up to APRS-IS — full round trip |
| Strict whitelist | Verified against Direwolf source; non-matching traffic shows `[ig>tx]` with no `[0L]` |
