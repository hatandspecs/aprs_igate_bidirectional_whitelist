#!/usr/bin/env bash
# Container entrypoint. Starts rigctld as a bridge between the two serial
# ports the FTX-1 exposes (CAT control and PTT), bound to loopback only so
# it is reachable from Direwolf inside this container and nowhere else, then
# execs Direwolf so it becomes PID 1 and receives `docker stop`'s signal
# directly. Radio settings arrive as env vars set by deploy_igate.sh; nothing
# here is meant to be edited by hand — see igate.conf instead.
set -euo pipefail

: "${RIG_MODEL:?RIG_MODEL not set}"
: "${CAT_DEVICE:?CAT_DEVICE not set}"
: "${CAT_BAUD:?CAT_BAUD not set}"
: "${PTT_DEVICE:?PTT_DEVICE not set}"
: "${PTT_TYPE:?PTT_TYPE not set}"

rigctld \
  -m "$RIG_MODEL" \
  -r "$CAT_DEVICE" \
  -s "$CAT_BAUD" \
  -p "$PTT_DEVICE" \
  -P "$PTT_TYPE" \
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
