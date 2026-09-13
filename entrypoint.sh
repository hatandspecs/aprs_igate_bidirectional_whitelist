#!/usr/bin/env bash
# Container entrypoint. For a radio with CAT control it starts rigctld, bound to
# loopback only so it is reachable from Direwolf inside this container and
# nowhere else; for the FTX-1 rigctld also bridges its separate CAT and PTT
# serial ports. It then execs Direwolf so Direwolf becomes PID 1 and receives
# `docker stop`'s signal directly. A radio without CAT (CAT = none) has no
# rigctld, and keys through whatever PTT line the rendered direwolf.conf names.
#
# Radio settings arrive as env vars set by deploy_igate.sh; nothing here is meant
# to be edited by hand — see igate.conf and radios/ instead.
set -euo pipefail

# Unset means the FTX-1 arrangement, which is what every container started
# before these variables existed was running.
CAT="${CAT:-hamlib}"
PTT_METHOD="${PTT_METHOD:-rig}"

case "$CAT" in
  hamlib)
    : "${RIG_MODEL:?RIG_MODEL not set}"
    : "${CAT_DEVICE:?CAT_DEVICE not set}"
    : "${CAT_BAUD:?CAT_BAUD not set}"

    # rigctld keys the radio only when PTT is a CAT command. With any other PTT
    # method it only sets frequency and mode, and must not hold a PTT port.
    rig_ptt=()
    if [[ "$PTT_METHOD" == rig ]]; then
      : "${PTT_DEVICE:?PTT_DEVICE not set}"
      : "${PTT_TYPE:?PTT_TYPE not set}"
      rig_ptt=(-p "$PTT_DEVICE" -P "$PTT_TYPE")
    fi

    rigctld \
      -m "$RIG_MODEL" \
      -r "$CAT_DEVICE" \
      -s "$CAT_BAUD" \
      "${rig_ptt[@]}" \
      -t 4532 \
      -T 127.0.0.1 &
    RIGCTLD_PID=$!

    # Give rigctld a moment to open the serial ports and start listening before
    # Direwolf's first PTT attempt.
    for _ in $(seq 1 20); do
      if ! kill -0 "$RIGCTLD_PID" 2>/dev/null; then
        echo "rigctld exited during startup — check CAT_DEVICE/PTT_DEVICE/RIG_MODEL." >&2
        exit 1
      fi
      (exec 3<>/dev/tcp/127.0.0.1/4532) 2>/dev/null && break
      sleep 0.5
    done
    ;;
  none)
    ;;
  *)
    echo "CAT = '${CAT}' is not valid (hamlib or none)." >&2
    exit 1
    ;;
esac

# stdbuf -oL -eL: stdout isn't a TTY under `docker run -d`, so without this
# Direwolf's C stdio fully-buffers output instead of flushing per line —
# lines sit invisible in `docker logs` until the internal buffer fills, which
# makes `deploy_igate.sh monitor` appear dead. Do not remove.
#
# -d i is REQUIRED, not optional debugging: Direwolf only prints the "[rx>ig]"
# line for RF->APRS-IS gating when the iGate debug level is >= 1
# (igate.c:1604, set from d_i_opt at direwolf.c:1129). Without it the entire
# uplink direction is invisible and you cannot tell whether this station
# gated a packet up or some other igate did. Keep it enabled.
#
# NOTE: it is "-d i", not "-d g". "-d g" is the GPS debug flag
# (direwolf.c:222) and does nothing useful here.
exec stdbuf -oL -eL direwolf -c /etc/direwolf/direwolf.conf -t 0 -d i
