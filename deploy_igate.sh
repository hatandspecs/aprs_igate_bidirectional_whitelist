#!/usr/bin/env bash
# Builds and runs the bidirectional APRS iGate described in
# aprs-igate-prototype-test.md, driven by igate.conf. See README.md for full
# setup instructions.
#
# Usage (every command takes an optional [config-file], default igate.conf):
#   ./deploy_igate.sh config [file]   Parse, validate, and print the resolved
#                                     config (like `docker-compose config`).
#   ./deploy_igate.sh build [file]    docker mode: build the image.
#                                     bare-metal mode: dnf install direwolf/hamlib.
#   ./deploy_igate.sh up [file]       Render direwolf.conf and start it.
#   ./deploy_igate.sh down [file]     Stop it.
#   ./deploy_igate.sh restart [file]  down, then up.
#   ./deploy_igate.sh status [file]   Is it running; also writes run/status.html.
#   ./deploy_igate.sh logs [file]     Follow the log ([rf>ig]/[ig>tx]).
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

load_and_resolve() {
  local file="${1:-$DEFAULT_CONFIG}"
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
IGTXVIA          = ${CFG[IGTXVIA]}
IGTXLIMIT        = ${CFG[IGTXLIMIT]}

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

IGSERVER ${CFG[IGSERVER]}
IGLOGIN  ${CFG[IGLOGIN_CALL]} ${CFG[IGLOGIN_PASSCODE]}

# Server-side subscription filter: without this, APRS-IS falls back to
# sending only traffic involving stations we've heard on RF recently — which
# is empty until this iGate has decoded something. IGFILTER asks the server
# to forward matching traffic regardless of RF-heard history.
IGFILTER  ${filter}

FILTER    IG 0 ${filter}

# Direwolf's "Message Sender Position" feature transmits a position report
# from a message's sender "regardless of any other filtering rules" (see
# Successful-APRS-IGate-Operation.pdf) — a documented bypass of FILTER IG,
# observed live: the SMS gateway's own beacon was transmitted after we gated
# its messages. IGMSP 0 disables it; this project's whitelist has no
# exceptions, courtesy or otherwise.
IGMSP     0

IGTXVIA   ${CFG[IGTXVIA]}
IGTXLIMIT ${CFG[IGTXLIMIT]}
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
<tr><td>TX via</td><td>${CFG[IGTXVIA]:-}</td></tr>
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
require_audio_device() {
  local adev="${CFG[ADEVICE]:-}" card
  # Handles plughw:N,M / hw:N,M / plughw:N. Non-numeric card names are skipped.
  card="$(sed -n 's/^[a-z]*hw:\([0-9][0-9]*\).*/\1/p' <<<"$adev")"
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

_bare_install() {
  echo "Installing direwolf, hamlib, alsa-utils (you may be prompted for your sudo password)..."
  sudo dnf install -y direwolf hamlib alsa-utils
  echo
  echo "First time only: make sure your user can open the serial/audio devices, then log out and back in:"
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

  image_exists || _docker_build

  render_conf

  local -a group_flags=()
  local dialout_gid audio_gid
  dialout_gid="$(gid_of dialout)"; audio_gid="$(gid_of audio)"
  [[ -n "$dialout_gid" ]] && group_flags+=(--group-add "$dialout_gid")
  [[ -n "$audio_gid" ]] && group_flags+=(--group-add "$audio_gid")

  local -a device_flags=(--device "${cat_device}:${cat_device}")
  [[ "$ptt_device" != "$cat_device" ]] && device_flags+=(--device "${ptt_device}:${ptt_device}")

  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --user "$(id -u):$(id -g)" \
    --cap-drop=ALL \
    --security-opt no-new-privileges:true \
    --pids-limit=64 \
    --read-only \
    --tmpfs /tmp \
    --tmpfs /var/lock \
    "${device_flags[@]}" \
    --device /dev/snd:/dev/snd \
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
    echo "Watch for [rf>ig] / [ig>tx] tag lines: ./deploy_igate.sh logs"
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

  nohup direwolf -c "$RENDERED_CONF" -t 0 >> "$BARE_LOG" 2>&1 &
  echo $! > "$BARE_DIREWOLF_PID"
  sleep 1

  if bare_is_running; then
    echo "iGate up (bare-metal). direwolf pid=$(cat "$BARE_DIREWOLF_PID") rigctld pid=$(cat "$BARE_RIGCTLD_PID")"
    echo "Log: ${BARE_LOG}. Watch for [rf>ig] / [ig>tx]: ./deploy_igate.sh logs"
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

case "${1:-}" in
  config) cmd_config "${2:-}" ;;
  build) cmd_build "${2:-}" ;;
  up) cmd_up "${2:-}" ;;
  down) cmd_down "${2:-}" ;;
  restart) cmd_down "${2:-}"; cmd_up "${2:-}" ;;
  status) cmd_status "${2:-}" ;;
  logs) cmd_logs "${2:-}" ;;
  uninstall) cmd_uninstall "${2:-}" ;;
  *)
    echo "Usage: $0 {config|build|up|down|restart|status|logs|uninstall} [config-file]" >&2
    exit 1
    ;;
esac
