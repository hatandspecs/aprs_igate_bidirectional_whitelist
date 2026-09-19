# Pi-Gate Setup — Blank Card to Gateway on the Air

Step-by-step build of the **pi-gate**: a headless Raspberry Pi running the
whitelisted bidirectional iGate. The station this was written from runs a **Pi
3B+**; a **3A+** also works, and the few places the two differ are called out.
Written assuming this is your first Raspberry Pi; the Linux, radio and networking
side assumes you know what you're doing.

Everything is done from your laptop. You never attach a keyboard or monitor to
the Pi.

**Contents**

- [What you end up with](#what-you-end-up-with)
- [What you need](#what-you-need)
- [Part 1 — Configure the build](#part-1--configure-the-build)
- [Part 2 — Build the image](#part-2--build-the-image)
- [Part 3 — Write the SD card](#part-3--write-the-sd-card)
- [Part 4 — First boot](#part-4--first-boot)
- [Part 5 — Log in](#part-5--log-in)
- [Part 6 — Point the config at the Pi's hardware](#part-6--point-the-config-at-the-pis-hardware)
- [Part 7 — Confirm it's working](#part-7--confirm-its-working)
- [Checking status](#checking-status)
- [The web monitor](#the-web-monitor)
- [Running the monitor](#running-the-monitor)
- [Editing the whitelist](#editing-the-whitelist)
- [Managing the service](#managing-the-service)
- [Changing WiFi networks later](#changing-wifi-networks-later)
- [Prove it survives a reboot](#prove-it-survives-a-reboot)
- [Shutting down and powering off](#shutting-down-and-powering-off)
- [Troubleshooting](#troubleshooting)
- [Starting over](#starting-over)
- [Appendix — How the image is actually built](#appendix--how-the-image-is-actually-built)

---

## What you end up with

A Pi that you plug into power and forget about. On boot it joins your WiFi,
starts the gateway against the radio on its USB port, and accepts SSH. You
manage it entirely over the network:

```
ssh igate@aprs-igate.local
cd aprs-igate
./deploy_igate.sh monitor
```

The gateway itself behaves exactly as on the laptop — the same `igate.conf`, byte
for byte, the same commands, the same strict whitelist. What differs is a small
per-machine file, `igate.local.conf`, which the image writes for the Pi: it says
`DEPLOY_MODE = bare-metal`, and it is where any Pi-specific device paths go. The
shared `igate.conf` never needs editing for the Pi, so copying it across never
breaks anything. "Configuration layers" in [README.md](README.md) explains what
can override what.

---

## What you need

**Hardware**

| Item | Notes |
|---|---|
| Raspberry Pi 3B+ (or 3A+) | The station in use is a **3B+**: four USB-A ports behind an on-board hub chip, Ethernet as well as WiFi. A **3A+** also works — 512 MB RAM, one USB-A port, **no Ethernet**, so WiFi is the only way in, which is why the credentials get baked into the card either way |
| microSD card, 8 GB or larger | Class 10 / A1 or better. Card quality is the single most common cause of a Pi that boots unreliably — buy a name brand |
| microSD reader for your laptop | Built-in slot is fine |
| 5 V 2.5 A micro-USB supply | **Not** a phone charger you had lying around. An underpowered Pi browns out under load, and on this project that means the USB link to the radio dropping mid-transmission — the exact failure that sticks the radio in TX |
| The radio's USB connection | **FTX-1:** a USB-A to USB-C cable. **VX-6R:** a Digirig Lite, a USB cable for it, and Digirig's VX-6R audio/PTT cable. On a **Pi 3A+** also a powered USB hub (with its own supply) between the Pi and the Digirig; a **Pi 3B+** takes the Digirig directly |
| The radio, antenna, and a real ground/counterpoise | Per the RFI notes in the main README. A VX-6R left running needs DC power (Yaesu E-DC-5B or E-DC-6); the battery does not last. **Not** from the hub or a USB boost cable — see Troubleshooting |

**On a 3B+ nothing extra is needed.** It has a USB hub chip on the board, and the
Digirig enumerates in any of its four ports — and, since the radio's sound card is
found by USB id rather than by port, in *whichever* port, changed at any time.

**On a 3A+ the Digirig needs a powered hub.** That board's single USB port
connects straight to the processor with no hub chip, and it does not detect the
Digirig at all, with the USB-C plug either way round. Put a powered USB hub
between them. The evidence is in
[README.md](README.md#why-the-digirig-needs-a-powered-hub-on-a-pi-3a), and the symptom is
under Troubleshooting. The FTX-1 plugs straight into a 3A+ without a hub.

Both radios have carried traffic on a pi-gate: the VX-6R directly on a 3B+, and
through a powered hub on a 3A+. See the Quickstarts in
[README.md](README.md#quickstarts).

**On your laptop**

`sudo` access, and these tools: `losetup`, `mount`, `rsync`, `openssl`, `curl`,
`xz`, `dd`. All are standard on Fedora. `build_pi_image.sh check` will tell you
if any are missing.

You also need a working `igate.secrets` in the project directory — the Pi can't
log in to APRS-IS without your passcode, and a headless Pi gives you no obvious
symptom when it can't. The build refuses to run without it.

---

## Part 1 — Configure the build

Two files drive the image. `pi.conf` is committed to the repo; `pi.secrets`
holds credentials and is gitignored.

```bash
cd ~/git_repos/aprs_igate_bidirectional_whitelist
cp pi.secrets.example pi.secrets
$EDITOR pi.secrets
```

`pi.secrets` — the login password for the Pi, and every WiFi network it should
be able to join:

```
PI_USER_PASSWORD = something-long-and-not-this

WIFI_1_SSID = HomeNetwork
WIFI_1_PSK  = home-wifi-password

WIFI_2_SSID = Phone Hotspot
WIFI_2_PSK  = hotspot-password
```

Networks are numbered from 1 and the builder reads until a number is missing —
so 1, 2, 3 works and 1, 3 silently stops at 1. Lower numbers are preferred when
several are in range, which is the right ordering for "home network normally,
phone hotspot in the field." Add `WIFI_2_HIDDEN = yes` for a non-broadcasting
SSID, so the Pi actively probes for it rather than waiting to hear a beacon.

Then `pi.conf`:

```bash
$EDITOR pi.conf
```

The settings that matter:

| Setting | Default | Notes |
|---|---|---|
| `PI_HOSTNAME` | `aprs-igate` | Also the mDNS name: `aprs-igate.local` |
| `PI_USER` | `igate` | The account you SSH in as |
| `PI_WIFI_COUNTRY` | `US` | **Required.** Not cosmetic — see below |
| `PI_SSH_PUBKEY` | blank | Path to a public key for key-based login. `check` lists what you have |
| `PI_IMAGE_VARIANT` | `armhf` | 32-bit. Leave it — 512 MB is tight for 64-bit |
| `PI_AUTOSTART` | `yes` | Start the gateway at boot |
| `PI_RADIO` | blank | The radio this Pi drives: `ftx1` or `vx6r` (`ls radios/`). Blank uses `RADIO` from `igate.conf`. Written into the Pi's own `igate.local.conf`; `check` rejects a name with no profile |

**About `PI_WIFI_COUNTRY`:** Raspberry Pi OS keeps the WiFi radio
rfkill-blocked until a regulatory domain is set. Get this wrong on a Pi with no
Ethernet port and the device is simply unreachable. The builder sets it three
different ways to survive the mechanism changing between OS releases, but it
can only set what you give it.

Now validate, before downloading half a gigabyte:

```bash
./build_pi_image.sh check
```

```
Configuration valid.
  hostname     aprs-igate   user igate
  variant      Raspberry Pi OS Lite (armhf)
  wifi         2 network(s), country US
  autostart    yes
  ssh key      none — password login only
  Public keys available on this host:
    /home/natale/.ssh/id_github_20260322.pub
  Set PI_SSH_PUBKEY in pi.conf to one of them for key-based login.
```

Fix anything it complains about and run it again until it's clean.

---

## Part 2 — Build the image

```bash
./build_pi_image.sh build
```

This downloads Raspberry Pi OS Lite (~500 MB, cached in `pi-build/` for next
time), decompresses it, loop-mounts both of its partitions, writes your
configuration in, and unmounts. It asks for your `sudo` password — mounting a
disk image needs root. No Pi is involved; this all happens on the laptop.

The result is `pi-build/aprs-igate-pi.img`, about 2.5 GB.

What went into it:

| Written | Purpose |
|---|---|
| `ssh` on the boot partition | Turns on the SSH server |
| `userconf.txt` | Creates your account. The password is stored as a SHA-512 hash — the plaintext never reaches the card |
| One NetworkManager profile per WiFi network | Mode 600, priority-ordered |
| `/etc/igate/authorized_keys` | Your public key, if configured; moved into place on first boot |
| WiFi country, three ways | Kernel module parameter, `/etc/default/crda`, and a service that runs before NetworkManager |
| `/opt/aprs-igate` | The whole project. `igate.conf` is installed unchanged; your laptop's own `igate.local.conf`, if you have one, is left out |
| `/opt/aprs-igate/igate.local.conf` | Settings for the Pi only: `DEPLOY_MODE = bare-metal`, `WEB_MONITOR = no` (the systemd unit runs it instead), `RADIO` if `PI_RADIO` is set, and commented examples for device overrides |
| `/etc/udev/rules.d/99-igate-cm108.rules` | Lets the `audio` group key a Digirig (CM108) through its `/dev/hidraw` node. Installed whatever the radio |
| `/etc/udev/rules.d/99-igate-watchdog.rules` | Asks for a watchdog check the moment a USB sound card appears, so a replugged interface recovers in seconds rather than at the next minute |
| `/etc/sudoers.d/010-igate-watchdog` | Lets the service account run `systemctl restart aprs-igate.service`, and nothing else, so the watchdog can restart the gateway through systemd |
| `dtoverlay=vc4-kms-v3d,noaudio` in `config.txt` | Turns off HDMI audio, which otherwise takes an ALSA card number at boot, so the radio's sound card is always card 1 |
| `/etc/rpi/swap.conf.d/50-igate.conf` | Keeps swap in compressed RAM with no writeback file on the card |
| Five systemd units | WiFi country → first-boot setup → the gateway → the web monitor → the watchdog |

Your `igate.secrets` is copied at mode 600. Your `pi.secrets` is **not** — the
WiFi keys are already in the NetworkManager profiles and the Pi has no use for
a second copy.

> Both the image file and the finished card contain your WiFi pre-shared keys
> and your APRS-IS passcode in recoverable form. Raspberry Pi OS has no disk
> encryption and the card pulls out with a fingernail. Treat physical
> possession of either as equivalent to possession of those credentials.

---

## Part 3 — Write the SD card

Put the card in your laptop's reader and find its device name:

```bash
lsblk
```

Look for the device whose size matches the card — typically `/dev/sdb` or
`/dev/mmcblk0`. **Get this right.** The next command overwrites the target
completely.

```bash
./build_pi_image.sh flash /dev/sdX
```

**`/dev/sdX` is not a real device, deliberately.** Every flash command in this
document uses it, so that a command pasted without thinking fails instead of
overwriting something that matters. Replace it with what `lsblk` showed you — a
USB reader is usually `/dev/sdb` or similar, a built-in card slot is usually
`/dev/mmcblk0`.

The script refuses anything that isn't a whole removable or hotplug disk —
so it won't take a partition (`/dev/sdb1`) or your system drive — refuses
anything with a mounted partition, shows you `lsblk` output for the target, and
makes you type the device name a second time. Writing takes a few minutes.

If your card auto-mounted, unmount it first (`udisksctl unmount -b /dev/sdX1`,
repeating for each mounted partition) — don't just
eject it in the file manager, which may also power the reader down.

Raspberry Pi Imager works too, if you'd rather: choose **Use custom** and select
`pi-build/aprs-igate-pi.img`. Decline its "customise settings" prompt — the
image already carries all of that, and Imager's own settings would conflict.

When it finishes, pull the card out.

---

## Part 4 — First boot

1. Card into the Pi (contacts facing the board; it only goes in one way).
2. The radio into the Pi's USB port, and the radio on.
   - **FTX-1:** its USB cable. `up` sets 144.390 MHz **D-FM** over CAT.
   - **VX-6R:** on a 3B+, the Digirig straight into a USB port. On a 3A+, a
     powered USB hub into the USB port with its own supply connected and the
     Digirig into the hub; a 3A+ does not detect the Digirig plugged straight in.
     Then Digirig's cable to the radio.
     Tune it to **144.390 MHz FM by hand**, and set it up as in "Yaesu VX-6R on a
     Digirig Lite" in [README.md](README.md#yaesu-vx-6r-on-a-digirig-lite),
     receive battery saver off above all. Nothing on the Pi can set or check the
     frequency.
3. Power last. The Pi has no power switch — plugging it in boots it.

Watch the two LEDs next to the power connector:

- **Red (PWR)** — solid means it has power. If it flickers or dims, your supply
  is inadequate. Fix that before anything else.
- **Green (ACT)** — flickers on SD card activity. You want to see it busy in the
  first minute. Solid red with a completely dark green LED means the Pi isn't
  finding a bootable card.

**The Pi reboots itself once**, early on, while it expands the filesystem to
fill the card. That's normal. Don't pull the power.

Then it joins WiFi and installs Direwolf, hamlib and the mDNS daemon over the
network. Over WiFi this takes **several minutes** — realistically five to ten on
a 3A+'s first boot, less on a 3B+, and less again on a 3B+ over Ethernet. There is
no progress indicator. Be patient before concluding something is wrong.

---

## Part 5 — Log in

```bash
ssh igate@aprs-igate.local
```

`.local` resolution works out of the box on Fedora and macOS. If it doesn't
resolve, the Pi is still booting, isn't on the network, or your router blocks
mDNS. Find it by IP instead:

```bash
avahi-browse -art | grep -i aprs-igate    # or check your router's DHCP leases
nmap -sn 192.168.1.0/24                   # adjust to your subnet
ssh igate@192.168.1.42
```

First connection asks you to accept the host key. If you've flashed this card
before, SSH will refuse with a host key mismatch — expected, since every flashed
card generates its own keys. Clear the old entry, either after seeing the warning
or as a matter of course right after flashing:

```bash
ssh-keygen -R aprs-igate.local
```

Once in, confirm first-boot setup finished:

```bash
systemctl status igate-firstboot
```

You want `Active: active (exited)`. If it's still `activating`, the package
install is still running — wait. `journalctl -u igate-firstboot -f` shows you
what it's doing.

Then check the gateway itself:

```bash
systemctl status aprs-igate
```

With the FTX-1 plugged in, this normally shows `Active: active (exited)` on the
first boot: the radio profile's device names match what both boards assign. If it
failed instead, the Pi numbered the radio's devices differently, and Part 6 fixes
that. Do Part 6's check either way. For the VX-6R, start Part 6 with `lsusb`,
which shows whether the Pi can see the Digirig at all.

---

## Part 6 — Point the config at the Pi's hardware

The Pi enumerates its own USB hardware, so the radio's device names need checking.
Their defaults come from the radio profile — `radios/ftx1.conf` or
`radios/vx6r.conf` — and the Pi's `igate.local.conf` carries a comment at the top
saying so. Start with what the gateway resolved:

```bash
cd ~/aprs-igate
./deploy_igate.sh config
```

`RADIO` says which profile is in use and where it was selected.

With the radio plugged in and powered on:

```bash
arecord -l
```

```
**** List of CAPTURE Hardware Devices ****
card 0: Device [Yaesu FTX-1], device 0: USB Audio [USB Audio]
```

What this tells you depends on the profile.

`radios/vx6r.conf` sets `ADEVICE = auto`, so the card number is not a setting at
all: the Digirig is found by its USB id (`USB_ID = 0d8c:0012`) at every start, in
whatever port it is in. `arecord -l` is then only a confirmation that the kernel
sees the card. `./deploy_igate.sh config` shows which number it resolved to:

```
ADEVICE          = plughw:1,0 (found by USB_ID 0d8c:0012 on ALSA card 1)
```

`radios/ftx1.conf` sets a fixed `ADEVICE = plughw:1,0`, so there the card number
does matter: a different number means overriding `ADEVICE` below. `card 0` means
`plughw:0,0`, and so on. The image turns HDMI audio off so the radio is card 1
every time. On a card built before that change, a radio plugged in after boot
comes up as card 2, and it can do so after a reboot too. To make the FTX-1 behave
like the VX-6R here, set `ADEVICE = auto` and `USB_ID` to what `lsusb` shows for
the radio.

**FTX-1: the serial ports.**

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
```

The FTX-1 presents two: a `ttyUSB` for CAT and a `ttyACM` for PTT. If you see
several, `dmesg | tail -30` right after plugging the radio in tells you which
belongs to what.

**VX-6R: is the Digirig there at all?**

```bash
lsusb
```

```
Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
Bus 001 Device 002: ID 2109:2817 VIA Labs, Inc. USB2.0 Hub
Bus 001 Device 005: ID 0d8c:0012 C-Media Electronics, Inc. USB Audio Device
```

The `0d8c` line is the Digirig. The other entries are hubs: a 3A+ shows the
powered hub you added (its make will vary), and a 3B+ shows its own on-board hubs
and Ethernet controller. If no `0d8c` line appears, nothing else here can work.
See "VX-6R: `lsusb` shows only the root hub" under Troubleshooting.

**VX-6R: the Digirig's PTT node.** The Digirig has no serial port. Its PTT is a
GPIO pin on its sound chip, reached through a `/dev/hidraw` node:

```bash
ls -l /dev/hidraw*
```

```
crw-rw---- 1 root audio 243, 0 ... /dev/hidraw0
```

The Digirig's node should show group `audio` and `crw-rw----`: the image's udev
rule does that. Nothing needs setting, because `deploy_igate.sh` finds the node on
the same USB device as the `ADEVICE` card. `config` shows it on the `CM108_DEVICE`
line. If that line says none was found, the Digirig is not plugged in, or — on a
profile with a fixed `ADEVICE` — it names the wrong card. If the node shows `root root` and `crw-------`, the
rule did not apply: unplug and replug the Digirig.

If the Pi's values differ from the profile, **override them in the Pi's
`igate.local.conf`** — not in `igate.conf`, which is shared with every machine, and
not in `radios/ftx1.conf`, which describes the radio wherever it is used:

```bash
cd ~/aprs-igate
nano igate.local.conf
```

(`~/aprs-igate` is a symlink to `/opt/aprs-igate`, which is where the project
actually lives. Either path works.)

Uncomment and set only the lines that differ — for example:

```
ADEVICE = plughw:0,0
```

Leave everything else alone: `MYCALL`, `RADIO_MODE = PKTFM`, the audio levels, the
whitelist. If the Pi's devices already match the profile, there is nothing to add.

Check your work without starting anything:

```bash
./deploy_igate.sh config
```

It prints every resolved setting, masks the passcode, and shows the Direwolf
filter your whitelist compiles to. It then lists which layer each setting came
from: `DEPLOY_MODE` should be credited to `igate.local.conf`, and any override you
added appears under *Overrides in effect*. Then start it:

```bash
sudo systemctl restart aprs-igate
systemctl status aprs-igate
```

`Active: active (exited)` is success — the unit is `oneshot`, so it starts
Direwolf in the background and exits. That's the expected steady state, not a
crash.

---

## Part 7 — Confirm it's working

```bash
cd ~/aprs-igate
./deploy_igate.sh status
./deploy_igate.sh monitor
```

`status` should report `iGate running (bare-metal): direwolf pid N, rigctld pid
M` for the FTX-1, or `direwolf pid N, no rigctld (CAT = none)` for the VX-6R,
which has no CAT and so no `rigctld`. Then watch `monitor` for a minute or two
and look for `RF RX` lines — real
packets being decoded off the air. If nothing appears while there's audible
activity on 144.390, your RX gain is wrong; see the audio levels section in the
main README.

`igate.test.conf` in the project directory is a ready-made test that forces
every transmission through a named digipeater and gates only what that
digipeater repeated. It leaves `igate.conf` alone — see "Forcing a digipeat
path" in [README.md](README.md), and the instructions in the file's own header.

For the full set of on-air tests — proving your transmitted signal is
decodable, proving the whitelist drops what it should, and the round trip
through the SMS gateway — follow **Testing it** in
[README.md](README.md#testing-it). Those procedures are identical on the Pi;
just swap `docker exec -i aprs-igate kissutil` for plain `kissutil`.

Confirm the local control ports are shut:

```bash
ss -tln | grep -E '8000|8001'
```

Silence is correct. Direwolf can listen for AGW and KISS TCP clients, binds them
to `0.0.0.0`, and authenticates nothing — on a shared WiFi network anything that
can reach port 8001 could transmit under your callsign. `AGW_PORT` and
`KISS_PORT` both default to `0` in `igate.conf` for that reason. On the laptop
this was masked by Docker publishing no ports; the Pi has no such boundary.

Enable `KISS_PORT = 8001` only for the duration of a `kissutil` test, then set it
back and restart.

Keep your radio's **time-out timer** set. Everything in the main README's safety
notes applies here, and more so — the Pi is in a corner somewhere rather than
open on your desk where you'd notice it keyed up.

---

## Checking status

Three separate questions, three commands. Run them in this order when you're not
sure where things stand.

```bash
# 1. Did first-boot setup (the package install) finish?
systemctl status igate-firstboot

# 2. Is the gateway service up?
systemctl status aprs-igate

# 3. What does the gateway itself say?
cd ~/aprs-igate && ./deploy_igate.sh status
```

Both units at once, without the pager:

```bash
systemctl status igate-firstboot aprs-igate --no-pager
```

### Reading the `Active:` line

Both units are `Type=oneshot` — they do their work and return rather than
staying in the foreground — so the healthy state is not the `running` you might
expect:

| `Active:` says | Means |
|---|---|
| `active (exited)` | **Success.** Did its job and returned. This is the goal for both units |
| `activating (start)` | Still working. For `igate-firstboot`, `apt` is still installing |
| `failed` | Something went wrong — see below |
| `inactive (dead)` | Never started |

`systemctl is-active aprs-igate` gives just the one word, if that's all you want.

### What `deploy_igate.sh status` adds

`systemctl` only knows whether the *start command* succeeded. The gateway's own
status command checks whether the processes are actually alive:

```
iGate running (bare-metal): direwolf pid 812, rigctld pid 806
```

Two PIDs is what you want — `rigctld` bridging CAT and PTT to the radio, and
`direwolf` doing the modem and gating work. `iGate not running (bare-metal).`
means they aren't there, regardless of what systemd thinks.

It also writes `run/status.html`, which is of limited use on a headless Pi;
ignore the `file://` path it prints, or `scp` the file to your laptop.

### When something failed

```bash
journalctl -u igate-firstboot -n 50    # package install problems
journalctl -u aprs-igate -n 50         # why the gateway wouldn't start
cat ~/aprs-igate/run/direwolf.log      # what Direwolf itself said
```

Add `-f` to either `journalctl` command to follow it live — useful while
first-boot setup is still running, which otherwise gives no sign of progress.

See [Troubleshooting](#troubleshooting) for what the common failures mean.

---

## The web monitor

The image runs a read-only web page on the LAN, so you can watch packet flow from
a phone or laptop without an SSH session:

```
http://aprs-igate.local:8080/
```

Live packet flow with the same labels as the terminal monitor, plus a panel
showing the callsign, whitelist, resolved filter, beacon and transmit path. It
works on a phone; the timestamp column collapses on narrow screens.

**On Android, use the IP address, not `aprs-igate.local`.** mDNS is built into
macOS and iOS and is configured on most Linux desktops, but Android browsers have
no mDNS resolver, so the `.local` name simply does not resolve there:

```
http://192.168.1.42:8080/        <- the Pi's address; find it with: hostname -I
```

Worth reserving that address in your router's DHCP settings, since the name is
not an option from every device.

Check it's up:

```bash
systemctl status igate-web --no-pager
ss -tln | grep 8080
```

Turn it off per-build with `PI_WEB_MONITOR = no` in `pi.conf`, or right now with
`sudo systemctl disable --now igate-web`.

On the Pi, `igate-web.service` is what runs it, not `deploy_igate.sh`. That is why
the Pi's `igate.local.conf` says `WEB_MONITOR = no`. It stops `up` starting a
second copy that would fight the service for port 8080. On a laptop in docker
mode it is the other way round: `./deploy_igate.sh up` starts the monitor, at
`http://localhost:8080/` on the laptop. `aprs-igate.local` is always the Pi's
name, never the laptop's.

**Read-only, and unauthenticated.** It cannot edit the whitelist, restart the
gateway, or show the raw log — there is no POST handler at all, and the APRS-IS
passcode is dropped rather than merely masked. But anyone on your WiFi can view
it, so treat it as a trusted-LAN tool. Whitelist edits stay on SSH, where you
have key-based authentication; see "Editing the whitelist" below.

If you want it reachable away from home, add a VPN (WireGuard or Tailscale)
rather than forwarding a port — a VPN makes a remote device look local and needs
no change to the page.

## Running the monitor

This is the day-to-day view:

```bash
ssh igate@aprs-igate.local
cd aprs-igate
./deploy_igate.sh monitor
```

It prints a legend, then annotates Direwolf's output live:

```
17:23:49  RF RX      AA3BR>SYRV6V,N3KTX-1,WIDE1,W3YA-1,WIDE2*:`h@dl#GYY`"5+}_0
17:23:49  RF->IS UP  AA3BR>SYRV6V,...,qAR,KD3CCO-10:`h@dl#GYY`"5+}_0
                       MIC-E, Yaesu/Standard*, Yaesu FT3D, Off Duty
17:24:02  IS GATED   SMS>APOSMS,TCPIP*,qAC,WA7BF::KD3CCO-7 :@4848324995 hello{99
17:24:11  IS DROP    QRX>APQRX,TCPIP*,qAC::KC3WRY-14:not whitelisted{1
```

`IS GATED` means it matched the whitelist and **was transmitted**. `IS DROP`
means it arrived and was refused — seeing those is the whitelist working.

**Ctrl-C stops the monitor, not the gateway.** It's a live tail of the log; the
gateway keeps running. Likewise, closing your SSH session doesn't stop it.

`./deploy_igate.sh monitor raw` drops the indented decode lines if you want it
terse. `./deploy_igate.sh logs` shows Direwolf's unannotated output — note that
`monitor` redacts your APRS-IS passcode from the login line and `logs` does not,
so prefer `monitor` if you're going to paste output anywhere.

**To keep a monitor running across SSH sessions**, use a terminal multiplexer.
Not installed by default on Lite:

```bash
sudo apt install tmux
tmux new -s igate
cd aprs-igate && ./deploy_igate.sh monitor
```

Detach with `Ctrl-B` then `d`; the monitor keeps running. Reattach any time,
from any machine, with `tmux attach -t igate`.

---

## Editing the whitelist

The whitelist is one line in `igate.conf`. Only APRS **messages** addressed to
these callsigns are ever transmitted onto RF — positions, telemetry, and
messages to anyone else are dropped.

There are two ways to change it on a running pi-gate:

- **On the Pi, over SSH** — the whitelist belongs to that Pi alone and the
  repository is left as it is. A card built later installs the repository's
  `igate.conf`, so re-apply the change after rebuilding.
- **On the laptop, pushed over SSH** — the repository and the Pi stay identical,
  and future cards carry the same list. See
  [From the laptop](#from-the-laptop-pushed-over-ssh) below.

Both need a restart to take effect, and a restart re-sends the beacon about a
minute later.

### On the Pi, over SSH

```bash
ssh igate@aprs-igate.local
cd aprs-igate
nano igate.conf
```

Find:

```
WHITELIST_CALLS = KD3CCO*
```

Comma-separated, and `*` covers all SSIDs of a call:

```
WHITELIST_CALLS = KD3CCO*, W3XYZ*, N0CALL-9
```

- `KD3CCO*` matches `KD3CCO`, `KD3CCO-7`, `KD3CCO-10`, …
- `N0CALL-9` matches that one SSID and nothing else.
- A `*` must be at the **end** of a pattern. Anywhere else and Direwolf rejects
  the filter — which fails closed, dropping everything, so the symptom is a
  gateway that transmits nothing rather than one that transmits too much.

Save, then check what it compiled to **before** restarting:

```bash
./deploy_igate.sh config
```

The last line shows the actual filter:

```
Resolved Direwolf FILTER: IG 0 g/KD3CCO*/W3XYZ*/N0CALL-9
```

`g/` is the APRS-IS "Group Message" filter — addressee-only, which is what makes
this a message whitelist rather than a general traffic filter. Confirm every
call you expect is in that line, then apply it:

```bash
sudo systemctl restart aprs-igate
```

The config is re-read and `direwolf.conf` regenerated on every start, so a
restart is all it takes. Watch `monitor` afterwards: traffic to a newly added
call should show as `IS GATED`, and anything else as `IS DROP`.

The change lives only on this Pi. A card built later starts from the
repository's `igate.conf`, so keep a note of the list, or make the same change in
the repository if future cards should have it.

### From the laptop, pushed over SSH

Edit the repository's copy and send it to the Pi, so both always match. From the
project folder on the laptop:

```bash
$EDITOR igate.conf                                             # edit WHITELIST_CALLS
./deploy_igate.sh config | grep -E 'WHITELIST_CALLS|FILTER'   # check it compiles
scp igate.conf igate@aprs-igate.local:aprs-igate/igate.conf
ssh -t igate@aprs-igate.local 'cd aprs-igate && ./deploy_igate.sh config | grep -E "WHITELIST_CALLS|FILTER" && sudo systemctl restart aprs-igate && ./deploy_igate.sh status'
```

The second `config` runs on the Pi, and its `Resolved Direwolf FILTER:` line is
the one that counts: confirm every call you expect is in it. The `&&` chain stops
before the restart if that `config` fails.

Copying `igate.conf` over the Pi's replaces nothing specific to the Pi. Its
deployment mode, radio (`RADIO = vx6r`), device overrides and `DEVICE_WAIT` live in
the Pi's own `igate.local.conf`, which `scp` does not touch — which is exactly why
those settings are kept out of `igate.conf`.

`ssh -t` gives the remote command a terminal so `sudo` can ask for the password.
Expect password prompts for `scp`, `ssh` and `sudo`; setting `PI_SSH_PUBKEY` in
`pi.conf` before building a card removes the first two. Commit the `igate.conf`
change as usual.

> Only add callsigns you're authorised to transmit on behalf of. Adding a call
> to this list means your station will key up carrying messages addressed to
> that operator.

The recipient's radio or app must handle APRS **third-party** packets. Messages
from APRS-IS go out wrapped as `KD3CCO-10>APDW18:}SMS>...::THEIRCALL :text`,
because the gateway must not transmit under the sender's callsign. An app that
ignores packets starting with `}` never shows the message and never acknowledges
it. The monitor then shows `IS GATED` on every retry, and the sender's gateway
keeps resending. Send a new recipient a test message first: an `RF RX` line
carrying `:ack` from their call proves it works. See "Editing the whitelist" in
[README.md](README.md#editing-the-whitelist).

---

## Managing the service

The gateway runs under systemd as `aprs-igate.service`.

| Task | Command |
|---|---|
| Is it running? | `systemctl status aprs-igate` |
| Web monitor state | `systemctl status igate-web` |
| Watchdog state | `systemctl status igate-watchdog.timer` and `journalctl -u igate-watchdog -n 30` |
| Calibrate audio levels | `cd ~/aprs-igate && ./deploy_igate.sh audio` |
| Start / stop | `sudo systemctl start aprs-igate` / `sudo systemctl stop aprs-igate` |
| Apply a config change | `sudo systemctl restart aprs-igate` |
| Why did it fail? | `journalctl -u aprs-igate -n 50` |
| Don't start at boot any more | `sudo systemctl disable aprs-igate` |
| Gateway's own view | `cd ~/aprs-igate && ./deploy_igate.sh status` |

`Active: active (exited)` is the healthy state. The unit is `Type=oneshot`: it
launches Direwolf in the background and returns, so systemd has nothing
left in the foreground to supervise.

The gateway comes up by itself when its radio does. `up` first waits up to 60
seconds for the radio's USB devices (`DEVICE_WAIT` in the Pi's
`igate.local.conf`). If they still aren't there, it fails, and the unit's
`Restart=on-failure` runs it again every 30 seconds for as long as it takes. A
radio plugged in or switched on after boot therefore needs nothing from you;
`systemctl status aprs-igate` shows `activating (auto-restart)` while it waits.
A gateway that started successfully and then died is not restarted by this — that
is what the watchdog below is for.

### The watchdog

`igate-watchdog.timer` runs `./deploy_igate.sh watchdog` every minute, and a udev
rule runs the same check the moment a USB sound card appears. Together they cover
the failures `Restart=on-failure` cannot see, because they happen after a start
has succeeded and leave the unit `active (exited)`:

* Direwolf died, or was killed.
* The interface was unplugged and replugged, possibly into a different USB port,
  and came back as a different ALSA card.
* The USB device was **reset in place** — RF getting into the cable does this.
  `lsusb` still lists it, the card number has not moved, and Direwolf keeps
  running while logging `Audio input device 0 error code -19` and decoding
  nothing. This is the failure that used to need a human.

It also reports, without restarting anything, the one failure it cannot fix: the
**radio itself** being switched off, flat, retuned, turned down, or unplugged from
its antenna. The VX-6R has no CAT link, so none of that can be read back — the
gateway keeps beaconing into a dead radio and reports itself as running. What
stops is decoding, so after `RF_QUIET_MINUTES` (default 30) of hearing nothing it
says so once, and says `hearing RF again` when a packet arrives. `status` carries
the same information as a `Last RF decode:` line.

The check is silent when nothing is wrong, so anything in its journal is
something it did:

```bash
journalctl -u igate-watchdog -n 30 --no-pager
```

```
watchdog: waiting for the radio (missing: ALSA-card-1 CM108-PTT-device)
watchdog: the radio is back
watchdog: Direwolf logged 3 new audio device errors — restarting the gateway
watchdog: restart complete
```

An unplugged radio is waited for rather than restarted into, and said once rather
than once a minute. Restarts are limited to one every three minutes, since each
one interrupts gating and can wait `DEVICE_WAIT` for the radio; the limit clears
as soon as the radio is seen to be missing, so a replug is never made to wait out
a limit set before it happened.

A replug is usually caught by the udev rule rather than the timer, which shows up
in the journal as a run a few seconds after the plug goes in, out of step with the
timer's one-a-minute cadence. An unplug and replug inside one minute is normally
never seen as an absence at all — the first the watchdog knows of it is the udev
event, and it restarts from there.

The restart goes through `systemctl restart aprs-igate.service`, which is what the
sudoers drop-in is for: done that way, the new Direwolf belongs to the gateway's
own unit and systemd's view stays accurate. If systemd cannot be reached the
watchdog says so and does the stop and start itself.

To turn it off: `sudo systemctl disable --now igate-watchdog.timer`.

**On a Pi built before the watchdog existed**, you do not have to rebuild the
card: see [Updating a running pi-gate over SSH](#updating-a-running-pi-gate-over-ssh).

You can also drive the script directly (`./deploy_igate.sh up` / `down` /
`restart`), which does the same work. Prefer `systemctl` so systemd's view of
the service stays accurate.

---

## Updating a running pi-gate over SSH

Most changes to this project are the script, the radio profiles and the docs, all
of which live in `/opt/aprs-igate` and can simply be copied over. Rebuilding a
card is only needed for what the build writes *outside* that directory — the boot
partition's `config.txt`, `/etc/fstab`, the swap drop-in, the first-boot script —
or for a Pi you want to reproduce from scratch.

The watchdog is the awkward middle case: it is mostly script, but it also needs
two systemd units, a sudoers drop-in and a udev rule. `build_pi_image.sh` writes
those four out for copying, from the same text it puts on a card, so an updated Pi
and a freshly built one cannot drift apart.

**1. On the laptop**, from the project folder, with the changes committed:

```bash
./build_pi_image.sh watchdog-files          # writes pi-build/watchdog/
rsync -av \
  --exclude '.git/' --exclude 'run/' --exclude 'pi-build/' \
  --exclude '__pycache__/' --exclude 'pi.secrets' \
  --exclude 'igate.local.conf' --exclude 'igate.secrets' \
  --exclude 'scratch_notes.txt' \
  ./ igate@aprs-igate.local:aprs-igate/
```

The excludes are the point: `igate.local.conf` is the Pi's own (`DEPLOY_MODE`,
`RADIO`, `DEVICE_WAIT`, any device override) and `igate.secrets` is already there
at mode 600. There is no `--delete`, so nothing on the Pi is removed; a file you
deleted in the repository stays behind until you remove it there by hand.

**2. Copy the four system files and install them**, still from the laptop:

```bash
ssh igate@aprs-igate.local 'mkdir -p /tmp/wd'
scp pi-build/watchdog/* igate@aprs-igate.local:/tmp/wd/
ssh -t igate@aprs-igate.local '
  sudo install -m 644 -o root -g root /tmp/wd/igate-watchdog.service /etc/systemd/system/ &&
  sudo install -m 644 -o root -g root /tmp/wd/igate-watchdog.timer   /etc/systemd/system/ &&
  sudo install -m 644 -o root -g root /tmp/wd/99-igate-watchdog.rules /etc/udev/rules.d/ &&
  sudo install -m 440 -o root -g root /tmp/wd/010-igate-watchdog /etc/sudoers.d/ &&
  sudo visudo -c &&
  sudo udevadm control --reload-rules &&
  sudo systemctl daemon-reload &&
  sudo systemctl enable --now igate-watchdog.timer &&
  rm -rf /tmp/wd'
```

`sudo visudo -c` in the middle of that chain is not decoration. A malformed file
in `/etc/sudoers.d/` breaks `sudo` for every user on the machine, and on a headless
Pi that is a reflash. The chain stops there if it does not parse — which is why it
is `&&` throughout and why the file is installed from a generated copy rather than
typed.

**3. Check, on the Pi:**

```bash
ssh igate@aprs-igate.local
cd aprs-igate
grep -n '^ADEVICE' igate.local.conf     # nothing? good. See below if there is
./deploy_igate.sh config | grep -E 'ADEVICE|USB_ID|CM108_DEVICE'
sudo systemctl restart aprs-igate
./deploy_igate.sh status
systemctl list-timers igate-watchdog.timer --no-pager
journalctl -u igate-watchdog -n 20 --no-pager
```

`config` should now credit the card to the USB id:

```
ADEVICE          = plughw:1,0 (found by USB_ID 0d8c:0012 on ALSA card 1)
USB_ID           = 0d8c:0012
```

**An `ADEVICE` line in the Pi's `igate.local.conf` overrides all of this**, because
a local file outranks the radio profile. If an older card number is set there —
`ADEVICE = plughw:2,0` from a Pi 3A+, say — comment it out, or the gateway keeps
looking at a fixed card and the watchdog has nothing to re-resolve.

An empty watchdog journal is the healthy state: it prints only when it acts.

---

## Changing WiFi networks later

Once you can reach the Pi, add a network from the command line:

```bash
sudo nmcli device wifi connect "NewSSID" password "newpassword"
```

That persists across reboots. To take it to a field site on a phone hotspot,
add the hotspot **before** you leave, while you still have a way in.

If you can no longer reach the Pi at all, the recovery path is the SD card.
Put it in your laptop and mount the second (ext4) partition:

```bash
lsblk                                    # find the card
sudo mount /dev/sdX2 /mnt
sudo ls /mnt/etc/NetworkManager/system-connections/
sudo nano /mnt/etc/NetworkManager/system-connections/HomeNetwork.nmconnection
sudo umount /mnt
```

Edit the `ssid=` and `psk=` lines. Keep the file at mode 600 — NetworkManager
silently ignores a profile that's group- or world-readable, which looks
identical to a wrong password.

Rebuilding the card from scratch also works and is often faster: update
`pi.secrets`, then `build` and `flash` again.

---

## Prove it survives a reboot

First boot and everyday running are different code paths. `igate-firstboot` only
runs once — on later boots its `ConditionPathExists` marker exists, so systemd
skips it — and a skipped condition counts as success, so `aprs-igate` starts
normally behind it. The marker lives in `/var/lib/`, deliberately outside the
`run/` tmpfs that would lose it. That is worth proving on your own hardware
rather than discovering during a power cut:

```bash
sudo reboot
# wait a couple of minutes
ssh igate@aprs-igate.local
systemctl status aprs-igate --no-pager
cd aprs-igate && ./deploy_igate.sh status
```

`active (exited)` and two PIDs means unattended restarts work. Do this once,
deliberately, while you are sitting in front of it.

Worth proving in the same sitting, because it is the failure that actually happens
in the field: pull the radio interface's USB plug out while the gateway is
running, wait ten seconds, and put it back — in a *different* port. Within a
minute, and usually within a few seconds:

```bash
journalctl -u igate-watchdog -f
```

should show the radio going missing, coming back, and the gateway being restarted,
and `./deploy_igate.sh config` should show `ADEVICE` resolved to whatever card
number it landed on this time.

## Shutting down and powering off

```bash
sudo shutdown -h now
```

As a one-liner from your laptop, note the **`-t`**: `sudo` needs a terminal to
prompt for a password, and `ssh host 'cmd'` does not allocate one, so without it
you get *"a terminal is required to read the password"*.

```bash
ssh -t igate@aprs-igate.local 'sudo shutdown -h now'
```

Either way the session ends with `Connection reset by peer` — that is the Pi
going down, not an error.

**What the LEDs do at the end of a shutdown:** the green ACT LED usually flashes
several times and then stops, and the red PWR LED **stays lit**. Red staying on
is not a sign that anything is still running — the Pi cannot switch off its own
power rail, so red is lit whenever power is applied and it never goes out by
itself. Depending on firmware, green may end up dark or simply stop flickering.

What you are waiting for is the *flickering* to stop, not for either LED to go
out. Once the SSH session has dropped and green has settled, twenty seconds is
ample — and on this build the card is barely written in the first place (§16.8),
so an imperfect moment to unplug is a much smaller risk than it would otherwise
be.

**Can you just pull the plug instead?** Largely yes — the image is built for it.

The card is only at risk while something is writing to it, so the build removes
the routine writers:

| Writer | What the image does |
|---|---|
| `run/` — `direwolf.log`, `direwolf.conf`, `status.html`, pidfiles | Mounted as a 32 MB **tmpfs**. All of it is regenerated on each start, so it lives in RAM and never touches the card |
| The packet log growing without bound | Rotated hourly at 8 MB, 2 generations, so it cannot exhaust that tmpfs |
| The systemd journal | `Storage=volatile` — kept in `/run`, capped at 16 MB |
| Swap | A `dphys-swapfile` swap **file on the card** is removed at first boot. Trixie's **zram** (compressed swap in RAM) is kept, but set to `Mechanism=zram`: its default also writes idle pages out to a `/var/swap` file on the card. `swapon --show` reporting `/dev/zram0` is expected |

What remains is a card that is written when you deliberately change something,
and essentially never otherwise.

**Two consequences to know about.** Nothing in `run/` survives a reboot, so the
packet log starts empty each time and `journalctl` cannot show you a previous
boot. For an appliance that is the right trade, but it does mean a post-mortem
after an unexpected power cut has little to work with.

**The remaining risk is the radio, not the card.** With the FTX-1, PTT is a CAT
command over USB, so if the Pi loses power mid-transmission the unkey command is
never sent and **the radio can stay keyed**. A Digirig is powered by the same USB
port, so its PTT should drop with the power, though that has not been tested. Transmissions are rare and brief on a
whitelist-only gate, so the window is small — but it is the same failure mode as
an RF-induced USB crash, and the radio's time-out timer is the only thing that
ends it. Keep the TOT set, and if you are unplugging deliberately, glance at
`monitor` first to confirm nothing is transmitting.

Making the card literally immune (a read-only overlay root) and getting a clean
shutdown on unplug (a supercapacitor or battery HAT) are both recorded as future
work in §15 of the [design document](aprs-igate-prototype-test.md).

---

## Troubleshooting

**Nothing on the network after 10+ minutes.**
Most likely the WiFi credentials or the country code. With no Ethernet port
there's no way in to check, so pull the card and inspect it on your laptop:

```bash
sudo mount /dev/sdX2 /mnt
sudo cat /mnt/etc/NetworkManager/system-connections/*.nmconnection   # SSID and PSK correct?
sudo cat /mnt/etc/modprobe.d/cfg80211-regdom.conf                    # country set?
sudo ls -l /mnt/var/lib/igate-firstboot-done                         # setup completed?
sudo umount /mnt
```

If `/var/lib/igate-firstboot-done` exists, the Pi did boot and did reach the network at least
once — so the problem is name resolution, not the Pi. Look for it by IP.

**SSH says the host key changed.** You reflashed the card. `ssh-keygen -R
aprs-igate.local` and reconnect.

**`Dependency failed for aprs-igate.service`, and it shows `inactive (dead)`.**
Seen on cards built before `aprs-igate.service` was changed to depend on
first-boot setup with `Wants=` rather than `Requires=`: the gateway never tried
to start, because the unit it required had failed. Newer cards start anyway and
fail on their own merits instead. Either way the real failure is upstream:

```bash
journalctl -u igate-firstboot -n 60 --no-pager
```

Almost always this is `apt`. The first-boot script is idempotent and safe to
re-run by hand, which also shows you the error live rather than through the
journal:

```bash
sudo /usr/local/sbin/igate-firstboot.sh
sudo systemctl start aprs-igate
```

Two distinct `apt` failures show up here.

**`404 Not Found` on a `.deb`.** The image carries a package index from whenever
it was built, so by the time you boot it may name versions that have since been
removed from the archive. Retrying the install cannot fix that — the index has
to be refreshed:

```bash
sudo rm -rf /var/lib/apt/lists/*
sudo apt-get update
sudo apt-get install -y direwolf libhamlib-utils alsa-utils avahi-daemon gawk
sudo /usr/local/sbin/igate-firstboot.sh
```

**Network not ready.** `network-online.target` means NetworkManager obtained an
address, which doesn't guarantee DNS is answering. Confirm with
`ping -c2 deb.debian.org`.

Cards built after this was addressed handle both: they wait for DNS, then retry
the whole *update-then-install* cycle five times, discarding the cached index
from the second attempt onward. They also carry `igate-firstboot.timer`, which
re-runs setup every 10 minutes until it succeeds, and depend on it with `Wants=`
rather than `Requires=` — so a failed attempt no longer blocks the gateway
indefinitely, and an archive outage heals itself without anyone logging in.

**`docker not found`, or the Pi tries to run Docker.** The Pi's
`igate.local.conf` is missing, so `DEPLOY_MODE` has fallen back to its default,
`docker`. `./deploy_igate.sh config` confirms it, listing `DEPLOY_MODE` under
*built-in default*. Recreate the file:

```bash
echo 'DEPLOY_MODE = bare-metal' > ~/aprs-igate/igate.local.conf
sudo systemctl restart aprs-igate
```

`down` still works in this state — it stops a running bare-metal gateway even when
the mode resolves to docker — so the gateway can always be stopped first.

This is also the one step needed before **updating a Pi built before
`igate.local.conf` existed** with newer files from the repository. The old
`igate.conf` on such a Pi carries `DEPLOY_MODE = bare-metal` itself; the current
one deliberately does not. Create the local file first, then copy the new files
across.

**`systemctl status aprs-igate` shows `failed`.**

```bash
journalctl -u aprs-igate -n 50
cat ~/aprs-igate/run/direwolf.log
```

In order of likelihood: `ADEVICE` doesn't match `arecord -l` (the gateway
refuses to start without its audio device, deliberately — otherwise PTT would
still key the radio and transmit an unmodulated carrier); `CAT_DEVICE` or
`PTT_DEVICE` wrong, which shows as `rigctld exited during startup`; or the radio
simply isn't powered on.

**`rigctld exited during startup`.** Wrong serial device or wrong `RIG_MODEL`.
Verify by hand:

```bash
rigctl -m 1035 -r /dev/ttyUSB0 -s 38400 f
```

That should print the radio's current frequency.

**Permission denied on `/dev/ttyUSB0`.** The account needs the `dialout` group,
which first-boot setup grants. If you changed users or something went wrong:
`sudo usermod -aG dialout,audio igate`, then log out and back in — group
membership is applied at login.

**`monitor` shows `RF RX` but nothing ever gets gated up.** Check the APRS-IS
login handshake in the log — look for the `IS LOGIN` line and a server response
accepting it. A wrong passcode shows as an unverified login.

**Direwolf version differs from the laptop's.** Raspberry Pi OS ships whatever
Debian packages — currently Direwolf 1.7 on Trixie, against 1.8.1 on Fedora.
Nothing this project relies on is known to differ between them, but an
unrecognised directive is only a *warning* in Direwolf, not an error, so a
missing feature fails silently. Check the config parsed cleanly after the first
start:

```bash
grep -i 'config file\|unrecog\|not recognized\|invalid' ~/aprs-igate/run/direwolf.log
```

One hit is expected and harmless:

```
Config file: FILTER IG ... on line 22.
```

Direwolf prints that for *any* use of `FILTER IG`, followed by "Warning! Don't
mess with IS>RF filtering unless you are an expert and have an unusual
situation." Deliberate IS→RF filtering is the whole point here, and the filter
is still stored and applied — verified in Direwolf's source, where the warning
is immediately followed by the assignment that saves it.

Anything else is worth reading. The directive that matters most is `IGMSP 0` —
it disables Direwolf's courtesy position report for message senders, which
bypasses the whitelist entirely and *will* transmit a station you never
authorised if it is not honoured. No complaint about `IGMSP`, `IGFILTER`,
`IGTXVIA` or `IGTXLIMIT` means the version you have parsed all of them.

**Gateway transmits but nobody decodes it.** This is the radio, not the Pi. On the
FTX-1, plain FM modulates from the microphone input rather than the USB codec,
producing a clean carrier with no data in it. The radio must be in **D-FM**. See
the radio setup section in [README.md](README.md#radio-setup--the-one-that-matters).
On the VX-6R there is no such mode, so the usual cause is transmit level: lower
`TX_AUDIO_LEVEL` in the Pi's `igate.local.conf`, or the radio's Set Mode 37
`MCGAIN`, one at a time, and repeat the digipeat test from "Testing it" in
README.md.

**VX-6R: `lsusb` shows only the root hub, and `arecord -l` lists no capture
device.** The Pi is not seeing the Digirig at all. Plugged directly into a Pi 3A+,
a Digirig Lite has been observed to produce no USB event whatsoever. There is no
error and nothing in `dmesg -w` when it is plugged in, even though the same
Digirig, adapter and cable work on a laptop and the FTX-1 works in the same port.

Check the USB-C plug's orientation first. The Digirig's connection works only one
way round, and the wrong way looks exactly like this (see "the gateway keeps
retrying" below). If it still doesn't appear, the fix is a **powered** USB hub,
with its own supply connected, between the Pi and the Digirig:

- Through an *unpowered* hub, the Pi logged `Undervoltage detected!` as the hub
  connected, and the Digirig still did not appear.
- Through the same hub on its own supply, it enumerated. The kernel may retry the
  port first. `device descriptor read/64, error -32`, then `attempt power cycle`,
  then `New USB device found, idVendor=0d8c` is a success.

Check the Pi's own supply with `vcgencmd get_throttled`. `throttled=0x0` means no
under-voltage since boot; bit 16 set (`0x10000`) means it has occurred. The kernel
USB settings sometimes suggested for this, `dwc_otg.speed=1` in `cmdline.txt` or
`dtoverlay=dwc2,dr_mode=host` in `config.txt`, did not make the Digirig appear,
and are not needed: a freshly built card has neither and works through a powered
hub.

**Direwolf logs `Audio input device 0 error code -19: No such device` while the
Digirig is still plugged in.** The sound card was reset, not removed: `dmesg`
shows a line like `usb 1-1.1.3: reset full-speed USB device number 4`, while
`lsusb` still lists `0d8c` and `arecord -l` still shows the card. A reset
invalidates the handle Direwolf opened at start, and Direwolf does not reopen it,
so every packet after that is lost. `./deploy_igate.sh status` still reports the
gateway as running, because Direwolf itself is alive.

The watchdog recovers this within a minute — it counts those log lines and
restarts when the count grows — so on a current image the symptom to look for is a
gateway that restarts itself every few minutes rather than one that goes quiet:

```bash
journalctl -u igate-watchdog -n 30 --no-pager
```

To recover immediately, or on a Pi without the watchdog:

```bash
sudo systemctl restart aprs-igate
```

Recovering from it is not the same as fixing it. Resets have been seen a minute or
so after boot, and immediately after a transmission. The second case points at RF getting into the USB cable: lower the
radio's transmit power, add ferrite chokes to the Digirig's cables near the Pi,
and move the antenna away from the Pi and its cabling. Lowering the VX-6R to its
minimum power stopped it in one case.

**VX-6R: the gateway keeps retrying and never starts.** The web
monitor shows `state stopped` and `gateway is not running — packets will appear
here when it starts`.
`journalctl -u aprs-igate -f` shows `Waiting up to 60s for the radio's devices`,
then `Still missing after 60s: ALSA-card-1 CM108-PTT-device`, then a refusal and
`Scheduled restart job`, over and over. `lsusb` lists the hub but no `0d8c` device,
and `dmesg | grep 0d8c` prints nothing: the hub sees no device at all.

**First, turn the Digirig's USB-C plug over.** On this setup the Digirig's USB-C
connection works in only one orientation. The other way round, the Digirig is
electrically invisible, with no error anywhere, on the Pi and on a laptop alike.
Once you find the way that works, mark the plug and the socket so it always goes
back that way. If the orientation was already right, unplug the Digirig's cable
at the hub and plug it back in. Either way, leave the service alone. The next
attempt, at most about 90 seconds later, finds it and starts the gateway; watch
for `Radio devices present after Ns` and `iGate up` in the journal. Unplugging
the hub's power instead also cuts a Pi powered from the hub.

Resetting the port from software does not help. The kernel's port `disable` switch
and `uhubctl -a cycle` were both tried while the plug was the wrong way round, and
neither brought the Digirig back. `sudo uhubctl` showed every port as `0100 power`,
meaning the hub saw no device connected at all. A plug the wrong way round
presents nothing to reset.

If you do experiment with the `disable` switch, check afterwards that every port
is enabled again: `grep . /sys/bus/usb/devices/1-1:1.0/1-1-port*/disable` should
end in `:0` on every line. After switching all four off and on together, one port
stayed at `1`, and a Digirig plugged into a disabled port never appears, however
often it is replugged. Fix one with
`echo 0 | sudo tee /sys/bus/usb/devices/1-1:1.0/1-1-port1/disable`, using the
right port number.

**VX-6R: the gateway starts, then stops when it transmits.** `status` says not
running, `arecord -l` lists nothing, and `~/aprs-igate/run/direwolf.log` shows a
transmission (`[0L]`) followed immediately by repeated
`Audio input device 0 error code -19: No such device`. `dmesg` shows
`USB disconnect` for the Digirig, with no reconnect. The Digirig lost power or its
USB link when the radio keyed. Check how the radio is powered first: a VX-6R
powered through a USB boost cable plugged into the same hub did exactly this, on
the first transmission after boot, with `vcgencmd get_throttled` still reporting
`0x0`. Power the radio from its battery or its own DC supply instead. To recover,
replug the Digirig's cable at the hub (unplugging the hub's power also reboots a
Pi powered from it), then `sudo systemctl restart aprs-igate`. The service does not
restart it by itself; the watchdog does. If the radio is
already on its own supply, suspect RF: lower transmit power, move the antenna away
from the Pi, hub and cables, and add ferrite chokes on the Digirig's cables.

On a current image the watchdog handles the replug for you: it sees the device
come back — in whatever port, on whatever card number — and restarts the gateway.
The cause still needs fixing; a gateway that spends its time restarting is not
gating.

**VX-6R: `up` refuses with an error about `/dev/hidraw`.** The Digirig's PTT
device could not be used, and the message says which of three things is wrong:

- **"has no hidraw node"** — the Digirig is unplugged, or `ADEVICE` names a card
  other than the Digirig's. Compare `arecord -l` with `ADEVICE` in `config`.
- **"not readable and writable by a non-root group"** — the udev rule has not
  applied. `ls /etc/udev/rules.d/99-igate-cm108.rules` should exist on the Pi.
  Unplug and replug the Digirig, then `sudo systemctl restart aprs-igate`.
- **"this user is not in it"** — the service account is not in the `audio` group.
  First-boot setup adds it; if first boot has not finished, wait for it. Otherwise
  `sudo usermod -aG audio igate` and reboot.

**VX-6R: running, but nothing decodes and nothing is heard.** Nothing on the Pi
can tell what frequency the VX-6R is on. Check its display reads 144.390, that it
is switched on (Set Mode 1 `APO` off), that receive battery saver is off (Set
Mode 53 `RXSAVE`), and that the VOL knob is not at zero — it is the receive level
into the Digirig.

A radio that plainly receives, its busy lamp flickering on each packet, while the
monitor stays empty is usually that volume knob: too quiet, and nothing decodes.
Measure what is arriving. Direwolf holds the sound card, so stop it first:

```bash
sudo systemctl stop aprs-igate
arecord -D plughw:1,0 -f S16_LE -r 48000 -c 1 -V mono -d 30 /dev/null
sudo systemctl start aprs-igate
```

The meter should swing to roughly 20-70% while a station is transmitting. Flat
means no audio is reaching the Pi; pinned at 100% means far too much. Turn the VOL
knob a few clicks and measure again. `./deploy_igate.sh audio` reports the levels
Direwolf measured on recent packets; it prefers around 50.

**`monitor` shows nothing, but the gateway is decoding.** Check `logs` — if raw
output is flowing, the annotator is the problem, not the gateway. It uses
`strftime()`, a gawk extension, and Debian ships **mawk** as the default `awk`,
which rejects the program at parse time and emits nothing:

```bash
sudo apt-get install -y gawk
```

Cards built after this was addressed install `gawk` during first-boot setup, and
`monitor` now calls `gawk` explicitly and says so plainly if it is missing rather
than printing an empty screen.

**`cannot change locale` warnings on every SSH login.** Cosmetic, and harmless
to the gateway — Direwolf, the whitelist and the radio are unaffected.

Two things combine to cause it. Your SSH client forwards its own locale
(`SendEnv LANG LC_*` is default in most distributions) and Debian's sshd accepts
it (`AcceptEnv LANG LC_*`), so the session arrives asking for a locale the Pi
may not have. And a locale only exists once it has been *compiled* by
`locale-gen` — naming it in `/etc/default/locale` is not enough.

Cards built with `PI_LOCALE = C.UTF-8` (the default) do not have this problem at
all: `C.UTF-8` is compiled into glibc, so it exists from the first second of the
first boot, and the build also stops sshd importing the client's variables.

Cards built with a generated locale such as `en_US.UTF-8` will warn until
`igate-firstboot` compiles it — which is *after* the package install, so an early
first login sees the warnings and they clear themselves a few minutes later. To
fix an existing card immediately:

```bash
sudo sed -i 's/^\(AcceptEnv[[:space:]].*\)$/#\1/' /etc/ssh/sshd_config
echo 'LANG=C.UTF-8' | sudo tee /etc/default/locale
exit
```

Then reconnect. If the warnings persist, `sudo systemctl restart ssh` and
reconnect again.

**Timestamps look wrong — files dated months ago, `systemctl status` saying
"since" a date in the past.** The Pi has no battery-backed clock. It starts from
`fake-hwclock`, which restores the time of the last shutdown — on a fresh card,
the date the OS image was built — and NTP corrects it once the network is up.
Anything created before that keeps the wrong stamp, so device nodes and unit
start times can read months old on a machine that booted five minutes ago.
Confirm the clock caught up with `timedatectl`; you want `System clock
synchronized: yes`. Nothing needs fixing, and APRS is unaffected.

**`arecord -l` shows `Subdevices: 0/1`.** That means zero of one subdevice is
*free* — Direwolf has the capture device open, which is exactly what you want
while the gateway is running. It reads `1/1` when the gateway is stopped.

**The web page loads on a laptop but not on a phone.** Almost always mDNS:
Android browsers cannot resolve `.local`, so use the Pi's IP address instead
(`hostname -I` on the Pi). If the IP also fails, the phone is not on the same
network segment — check it is on your main WiFi rather than a guest network, and
that the router does not have client isolation (sometimes "AP isolation")
enabled, which blocks device-to-device traffic entirely.

**The web page doesn't load.** Confirm the service and the port:

```bash
systemctl status igate-web --no-pager
journalctl -u igate-web -n 30 --no-pager
ss -tln | grep 8080
```

A blank page with the chips reading `unreachable` means the page loaded but
`/api/status` failed — check the journal. `feed reconnecting` means the page is
fine but the monitor subprocess isn't producing lines, which usually means the
gateway itself is stopped. If the page renders but stays empty, that may simply
be a quiet band; give it a few minutes.

**`swapon --show` reports `/dev/zram0`.** Expected. Raspberry Pi OS Trixie swaps
to compressed RAM, and on 512 MB that is worth having. Its default also keeps a
`/var/swap` file on the card that idle pages are written out to, so the image
sets `Mechanism=zram` in `/etc/rpi/swap.conf.d/50-igate.conf`. On such a card
`ls /var/swap` reports no such file. A card built before that change has the file,
and `dmesg | grep backing` shows `zram: setup backing device`.

**Everything is slow.** It's a 512 MB single-board computer. `nano` on a config
file is fine; don't expect to run a browser.

---

## Starting over

The card is disposable — nothing on the Pi is state you can't rebuild. The full
cycle, in order:

```bash
cd ~/git_repos/aprs_igate_bidirectional_whitelist
./build_pi_image.sh check                  # confirm settings before building
./build_pi_image.sh build                  # reuses the cached download

# stop the running Pi cleanly, then move the card to this machine
ssh -t igate@aprs-igate.local 'sudo shutdown -h now'

udisksctl unmount -b /dev/sdX1             # repeat for each mounted partition
findmnt | grep media                        # must print nothing
./build_pi_image.sh flash /dev/sdX         # sdX is a placeholder — use lsblk

# a new card means new SSH host keys, so drop the old one BEFORE reconnecting
ssh-keygen -R aprs-igate.local
```

That `ssh-keygen -R` is not optional housekeeping. Every flashed card generates
its own host keys, so without it your next connection fails with a host key
mismatch warning that looks alarming and is entirely expected.

Then boot the Pi and allow the usual 5–10 minutes: first boot reinstalls packages,
because the filesystem is new.

To clear the cached download and built image as well:

```bash
rm -rf pi-build/
```

To stop the gateway on a Pi you want to keep but repurpose:

```bash
sudo systemctl disable --now aprs-igate igate-web igate-firstboot \
  igate-logrotate.timer igate-watchdog.timer
```

---

## Appendix — How the image is actually built

Nothing in this appendix is needed to use the gateway. It's here because the
build does something slightly unusual, and the technique is worth knowing in its
own right.

### It doesn't use Raspberry Pi Imager

Not the GUI, not `rpi-imager --cli`, not its customisation format.
`build_pi_image.sh` downloads the same official image Imager would fetch, then
customises it itself by mounting it.

```
downloads.raspberrypi.com/raspios_lite_armhf_latest
        │  curl
        ▼
raspios_lite_armhf.img.xz          cached in pi-build/
        │  xz -dc
        ▼
aprs-igate-pi.img                  a byte-for-byte disk image:
        │                          MBR partition table + two partitions
        │  sudo losetup --find --show --partscan
        ▼
/dev/loop0  →  /dev/loop0p1        FAT32 boot
               /dev/loop0p2        ext4 root
        │  sudo mount
        ▼
ordinary cp / tee / sed / rsync into two directories
        │  umount, losetup -d
        ▼
aprs-igate-pi.img, customised
        │  dd
        ▼
SD card
```

Imager's customisation is a fixed, closed set — hostname, user, WiFi, SSH,
locale. Mechanically it writes a generated first-boot script into the boot
partition (older versions patched `cmdline.txt` to run `/boot/firstrun.sh`;
v1.8+ on Bookworm writes `custom.toml`, parsed on the Pi by
`raspberrypi-sys-mods`). It has no way to express "also install this project
tree, three systemd units, and a list of apt packages." Going through it would
have meant generating a script to be consumed by their script, constrained by
their schema, and still not being able to carry the project.

Building the filesystem directly also yields a **reusable `.img` artefact**
rather than a written card: inspectable, diffable, keepable, flashable as many
times as you like — including by Imager, via "Use custom", which is why that
option is offered in Part 3.

### What loop mounting is

`mount` wants a **block device** — something the kernel can read and write in
fixed-size blocks at arbitrary offsets. `/dev/sda1`, `/dev/mmcblk0p2`. A
filesystem like ext4 is just a data structure laid out across those numbered
blocks: superblock here, inode table there, data blocks after.

Nothing in that description requires the blocks to live on hardware. A regular
file also supports random-access reads and writes at arbitrary offsets, so it
can play the same role — it just isn't a block device, so `mount` won't take it.

The **loop driver** is the adapter: a kernel block-device driver whose backing
store is a file instead of a disk controller. Attach a file to it and you get
`/dev/loop0`, a real block device, where a read of block *N* becomes a read at
offset *N × blocksize* inside the file. Everything above the block layer — the
ext4 driver, the page cache, mount options, permissions — is unchanged and
cannot tell the difference.

The name is historical: it "loops back" through the filesystem layer, since the
block device is served by a file that itself lives on another filesystem.
Filesystem → block device → file → filesystem. It has no connection to the
network loopback interface, which is a common source of confusion.

The explicit form is two steps:

```bash
sudo losetup --find --show --partscan disk.img   # → /dev/loop0
sudo mount /dev/loop0p2 /mnt
...
sudo umount /mnt
sudo losetup -d /dev/loop0
```

For a file that *is* a single filesystem there's a shorthand that attaches and
mounts in one go, and detaches on unmount:

```bash
sudo mount -o loop filesystem.img /mnt
```

### Why `--partscan` is the part that matters

That shorthand doesn't apply here, because a Raspberry Pi OS image isn't a
filesystem — it's a whole-disk image: partition table first, then partitions.

A partition table is only some bytes in a known place, so you can read one out
of a plain file with no root and no loop device at all. Try it on any disk
image:

```bash
$ fdisk -l disk.img
Disk disk.img: 64 MiB, 67108864 bytes, 131072 sectors
Units: sectors of 1 * 512 = 512 bytes

Device    Boot Start    End Sectors Size Id Type
disk.img1       2048  18431   16384   8M  c W95 FAT32 (LBA)
disk.img2      18432 131071  112640  55M 83 Linux
```

Point `mount` at byte 0 of that file and it finds an MBR, not a filesystem, and
fails. The ext4 actually begins at sector 18432 — byte 18432 × 512 =
**9,437,184**. So the manual route is to do the arithmetic yourself:

```bash
sudo mount -o loop,offset=9437184 disk.img /mnt
```

`--partscan` (equivalently `losetup -P`) is the kernel doing that for you: it
parses the partition table in the backing file and creates `/dev/loop0p1`,
`/dev/loop0p2` as separate devices at the correct offsets. `kpartx` is the older
userspace tool for the same job, via device-mapper, from before the kernel could
do it directly.

That single option is what makes the rest of the script boring. After it, the
image is indistinguishable from a plugged-in SD card, and `cp`, `sed`, `rsync`
and `chmod` all simply work.

### Where else this shows up

- **ISO files.** `mount -o loop ubuntu.iso /mnt` to browse a disc image without
  burning it — the original use case.
- **Snap packages.** Each is a squashfs image, loop-mounted. That's why `df` on
  an Ubuntu box is buried in `/dev/loop0` through `/dev/loop40`.
- **LUKS containers.** An encrypted file you mount as a volume is a loop device
  plus dm-crypt.
- **Docker**, historically, with the devicemapper storage driver on a sparse
  file.

### What gets written where, and why

Only two files land on the **boot** partition (FAT32), and both are Raspberry
Pi's own long-documented headless mechanisms:

| File | Effect |
|---|---|
| `ssh` | Empty file; its presence alone enables the SSH server |
| `userconf.txt` | `username:sha512-hash`, consumed on first boot by `userconf.service` |

Everything else goes into the **root** partition (ext4) as ordinary filesystem
writes: the NetworkManager profiles, `/opt/aprs-igate`, the systemd units, and
symlinks into `multi-user.target.wants/` to enable them. You can't run
`systemctl enable` against an offline image, but creating those symlinks by hand
is all that `systemctl enable` does anyway.

We deliberately don't touch `cmdline.txt`, don't write a `firstrun.sh`, and
don't write a `custom.toml`.

### Trade-offs of doing it this way

**It only runs on Linux, as root.** Imager only ever touches the FAT boot
partition, which is precisely why it works on macOS and Windows. Writing into
ext4 requires a Linux host with loop devices and `sudo`.

**It's coupled to the image's internals.** The build assumes exactly two
partitions in that order, systemd, NetworkManager for WiFi, `/etc/hostname`, and
`/etc/default/keyboard`. Most of that is stable, but one item is a recent
change: Bookworm moved from `dhcpcd`/`wpa_supplicant` to NetworkManager, and the
`.nmconnection` profiles this builder writes would be inert on an older image.
`build` validates the partition count and fails clearly if the image isn't
shaped as expected, but it cannot detect a network stack having been swapped out
underneath it.

**One thing it buys that the boot-partition approach can't.** Bookworm moved the
boot partition's *mountpoint on the running system* from `/boot` to
`/boot/firmware`. Anything writing paths relative to the booted OS has to track
that. Mounting partition 1 directly makes the in-OS mountpoint name irrelevant.

**Packages still can't be installed offline.** The `.deb`s are ARM and the build
host is x86-64, so Direwolf and hamlib install on first boot over WiFi rather
than being baked in. Doing it offline would mean `qemu-user-static` and an ARM
chroot — a large increase in build complexity to save a few minutes, once. This
is the reason first boot takes several minutes and needs the network up before
it can finish, which in turn is why the WiFi regulatory domain is set three
separate ways *before* NetworkManager starts (Part 1).

### Loop device housekeeping

Attaching a loop device is privileged, there are a finite number of them, and
leaking one is easy — a stale `/dev/loop0` still holding a file you've since
deleted is an irritating thing to debug. `build_pi_image.sh` installs a cleanup
trap that unmounts and detaches on every exit path, including Ctrl-C.

Writes also land in the host's page cache before reaching the backing file, so
the `.img` isn't consistent until unmount or `sync`; the script does both before
declaring the image ready. And you now have two filesystems stacked, each with
its own caching and journaling — fine for writing config files, a poor choice
for anything performance-sensitive.
