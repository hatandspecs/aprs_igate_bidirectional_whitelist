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
#   ./build_pi_image.sh flash /dev/sdX Write a built image to an SD card.
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
  rm -f "$OUTPUT_IMG"
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

  # Raspberry Pi OS images are two partitions: FAT boot, then ext4 root.
  [[ -e "${LOOP_DEV}p1" && -e "${LOOP_DEV}p2" ]] \
    || die "expected two partitions on $LOOP_DEV — is this a Raspberry Pi OS image?"

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
    "${SCRIPT_DIR}/" "${dest}/"

  # The Pi runs bare-metal. Rewrite only the installed copy.
  sudo sed -i 's/^DEPLOY_MODE *=.*/DEPLOY_MODE = bare-metal/' "${dest}/igate.conf"

  # The Pi is a different machine: its audio card and serial ports are
  # enumerated at first boot, not known now. Flag them at the top of the file.
  local hdr; hdr="$(mktemp)"
  {
    echo "# NOTE: ADEVICE, CAT_DEVICE and PTT_DEVICE below were copied from the"
    echo "# build host. Verify them on the Pi with 'arecord -l' and"
    echo "# 'ls /dev/ttyUSB* /dev/ttyACM*' before trusting the gateway."
    sudo cat "${dest}/igate.conf"
  } > "$hdr"
  sudo cp "$hdr" "${dest}/igate.conf"
  rm -f "$hdr"

  sudo chmod 600 "${dest}/igate.secrets"
  note "installed to /opt/${CFG[PI_INSTALL_DIR]:-aprs-igate}"
  note "DEPLOY_MODE set to bare-metal in the installed copy"
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
ConditionPathExists=!${dir}/run/.firstboot-done

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

  cat > "$tmp" <<EOF
[Unit]
Description=Bidirectional APRS iGate (strict whitelist)
After=network-online.target igate-firstboot.service
Wants=network-online.target
Requires=igate-firstboot.service
ConditionPathExists=/opt/${CFG[PI_INSTALL_DIR]:-aprs-igate}/deploy_igate.sh

[Service]
Type=oneshot
RemainAfterExit=yes
User=${user}
WorkingDirectory=${dir}
ExecStart=${dir}/deploy_igate.sh up
ExecStop=${dir}/deploy_igate.sh down
# deploy_igate.sh launches Direwolf in the background and returns, so this is a
# oneshot. systemd rejects Restart= on Type=oneshot; if the radio was not
# plugged in at boot, "systemctl start aprs-igate" after plugging it in.

[Install]
WantedBy=multi-user.target
EOF
  sudo cp "$tmp" "${sysd}/aprs-igate.service"
  sudo chmod 644 "${sysd}/aprs-igate.service"
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

# Retry rather than abort: first-boot networking is the flakiest moment in the
# life of this machine, and there is nobody watching the console.
retry() {
  local n
  for n in 1 2 3 4 5; do
    "\$@" && return 0
    echo "  attempt \$n of 5 failed: \$*" >&2
    sleep \$((n * 10))
  done
  return 1
}

retry apt-get update -qq
# direwolf is the modem; libhamlib-utils supplies rigctl/rigctld (Debian splits
# these out of the library package); alsa-utils supplies arecord and amixer.
# gawk is not optional: the monitor uses strftime(), a gawk extension, and
# Debian ships mawk as the default awk.
retry apt-get install -y direwolf libhamlib-utils alsa-utils avahi-daemon gawk

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

# Compile the locale selected at build time. Without this every login shell
# warns about an invalid locale, and SSH sessions warn once per shell.
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_change_locale "${CFG[PI_LOCALE]:-en_US.UTF-8}" || true
elif command -v locale-gen >/dev/null; then
  locale-gen || true
fi

# Set the WiFi regulatory domain in the running system as well as the config,
# so the radio is usable without a further reboot.
if command -v raspi-config >/dev/null; then
  raspi-config nonint do_wifi_country "${CFG[PI_WIFI_COUNTRY]}" || true
fi

mkdir -p "\$INSTALL_DIR/run"
chown -R "\$USER_NAME:\$USER_NAME" "\$INSTALL_DIR"
touch "\$INSTALL_DIR/run/.firstboot-done"
chown "\$USER_NAME:\$USER_NAME" "\$INSTALL_DIR/run/.firstboot-done"

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

  if [[ "${CFG[PI_AUTOSTART]:-yes}" == "yes" ]]; then
    sudo ln -sf /etc/systemd/system/aprs-igate.service "${wants}/aprs-igate.service"
    note "aprs-igate.service enabled (starts at boot)"
  else
    note "aprs-igate.service installed but not enabled (PI_AUTOSTART is not yes)"
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
    # Setting LANG is not sufficient on its own — the locale must also be
    # compiled, which only locale-gen on the Pi can do. Uncomment it here so
    # that first-boot setup has something to generate; otherwise every shell
    # warns "cannot change locale", including over SSH, where the client
    # forwards its own LC_* variables.
    if [[ -f "${ROOT_MNT}/etc/locale.gen" ]]; then
      sudo sed -i "s/^# *\(${CFG[PI_LOCALE]} UTF-8\)/\1/" "${ROOT_MNT}/etc/locale.gen"
    fi
    note "locale ${CFG[PI_LOCALE]} (generated on first boot)"
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
  install_project
  install_services

  step "Finalising"
  sudo sync
  cleanup
  trap - EXIT INT TERM

  echo
  echo "Image ready: ${OUTPUT_IMG}"
  echo
  echo "Write it to an SD card with:"
  echo "  ./build_pi_image.sh flash /dev/sdX"
  echo "or with Raspberry Pi Imager, choosing 'Use custom' and selecting that file."
  echo
  echo "On first boot the Pi joins WiFi, installs Direwolf and hamlib, and starts"
  echo "the gateway. Allow a few minutes, then:"
  echo "  ssh ${CFG[PI_USER]}@${CFG[PI_HOSTNAME]}.local"
  echo "  cd ${CFG[PI_INSTALL_DIR]:-aprs-igate} && ./deploy_igate.sh monitor"
  echo
  echo "Step-by-step instructions are in PI-SETUP.md."
  echo
  echo "Verify ADEVICE, CAT_DEVICE and PTT_DEVICE on the Pi before trusting the"
  echo "gateway — they were copied from this host and the Pi enumerates its own."
}

cmd_flash() {
  local dev="${1:-}"
  [[ -n "$dev" ]] || die "usage: $0 flash /dev/sdX"
  [[ -f "$OUTPUT_IMG" ]] || die "no built image at $OUTPUT_IMG — run '$0 build' first"
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
    die "$dev has mounted partitions. Unmount them first (umount ${dev}*)."
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
      echo "Usage: $0 {check|build|flash /dev/sdX}" >&2
      exit 1
      ;;
  esac
}

# Guard so the customisation functions can be sourced and tested individually.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi
