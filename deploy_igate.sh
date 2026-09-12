#!/usr/bin/env bash
# Builds and runs the bidirectional APRS iGate described in
# aprs-igate-prototype-test.md, driven by igate.conf. See README.md for full
# setup instructions.
#
# Usage (every command takes an optional [config-file], default igate.conf):
#   ./deploy_igate.sh config [file]   Parse, validate, and print the resolved
#                                     config (like `docker-compose config`).
#   ./deploy_igate.sh build [file]    docker mode: build the image.
#                                     bare-metal mode: install direwolf/hamlib.
#   ./deploy_igate.sh up [file]       Render direwolf.conf and start it.
#   ./deploy_igate.sh down [file]     Stop it.
#   ./deploy_igate.sh restart [file]  down, then up.
#   ./deploy_igate.sh status [file]   Is it running; also writes run/status.html.
#   ./deploy_igate.sh logs [file]     Follow the raw Direwolf log.
#   ./deploy_igate.sh monitor [file]  Follow the log annotated: what was heard,
#                                     what was gated to RF, what was dropped.
#   ./deploy_igate.sh uninstall [file] Tear down to a zero state. docker mode:
#                                     stop/remove container + image. bare-metal
#                                     mode: stop processes, remove the direwolf
#                                     package (installed just for this), leave
#                                     hamlib/alsa-utils (shared with other ham
#                                     radio software — see README.md).
#
# DEPLOY_MODE in igate.conf picks docker (default) or bare-metal; override
# per-invocation with the IGATE_MODE env var.
#
# The APRS-IS passcode is never required to live in igate.conf. It is
# resolved in this order (highest priority first): the IGATE_PASSCODE
# environment variable, then igate.secrets next to this script (gitignored),
# then IGLOGIN_PASSCODE in the config file itself. See README.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_CONFIG="${SCRIPT_DIR}/igate.conf"
SECRETS_FILE="${SCRIPT_DIR}/igate.secrets"
RUN_DIR="${SCRIPT_DIR}/run"
RENDERED_CONF="${RUN_DIR}/direwolf.conf"
STATUS_HTML="${RUN_DIR}/status.html"

IMAGE_NAME="aprs-igate:latest"
CONTAINER_NAME="aprs-igate"
NETWORK_NAME="aprs-igate-net"
NETWORK_SUBNET="172.28.7.0/29"

BARE_DIREWOLF_PID="${RUN_DIR}/direwolf.pid"
BARE_RIGCTLD_PID="${RUN_DIR}/rigctld.pid"
BARE_LOG="${RUN_DIR}/direwolf.log"

declare -A CFG
MODE=""

# --- config file parsing (plain "key = value", # comments, blank lines) ---
load_config() {
  local file="$1"
  [[ -f "$file" ]] || { echo "Config file not found: $file" >&2; exit 1; }

  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$line")"
    [[ -z "$line" || "$line" != *"="* ]] && continue
    key="$(sed -e 's/[[:space:]]*$//' <<<"${line%%=*}")"
    val="$(sed -e 's/^[[:space:]]*//' <<<"${line#*=}")"
    CFG["$key"]="$val"
  done < "$file"
}

apply_secrets() {
  if [[ -f "$SECRETS_FILE" ]]; then
    load_config "$SECRETS_FILE"
  fi
  if [[ -n "${IGATE_PASSCODE:-}" ]]; then
    CFG[IGLOGIN_PASSCODE]="$IGATE_PASSCODE"
  fi
}

resolve_mode() {
  MODE="${IGATE_MODE:-${CFG[DEPLOY_MODE]:-docker}}"
  case "$MODE" in
    docker|bare-metal) ;;
    *)
      echo "Invalid mode '$MODE' (DEPLOY_MODE in config or IGATE_MODE env var)." >&2
      echo "Must be 'docker' or 'bare-metal'." >&2
      exit 1
      ;;
  esac
}

CONFIG_IN_USE=""
load_and_resolve() {
  local file="${1:-$DEFAULT_CONFIG}"
  CONFIG_IN_USE="$file"
  load_config "$file"
  apply_secrets
  resolve_mode
}

require() {
  local name="$1" placeholder="${2:-}"
  local val="${CFG[$name]:-}"
  if [[ -z "$val" || "$val" == "$placeholder" ]]; then
    echo "Config: $name is still unset or a placeholder." >&2
    if [[ "$name" == "IGLOGIN_PASSCODE" ]]; then
      echo "  Set it in igate.secrets, or export IGATE_PASSCODE. See README.md." >&2
    fi
    exit 1
  fi
}

validate_config() {
  require MYCALL
  require ADEVICE
  require RIG_MODEL
  require CAT_DEVICE
  require CAT_BAUD
  require PTT_DEVICE
  require PTT_TYPE
  require IGLOGIN_CALL
  require IGLOGIN_PASSCODE
  require WHITELIST_CALLS
}

# WHITELIST_CALLS is comma-separated in the config file; render as the
# slash-chained addressee filter Direwolf expects: g/KD3CCO*/W3XYZ*
# A position beacon so the station appears on aprs.fi. Deliberately optional and
# off by default: this project's premise is transmitting as little as possible.
#
# BEACON_TO = IG sends it to APRS-IS over the internet and never keys the radio,
# which is what puts the station on the map without using airtime. BEACON_TO = RF
# transmits it on 144.390 as well, which is a real cost on a shared national
# channel and should be a considered choice.
# The digipeat path applied to everything this station transmits: gated
# messages and beacons alike. Blank means direct, with no digipeater.
#
# Naming a digipeater explicitly is the deterministic choice, because a
# digipeater repeats any packet carrying its own callsign in the path whatever
# WIDEn-N aliases it answers to. The generic form depends on that digi's alias
# configuration — W3YA-1 answers WIDE2, not WIDE1.
build_tx_via() {
  local via="${CFG[TX_VIA]:-}"
  if [[ -z "$via" ]]; then
    # Legacy: IGTXVIA carried the channel and the path together ("0 WIDE2-1").
    # Keep honouring the path part of an older config file.
    local legacy="${CFG[IGTXVIA]:-}"
    legacy="${legacy#0}"
    via="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$legacy")"
  fi
  # Commas are the natural separator to write; AX.25 wants spaces.
  tr ',' ' ' <<<"$via" | sed -e 's/  */ /g' -e 's/^ //' -e 's/ $//'
}

# Maidenhead grid square -> decimal degrees, at the centre of the square.
# Deliberately the preferred way to give a beacon position: a 6-character grid
# is about 4 x 6 km, so it is rounded by construction rather than by remembering
# to round. Plain awk, not gawk — no strftime here, so mawk is fine.
grid_to_latlon() {
  local g="${1^^}" n="${#1}"
  [[ "$g" =~ ^[A-R][A-R][0-9][0-9]([A-X][A-X])?$ ]] || return 1
  awk -v g="$g" -v n="$n" '
    BEGIN {
      F = index("ABCDEFGHIJKLMNOPQR", substr(g,1,1)) - 1
      S = index("ABCDEFGHIJKLMNOPQR", substr(g,2,1)) - 1
      lon = -180 + F * 20
      lat =  -90 + S * 10
      lon += substr(g,3,1) * 2
      lat += substr(g,4,1) * 1
      if (n >= 6) {
        lon += (index("ABCDEFGHIJKLMNOPQRSTUVWX", substr(g,5,1)) - 1) * (5/60)
        lat += (index("ABCDEFGHIJKLMNOPQRSTUVWX", substr(g,6,1)) - 1) * (2.5/60)
        lon += (5/60)/2; lat += (2.5/60)/2
      } else {
        lon += 1; lat += 0.5
      }
      printf "%.4f %.4f\n", lat, lon
    }'
}

build_beacon() {
  local to="${CFG[BEACON_TO]:-off}"
  local lat="${CFG[BEACON_LAT]:-}" lon="${CFG[BEACON_LON]:-}"
  local every="${CFG[BEACON_EVERY]:-30:00}"
  local comment="${CFG[BEACON_COMMENT]:-}"

  [[ "$to" == "off" || -z "$to" ]] && return 0

  # A grid square, if given, is authoritative — it is the rounded form.
  local grid="${CFG[BEACON_GRID]:-}"
  if [[ -n "$grid" ]]; then
    local derived
    if ! derived="$(grid_to_latlon "$grid")"; then
      echo "Warning: BEACON_GRID='${grid}' is not a valid Maidenhead locator (e.g. FN10cs); no beacon." >&2
      return 0
    fi
    read -r lat lon <<<"$derived"
  fi

  if [[ -z "$lat" || -z "$lon" ]]; then
    echo "Warning: BEACON_TO=${to} but no position is set (BEACON_GRID or BEACON_LAT/BEACON_LON); no beacon." >&2
    return 0
  fi

  # Overlay character on the "&" gateway symbol, which is how APRS advertises
  # what kind of gate this is:
  #   R  receive-only iGate      I  generic iGate
  #   T  transmitting iGate, 1-hop path    2  transmitting iGate, 2-hop path
  # R is the default here because it describes what this station does for
  # everybody else: the transmit path is whitelist-only, so no other operator's
  # messages are ever relayed to RF. Advertising T would invite someone to rely
  # on delivery this gate will not perform.
  local overlay="${CFG[BEACON_OVERLAY]:-R}"

  # via= applies only to the RF beacon; a digipeat path is meaningless on the
  # copy sent straight to APRS-IS over the internet.
  local txvia; txvia="$(build_tx_via)"
  _emit_beacon() {
    printf 'PBEACON %sdelay=%s every=%s overlay=%s symbol="igate" lat=%s long=%s' \
      "$1" "$2" "$every" "$overlay" "$lat" "$lon"
    [[ -n "$comment" ]] && printf ' comment="%s"' "$comment"
    [[ -z "$1" && -n "$txvia" ]] && printf ' via="%s"' "${txvia// /,}"
    printf '\n'
  }

  case "$to" in
    IG)   _emit_beacon "sendto=IG " "0:30" ;;
    RF)   _emit_beacon "" "1:00" ;;
    # Both paths: the RF beacon is what local stations see on their radios, and
    # the IS beacon guarantees the station appears on aprs.fi even when no
    # neighbouring iGate happens to hear and gate the RF one.
    BOTH) _emit_beacon "sendto=IG " "0:30"; _emit_beacon "" "1:00" ;;
    *)    echo "Warning: BEACON_TO='${to}' is not IG, RF, BOTH or off; no beacon." >&2; return 0 ;;
  esac
}

# Optional restriction on what gets gated UP. "FILTER 0 IG" is the RF->APRS-IS
# direction (the reverse of "FILTER IG 0"), and d/ matches packets that were
# actually repeated by the named digipeater — Direwolf checks the AX.25
# has-been-used bit, not merely the presence of the callsign in the path.
#
# This narrows the station's usefulness to the network, so it is off by default
# and belongs in a test, not in normal operation.
build_rx_filter() {
  local via="${CFG[RX_VIA]:-}"
  [[ -z "$via" ]] && return 0
  local IFS=',' call
  local -a calls=()
  for call in $via; do
    call="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$call")"
    [[ -n "$call" ]] && calls+=("$call")
  done
  [[ ${#calls[@]} -eq 0 ]] && return 0
  local joined; IFS=/ joined="${calls[*]}"
  echo "FILTER    0 IG d/${joined}"
}

build_filter() {
  local raw="${CFG[WHITELIST_CALLS]}"
  local call trimmed
  local -a calls=()
  IFS=',' read -ra raw_calls <<<"$raw"
  for call in "${raw_calls[@]}"; do
    trimmed="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$call")"
    [[ -n "$trimmed" ]] && calls+=("$trimmed")
  done
  local IFS=/
  echo "g/${calls[*]}"
}

mask_secret() {
  local val="$1" n=${#1}
  if (( n <= 2 )); then printf '%s' "***"; return; fi
  printf '***%s' "${val: -2}"
}

# A bare "0" in the config listing reads like a port number, not like "off".
via_desc() {
  local v; v="$(build_tx_via)"
  if [[ -z "$v" ]]; then echo "direct (no digipeater)"; else echo "via ${v}"; fi
}

rx_via_desc() {
  local v="${CFG[RX_VIA]:-}"
  if [[ -z "$v" ]]; then
    echo "gate everything heard"
  else
    echo "ONLY gate packets digipeated by ${v}"
  fi
}

beacon_desc() {
  local to="${CFG[BEACON_TO]:-off}"
  local where=""
  if [[ -n "${CFG[BEACON_GRID]:-}" ]]; then
    local d
    if d="$(grid_to_latlon "${CFG[BEACON_GRID]}")"; then
      where=" at ${CFG[BEACON_GRID]^^} (${d% *}, ${d#* })"
    else
      where=" at INVALID GRID '${CFG[BEACON_GRID]}'"
    fi
  elif [[ -n "${CFG[BEACON_LAT]:-}" && -n "${CFG[BEACON_LON]:-}" ]]; then
    where=" at ${CFG[BEACON_LAT]}, ${CFG[BEACON_LON]}"
  fi
  case "$to" in
    off|"") echo "off" ;;
    IG)   echo "every ${CFG[BEACON_EVERY]:-30:00} to APRS-IS only (no RF)${where}, overlay ${CFG[BEACON_OVERLAY]:-R}" ;;
    RF)   echo "every ${CFG[BEACON_EVERY]:-30:00} TRANSMITTED ON RF${where}, overlay ${CFG[BEACON_OVERLAY]:-R}" ;;
    BOTH) echo "every ${CFG[BEACON_EVERY]:-30:00} TRANSMITTED ON RF and to APRS-IS${where}, overlay ${CFG[BEACON_OVERLAY]:-R}" ;;
    *)    echo "invalid BEACON_TO='${to}'" ;;
  esac
}

port_desc() {
  if [[ "${1:-0}" == "0" ]]; then echo "0 (disabled)"; else echo "$1 (LISTENING ON ALL INTERFACES)"; fi
}

print_config() {
  local filter
  filter="$(build_filter)"
  cat <<EOF
DEPLOY_MODE      = ${MODE}
MYCALL           = ${CFG[MYCALL]}
MODEM            = ${CFG[MODEM]}
ACHANNELS        = ${CFG[ACHANNELS]}
ADEVICE          = ${CFG[ADEVICE]}
RIG_MODEL        = ${CFG[RIG_MODEL]}
CAT_DEVICE       = ${CFG[CAT_DEVICE]}
CAT_BAUD         = ${CFG[CAT_BAUD]}
PTT_DEVICE       = ${CFG[PTT_DEVICE]}
PTT_TYPE         = ${CFG[PTT_TYPE]}
IGSERVER         = ${CFG[IGSERVER]}
IGLOGIN_CALL     = ${CFG[IGLOGIN_CALL]}
IGLOGIN_PASSCODE = $(mask_secret "${CFG[IGLOGIN_PASSCODE]}")
WHITELIST_CALLS  = ${CFG[WHITELIST_CALLS]}
TX_VIA           = $(via_desc)
RX_VIA           = $(rx_via_desc)
BEACON           = $(beacon_desc)
AGW_PORT         = $(port_desc "${CFG[AGW_PORT]:-0}")
KISS_PORT        = $(port_desc "${CFG[KISS_PORT]:-0}")
IGTXLIMIT        = ${CFG[IGTXLIMIT]:-6 10}

Resolved Direwolf FILTER: IG 0 ${filter}
EOF
}

render_conf() {
  local filter
  filter="$(build_filter)"
  mkdir -p "$RUN_DIR"
  cat > "$RENDERED_CONF" <<EOF
# Generated by deploy_igate.sh from igate.conf — do not edit by hand.
ACHANNELS ${CFG[ACHANNELS]}
ADEVICE   ${CFG[ADEVICE]}

CHANNEL 0
MYCALL   ${CFG[MYCALL]}
MODEM    ${CFG[MODEM]}

# rigctld bridges the radio's separate CAT and PTT serial ports; Direwolf
# just talks to it over loopback. See README.md.
PTT RIG 2 localhost:4532

# Direwolf listens for AGW and KISS TCP clients and binds them to 0.0.0.0, with
# no authentication of any kind — anything that can reach the KISS port can
# transmit arbitrary packets under MYCALL. Docker mode concealed this by
# publishing no ports; bare-metal mode does not, so both are disabled unless
# deliberately enabled in igate.conf. Direwolf has no bind-address option, so
# "off" is the only way to make them unreachable.
AGWPORT   ${CFG[AGW_PORT]:-0}
KISSPORT  ${CFG[KISS_PORT]:-0}

IGSERVER ${CFG[IGSERVER]}
IGLOGIN  ${CFG[IGLOGIN_CALL]} ${CFG[IGLOGIN_PASSCODE]}

# Server-side subscription filter: without this, APRS-IS falls back to
# sending only traffic involving stations we've heard on RF recently — which
# is empty until this iGate has decoded something. IGFILTER asks the server
# to forward matching traffic regardless of RF-heard history.
IGFILTER  ${filter}

FILTER    IG 0 ${filter}
$(build_rx_filter)

# Direwolf's "Message Sender Position" feature transmits a position report
# from a message's sender "regardless of any other filtering rules" (see
# Successful-APRS-IGate-Operation.pdf) — a documented bypass of FILTER IG,
# observed live: the SMS gateway's own beacon was transmitted after we gated
# its messages. IGMSP 0 disables it; this project's whitelist has no
# exceptions, courtesy or otherwise.
IGMSP     0

IGTXVIA   0 $(build_tx_via)
IGTXLIMIT ${CFG[IGTXLIMIT]:-6 10}

$(build_beacon)
EOF
  chmod 600 "$RENDERED_CONF"
  echo "Wrote ${RENDERED_CONF} (whitelist filter: ${filter})"
}

