#!/usr/bin/env bash
#
# Daily log archive and summary for the APRS iGate.
#
# `run/` is a tmpfs and the packet log rotates at 8M with two generations, so
# the readable history is about eight days at this station's traffic however
# long the station has been up — measured 2026-10-30, when `uptime -s` said
# 10-12 and the log reached back only to 10-22. Anything asked of the log
# later than that is asked of data that no longer exists.
#
# This runs daily and does three things:
#
#   1. Copies each ROTATED generation of the packet log to the card, named by
#      content hash so the same chunk is never stored twice. The live log is
#      left alone: the tmpfs design is about not writing to the card
#      continuously, and that reason is unchanged by the UPS.
#
#   2. Appends one row to a summary TSV. The receive columns are exact for the
#      named day, taken from Direwolf's own dated log (LOGDIR). The transmit
#      columns are a ROLLING-WINDOW snapshot of whatever the packet log still
#      holds, because the console log carries no per-line timestamp and cannot
#      be sliced by date. Difference consecutive rows for a rate.
#
#   3. Prunes both to ARCHIVE_KEEP_DAYS.
#
# Nothing here transmits and nothing restarts the gateway.
#
# Usage: ./igate-archive.sh [config-file]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${1:-${SCRIPT_DIR}/igate.conf}"

# Read one setting out of the config files without sourcing them. Deliberately
# not `deploy_igate.sh`'s loader: this runs from a timer, must not depend on
# the radio being resolvable, and wants three keys rather than forty.
conf_get() {  # key default
  local key="$1" def="${2:-}" out=""
  local f
  for f in "$CONF" "${SCRIPT_DIR}/igate.local.conf"; do
    [[ -f "$f" ]] || continue
    local v
    v="$(sed -E -n "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*(.*)$/\1/p" "$f" \
         | sed -E 's/[[:space:]]*#.*$//; s/[[:space:]]+$//' | tail -1)"
    [[ -n "$v" ]] && out="$v"
  done
  printf '%s' "${out:-$def}"
}

RUN_DIR="${SCRIPT_DIR}/run"
BARE_LOG="${RUN_DIR}/direwolf.log"
LOG_DIR="$(conf_get LOG_DIR "${SCRIPT_DIR}/logs")"
ARCHIVE_DIR="$(conf_get ARCHIVE_DIR "${SCRIPT_DIR}/archive")"
KEEP_DAYS="$(conf_get ARCHIVE_KEEP_DAYS 400)"
MYCALL="$(conf_get MYCALL KD3CCO-10)"

# Expand a leading ~ the way a shell would, then resolve a RELATIVE path
# against the install directory. Relative is the only form that works on both
# sides of the Pi's `~/aprs-igate` -> `/opt/aprs-igate` symlink: the station
# resolves through /home and igate-web.service runs from /opt under
# ProtectHome=yes, where /home is not visible. Found 2026-10-30.
LOG_DIR="${LOG_DIR/#\~/$HOME}"
ARCHIVE_DIR="${ARCHIVE_DIR/#\~/$HOME}"
[[ "$LOG_DIR"     != /* ]] && LOG_DIR="${SCRIPT_DIR}/${LOG_DIR}"
[[ "$ARCHIVE_DIR" != /* ]] && ARCHIVE_DIR="${SCRIPT_DIR}/${ARCHIVE_DIR}"

FRAMES_DIR="${ARCHIVE_DIR}/frames"
SUMMARY="${ARCHIVE_DIR}/daily-summary.tsv"
mkdir -p "$FRAMES_DIR"

# The day being summarized is YESTERDAY in UTC: a timer that fires early on the
# 1st is reporting the 31st, and a partial day in the series is worse than a
# missing one. Overridable for testing.
DAY="${IGATE_ARCHIVE_DAY:-$(date -u -d 'yesterday' +%Y-%m-%d)}"

# ---------------------------------------------------------------------------
# 1. The rotated generations, deduplicated by content.
# ---------------------------------------------------------------------------
#
# logrotate uses `copytruncate`, so .1 is a complete chunk the moment it
# appears and does not change afterwards. Hashing the content rather than
# trusting the name is what makes running this twice in a day, or after a
# reboot, cost nothing.
archive_rotations() {
  local saved=0 f h
  for f in "${BARE_LOG}.1.gz" "${BARE_LOG}.2.gz" "${BARE_LOG}.1"; do
    [[ -f "$f" ]] || continue
    h="$(md5sum "$f" | cut -c1-10)"
    # Any existing file carrying this hash means the chunk is already held,
    # whatever day it was first seen on.
    if compgen -G "${FRAMES_DIR}/frames-*-${h}.log.gz" >/dev/null 2>&1; then
      continue
    fi
    local dest="${FRAMES_DIR}/frames-${DAY}-${h}.log.gz"
    if [[ "$f" == *.gz ]]; then
      cp -- "$f" "$dest"
    else
      gzip -c -- "$f" > "$dest"
    fi
    saved=$((saved + 1))
  done
  printf '%s' "$saved"
}

# ---------------------------------------------------------------------------
# 2. The summary row.
# ---------------------------------------------------------------------------
#
# Receive, from Direwolf's dated log. `heard` is the station actually copied —
# the last USED digipeater, or the source itself on a direct copy — so
# `heard == source` is the direct test, computed by Direwolf rather than by
# parsing asterisks out of a path. That distinction is worth the whole feature:
# doing it by hand on 2026-10-30 got 6 stations with a too-strict pattern and
# 22 with the right one.
rx_stats() {  # -> "packets direct_packets stations direct_stations"
  local f="${LOG_DIR}/${DAY}.log"
  [[ -f "$f" ]] || { printf '%s' "0 0 0 0"; return; }
  awk -F, -v OFS=' ' '
    NR == 1 {                                   # header: find the columns
      for (i = 1; i <= NF; i++) { if ($i == "source") s = i; if ($i == "heard") h = i }
      next
    }
    !s || !h { next }
    {
      pkts++
      src = $s; hrd = $h
      # Direwolf marks an UNCERTAIN heard-station with a trailing `?` --
      # `WO3T-1?` -- seen in the first minutes of real logging on 2026-10-30.
      # Comparing the raw strings would count every direct copy it was unsure
      # about as a digipeated one, understating the number this whole exercise
      # exists to measure. Strip it from both sides.
      sub(/\?$/, "", src); sub(/\?$/, "", hrd)
      if (src == "") next
      seen[src] = 1
      if (hrd == src || hrd == "") { direct++; dseen[src] = 1 }
    }
    END {
      ns = 0; for (k in seen)  ns++
      nd = 0; for (k in dseen) nd++
      printf "%d %d %d %d", pkts + 0, direct + 0, ns, nd
    }' "$f"
}

# Transmit, from the console log. A ROLLING WINDOW, not a day: there are no
# per-line timestamps to slice on. `[0L]` is a frame this station put on the
# air; `:!` is its position beacon and `:}` a third-party frame gated down from
# APRS-IS. A repeat is this station's own callsign coming back with a USED hop
# — an asterisk BEFORE the first colon, because a third-party payload contains
# `TCPIP,MYCALL*` and a naive whole-line match counts the gateway as its own
# digipeater.
tx_stats() {  # -> "frames beacons beacon_repeats gated gated_repeats"
  local all
  all="$( { [[ -f "${BARE_LOG}.2.gz" ]] && zcat -- "${BARE_LOG}.2.gz"
            [[ -f "${BARE_LOG}.1.gz" ]] && zcat -- "${BARE_LOG}.1.gz"
            [[ -f "${BARE_LOG}.1"    ]] && cat  -- "${BARE_LOG}.1"
            [[ -f "$BARE_LOG"        ]] && cat  -- "$BARE_LOG"; } 2>/dev/null || true )"
  [[ -z "$all" ]] && { printf '%s' "0 0 0 0 0"; return; }
  # -a throughout: some packets carry raw bytes and GNU grep otherwise treats
  # the whole stream as binary and prints nothing. Found 2026-10-30.
  local frames beacons brep gated grep_
  # `^\[0` is what `reach` calls a frame: one that touched the RADIO, channel
  # 0, transmitted or received. It excludes `[rx>ig]` and `[ig>tx]`, which are
  # the APRS-IS side and say nothing about the air. Counting non-empty lines
  # instead gives 279,294 against reach's 30,247 on the same log, and the two
  # columns stop being comparable.
  frames="$(printf '%s\n' "$all" | grep -acE '^\[0' || true)"
  beacons="$(printf '%s\n' "$all" | grep -acE "^\[0L\] ${MYCALL}>[^:]*:!" || true)"
  gated="$(printf '%s\n' "$all"  | grep -acE "^\[0L\] ${MYCALL}>[^:]*:\}" || true)"
  brep="$(printf '%s\n' "$all"   | grep -acE "^\[0\.[0-9]+\] ${MYCALL}>[^:]*\*[^:]*:!" || true)"
  grep_="$(printf '%s\n' "$all"  | grep -acE "^\[0\.[0-9]+\] ${MYCALL}>[^:]*\*[^:]*:\}" || true)"
  printf '%s' "${frames:-0} ${beacons:-0} ${brep:-0} ${gated:-0} ${grep_:-0}"
}

write_summary() {
  local saved="$1"
  if [[ ! -f "$SUMMARY" ]]; then
    printf '# Receive columns are EXACT for the named UTC day, from Direwolf LOGDIR.\n' > "$SUMMARY"
    printf '# Transmit columns are a ROLLING WINDOW over whatever the packet log still\n' >> "$SUMMARY"
    printf '# holds (about 8 days) -- the console log has no per-line timestamps.\n' >> "$SUMMARY"
    printf '# Difference consecutive rows for a rate. See igate-archive.sh.\n' >> "$SUMMARY"
    printf 'date\trx_packets\trx_direct\trx_stations\trx_stations_direct\twin_frames\twin_beacons\twin_beacon_repeats\twin_gated\twin_gated_repeats\tchunks_saved\n' >> "$SUMMARY"
  fi
  # Idempotent: a second run on the same day replaces that row rather than
  # appending a duplicate, so a manual run and the timer cannot both land.
  if grep -q "^${DAY}	" "$SUMMARY" 2>/dev/null; then
    grep -v "^${DAY}	" "$SUMMARY" > "${SUMMARY}.tmp" && mv -- "${SUMMARY}.tmp" "$SUMMARY"
  fi
  printf '%s\t%s\t%s\t%s\n' "$DAY" "$(rx_stats)" "$(tx_stats)" "$saved" \
    | tr ' ' '\t' >> "$SUMMARY"
}

# ---------------------------------------------------------------------------
# 3. Prune.
# ---------------------------------------------------------------------------
prune() {
  find "$FRAMES_DIR" -name 'frames-*.log.gz' -type f -mtime "+${KEEP_DAYS}" -delete 2>/dev/null || true
  [[ -d "$LOG_DIR" ]] && \
    find "$LOG_DIR" -name '????-??-??.log' -type f -mtime "+${KEEP_DAYS}" -delete 2>/dev/null || true
  return 0
}

main() {
  local saved; saved="$(archive_rotations)"
  write_summary "$saved"
  prune
  printf 'igate-archive: day %s, %s new chunk(s), summary %s\n' \
    "$DAY" "$saved" "$SUMMARY"
}

main "$@"
