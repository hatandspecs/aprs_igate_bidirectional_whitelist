#!/usr/bin/env bash
# Builds a Raspberry Pi SD card image that boots straight into this iGate.
#
# The image is customised offline — no Pi required to build it — by mounting the
# downloaded Raspberry Pi OS image and writing into its boot and root
# partitions. On first boot the Pi joins WiFi, enables SSH, installs Direwolf
# and hamlib, and starts the gateway.
#
# Usage:
#   ./build_pi_image.sh check          Validate pi.conf and pi.secrets only.
#   ./build_pi_image.sh build          Produce the customised image.
#   ./build_pi_image.sh flash /dev/sdX  Write a built image to an SD card.
#       sdX is a placeholder on purpose, so a thoughtless paste cannot destroy a
#       real disk. Find the device with lsblk: a USB reader is typically
#       /dev/sdb, a built-in card slot /dev/mmcblk0.
#
# Settings are in pi.conf; credentials in pi.secrets (gitignored).
# Requires sudo for loop-mounting the image.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PI_CONF="${SCRIPT_DIR}/pi.conf"
PI_SECRETS="${SCRIPT_DIR}/pi.secrets"
BUILD_DIR="${SCRIPT_DIR}/pi-build"
OUTPUT_IMG="${BUILD_DIR}/aprs-igate-pi.img"

declare -A CFG

# Populated by mount_image, consumed by the cleanup trap.
LOOP_DEV=""
BOOT_MNT=""
ROOT_MNT=""
SOURCE_IMG=""

# ---------------------------------------------------------------- helpers --

die() { echo "Error: $*" >&2; exit 1; }
note() { echo "  $*"; }
step() { echo; echo "==> $*"; }