render_status_html() {
  local running="$1" detail="$2" filter now status_label status_class
  filter="$(build_filter)"
  now="$(date '+%Y-%m-%d %H:%M:%S %Z')"

  local -a raw_calls=()
  IFS=',' read -ra raw_calls <<<"${CFG[WHITELIST_CALLS]:-}"
  local call trimmed calls_html=""
  for call in "${raw_calls[@]}"; do
    trimmed="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$call")"
    [[ -n "$trimmed" ]] && calls_html="${calls_html}<li><code>${trimmed}</code></li>"
  done

  if [[ "$running" == "true" ]]; then
    status_label="RUNNING"; status_class="up"
  else
    status_label="STOPPED"; status_class="down"
  fi

  mkdir -p "$RUN_DIR"
  cat > "$STATUS_HTML" <<EOF
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>${CFG[MYCALL]:-iGate} Status</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; background: #fafafa; }
  h1 { font-size: 1.4rem; }
  h2 { font-size: 1.1rem; margin-top: 1.5rem; }
  .badge { display: inline-block; padding: .25rem .75rem; border-radius: .5rem; font-weight: 600; font-size: .85rem; vertical-align: middle; }
  .up { background: #d4f4dd; color: #146c2e; }
  .down { background: #f4d4d4; color: #8a1f1f; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
  td { padding: .35rem .5rem; border-bottom: 1px solid #e2e2e2; font-size: .95rem; }
  td:first-child { color: #666; width: 40%; }
  code { background: #eee; padding: .1rem .35rem; border-radius: .25rem; }
  ul { margin: .5rem 0; padding-left: 1.25rem; }
  .stamp { color: #888; font-size: .8rem; margin-top: 2rem; }
  @media (prefers-color-scheme: dark) {
    body { background: #181818; color: #e4e4e4; }
    td { border-color: #333; }
    td:first-child { color: #999; }
    code { background: #2a2a2a; }
    .up { background: #143d24; color: #6fe3a0; }
    .down { background: #3d1414; color: #e36f6f; }
    .stamp { color: #777; }
  }
</style>
</head>
<body>
<h1>${CFG[MYCALL]:-?} <span class="badge ${status_class}">${status_label}</span></h1>
<p>${detail}</p>

<table>
<tr><td>Mode</td><td>${MODE}</td></tr>
<tr><td>APRS-IS server</td><td>${CFG[IGSERVER]:-}</td></tr>
<tr><td>Login call</td><td>${CFG[IGLOGIN_CALL]:-}</td></tr>
<tr><td>Audio device</td><td><code>${CFG[ADEVICE]:-}</code></td></tr>
<tr><td>CAT device</td><td><code>${CFG[CAT_DEVICE]:-}</code></td></tr>
<tr><td>PTT device</td><td><code>${CFG[PTT_DEVICE]:-}</code></td></tr>
<tr><td>TX via</td><td>$(via_desc)</td></tr>
<tr><td>RX gating</td><td>$(rx_via_desc)</td></tr>
<tr><td>TX rate limit</td><td>${CFG[IGTXLIMIT]:-}</td></tr>
<tr><td>Direwolf FILTER</td><td><code>IG 0 ${filter}</code></td></tr>
</table>

<h2>Whitelisted calls</h2>
<ul>${calls_html}</ul>

<p class="stamp">Static snapshot generated ${now} by <code>./deploy_igate.sh status</code>.
This page does not auto-update — re-run <code>status</code> (or <code>restart</code>
after editing <code>igate.conf</code>) to refresh it.</p>
</body>
</html>
EOF
}

require_docker() {
  command -v docker >/dev/null || {
    echo "docker not found on PATH. Install Docker, or see README.md for the Podman equivalent." >&2
    exit 1
  }
}

image_exists() {
  docker image inspect "$IMAGE_NAME" >/dev/null 2>&1
}

is_running() {
  [[ -n "$(docker ps -q --filter "name=^/${CONTAINER_NAME}$")" ]]
}

container_exists() {
  [[ -n "$(docker ps -aq --filter "name=^/${CONTAINER_NAME}$")" ]]
}

gid_of() {
  getent group "$1" 2>/dev/null | cut -d: -f3
}

# Refuse to start if the ALSA card named by ADEVICE isn't present. Learned the
# hard way: PTT rides the serial port while audio rides the USB codec, so if
# the codec is gone (unplugged, re-enumerated) Direwolf still keys the radio
# and transmits an unmodulated carrier — audible, but nothing can decode it.
# Card number out of ADEVICE. Handles plughw:N,M / hw:N,M / plughw:N.
# Empty for non-numeric card names.
audio_card_number() {
  sed -n 's/^[a-z]*hw:\([0-9][0-9]*\).*/\1/p' <<<"${CFG[ADEVICE]:-}"
}

require_audio_device() {
  local adev="${CFG[ADEVICE]:-}" card
  card="$(audio_card_number)"
  if [[ -z "$card" ]]; then
    echo "Note: cannot parse a card number from ADEVICE='${adev}'; skipping audio device check." >&2
    return 0
  fi
  if [[ ! -e "/dev/snd/controlC${card}" ]]; then
    echo "ERROR: ADEVICE='${adev}' refers to ALSA card ${card}, but /dev/snd/controlC${card} does not exist." >&2
    echo "  The radio's USB audio codec is not connected (or re-enumerated to a different card)." >&2
    echo "  Refusing to start: PTT would still key the radio, transmitting a carrier with no audio." >&2
    echo "  Check the USB cable, then run 'arecord -l' and update ADEVICE in igate.conf if the card number changed." >&2
    exit 1
  fi
}

# Apply the calibrated ALSA mixer levels. These reset to device defaults every
# time the radio's USB re-enumerates, and the defaults are wrong: the default
# TX level over-deviates (signal is audible but nothing can decode it) and the
# default capture gain is far too low to decode anything. Getting this wrong
# fails silently, so `up` always sets it rather than trusting what survived.
apply_audio_levels() {
  local card tx rx agc
  card="$(audio_card_number)"
  [[ -z "$card" ]] && return 0
  command -v amixer >/dev/null || {
    echo "Note: amixer not found; skipping audio level setup (install alsa-utils)." >&2
    return 0
  }

  tx="${CFG[TX_AUDIO_LEVEL]:-}"
  rx="${CFG[RX_AUDIO_LEVEL]:-}"
  agc="${CFG[DISABLE_AGC]:-yes}"

  if [[ -n "$tx" ]]; then
    amixer -c "$card" cset name='Speaker Playback Volume' "${tx},${tx}" >/dev/null 2>&1 \
      && echo "Audio: TX level ${tx}" \
      || echo "Note: could not set TX level (no 'Speaker Playback Volume' on card ${card})." >&2
  fi
  if [[ -n "$rx" ]]; then
    amixer -c "$card" cset name='Mic Capture Volume' "${rx},${rx}" >/dev/null 2>&1 \
      && echo "Audio: RX level ${rx}" \
      || echo "Note: could not set RX level (no 'Mic Capture Volume' on card ${card})." >&2
  fi
  if [[ "$agc" == "yes" ]]; then
    amixer -c "$card" cset name='Auto Gain Control' off >/dev/null 2>&1 \
      && echo "Audio: AGC off"
  fi
}



# Put the radio on the right frequency and mode via CAT, after rigctld is up.
#
# This exists because a mode slip is silent and catastrophic: in plain FM the
# FTX-1 modulates from the MIC input rather than the USB codec, so Direwolf
# keys the radio and transmits a clean carrier with no data in it. Everything
# looks correct — PTT works, the waterfall shows a signal — but nothing on
# earth can decode it. PKTFM is hamlib's name for the radio's FM-D mode.
apply_radio_settings() {
  [[ "${CFG[RADIO_SET_ON_UP]:-yes}" == "yes" ]] || return 0
  local freq="${CFG[RADIO_FREQ]:-}" mode="${CFG[RADIO_MODE]:-PKTFM}"
  local pb="${CFG[RADIO_PASSBAND]:-16000}"
  [[ -z "$freq" ]] && return 0

  local rc=(rigctl -m 2 -r 127.0.0.1:4532)
  [[ "$MODE" == docker ]] && rc=(docker exec "$CONTAINER_NAME" rigctl -m 2 -r 127.0.0.1:4532)

  # rigctld needs a moment after container start before it answers.
  local i
  for i in $(seq 1 10); do
    timeout 5 "${rc[@]}" f >/dev/null 2>&1 && break
    sleep 1
  done

  if timeout 5 "${rc[@]}" F "$freq" >/dev/null 2>&1 \
     && timeout 5 "${rc[@]}" M "$mode" "$pb" >/dev/null 2>&1; then
    local got_f got_m
    got_f="$(timeout 5 "${rc[@]}" f 2>/dev/null | head -1)"
    got_m="$(timeout 5 "${rc[@]}" m 2>/dev/null | head -1)"
    echo "Radio: ${got_f} Hz, mode ${got_m}"
    if [[ "$got_m" == "FM" ]]; then
      echo "  WARNING: radio reports plain FM, not data-FM. Transmit audio comes" >&2
      echo "  from the mic instead of USB — transmissions will NOT decode." >&2
    fi
  else
    echo "Note: could not set radio frequency/mode via CAT (is rigctld up?)." >&2
  fi
}


# --- egress restriction ------------------------------------------------------
# The container needs exactly two things off-box: DNS, and a TCP connection to
# the APRS-IS server. Docker's default bridge grants unrestricted outbound
# access, so the container is placed on its own network and filtered in the
# DOCKER-USER chain, which Docker consults before its own forwarding rules.
#
# Requires root (iptables). Set RESTRICT_EGRESS = no to skip.
egress_enabled() { [[ "${CFG[RESTRICT_EGRESS]:-yes}" == "yes" ]]; }

network_ensure() {
  docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 && return 0
  docker network create --subnet "$NETWORK_SUBNET" "$NETWORK_NAME" >/dev/null \
    && echo "Created network ${NETWORK_NAME} (${NETWORK_SUBNET})"
}

egress_rules_present() {
  sudo -n iptables -C DOCKER-USER -s "$NETWORK_SUBNET" -j DROP 2>/dev/null
}

egress_apply() {
  egress_enabled || return 0
  command -v iptables >/dev/null || { echo "Note: iptables not found; egress unrestricted." >&2; return 0; }

  local port="${CFG[IGSERVER_PORT]:-14580}"
  if ! sudo -n true 2>/dev/null; then
    echo "Note: egress restriction needs sudo. Run 'sudo -v' then 'up' again," >&2
    echo "      or set RESTRICT_EGRESS = no in igate.conf." >&2
    return 0
  fi

  egress_remove_rules
  # Order matters: these are inserted, so the DROP must go in first to end up last.
  sudo iptables -I DOCKER-USER 1 -s "$NETWORK_SUBNET" -j DROP
  sudo iptables -I DOCKER-USER 1 -s "$NETWORK_SUBNET" -p tcp --dport "$port" -j RETURN
  sudo iptables -I DOCKER-USER 1 -s "$NETWORK_SUBNET" -p udp --dport 53 -j RETURN
  sudo iptables -I DOCKER-USER 1 -s "$NETWORK_SUBNET" -p tcp --dport 53 -j RETURN
  sudo iptables -I DOCKER-USER 1 -s "$NETWORK_SUBNET" -m state --state ESTABLISHED,RELATED -j RETURN
  echo "Egress restricted: DNS + tcp/${port} only (from ${NETWORK_SUBNET})"
}

egress_remove_rules() {
  sudo -n true 2>/dev/null || return 0
  local port="${CFG[IGSERVER_PORT]:-14580}"
  sudo iptables -D DOCKER-USER -s "$NETWORK_SUBNET" -m state --state ESTABLISHED,RELATED -j RETURN 2>/dev/null || true
  sudo iptables -D DOCKER-USER -s "$NETWORK_SUBNET" -p tcp --dport 53 -j RETURN 2>/dev/null || true
  sudo iptables -D DOCKER-USER -s "$NETWORK_SUBNET" -p udp --dport 53 -j RETURN 2>/dev/null || true
  sudo iptables -D DOCKER-USER -s "$NETWORK_SUBNET" -p tcp --dport "$port" -j RETURN 2>/dev/null || true
  sudo iptables -D DOCKER-USER -s "$NETWORK_SUBNET" -j DROP 2>/dev/null || true
}

bare_is_running() {
  [[ -f "$BARE_DIREWOLF_PID" ]] && kill -0 "$(cat "$BARE_DIREWOLF_PID")" 2>/dev/null
}

bare_stop() {
  local pid
  if [[ -f "$BARE_DIREWOLF_PID" ]]; then
    pid="$(cat "$BARE_DIREWOLF_PID")"
    kill "$pid" 2>/dev/null || true
    rm -f "$BARE_DIREWOLF_PID"
  fi
  if [[ -f "$BARE_RIGCTLD_PID" ]]; then
    pid="$(cat "$BARE_RIGCTLD_PID")"
    kill "$pid" 2>/dev/null || true
    rm -f "$BARE_RIGCTLD_PID"
  fi
}

# Package names differ by distribution. The hamlib CLI tools (rigctl, rigctld)
# ship as "hamlib" on Fedora but "libhamlib-utils" on Debian and Raspberry Pi OS.
_bare_install() {
  local mgr pkgs
  if command -v apt-get >/dev/null; then
    mgr="apt"; pkgs="direwolf libhamlib-utils alsa-utils gawk"
  elif command -v dnf >/dev/null; then
    mgr="dnf"; pkgs="direwolf hamlib alsa-utils gawk"
  else
    echo "No supported package manager found (apt or dnf)." >&2
    echo "Install manually: direwolf, the hamlib CLI tools, alsa-utils." >&2
    return 1
  fi

  echo "Installing ${pkgs} via ${mgr} (sudo password may be required)..."
  if [[ "$mgr" == apt ]]; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y $pkgs
  else
    sudo dnf install -y $pkgs
  fi

  echo
  echo "First time only: allow this user to open the serial and audio devices,"
  echo "then log out and back in:"
  echo "  sudo usermod -aG dialout,audio \$USER"
}

# ---------------------------------------------------------------- commands --

cmd_config() {
  load_and_resolve "${1:-}"
  validate_config
  echo "Config OK: ${1:-$DEFAULT_CONFIG}"
  echo
  print_config
}

_docker_build() {
  require_docker
  docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"
}

cmd_build() {
  load_and_resolve "${1:-}"
  if [[ "$MODE" == docker ]]; then _docker_build; else _bare_install; fi
}

_docker_up() {
  if is_running; then
    echo "iGate already running."
    return 0
  fi
  container_exists && docker rm "$CONTAINER_NAME" >/dev/null

  local cat_device="${CFG[CAT_DEVICE]}" ptt_device="${CFG[PTT_DEVICE]}"
  [[ -e "$cat_device" ]] || echo "Warning: $cat_device does not exist on this host yet (radio unplugged?)." >&2
  [[ "$ptt_device" != "$cat_device" && ! -e "$ptt_device" ]] && echo "Warning: $ptt_device does not exist on this host yet (radio unplugged?)." >&2
  [[ -e /dev/snd ]] || echo "Warning: /dev/snd does not exist on this host (no ALSA audio devices)." >&2

  require_audio_device
  apply_audio_levels

  image_exists || _docker_build

  render_conf

  local -a group_flags=()
  local dialout_gid audio_gid
  dialout_gid="$(gid_of dialout)"; audio_gid="$(gid_of audio)"
  [[ -n "$dialout_gid" ]] && group_flags+=(--group-add "$dialout_gid")
  [[ -n "$audio_gid" ]] && group_flags+=(--group-add "$audio_gid")

  # Serial: only the specific port(s) this radio uses, read/write, no mknod.
  local -a device_flags=(--device "${cat_device}:${cat_device}:rw")
  [[ "$ptt_device" != "$cat_device" ]] && device_flags+=(--device "${ptt_device}:${ptt_device}:rw")

  # Audio: pass ONLY this radio's ALSA card, not all of /dev/snd. Passing the
  # whole directory would also hand over the machine's built-in microphone
  # (controlC0/pcmC0D0c), which this container has no business reaching.
  local card snd_node
  card="$(audio_card_number)"
  if [[ -n "$card" ]]; then
    for snd_node in "/dev/snd/controlC${card}" /dev/snd/pcmC${card}D*; do
      [[ -e "$snd_node" ]] && device_flags+=(--device "${snd_node}:${snd_node}:rw")
    done
    # ALSA needs the timer node for its scheduling.
    [[ -e /dev/snd/timer ]] && device_flags+=(--device "/dev/snd/timer:/dev/snd/timer:rw")
  else
    echo "Note: ADEVICE has no numeric card; falling back to passing all of /dev/snd." >&2
    device_flags+=(--device /dev/snd:/dev/snd:rw)
  fi

  if egress_enabled; then
    network_ensure
    egress_apply
  fi

  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --network "$(egress_enabled && echo "$NETWORK_NAME" || echo bridge)" \
    --user "$(id -u):$(id -g)" \
    --cap-drop=ALL \
    --security-opt no-new-privileges:true \
    --pids-limit=64 \
    --memory=512m \
    --read-only \
    --tmpfs /tmp \
    "${device_flags[@]}" \
    "${group_flags[@]}" \
    -e RIG_MODEL="${CFG[RIG_MODEL]}" \
    -e CAT_DEVICE="${cat_device}" \
    -e CAT_BAUD="${CFG[CAT_BAUD]}" \
    -e PTT_DEVICE="${ptt_device}" \
    -e PTT_TYPE="${CFG[PTT_TYPE]}" \
    -v "${RENDERED_CONF}:/etc/direwolf/direwolf.conf:ro" \
    "$IMAGE_NAME" >/dev/null

  sleep 1
  if is_running; then
    echo "iGate up (docker). container=${CONTAINER_NAME}"
    apply_radio_settings
    echo "Watch packet flow: ./deploy_igate.sh monitor"
  else
    echo "Container exited immediately — check: docker logs ${CONTAINER_NAME}" >&2
    exit 1
  fi
}

_bare_up() {
  if bare_is_running; then
    echo "iGate already running (bare-metal, pid $(cat "$BARE_DIREWOLF_PID"))."
    return 0
  fi

  local cat_device="${CFG[CAT_DEVICE]}" ptt_device="${CFG[PTT_DEVICE]}"
  [[ -e "$cat_device" ]] || echo "Warning: $cat_device does not exist on this host yet (radio unplugged?)." >&2
  [[ "$ptt_device" != "$cat_device" && ! -e "$ptt_device" ]] && echo "Warning: $ptt_device does not exist on this host yet (radio unplugged?)." >&2

  require_audio_device
  apply_audio_levels

  { command -v direwolf >/dev/null && command -v rigctld >/dev/null; } || _bare_install

  render_conf

  rigctld \
    -m "${CFG[RIG_MODEL]}" \
    -r "$cat_device" \
    -s "${CFG[CAT_BAUD]}" \
    -p "$ptt_device" \
    -P "${CFG[PTT_TYPE]}" \
    -t 4532 \
    -T 127.0.0.1 \
    >> "$BARE_LOG" 2>&1 &
  echo $! > "$BARE_RIGCTLD_PID"

  local i
  for i in $(seq 1 20); do
    kill -0 "$(cat "$BARE_RIGCTLD_PID")" 2>/dev/null || {
      echo "rigctld exited during startup — check ${BARE_LOG} (bad CAT_DEVICE/PTT_DEVICE/RIG_MODEL?)." >&2
      rm -f "$BARE_RIGCTLD_PID"
      exit 1
    }
    (exec 3<>/dev/tcp/127.0.0.1/4532) 2>/dev/null && break
    sleep 0.5
  done

  # -d i and stdbuf for the same reasons as the container path: without -d i the
  # RF->APRS-IS direction is invisible in the log, and without stdbuf the log is
  # block-buffered so `monitor` lags behind reality.
  nohup stdbuf -oL -eL direwolf -c "$RENDERED_CONF" -t 0 -d i >> "$BARE_LOG" 2>&1 &
  echo $! > "$BARE_DIREWOLF_PID"
  sleep 1

  if bare_is_running; then
    echo "iGate up (bare-metal). direwolf pid=$(cat "$BARE_DIREWOLF_PID") rigctld pid=$(cat "$BARE_RIGCTLD_PID")"
    apply_radio_settings
    echo "Log: ${BARE_LOG}. Watch packet flow: ./deploy_igate.sh monitor"
  else
    echo "direwolf exited immediately — check ${BARE_LOG}" >&2
    exit 1
  fi
}

cmd_up() {
  load_and_resolve "${1:-}"
  validate_config
  if [[ "$MODE" == docker ]]; then _docker_up; else _bare_up; fi
}

_docker_down() {
  require_docker
  if is_running; then
    docker stop "$CONTAINER_NAME" >/dev/null
  fi
  if container_exists; then
    docker rm "$CONTAINER_NAME" >/dev/null
    echo "iGate stopped (docker)."
  else
    echo "iGate not running (docker)."
  fi
}

_bare_down() {
  if bare_is_running; then
    bare_stop
    echo "iGate stopped (bare-metal)."
  else
    bare_stop
    echo "iGate not running (bare-metal)."
  fi
}

cmd_down() {
  load_and_resolve "${1:-}"
  if [[ "$MODE" == docker ]]; then _docker_down; else _bare_down; fi
}

cmd_status() {
  load_and_resolve "${1:-}"
  local running="false" detail=""
  if [[ "$MODE" == docker ]]; then
    require_docker
    if is_running; then
      running="true"
      detail="$(docker ps --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}  {{.Status}}')"
    fi
  else
    if bare_is_running; then
      running="true"
      detail="direwolf pid $(cat "$BARE_DIREWOLF_PID"), rigctld pid $(cat "$BARE_RIGCTLD_PID" 2>/dev/null || echo '?')"
    fi
  fi

  if [[ "$running" == "true" ]]; then
    echo "iGate running (${MODE}): ${detail}"
  else
    echo "iGate not running (${MODE})."
  fi

  render_status_html "$running" "$detail"
  echo "Status page: file://${STATUS_HTML}"
}

cmd_logs() {
  load_and_resolve "${1:-}"
  if [[ "$MODE" == docker ]]; then
    require_docker
    container_exists || { echo "No container yet — run './deploy_igate.sh up' first." >&2; exit 1; }
    docker logs -f "$CONTAINER_NAME"
  else
    [[ -f "$BARE_LOG" ]] || { echo "No log yet — run './deploy_igate.sh up' first." >&2; exit 1; }
    tail -f "$BARE_LOG"
  fi
}

# Annotate Direwolf's raw output into plain-language packet flow.
#
# The important thing this fixes: Direwolf prints "[ig>tx]" when a packet
# ARRIVES from APRS-IS, before the whitelist runs — NOT when it transmits.
# The real transmit line is "[0L]". Reading "[ig>tx]" as "transmitted" makes
# the whitelist look broken when it is working correctly. So this pairs them:
# an [ig>tx] followed by [0L] is GATED, an [ig>tx] with nothing after is DROP.
# gawk specifically, not whatever "awk" happens to be. strftime() is a gawk
# extension and Debian-family systems (Raspberry Pi OS included) ship mawk as
# the default awk, which rejects the program at parse time and silently emits
# nothing — a monitor that shows no packets looks like a dead gateway.
require_gawk() {
  command -v gawk >/dev/null && return 0
  echo "monitor requires gawk (the default 'awk' on this system lacks strftime)." >&2
  if command -v apt-get >/dev/null; then
    echo "  Install it with: sudo apt-get install -y gawk" >&2
  elif command -v dnf >/dev/null; then
    echo "  Install it with: sudo dnf install -y gawk" >&2
  fi
  echo "  Meanwhile './deploy_igate.sh logs' shows the raw, unannotated log" >&2
  echo "  (note that it does NOT redact the APRS-IS passcode)." >&2
  exit 1
}

monitor_filter() {
  gawk -v use_color="$1" -v show_detail="$2" '
    function ts()   { return strftime("%H:%M:%S") }
    function C(c,s) { return use_color ? "\033[" c "m" s "\033[0m" : s }
    # fflush is required: gawk block-buffers when stdout is a pipe, which
    # would make a live monitor emit nothing until the buffer filled.
    function emit(color, label, text) {
      printf "%s  %s  %s\n", ts(), C(color, label), text
      fflush()
    }
    # Direwolf prints the human-readable decode BETWEEN the received frame and
    # the "gated up" line. Buffer it so the output reads in a sensible order:
    #   RF RX  ->  RF->IS UP  ->  decoded
    function flushdetail(   i) {
      for (i = 1; i <= ndetail; i++) {
        if (show_detail) { printf "                       %s\n", C("0;90", detail[i]) }
      }
      ndetail = 0
      fflush()
    }
    # Pairing [ig>tx] with [0L] cannot assume they alternate. Direwolf accepts
    # packets from APRS-IS as they arrive but transmits under IGTXLIMIT, so the
    # real log looks like  ig>tx, 0L, ig>tx, ig>tx, 0L, 0L  — and matching by
    # position labels a gated packet as dropped and vice versa. Match on the
    # payload instead, and keep a queue rather than one slot.
    #
    # The two forms carry the same payload in different wrappers:
    #   [ig>tx] SMS>APOSMS,TCPIP*,qAC,WA7BF::KD3CCO-7 :hello{50
    #   [0L]    KD3CCO-10>APDW17:}SMS>APOSMS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello{50
    # Strip the AX.25 header, then the third-party "}" wrapper and its header,
    # and both reduce to  :KD3CCO-7 :hello{50
    function payload(s,   i, rest) {
      i = index(s, ":"); if (i == 0) return s
      rest = substr(s, i + 1)
      if (substr(rest, 1, 1) == "}") {
        rest = substr(rest, 2)
        i = index(rest, ":")
        if (i > 0) rest = substr(rest, i + 1)
      }
      return rest
    }
    function addpending(text) {
      ptail++
      pkey[ptail] = payload(text); ptext[ptail] = text; ptime[ptail] = systime()
    }
    # A packet is only known to have been dropped by the absence of a [0L], so
    # it can only be declared after waiting longer than the transmit queue could
    # plausibly hold it. Too short and a gated packet is reported as dropped.
    function expirepending(   i) {
      for (i = phead; i <= ptail; i++) {
        if (pkey[i] == "") continue
        if (systime() - ptime[i] >= drop_after) {
          emit("1;31", "IS DROP  ", ptext[i]); pkey[i] = ""; ptext[i] = ""
        }
      }
      while (phead <= ptail && pkey[phead] == "") phead++
    }
    function matchpending(text,   k, i) {
      k = payload(text)
      for (i = phead; i <= ptail; i++) {
        if (pkey[i] == k) { matched = ptext[i]; pkey[i] = ""; ptext[i] = ""; return 1 }
      }
      return 0
    }
    function flushallpending(   i) {
      for (i = phead; i <= ptail; i++)
        if (pkey[i] != "") emit("1;31", "IS DROP  ", ptext[i])
      phead = ptail + 1
    }
    BEGIN { ndetail = 0; collecting = 0; phead = 1; ptail = 0; drop_after = 15 }

    # --- redaction ---
    # Direwolf echoes its APRS-IS login, which contains the passcode in clear
    # text. Never render it: the whole point of igate.secrets is to keep that
    # value out of sight. (It is still present in the raw `logs` output.)
    /pass [0-9]+/ {
      flushdetail(); collecting = 0; expirepending()
      sub(/pass [0-9]+/, "pass ****")
      emit("0;35", "IS LOGIN ", $0)
      next
    }

    # --- noise ---
    /^\[rx>ig\] #/                                { next }   # APRS-IS keepalive
    /^Rx IGate: Truncated information part at CR/ { next }

    # --- events ---
    /^\[rx>ig\]/ {
      expirepending(); emit("1;35", "RF->IS UP", substr($0, 9))
      collecting = 0; flushdetail(); next
    }
    /^\[ig>tx\]/ { flushdetail(); collecting = 0; expirepending(); addpending(substr($0, 9)); next }
    /^\[0L\]/ {
      flushdetail(); collecting = 0
      if (matchpending(substr($0, 6))) { emit("1;32", "IS GATED ", matched) }
      else                             { emit("1;36", "TX LOCAL ", substr($0, 6)) }
      expirepending(); next
    }
    /^\[0\.[0-9]+\]/ {
      flushdetail(); expirepending()
      sub(/^\[[^]]*\][ ]?/, "")
      emit("1;34", "RF RX    ", $0)
      collecting = 1; next
    }
    /^\[ig\]/ { flushdetail(); collecting = 0; expirepending(); emit("0;35", "IS SERVER", substr($0, 6)); next }

    # --- state and problems ---
    /Audio input level is too low|Audio input level is too high|[Ee]rror|ERROR|No such device|failed/ {
      flushdetail(); collecting = 0; expirepending(); emit("1;33", "WARN     ", $0); next
    }
    /Now connected to IGate|Attached to KISS|Ready to accept/ {
      flushdetail(); collecting = 0; expirepending(); emit("0;32", "INFO     ", $0); next
    }

    # --- decode detail belonging to the frame above ---
    /^[[:space:]]*$/ { flushdetail(); collecting = 0; next }

    # Anything still queued at end of input never got its [0L], so it was dropped.
    END { flushdetail(); flushallpending() }
    {
      if (collecting && ndetail < 12) { detail[++ndetail] = $0 }
    }
  '
}

cmd_monitor() {
  load_and_resolve "${1:-}"
  require_gawk
  local use_color=1 show_detail=1
  [[ -t 1 ]] || use_color=0
  # "monitor raw" hides Direwolf's decoded interpretation of each frame.
  [[ "${2:-}" == "raw" || "${1:-}" == "raw" ]] && show_detail=0

  cat <<'LEGEND'
Packet flow monitor.  Ctrl-C to stop.

  RF RX      heard on the air and decoded
  RF->IS UP  heard on RF and gated UP to APRS-IS by THIS station
  IS GATED   came from APRS-IS, matched the whitelist, TRANSMITTED
  IS DROP    came from APRS-IS, did NOT match the whitelist, dropped
  TX LOCAL   transmitted from this station (beacon or injected packet)
  IS SERVER  APRS-IS server chatter        WARN/INFO  problems and state
  IS LOGIN   APRS-IS login handshake (passcode redacted)

  Indented grey lines are Direwolf's decode of the frame above
  ("monitor raw" hides them).

LEGEND

  if [[ "$MODE" == docker ]]; then
    require_docker
    container_exists || { echo "No container yet — run './deploy_igate.sh up' first." >&2; exit 1; }
    docker logs -f --tail 30 "$CONTAINER_NAME" 2>&1 | monitor_filter "$use_color" "$show_detail"
  else
    [[ -f "$BARE_LOG" ]] || { echo "No log yet — run './deploy_igate.sh up' first." >&2; exit 1; }
    tail -f -n 30 "$BARE_LOG" | monitor_filter "$use_color" "$show_detail"
  fi
}

cmd_uninstall() {
  load_and_resolve "${1:-}"
  if [[ "$MODE" == docker ]]; then
    require_docker
    is_running && docker stop "$CONTAINER_NAME" >/dev/null
    container_exists && docker rm "$CONTAINER_NAME" >/dev/null
    if image_exists; then
      docker rmi "$IMAGE_NAME" >/dev/null
      echo "Removed image ${IMAGE_NAME}."
    fi
    rm -rf "$RUN_DIR"
    echo "Uninstalled (docker mode) — back to a freshly-cloned state."
    echo "(The fedora:43 base layers stay in Docker's cache; harmless, and reused if you rebuild.)"
  else
    bare_stop
    rm -rf "$RUN_DIR"
    if rpm -q direwolf >/dev/null 2>&1; then
      echo "Removing the direwolf package (installed specifically for this project)..."
      sudo dnf remove -y direwolf
    fi
    echo
    echo "Left hamlib and alsa-utils installed: they're general-purpose packages"
    echo "likely used by other ham radio software on this machine (e.g. WSJT-X"
    echo "depends on hamlib for CAT control). Remove them yourself only if you're"
    echo "sure nothing else on this machine needs them:"
    echo "  sudo dnf remove hamlib alsa-utils"
    echo
    echo "Uninstalled (bare-metal mode)."
  fi
}

# Wrapped in a function and invoked on the final line so bash parses the whole
# script before executing any of it. Bash otherwise reads a script incrementally
# by byte offset: editing this file while a long-running subcommand (monitor,
# logs) is active would shift those offsets and make the running shell resume
# mid-construct, producing a spurious syntax error.
main() {
  case "${1:-}" in
    config) cmd_config "${2:-}" ;;
    build) cmd_build "${2:-}" ;;
    up) cmd_up "${2:-}" ;;
    down) cmd_down "${2:-}" ;;
    restart) cmd_down "${2:-}"; cmd_up "${2:-}" ;;
    status) cmd_status "${2:-}" ;;
    logs) cmd_logs "${2:-}" ;;
    monitor) cmd_monitor "${2:-}" ;;
    uninstall) cmd_uninstall "${2:-}" ;;
    *)
      echo "Usage: $0 {config|build|up|down|restart|status|logs|monitor|uninstall} [config-file]" >&2
      exit 1
      ;;
  esac
}

main "$@"

