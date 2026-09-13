# Pi-Gate Setup — Blank Card to Gateway on the Air

Step-by-step build of the **pi-gate**: a headless Raspberry Pi 3A+ running the
whitelisted bidirectional iGate. Written assuming this is your first Raspberry Pi; the
Linux, radio and networking side assumes you know what you're doing.

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

The gateway itself behaves exactly as on the laptop — same `igate.conf`, same
commands, same strict whitelist. The only difference is `DEPLOY_MODE`, which is
`bare-metal` on the Pi rather than `docker`.

---

## What you need

**Hardware**

| Item | Notes |
|---|---|
| Raspberry Pi 3A+ | 512 MB RAM, one USB-A port, **no Ethernet** — WiFi is the only way in, which is why the credentials get baked into the card |
| microSD card, 8 GB or larger | Class 10 / A1 or better. Card quality is the single most common cause of a Pi that boots unreliably — buy a name brand |
| microSD reader for your laptop | Built-in slot is fine |
| 5 V 2.5 A micro-USB supply | **Not** a phone charger you had lying around. An underpowered Pi browns out under load, and on this project that means the USB link to the radio dropping mid-transmission — the exact failure that sticks the radio in TX |
| USB-A to USB-C cable | Pi's USB-A port to the FTX-1 |
| The radio, antenna, and a real ground/counterpoise | Per the RFI notes in the main README |

The 3A+ has **one** USB port. The radio takes it. Anything else needs a powered
hub.

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
| `/opt/aprs-igate` | The whole project, with `DEPLOY_MODE = bare-metal` |
| Three systemd units | WiFi country → first-boot setup → the gateway |

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
2. Radio's USB cable into the Pi's USB port. Radio on, set to 144.390, **D-FM**.
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
network. On a 3A+ over WiFi this takes **several minutes** — realistically five
to ten on first boot. There is no progress indicator. Be patient before
concluding something is wrong.

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

**On the very first boot this will probably have failed, and that's expected.**
The audio and serial device names were copied from your laptop and almost
certainly don't match the Pi. Part 6 fixes that.

---

## Part 6 — Point the config at the Pi's hardware

The Pi enumerates its own USB hardware, so `ADEVICE`, `CAT_DEVICE` and
`PTT_DEVICE` need checking. The installed `igate.conf` carries a comment at the
top saying exactly this.

With the radio plugged in and powered on:

```bash
arecord -l
```

```
**** List of CAPTURE Hardware Devices ****
card 0: Device [Yaesu FTX-1], device 0: USB Audio [USB Audio]
```

The card number is what matters. `card 0` means `ADEVICE = plughw:0,0`. On the
Pi this is usually card 0, because unlike your laptop the Pi has no built-in
capture device competing for the slot.

Then the serial ports:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
```

The FTX-1 presents two: a `ttyUSB` for CAT and a `ttyACM` for PTT. If you see
several, `dmesg | tail -30` right after plugging the radio in tells you which
belongs to what.

Edit the config:

```bash
cd ~/aprs-igate
nano igate.conf
```

(`~/aprs-igate` is a symlink to `/opt/aprs-igate`, which is where the project
actually lives. Either path works.)

Set the three device lines to match. Leave everything else — `MYCALL`,
`RADIO_MODE = PKTFM`, the audio levels, the whitelist — exactly as calibrated on
the laptop.

Check your work without starting anything:

```bash
./deploy_igate.sh config
```

It prints every resolved setting, masks the passcode, and shows the Direwolf
filter your whitelist compiles to. Then start it:

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
M`. Then watch `monitor` for a minute or two and look for `RF RX` lines — real
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

> Only add callsigns you're authorised to transmit on behalf of. Adding a call
> to this list means your station will key up carrying messages addressed to
> that operator.

---

## Managing the service

The gateway runs under systemd as `aprs-igate.service`.

| Task | Command |
|---|---|
| Is it running? | `systemctl status aprs-igate` |
| Web monitor state | `systemctl status igate-web` |
| Calibrate audio levels | `cd ~/aprs-igate && ./deploy_igate.sh audio` |
| Start / stop | `sudo systemctl start aprs-igate` / `sudo systemctl stop aprs-igate` |
| Apply a config change | `sudo systemctl restart aprs-igate` |
| Why did it fail? | `journalctl -u aprs-igate -n 50` |
| Don't start at boot any more | `sudo systemctl disable aprs-igate` |
| Gateway's own view | `cd ~/aprs-igate && ./deploy_igate.sh status` |

`Active: active (exited)` is the healthy state. The unit is `Type=oneshot`: it
launches Direwolf in the background and returns, so systemd has nothing
left in the foreground to supervise.

There is deliberately no `Restart=` on the unit — systemd rejects that setting on
oneshot services. If the radio wasn't connected at boot, plug it in and
`sudo systemctl start aprs-igate`.

You can also drive the script directly (`./deploy_igate.sh up` / `down` /
`restart`), which does the same work. Prefer `systemctl` so systemd's view of
the service stays accurate.

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
| Swap | A `dphys-swapfile` swap **file on the card** is removed at first boot. Trixie instead uses **zram** — compressed swap in RAM, which writes nothing to the card — so that is left alone. `swapon --show` reporting `/dev/zram0` is expected |

What remains is a card that is written when you deliberately change something,
and essentially never otherwise.

**Two consequences to know about.** Nothing in `run/` survives a reboot, so the
packet log starts empty each time and `journalctl` cannot show you a previous
boot. For an appliance that is the right trade, but it does mean a post-mortem
after an unexpected power cut has little to work with.

**The remaining risk is the radio, not the card.** PTT rides the USB serial link,
so if the Pi loses power mid-transmission the unkey command is never sent and
**the radio can stay keyed**. Transmissions are rare and brief on a
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

**Gateway transmits but nobody decodes it.** This is the radio, not the Pi. Plain
FM modulates from the microphone input rather than the USB codec, producing a
clean carrier with no data in it. The radio must be in **D-FM**. See the radio
setup section in [README.md](README.md#radio-setup--the-one-that-matters).

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
to compressed RAM rather than to a file on the card, so it writes nothing to the
card and is left in place deliberately — on 512 MB it is worth having.

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
sudo systemctl disable --now aprs-igate igate-firstboot
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