# Same plain "key = value" parser deploy_igate.sh uses.
load_kv() {
  local file="$1" line key val
  [[ -f "$file" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$line")"
    [[ -z "$line" || "$line" != *"="* ]] && continue
    key="$(sed -e 's/[[:space:]]*$//' <<<"${line%%=*}")"
    val="$(sed -e 's/^[[:space:]]*//' <<<"${line#*=}")"
    CFG["$key"]="$val"
  done < "$file"
}

expand_tilde() {
  local p="$1"
  if [[ "$p" == "~"* ]]; then p="${HOME}${p:1}"; fi
  printf '%s' "$p"
}

require_tools() {
  local missing=() t
  for t in losetup mount umount sudo openssl rsync curl xz dd; do
    command -v "$t" >/dev/null || missing+=("$t")
  done
  if ((${#missing[@]})); then die "missing required tools: ${missing[*]}"; fi
}

# ------------------------------------------------------------ validation --

validate() {
  load_kv "$PI_CONF" || die "missing $PI_CONF"
  load_kv "$PI_SECRETS" || die "missing pi.secrets — copy pi.secrets.example and fill it in"

  local k
  for k in PI_HOSTNAME PI_USER PI_WIFI_COUNTRY PI_IMAGE_VARIANT; do
    [[ -n "${CFG[$k]:-}" ]] || die "$k is not set in pi.conf"
  done

  [[ -n "${CFG[PI_USER_PASSWORD]:-}" ]] || die "PI_USER_PASSWORD is not set in pi.secrets"
  [[ "${CFG[PI_USER_PASSWORD]}" != "change-me" ]] || die "PI_USER_PASSWORD is still the example value"

  [[ "${CFG[PI_IMAGE_VARIANT]}" =~ ^(armhf|arm64)$ ]] \
    || die "PI_IMAGE_VARIANT must be armhf or arm64"

  # Country code gates the radio: wrong or missing means no WiFi at all.
  [[ "${CFG[PI_WIFI_COUNTRY]}" =~ ^[A-Z]{2}$ ]] \
    || die "PI_WIFI_COUNTRY must be a two-letter ISO code such as US"

  [[ -n "${CFG[WIFI_1_SSID]:-}" ]] \
    || die "no WiFi networks defined — set WIFI_1_SSID and WIFI_1_PSK in pi.secrets"

  # The Pi is headless and wireless-only; without the APRS-IS passcode the
  # gateway would come up unable to log in, with no easy way to notice.
  [[ -f "${SCRIPT_DIR}/igate.secrets" ]] \
    || die "igate.secrets not found — the Pi needs the APRS-IS passcode to run"

  # PI_RADIO names the radio this Pi drives, and is written into the Pi's own
  # igate.local.conf. Blank leaves igate.conf's RADIO in charge. Checked here
  # because on the Pi a missing profile stops the gateway starting, on a machine
  # with no screen.
  local radio="${CFG[PI_RADIO]:-}" station_radio
  station_radio="$(sed -n 's/^[[:space:]]*RADIO[[:space:]]*=[[:space:]]*\([^#[:space:]]*\).*/\1/p' \
                     "${SCRIPT_DIR}/igate.conf" 2>/dev/null | tail -n 1)"
  if [[ -n "$radio" ]]; then
    if [[ ! "$radio" =~ ^[a-z0-9][a-z0-9._-]*$ || ! -f "${SCRIPT_DIR}/radios/${radio}.conf" ]]; then
      die "PI_RADIO = '${radio}' has no radios/${radio}.conf. Available: $(cd "${SCRIPT_DIR}/radios" 2>/dev/null && ls -- *.conf | sed 's/\.conf$//' | tr '\n' ' ')"
    fi
  fi

  local n=1 count=0
  while [[ -n "${CFG[WIFI_${n}_SSID]:-}" ]]; do
    [[ -n "${CFG[WIFI_${n}_PSK]:-}" ]] || die "WIFI_${n}_SSID is set but WIFI_${n}_PSK is not"
    count=$((count + 1)); n=$((n + 1))
  done

  echo "Configuration valid."
  note "hostname     ${CFG[PI_HOSTNAME]}   user ${CFG[PI_USER]}"
  note "variant      Raspberry Pi OS Lite (${CFG[PI_IMAGE_VARIANT]})"
  note "wifi         ${count} network(s), country ${CFG[PI_WIFI_COUNTRY]}"
  note "autostart    ${CFG[PI_AUTOSTART]:-yes}"
  if [[ -n "$radio" ]]; then
    note "radio        ${radio} (PI_RADIO in pi.conf)"
  else
    note "radio        ${station_radio:-none} (RADIO in igate.conf)"
  fi
  if [[ "${CFG[PI_WEB_MONITOR]:-yes}" == "yes" ]]; then
    note "web monitor  enabled, port ${CFG[PI_WEB_PORT]:-8080} (read-only, no auth — LAN only)"
  else
    note "web monitor  disabled"
  fi

  local pubkey; pubkey="$(expand_tilde "${CFG[PI_SSH_PUBKEY]:-}")"
  if [[ -n "$pubkey" && -f "$pubkey" ]]; then
    note "ssh key      ${pubkey}"
  else
    note "ssh key      none — password login only"
    if [[ -n "$pubkey" ]]; then
      echo "  WARNING: PI_SSH_PUBKEY points at ${pubkey}, which does not exist."
    fi
    local found; found="$(ls "${HOME}"/.ssh/*.pub 2>/dev/null | head -5)"
    if [[ -n "$found" ]]; then
      echo "  Public keys available on this host:"
      echo "$found" | sed 's/^/    /'
      echo "  Set PI_SSH_PUBKEY in pi.conf to one of them for key-based login."
    fi
  fi
}

# ------------------------------------------------------------- image prep --

fetch_image() {
  mkdir -p "$BUILD_DIR"
  local supplied; supplied="$(expand_tilde "${CFG[PI_IMAGE_PATH]:-}")"

  if [[ -n "$supplied" ]]; then
    [[ -f "$supplied" ]] || die "PI_IMAGE_PATH set but not found: $supplied"
    SOURCE_IMG="$supplied"
    note "using supplied image: $SOURCE_IMG"
  else
    local url="https://downloads.raspberrypi.com/raspios_lite_${CFG[PI_IMAGE_VARIANT]}_latest"
    local xz="${BUILD_DIR}/raspios_lite_${CFG[PI_IMAGE_VARIANT]}.img.xz"
    if [[ -f "$xz" ]]; then
      note "reusing previously downloaded $(basename "$xz")"
    else
      note "downloading Raspberry Pi OS Lite (${CFG[PI_IMAGE_VARIANT]})..."
      curl -fL --progress-bar -o "${xz}.part" "$url" || die "download failed"
      mv "${xz}.part" "$xz"
    fi
    SOURCE_IMG="$xz"
  fi

  step "Preparing working image"
  # The marker must die with the old image: a stale one would vouch for a fresh
  # decompression that has not been customised yet.
  rm -f "$OUTPUT_IMG" "${OUTPUT_IMG}.built"
  if [[ "$SOURCE_IMG" == *.xz ]]; then
    note "decompressing..."
    xz -dc "$SOURCE_IMG" > "$OUTPUT_IMG"
  else
    cp --reflink=auto "$SOURCE_IMG" "$OUTPUT_IMG"
  fi
  note "working image: $OUTPUT_IMG ($(du -h "$OUTPUT_IMG" | cut -f1))"
}

cleanup() {
  local had_e=""
  [[ $- == *e* ]] && had_e=yes
  set +e
  [[ -n "$ROOT_MNT" ]] && sudo umount "$ROOT_MNT" 2>/dev/null
  [[ -n "$BOOT_MNT" ]] && sudo umount "$BOOT_MNT" 2>/dev/null
  [[ -n "$LOOP_DEV" ]] && sudo losetup -d "$LOOP_DEV" 2>/dev/null
  [[ -n "$ROOT_MNT" ]] && rmdir "$ROOT_MNT" 2>/dev/null
  [[ -n "$BOOT_MNT" ]] && rmdir "$BOOT_MNT" 2>/dev/null
  LOOP_DEV=""; BOOT_MNT=""; ROOT_MNT=""
  [[ -n "$had_e" ]] && set -e
  return 0
}

mount_image() {
  step "Mounting image"
  LOOP_DEV="$(sudo losetup --find --show --partscan "$OUTPUT_IMG")" \
    || die "losetup failed"
  note "loop device: $LOOP_DEV"

  # losetup returns once the kernel has read the partition table, but the
  # /dev/loopNpM nodes are created afterwards by udev. Testing for them at once
  # is a race, usually won; lost, it reported a sound image as not a Raspberry Pi
  # OS image. Wait for udev, then poll, nudging the kernel to re-read the table
  # once in case the scan itself was missed.
  command -v udevadm >/dev/null && sudo udevadm settle --timeout=10 2>/dev/null
  local i
  for i in $(seq 1 20); do
    [[ -e "${LOOP_DEV}p1" && -e "${LOOP_DEV}p2" ]] && break
    if (( i == 8 )); then
      sudo partx -a "$LOOP_DEV" 2>/dev/null || sudo partprobe "$LOOP_DEV" 2>/dev/null || true
    fi
    sleep 0.25
  done

  # Raspberry Pi OS images are two partitions: FAT boot, then ext4 root.
  [[ -e "${LOOP_DEV}p1" && -e "${LOOP_DEV}p2" ]] \
    || die "expected two partitions on $LOOP_DEV after 5s — is this a Raspberry Pi OS image? (sfdisk -d $OUTPUT_IMG shows its table)"

  BOOT_MNT="$(mktemp -d)"; ROOT_MNT="$(mktemp -d)"
  sudo mount "${LOOP_DEV}p1" "$BOOT_MNT" || die "cannot mount boot partition"
  sudo mount "${LOOP_DEV}p2" "$ROOT_MNT" || die "cannot mount root partition"
  note "boot: $BOOT_MNT"
  note "root: $ROOT_MNT"
}

# --------------------------------------------------------- customisation --

configure_access() {
  step "Configuring SSH and user account"

  # An empty file named "ssh" in the boot partition enables sshd on first boot.
  sudo touch "${BOOT_MNT}/ssh"
  note "SSH enabled"

  # Raspberry Pi OS has had no default user since 2022; userconf.txt creates one.
  local hash
  hash="$(openssl passwd -6 "${CFG[PI_USER_PASSWORD]}")" || die "password hashing failed"
  echo "${CFG[PI_USER]}:${hash}" | sudo tee "${BOOT_MNT}/userconf.txt" >/dev/null
  sudo chmod 600 "${BOOT_MNT}/userconf.txt"
  note "user ${CFG[PI_USER]} created (password hashed with SHA-512)"

  # The key is staged outside /home and installed by the first-boot script.
  # Raspberry Pi OS processes userconf.txt with "usermod -m -d /home/<name>",
  # which refuses to run if that directory already exists — so nothing may be
  # written under /home at build time.
  local pubkey; pubkey="$(expand_tilde "${CFG[PI_SSH_PUBKEY]:-}")"
  if [[ -n "$pubkey" && -f "$pubkey" ]]; then
    sudo mkdir -p "${ROOT_MNT}/etc/igate"
    sudo cp "$pubkey" "${ROOT_MNT}/etc/igate/authorized_keys"
    sudo chmod 644 "${ROOT_MNT}/etc/igate/authorized_keys"
    note "SSH key staged for key-based login (installed on first boot)"
  fi

  echo "${CFG[PI_HOSTNAME]}" | sudo tee "${ROOT_MNT}/etc/hostname" >/dev/null
  sudo sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t${CFG[PI_HOSTNAME]}/" "${ROOT_MNT}/etc/hosts"
  note "hostname ${CFG[PI_HOSTNAME]} (reachable as ${CFG[PI_HOSTNAME]}.local via mDNS)"
}

configure_wifi() {
  step "Configuring WiFi"

  local nm_dir="${ROOT_MNT}/etc/NetworkManager/system-connections"
  sudo mkdir -p "$nm_dir"

  local n=1 ssid psk hidden prio
  while [[ -n "${CFG[WIFI_${n}_SSID]:-}" ]]; do
    ssid="${CFG[WIFI_${n}_SSID]}"
    psk="${CFG[WIFI_${n}_PSK]:-}"
    hidden="${CFG[WIFI_${n}_HIDDEN]:-no}"
    # validate() rejects this, but configure_wifi is also reachable directly.
    [[ -n "$psk" ]] || die "WIFI_${n}_SSID is set but WIFI_${n}_PSK is not"
    # Lower-numbered networks win; NetworkManager prefers higher priority.
    prio=$((100 - n))

    local tmp; tmp="$(mktemp)"
    cat > "$tmp" <<EOF
[connection]
id=${ssid}
type=wifi
autoconnect=true
autoconnect-priority=${prio}

[wifi]
mode=infrastructure
ssid=${ssid}
# Associate with the hardware MAC rather than a randomised one. A DHCP
# reservation on the router is keyed to that address, and it is the only stable
# way to reach the web monitor from a device whose browser cannot resolve
# .local — Android, in particular. NetworkManager's default here has varied
# between releases, so state it rather than inherit it.
cloned-mac-address=permanent
$([[ "$hidden" == "yes" ]] && echo "hidden=true")

[wifi-security]
key-mgmt=wpa-psk
psk=${psk}

[ipv4]
method=auto

[ipv6]
method=auto
EOF
    # NetworkManager refuses to load a profile that is group/world readable.
    sudo cp "$tmp" "${nm_dir}/${ssid}.nmconnection"
    sudo chmod 600 "${nm_dir}/${ssid}.nmconnection"
    sudo chown 0:0 "${nm_dir}/${ssid}.nmconnection"
    rm -f "$tmp"
    note "network ${n}: ${ssid} (priority ${prio}$([[ "$hidden" == "yes" ]] && echo ", hidden"))"
    n=$((n + 1))
  done

  # Raspberry Pi OS keeps the WiFi radio rfkill-blocked until a regulatory
  # domain is set. That has to happen before NetworkManager comes up, because
  # first boot needs the network to install packages — set it three ways so no
  # single mechanism's absence on a given OS release leaves the Pi offline.
  local country="${CFG[PI_WIFI_COUNTRY]}"

  # 1. Kernel module parameter — applied as the driver loads, before userspace.
  sudo mkdir -p "${ROOT_MNT}/etc/modprobe.d"
  echo "options cfg80211 ieee80211_regdom=${country}" \
    | sudo tee "${ROOT_MNT}/etc/modprobe.d/cfg80211-regdom.conf" >/dev/null

  # 2. Legacy CRDA default, still read on older releases.
  echo "REGDOMAIN=${country}" | sudo tee "${ROOT_MNT}/etc/default/crda" >/dev/null

  # 3. An early oneshot that unblocks the radio and tells raspi-config the
  #    country, ordered ahead of NetworkManager.
  local tmp; tmp="$(mktemp)"
  cat > "$tmp" <<EOF
[Unit]
Description=Set WiFi regulatory domain before the network comes up
Before=NetworkManager.service wpa_supplicant.service
After=local-fs.target
ConditionPathExists=!/var/lib/igate-wifi-country-done

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/igate-wifi-country.sh

[Install]
WantedBy=multi-user.target
EOF
  sudo mkdir -p "${ROOT_MNT}/etc/systemd/system"
  sudo cp "$tmp" "${ROOT_MNT}/etc/systemd/system/igate-wifi-country.service"
  sudo chmod 644 "${ROOT_MNT}/etc/systemd/system/igate-wifi-country.service"

  cat > "$tmp" <<EOF
#!/usr/bin/env bash
# Unblocks the WiFi radio and records the regulatory domain. Runs once, before
# NetworkManager, so that first boot has a network to install packages over.
set -uo pipefail
COUNTRY="${country}"

command -v rfkill >/dev/null && rfkill unblock wifi
command -v iw >/dev/null && iw reg set "\$COUNTRY"
command -v raspi-config >/dev/null && raspi-config nonint do_wifi_country "\$COUNTRY"

mkdir -p /var/lib
touch /var/lib/igate-wifi-country-done
exit 0
EOF
  sudo mkdir -p "${ROOT_MNT}/usr/local/sbin"
  sudo cp "$tmp" "${ROOT_MNT}/usr/local/sbin/igate-wifi-country.sh"
  sudo chmod 755 "${ROOT_MNT}/usr/local/sbin/igate-wifi-country.sh"
  rm -f "$tmp"

  note "regulatory domain ${country} (module param, CRDA, and a pre-network unit)"
}

install_project() {
  step "Installing iGate project"

  # Under /opt, not /home: see configure_access. The first-boot script chowns
  # this to the service account and symlinks it into the home directory.
  local dest="${ROOT_MNT}/opt/${CFG[PI_INSTALL_DIR]:-aprs-igate}"
  sudo mkdir -p "$dest"

  # Copy the project without build artefacts, git history, or the host's own
  # rendered config and pidfiles.
  sudo rsync -a \
    --exclude '.git/' \
    --exclude 'run/' \
    --exclude 'pi-build/' \
    --exclude '__pycache__/' \
    --exclude 'pi.secrets' \
    --exclude 'igate.local.conf' \
    --exclude 'scratch_notes.txt' \
    "${SCRIPT_DIR}/" "${dest}/"

  # igate.conf is installed exactly as it is in the repository. It is the shared
  # station configuration, and a copy of it has to work unchanged on any machine —
  # so nothing specific to the Pi is written into it. What is true only of the Pi
  # goes in the Pi's own igate.local.conf, written fresh here. The build host's
  # igate.local.conf is excluded above: it describes that machine, not this one.
  local tmp; tmp="$(mktemp)"
  cat > "$tmp" <<'LOCAL'
# igate.local.conf — settings true of THIS machine only. Written by
# build_pi_image.sh for the pi-gate. Gitignored; see igate.local.conf.example.
#
# Layers, lowest priority first; each overrides only the keys it sets:
#   1. radios/<RADIO>.conf   how to drive the radio      hardware keys only
#   2. igate.conf            the station                 any key
#   3. igate.local.conf      THIS FILE: one machine      DEPLOY_MODE, RADIO,
#                                                        WEB_*, DEVICE_WAIT,
#                                                        hardware keys only
#   4. igate.secrets         APRS-IS passcode            IGLOGIN_PASSCODE only
#   5. environment           IGATE_MODE, IGATE_PASSCODE  one key each
#
# This file may not set the whitelist, beacon, callsign, APRS-IS login or
# transmit path; those live only in igate.conf and a local file that tries is
# refused. `./deploy_igate.sh config` shows which layer supplied every setting.

# The Pi runs Direwolf directly under systemd, not in a container.
DEPLOY_MODE = bare-metal

# The web monitor runs as its own hardened systemd unit here, igate-web.service
# (PI_WEB_MONITOR and PI_WEB_PORT in pi.conf), which restarts it on failure and
# caps its memory. This stops deploy_igate.sh starting a second copy on the same
# port. Check it with: systemctl status igate-web
WEB_MONITOR = no

# Seconds `up` waits for the radio's USB devices before giving up. At boot the
# gateway can start while a USB hub is still bringing the radio up, and a radio
# switched on after the Pi appears later still. If they never appear, `up`
# refuses as usual, and aprs-igate.service tries again every 30 seconds.
DEVICE_WAIT = 60

# Device names come from the radio profile. Check them here with 'arecord -l',
# 'ls -l /dev/ttyUSB* /dev/ttyACM*' and, for a CM108 interface such as the
# Digirig Lite, 'ls -l /dev/hidraw*'. If this Pi numbers them differently,
# override them below rather than editing the shared profile:
#   ADEVICE = plughw:2,0
#   CAT_DEVICE = /dev/serial/by-id/usb-Silicon_Labs_CP2105_..._if00-port0
#   PTT_DEVICE = /dev/ttyACM0

# To run this Pi with a different radio than igate.conf names (or set PI_RADIO
# in pi.conf before building):
#   RADIO = vx6r
LOCAL
  if [[ -n "${CFG[PI_RADIO]:-}" ]]; then
    printf '\n# From PI_RADIO in pi.conf at build time: this Pi drives this radio,\n# whatever RADIO igate.conf names.\nRADIO = %s\n' \
      "${CFG[PI_RADIO]}" >> "$tmp"
  fi
  sudo cp "$tmp" "${dest}/igate.local.conf"
  sudo chmod 644 "${dest}/igate.local.conf"
  rm -f "$tmp"

  sudo chmod 600 "${dest}/igate.secrets"
  # The tmpfs needs an existing directory to mount over; rsync excluded run/.
  sudo mkdir -p "${dest}/run"
  note "installed to /opt/${CFG[PI_INSTALL_DIR]:-aprs-igate}"
  note "igate.local.conf written with DEPLOY_MODE = bare-metal${CFG[PI_RADIO]:+ and RADIO = ${CFG[PI_RADIO]}}; igate.conf installed unchanged"

  # A CM108 interface (PTT_METHOD = cm108) keys through a hidraw node that only
  # root may open by default. Installed whatever radio is selected, so moving
  # the Pi to such a radio later needs nothing but a config change, and before
  # first boot so it already applies when the interface is first plugged in.
  # It matches only C-Media devices and does nothing without one.
  sudo install -D -m 644 "${SCRIPT_DIR}/udev/99-igate-cm108.rules" \
    "${ROOT_MNT}/etc/udev/rules.d/99-igate-cm108.rules"
  note "udev rule for CM108 PTT installed (/etc/udev/rules.d/99-igate-cm108.rules)"
}

# The pi-gate is unplugged rather than shut down, so the design goal is that
# nothing writes to the SD card during normal operation. Three routine writers
# exist; this removes two of them (the third, swap, is handled at first boot).
harden_against_power_loss() {
  step "Reducing SD card writes"

  local dir="/opt/${CFG[PI_INSTALL_DIR]:-aprs-igate}"
  local size="${CFG[PI_RUN_TMPFS_SIZE]:-32M}"
  local log="${dir}/run/direwolf.log"

  # 1. run/ on tmpfs. Everything in it is regenerated on each start —
  #    direwolf.conf, status.html, the pidfiles — and direwolf.log is the only
  #    thing on the system writing continuously. In RAM it cannot corrupt
  #    anything, and it is capped so it cannot exhaust 512 MB either.
  #    fstab rather than a .mount unit: no unit-name escaping to get wrong.
  local fstab="${ROOT_MNT}/etc/fstab"
  if ! sudo grep -q "${dir}/run" "$fstab" 2>/dev/null; then
    printf 'tmpfs %s/run tmpfs defaults,noatime,nosuid,nodev,noexec,size=%s,mode=0755,uid=1000,gid=1000 0 0\n' \
      "$dir" "$size" | sudo tee -a "$fstab" >/dev/null
  fi
  note "run/ mounted as ${size} tmpfs (regenerated content only)"

  # 2. Rotate the log so a long-running gateway cannot fill that tmpfs. Kept
  #    out of /etc/logrotate.d and given its own state file so the daily system
  #    logrotate does not also act on it; a dedicated hourly timer runs it,
  #    because daily is too coarse for a busy band.
  local tmp; tmp="$(mktemp)"
  cat > "$tmp" <<EOF
${log} {
    size 8M
    rotate 2
    copytruncate
    compress
    missingok
    notifempty
}
EOF
  sudo mkdir -p "${ROOT_MNT}/etc/igate"
  sudo cp "$tmp" "${ROOT_MNT}/etc/igate/logrotate.conf"
  sudo chmod 644 "${ROOT_MNT}/etc/igate/logrotate.conf"

  cat > "$tmp" <<'EOF'
[Unit]
Description=Rotate the APRS iGate packet log
Documentation=man:logrotate(8)

[Service]
Type=oneshot
ExecStart=/usr/sbin/logrotate /etc/igate/logrotate.conf --state /var/lib/igate-logrotate.state
EOF
  sudo cp "$tmp" "${ROOT_MNT}/etc/systemd/system/igate-logrotate.service"
  sudo chmod 644 "${ROOT_MNT}/etc/systemd/system/igate-logrotate.service"

  cat > "$tmp" <<'EOF'
[Unit]
Description=Hourly rotation of the APRS iGate packet log

[Timer]
OnCalendar=hourly
Persistent=false
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
EOF
  sudo cp "$tmp" "${ROOT_MNT}/etc/systemd/system/igate-logrotate.timer"
  sudo chmod 644 "${ROOT_MNT}/etc/systemd/system/igate-logrotate.timer"

  # 3. The journal. Storage=volatile keeps it in /run, so systemd stops writing
  #    to the card too. The cost is that logs do not survive a reboot — an
  #    acceptable trade for an appliance, and unavoidable anyway once run/ is
  #    tmpfs, since the packet log does not survive either.
  sudo mkdir -p "${ROOT_MNT}/etc/systemd/journald.conf.d"
  printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=16M\n' \
    | sudo tee "${ROOT_MNT}/etc/systemd/journald.conf.d/volatile.conf" >/dev/null
  note "journal kept in RAM, capped at 16M"
  note "log rotated hourly at 8M, 2 generations kept"

  # 4. Swap. Raspberry Pi OS's rpi-swap defaults to Mechanism=auto, which is
  #    currently "zram+file": compressed swap in RAM, plus a /var/swap file on the
  #    root filesystem that idle pages are periodically written out to
  #    (rpi-zram-writeback). That file is on the SD card, so it is a card writer
  #    on a timer. Mechanism=zram keeps the compressed RAM swap, which is worth
  #    having on 512 MB, and per swap.conf(5) removes the file.
  sudo mkdir -p "${ROOT_MNT}/etc/rpi/swap.conf.d"
  printf '# build_pi_image.sh: compressed swap in RAM only, no writeback file on\n# the SD card. See swap.conf(5).\n[Main]\nMechanism=zram\n' \
    | sudo tee "${ROOT_MNT}/etc/rpi/swap.conf.d/50-igate.conf" >/dev/null
  note "swap is zram only (no /var/swap writeback file on the card)"

  rm -f "$tmp"
}

install_services() {
  step "Installing systemd units"

  local user="${CFG[PI_USER]}"
  local dir="/opt/${CFG[PI_INSTALL_DIR]:-aprs-igate}"
  local sysd="${ROOT_MNT}/etc/systemd/system"
  sudo mkdir -p "$sysd"

  # First boot: install packages and grant device access. Needs the network up,
  # so it cannot run from the boot partition's firstrun mechanism.
  local tmp; tmp="$(mktemp)"
  cat > "$tmp" <<EOF
[Unit]
Description=First-boot setup for the APRS iGate
# userconf.service creates the account and moves its home directory; nothing
# here may touch /home before it has run.
After=network-online.target userconf.service
Wants=network-online.target
ConditionPathExists=!/var/lib/igate-firstboot-done

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/igate-firstboot.sh
StandardOutput=journal+console
StandardError=journal+console

[Install]
WantedBy=multi-user.target
EOF
  sudo cp "$tmp" "${sysd}/igate-firstboot.service"
  sudo chmod 644 "${sysd}/igate-firstboot.service"

  # If first-boot setup fails — an archive outage, a mirror mid-sync, WiFi not up
  # in time — the gateway is blocked behind it and stays blocked. On an
  # unattended appliance that has to heal itself rather than wait for a human,
  # so retry periodically. Once the marker exists the service's condition skips
  # it, and each later firing is a no-op.
  cat > "$tmp" <<'EOF'
[Unit]
Description=Retry APRS iGate first-boot setup until it succeeds

[Timer]
OnBootSec=6min
OnUnitActiveSec=10min
Unit=igate-firstboot.service

[Install]
WantedBy=timers.target
EOF
  sudo cp "$tmp" "${sysd}/igate-firstboot.timer"
  sudo chmod 644 "${sysd}/igate-firstboot.timer"

  cat > "$tmp" <<EOF
[Unit]
Description=Bidirectional APRS iGate (strict whitelist)
After=network-online.target igate-firstboot.service
Wants=network-online.target
# Wants, not Requires: a failed first-boot attempt must not permanently block
# the gateway. igate-firstboot.timer keeps retrying, and the gateway's own
# preflight refuses to start without a working audio device anyway.
Wants=igate-firstboot.service
ConditionPathExists=/opt/${CFG[PI_INSTALL_DIR]:-aprs-igate}/deploy_igate.sh
# Retry for as long as it takes (see Restart= below): an unattended gateway
# should come up whenever its radio does, not give up after five attempts.
StartLimitIntervalSec=0

# run/ is a tmpfs (see /etc/fstab); without this the gateway can start before it
# is mounted and write its pidfiles to the underlying directory instead.
RequiresMountsFor=${dir}/run

[Service]
Type=oneshot
RemainAfterExit=yes
User=${user}
WorkingDirectory=${dir}
ExecStart=${dir}/deploy_igate.sh up
ExecStop=${dir}/deploy_igate.sh down
# deploy_igate.sh launches Direwolf in the background and returns, so this is a
# oneshot. systemd refuses Restart=always and on-success on a oneshot, but
# accepts on-failure: if "deploy_igate.sh up" fails (the radio still missing
# after DEVICE_WAIT, say),
# systemd stops whatever it had started and runs it again 30 seconds later, so a
# radio plugged in or switched on after boot brings the gateway up by itself.
# A start that succeeds is not restarted, so a Direwolf that dies later is not
# covered by this.
Restart=on-failure
RestartSec=30
# DEVICE_WAIT is 60 seconds; the default 90-second start timeout leaves too little
# room after it.
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF
  sudo cp "$tmp" "${sysd}/aprs-igate.service"
  sudo chmod 644 "${sysd}/aprs-igate.service"

  # Read-only LAN monitor. Hardened more than the gateway itself because this is
  # the only thing on the Pi that listens on the network besides sshd: it writes
  # nothing, needs no privilege, and is capped so it cannot starve Direwolf's
  # DSP on a 512 MB machine.
  cat > "$tmp" <<EOF
[Unit]
Description=Read-only LAN web monitor for the APRS iGate
After=network.target aprs-igate.service
Wants=aprs-igate.service
ConditionPathExists=${dir}/igate_web.py

[Service]
Type=simple
User=${user}
WorkingDirectory=${dir}
Environment=IGATE_WEB_PORT=${CFG[PI_WEB_PORT]:-8080}
ExecStart=/usr/bin/python3 ${dir}/igate_web.py
Restart=on-failure
RestartSec=10

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictSUIDSGID=yes
MemoryMax=96M

[Install]
WantedBy=multi-user.target
EOF
  sudo cp "$tmp" "${sysd}/igate-web.service"
  sudo chmod 644 "${sysd}/igate-web.service"
  rm -f "$tmp"

  # The first-boot script itself.
  tmp="$(mktemp)"
  cat > "$tmp" <<EOF
#!/usr/bin/env bash
# Runs once on first boot: installs the gateway's dependencies and grants the
# service account access to the radio's serial and audio devices.
set -euo pipefail

USER_NAME="${user}"
INSTALL_DIR="${dir}"

echo "APRS iGate first-boot setup starting..."

export DEBIAN_FRONTEND=noninteractive

# network-online.target means NetworkManager got an address, which does not mean
# DNS is answering yet. Hitting apt before it is means a transient failure that,
# on a headless appliance, leaves the gateway dead until someone notices.
for i in \$(seq 1 30); do
  getent hosts deb.debian.org >/dev/null 2>&1 && break
  echo "  waiting for DNS (\$i/30)..."
  sleep 5
done

# direwolf is the modem; libhamlib-utils supplies rigctl/rigctld (Debian splits
# these out of the library package); alsa-utils supplies arecord and amixer.
# gawk is not optional: the monitor uses strftime(), a gawk extension, and
# Debian ships mawk as the default awk.
PKGS="direwolf libhamlib-utils alsa-utils avahi-daemon gawk python3"

# Retry the whole update-then-install cycle, not just the install.
#
# The image carries an apt index from whenever it was built, so by first boot it
# is weeks stale and may name package versions that have since been removed from
# the pool — which surfaces as "404 Not Found" on a .deb. Retrying the same
# install cannot fix that, and neither can a single apt-get update if the mirror
# node happens to be mid-sync. Refreshing the index each round, and discarding
# the cached lists entirely from the second round on, is what actually recovers.
apt_install() {
  local n
  for n in 1 2 3 4 5; do
    if [ "\$n" -gt 1 ]; then
      echo "  discarding cached package lists before attempt \$n..."
      rm -rf /var/lib/apt/lists/*
    fi
    apt-get update -qq -o Acquire::Retries=3 || true
    if apt-get install -y -o Acquire::Retries=3 \$PKGS; then
      return 0
    fi
    echo "  attempt \$n of 5 failed to install: \$PKGS" >&2
    sleep \$((n * 15))
  done
  return 1
}

apt_install

# Device access without root, and sudo so the gateway can be managed over SSH.
# On Raspberry Pi OS the first user is in sudo already; this covers the case
# where the account was created fresh rather than renamed.
usermod -aG dialout,audio,sudo "\$USER_NAME"

# Install the staged SSH key now that the home directory exists in its final
# place — userconf.service has renamed and moved it by this point.
HOME_DIR="\$(getent passwd "\$USER_NAME" | cut -d: -f6)"
if [[ -f /etc/igate/authorized_keys && -n "\$HOME_DIR" ]]; then
  mkdir -p "\$HOME_DIR/.ssh"
  cp /etc/igate/authorized_keys "\$HOME_DIR/.ssh/authorized_keys"
  chmod 700 "\$HOME_DIR/.ssh"
  chmod 600 "\$HOME_DIR/.ssh/authorized_keys"
  chown -R "\$USER_NAME:\$USER_NAME" "\$HOME_DIR/.ssh"
fi

# A shortcut, so that "cd aprs-igate" works straight after logging in.
if [[ -n "\$HOME_DIR" && ! -e "\$HOME_DIR/\$(basename "\$INSTALL_DIR")" ]]; then
  ln -s "\$INSTALL_DIR" "\$HOME_DIR/\$(basename "\$INSTALL_DIR")"
  chown -h "\$USER_NAME:\$USER_NAME" "\$HOME_DIR/\$(basename "\$INSTALL_DIR")"
fi

# Compile the locale selected at build time, unless it is one glibc already
# carries, in which case there is nothing to do.
IGATE_LOCALE="${CFG[PI_LOCALE]:-C.UTF-8}"
case "\$IGATE_LOCALE" in
  C|C.UTF-8|C.utf8|POSIX)
    echo "  locale \$IGATE_LOCALE is built into glibc; nothing to generate"
    ;;
  *)
    if command -v raspi-config >/dev/null; then
      raspi-config nonint do_change_locale "\$IGATE_LOCALE" || true
    elif command -v locale-gen >/dev/null; then
      locale-gen || true
    fi
    ;;
esac

# Set the WiFi regulatory domain in the running system as well as the config,
# so the radio is usable without a further reboot.
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_wifi_country "${CFG[PI_WIFI_COUNTRY]}" || true
fi

# Swap only matters here if it lives on the SD card. dphys-swapfile does — it is
# a file on the root filesystem — so remove it where it is in use.
#
# Raspberry Pi OS Trixie does not use it: swap is zram, a compressed block
# device in RAM, managed by rpi-swap. Its default also keeps a /var/swap
# writeback file on the card; the image sets Mechanism=zram in
# /etc/rpi/swap.conf.d/50-igate.conf so that it does not (see
# harden_against_power_loss). zram itself is kept deliberately: on a 512 MB
# machine it trades a little CPU for effective memory, and removing it would make
# things worse rather than safer. "swapon --show" reporting /dev/zram0 is the
# expected result, not a leftover.
if command -v dphys-swapfile >/dev/null; then
  dphys-swapfile swapoff || true
  dphys-swapfile uninstall || true
  systemctl disable --now dphys-swapfile.service 2>/dev/null || true
  echo "  removed dphys-swapfile (swap file on the SD card)"
else
  echo "  no dphys-swapfile; zram swap lives in RAM and is left in place"
fi

chown -R "\$USER_NAME:\$USER_NAME" "\$INSTALL_DIR"

# Outside the install directory on purpose: run/ is a tmpfs and does not
# survive a reboot, and a marker that vanishes would re-run this every boot.
touch /var/lib/igate-firstboot-done

echo "First-boot setup complete."
EOF
  sudo cp "$tmp" "${ROOT_MNT}/usr/local/sbin/igate-firstboot.sh"
  sudo chmod 755 "${ROOT_MNT}/usr/local/sbin/igate-firstboot.sh"
  rm -f "$tmp"

  # Enable units by hand: systemctl cannot run against an offline image.
  local wants="${ROOT_MNT}/etc/systemd/system/multi-user.target.wants"
  sudo mkdir -p "$wants"
  sudo ln -sf /etc/systemd/system/igate-wifi-country.service "${wants}/igate-wifi-country.service"
  sudo ln -sf /etc/systemd/system/igate-firstboot.service "${wants}/igate-firstboot.service"
  note "igate-wifi-country.service enabled (runs before NetworkManager)"
  note "igate-firstboot.service enabled"

  sudo mkdir -p "${ROOT_MNT}/etc/systemd/system/timers.target.wants"
  sudo ln -sf /etc/systemd/system/igate-logrotate.timer \
    "${ROOT_MNT}/etc/systemd/system/timers.target.wants/igate-logrotate.timer"
  sudo ln -sf /etc/systemd/system/igate-firstboot.timer \
    "${ROOT_MNT}/etc/systemd/system/timers.target.wants/igate-firstboot.timer"
  note "igate-logrotate.timer enabled"
  note "igate-firstboot.timer enabled (retries setup every 10 min until it succeeds)"

  if [[ "${CFG[PI_AUTOSTART]:-yes}" == "yes" ]]; then
    sudo ln -sf /etc/systemd/system/aprs-igate.service "${wants}/aprs-igate.service"
    note "aprs-igate.service enabled (starts at boot)"
  else
    note "aprs-igate.service installed but not enabled (PI_AUTOSTART is not yes)"
  fi

  if [[ "${CFG[PI_WEB_MONITOR]:-yes}" == "yes" ]]; then
    sudo ln -sf /etc/systemd/system/igate-web.service "${wants}/igate-web.service"
    note "igate-web.service enabled on port ${CFG[PI_WEB_PORT]:-8080} (read-only, LAN)"
  else
    note "igate-web.service installed but not enabled (PI_WEB_MONITOR is not yes)"
  fi
}

# Settings in the boot partition's config.txt, read by the firmware.
configure_boot_config() {
  step "Configuring boot settings"
  local cfg="${BOOT_MNT}/config.txt"
  [[ -f "$cfg" ]] || { note "no config.txt in the boot partition; left alone"; return 0; }

  # No HDMI audio. The Pi is headless, and the HDMI audio device otherwise takes
  # an ALSA card number as the vc4 driver loads, about nine seconds into boot.
  # A USB sound card gets whichever number is free when it enumerates, so the
  # radio's codec came up as card 1 when attached at boot and card 2 when plugged
  # in later, or when it lost the race at boot — and ADEVICE names a number.
  # Without HDMI audio the onboard headphone output is card 0 and the radio's
  # codec is card 1 every time.
  if sudo grep -qE '^dtoverlay=vc4-kms-v3d$' "$cfg"; then
    sudo sed -i 's/^dtoverlay=vc4-kms-v3d$/dtoverlay=vc4-kms-v3d,noaudio/' "$cfg"
    note "HDMI audio disabled (vc4-kms-v3d,noaudio): the radio's sound card is card 1"
  elif sudo grep -qE '^dtoverlay=vc4-kms-v3d,.*noaudio' "$cfg"; then
    note "HDMI audio already disabled"
  else
    # A release that loads the overlay differently. Changing a line not
    # understood here could cost the display driver, so leave it and say so.
    note "WARNING: no plain 'dtoverlay=vc4-kms-v3d' line in config.txt; HDMI audio left as is."
    note "         The radio's card number may then vary between boots (check 'arecord -l')."
  fi
}

configure_locale() {
  step "Configuring locale and timezone"
  if [[ -n "${CFG[PI_TIMEZONE]:-}" ]]; then
    echo "${CFG[PI_TIMEZONE]}" | sudo tee "${ROOT_MNT}/etc/timezone" >/dev/null
    # Relative target: the symlink must resolve on the Pi, not on this host.
    sudo ln -sf "../usr/share/zoneinfo/${CFG[PI_TIMEZONE]}" "${ROOT_MNT}/etc/localtime"
    note "timezone ${CFG[PI_TIMEZONE]}"
  fi
  if [[ -n "${CFG[PI_KEYBOARD]:-}" && -f "${ROOT_MNT}/etc/default/keyboard" ]]; then
    sudo sed -i "s/^XKBLAYOUT=.*/XKBLAYOUT=\"${CFG[PI_KEYBOARD]}\"/" \
      "${ROOT_MNT}/etc/default/keyboard"
    note "keyboard ${CFG[PI_KEYBOARD]}"
  fi
  if [[ -n "${CFG[PI_LOCALE]:-}" ]]; then
    echo "LANG=${CFG[PI_LOCALE]}" | sudo tee "${ROOT_MNT}/etc/default/locale" >/dev/null
    case "${CFG[PI_LOCALE]}" in
      C|C.UTF-8|C.utf8|POSIX)
        # Compiled into glibc, so there is nothing to generate and no window in
        # which the configured locale does not yet exist.
        note "locale ${CFG[PI_LOCALE]} (built into glibc; nothing to generate)"
        ;;
      *)
        # Setting LANG is not sufficient on its own — the locale must also be
        # compiled, and only locale-gen on the Pi can do that. Uncomment it here
        # so first-boot setup has something to generate. Note that until it
        # does, every shell warns "cannot change locale", which is why
        # C.UTF-8 is the default.
        if [[ -f "${ROOT_MNT}/etc/locale.gen" ]]; then
          sudo sed -i "s/^# *\(${CFG[PI_LOCALE]} UTF-8\)/\1/" "${ROOT_MNT}/etc/locale.gen"
        fi
        note "locale ${CFG[PI_LOCALE]} (generated during first-boot setup)"
        ;;
    esac
  fi

  # Do not import the client's locale over SSH. Debian enables
  # "AcceptEnv LANG LC_*", so an ssh session arrives carrying whatever the
  # laptop uses — and a locale this appliance has never generated makes every
  # shell emit setlocale warnings. AcceptEnv is additive, so a drop-in cannot
  # subtract it; the main config has to be edited. The Pi then uses its own
  # LANG, which is the deterministic behaviour an appliance wants anyway.
  local sshd="${ROOT_MNT}/etc/ssh/sshd_config"
  if [[ -f "$sshd" ]]; then
    sudo sed -i 's/^\(AcceptEnv[[:space:]].*\)$/#\1   # disabled: appliance uses its own locale/' "$sshd"
    note "sshd no longer imports the client's LANG/LC_* variables"
  fi
}

# -------------------------------------------------------------- commands --

cmd_build() {
  validate
  require_tools
  trap cleanup EXIT INT TERM

  fetch_image
  mount_image
  configure_access
  configure_wifi
  configure_locale
  configure_boot_config
  install_project
  harden_against_power_loss
  install_services

  step "Finalising"
  sudo sync
  cleanup
  trap - EXIT INT TERM

  # Written only here, after every customisation stage has succeeded. If the
  # build dies anywhere earlier this file does not exist, and flash refuses.
  {
    echo "built=$(date -Is)"
    echo "hostname=${CFG[PI_HOSTNAME]}"
    echo "user=${CFG[PI_USER]}"
    echo "variant=${CFG[PI_IMAGE_VARIANT]}"
    echo "web_monitor=${CFG[PI_WEB_MONITOR]:-yes}:${CFG[PI_WEB_PORT]:-8080}"
  } > "${OUTPUT_IMG}.built"

  echo
  echo "Image ready: ${OUTPUT_IMG}"
  echo
  echo "Write it to an SD card with:"
  echo "  ./build_pi_image.sh flash /dev/sdX     <- replace sdX; find it with lsblk"
  echo "or with Raspberry Pi Imager, choosing 'Use custom' and selecting that file."
  echo
  echo "On first boot the Pi joins WiFi, installs Direwolf and hamlib, and starts"
  echo "the gateway. Allow a few minutes, then:"
  echo "  ssh ${CFG[PI_USER]}@${CFG[PI_HOSTNAME]}.local"
  echo "  cd ${CFG[PI_INSTALL_DIR]:-aprs-igate} && ./deploy_igate.sh monitor"
  echo
  echo "Step-by-step instructions are in PI-SETUP.md."
  echo
  echo "Check the radio's device names on the Pi before trusting the gateway"
  echo "(./deploy_igate.sh config there shows them). They come from the radio"
  echo "profile, and the Pi numbers its own USB devices."
}

cmd_flash() {
  local dev="${1:-}"
  [[ -n "$dev" ]] || die "usage: $0 flash <device>   e.g. /dev/mmcblk0 (built-in reader) or /dev/sdb (USB). Check with lsblk."
  [[ -f "$OUTPUT_IMG" ]] || die "no built image at $OUTPUT_IMG — run '$0 build' first"

  # An image that exists but was never customised is the dangerous case: it boots,
  # expands its filesystem, and then sits there with no account, no WiFi and no
  # SSH, which is easily mistaken for a hardware or SD card fault.
  local marker="${OUTPUT_IMG}.built"
  if [[ ! -f "$marker" ]]; then
    echo "Error: ${OUTPUT_IMG} exists but was never finished." >&2
    echo "  No completion marker (${marker})." >&2
    echo "  A build that dies partway — a missed sudo password at the loop mount is" >&2
    echo "  the usual cause — leaves a decompressed but UNCUSTOMISED image: no SSH," >&2
    echo "  no user account, and no WiFi credentials. Re-run '$0 build' and check it" >&2
    echo "  ends with 'Image ready:'." >&2
    exit 1
  fi
  if [[ "$marker" -ot "$OUTPUT_IMG" ]]; then
    die "${OUTPUT_IMG} is newer than its completion marker — re-run '$0 build'."
  fi
  echo "Image completed: $(sed -n 's/^built=//p' "$marker")"
  [[ -b "$dev" ]] || die "$dev is not a block device"

  # Writing to the wrong device destroys it silently, so refuse anything that is
  # not a whole removable disk, and anything currently mounted.
  #
  # lsblk rather than /sys/block: deriving the base device by stripping digits
  # gets "mmcblk" from "mmcblk0p1", which is not a device at all. Built-in card
  # readers also commonly report RM=0 while still being hotplug, so accept
  # either flag.
  local type rm hotplug
  read -r type rm hotplug < <(lsblk -dno TYPE,RM,HOTPLUG "$dev" 2>/dev/null) \
    || die "cannot query $dev with lsblk"

  [[ "$type" == "disk" ]] \
    || die "$dev is a ${type:-unknown}, not a whole disk. Pass the card itself (e.g. /dev/sdb, /dev/mmcblk0), not a partition."
  [[ "$rm" == "1" || "$hotplug" == "1" ]] \
    || die "$dev is neither removable nor hotplug. Refusing — check the device name with 'lsblk'."

  if lsblk -no MOUNTPOINT "$dev" 2>/dev/null | grep -q .; then
    # udisksctl rather than umount: these are almost always desktop-mounted
    # removable media, and udisksctl also tells the desktop the card was
    # released, so it does not helpfully remount it a moment later.
    echo "Error: $dev has mounted partitions. Unmount them first:" >&2
    lsblk -rno PATH,MOUNTPOINT "$dev" 2>/dev/null \
      | awk '$2 != "" { print "  udisksctl unmount -b " $1 }' >&2
    echo "  (plain 'umount' may work too, but leaves the desktop free to remount it.)" >&2
    exit 1
  fi

  echo "About to OVERWRITE this device:"
  lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINT "$dev"
  echo
  read -r -p "Type the device name again to confirm ($dev): " confirm
  [[ "$confirm" == "$dev" ]] || die "confirmation did not match; nothing written"

  step "Writing image"
  sudo dd if="$OUTPUT_IMG" of="$dev" bs=4M conv=fsync status=progress
  sudo sync
  echo "Done. Eject the card and boot the Pi."
}

main() {
  case "${1:-build}" in
    check) validate ;;
    build) cmd_build ;;
    flash) cmd_flash "${2:-}" ;;
    *)
      echo "Usage: $0 {check|build|flash <device>}" >&2
      echo "  flash device: /dev/mmcblk0 for a built-in reader, /dev/sdX for USB (check lsblk)" >&2
      exit 1
      ;;
  esac
}

# Guard so the customisation functions can be sourced and tested individually.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
