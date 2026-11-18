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
#   2. Appends one row to a summary TSV. Three groups of columns:
#
#      rx_*   exact for the named UTC day, from Direwolf's own dated log.
#      day_*  exact for the named UTC day, from the console log's -T
#             timestamps, which this station has written since 2026-11-08.
#             Computed by igate_counts.py. `day_partial` says whether the log
#             actually covered the whole day; a reboot clears the tmpfs it
#             lives in, so it often does not.
#      win_*  a ROLLING-WINDOW snapshot over whatever the console log still
#             holds, about eight days. Kept because months of history are in
#             this file and the series must not break, but superseded by the
#             day_* columns for anything per-day. Difference consecutive rows
#             for a rate.
#
#      The win_* columns were the only transmit figures until 2026-11-17 and
#      the web monitor displayed them in a table keyed by date, which read
#      about eight times high at BEACON_EVERY = 2:00:00. Do not reintroduce
#      that: a rolling number does not belong in a dated row.
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
# Days in the rolling station-union window, the day itself included. Unique
# stations over a week is a UNION and not the sum of seven daily counts, so it
# has to be computed over the window rather than added up afterwards.
SUMMARY_WINDOW_DAYS="$(conf_get SUMMARY_WINDOW_DAYS 7)"

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
  # `[^]]*` absorbs direwolf's -T timestamp, which sits INSIDE the bracket:
  # "[0L]" became "[0L 2026-11-14T13:49:16-0500]" on 2026-11-08 and these four
  # counters silently read zero afterwards. Found 2026-11-14 by mixing both
  # bracket shapes into test_archive.sh's fixtures, which is the only reason
  # the suite caught it.
  beacons="$(printf '%s\n' "$all" | grep -acE "^\[0L[^]]*\] ${MYCALL}>[^:]*:!" || true)"
  gated="$(printf '%s\n' "$all"  | grep -acE "^\[0L[^]]*\] ${MYCALL}>[^:]*:\}" || true)"
  brep="$(printf '%s\n' "$all"   | grep -acE "^\[0\.[0-9]+[^]]*\] ${MYCALL}>[^:]*\*[^:]*:!" || true)"
  grep_="$(printf '%s\n' "$all"  | grep -acE "^\[0\.[0-9]+[^]]*\] ${MYCALL}>[^:]*\*[^:]*:\}" || true)"
  printf '%s' "${frames:-0} ${beacons:-0} ${brep:-0} ${gated:-0} ${grep_:-0}"
}

# Exact per-day counts from the console log, and the station-set arithmetic
# over the dated CSVs. Both come from igate_counts.py so that the archiver and
# the web monitor cannot disagree about what a line means.
#
# A missing or unrunnable helper must not take the whole row with it: the rx_*
# columns are the irreplaceable ones and they do not depend on it. Empty
# fields are what a reader of the TSV already handles for the days that
# predate these columns.
COUNTS="${SCRIPT_DIR}/igate_counts.py"

# Invoked as `python3 igate_counts.py`, never as `./igate_counts.py`. The
# exec bit is one more thing that can be lost -- by a copy, by a noexec
# mount, by a checkout on a filesystem that does not carry it -- and losing
# it would silently empty the day_* columns rather than fail loudly.
helper_ok() {
  [[ -f "$COUNTS" ]] && command -v python3 >/dev/null
}

# Both of these print nothing at all when they cannot compute, rather than a
# guessed number of empty fields. The row is then squared up against the
# file's own header in write_summary, which is the only place that knows how
# wide a row has to be. Computing the width in two places is how a header of
# eleven columns ended up over a row of twenty-five.
day_stats() {  # -> CONSOLE_FIELDS, tab-separated, or nothing
  helper_ok || return 0
  # Oldest chunk first, so the carried timestamp moves forward in time. An
  # unstamped igate line takes the stamp of the frame above it, which is the
  # frame that caused it; feeding the newest chunk first would attribute it to
  # a stamp from days later.
  local -a logs=()
  [[ -f "${BARE_LOG}.2.gz" ]] && logs+=("${BARE_LOG}.2.gz")
  [[ -f "${BARE_LOG}.1.gz" ]] && logs+=("${BARE_LOG}.1.gz")
  [[ -f "${BARE_LOG}.1"    ]] && logs+=("${BARE_LOG}.1")
  [[ -f "$BARE_LOG"        ]] && logs+=("$BARE_LOG")
  (( ${#logs[@]} == 0 )) && return 0
  python3 "$COUNTS" --day "$DAY" --mycall "$MYCALL" "${logs[@]}" 2>/dev/null || true
}

station_stats() {  # -> STATION_FIELDS, tab-separated, or nothing
  helper_ok || return 0
  # The window is the day itself plus the SUMMARY_WINDOW_DAYS - 1 days before
  # it. The dated logs are pruned at ARCHIVE_KEEP_DAYS, 400 here, so a
  # seven-day window is always present once the station has run a week. Days
  # with no log are simply absent from the list rather than counted as zero.
  local -a csvs=(); local d i
  for (( i = 0; i < SUMMARY_WINDOW_DAYS; i++ )); do
    d="$(date -u -d "${DAY} -${i} day" +%Y-%m-%d 2>/dev/null)" || continue
    [[ -f "${LOG_DIR}/${d}.log" ]] && csvs+=("${LOG_DIR}/${d}.log")
  done
  (( ${#csvs[@]} == 0 )) && return 0
  python3 "$COUNTS" --mode stations --day "$DAY" --mycall "$MYCALL" \
    "${csvs[@]}" 2>/dev/null || true
}

summary_comments() {
  printf '# rx_*  columns are EXACT for the named UTC day, from Direwolf LOGDIR.\n'
  printf '# day_* columns are EXACT for the named UTC day, from the console log'"'"'s -T\n'
  printf '#       timestamps; day_partial = 1 means the log did not span the day.\n'
  printf '# win_* columns are a ROLLING WINDOW over whatever the console log still\n'
  printf '#       holds, about 8 days. NOT a daily figure. Difference rows for a rate.\n'
  printf '# rx_stations_* exclude MYCALL; rx_stations includes it. See igate_counts.py.\n'
}

# The full header this version writes. Appended to, never reordered: every
# column that existed before keeps its index, because test_archive.sh and
# anything else reading this file addresses columns by number.
summary_header() {
  local day_cols station_cols
  day_cols="$(python3 "$COUNTS" --fields 2>/dev/null || true)"
  station_cols="$(python3 "$COUNTS" --mode stations --fields 2>/dev/null || true)"
  printf 'date\trx_packets\trx_direct\trx_stations\trx_stations_direct\twin_frames\twin_beacons\twin_beacon_repeats\twin_gated\twin_gated_repeats\tchunks_saved'
  [[ -n "$day_cols"     ]] && printf '\t%s' "$day_cols"
  [[ -n "$station_cols" ]] && printf '\t%s' "$station_cols"
  printf '\n'
}

# Bring an older file up to the current header.
#
# Without this the file would hold a short header over long rows and every
# consumer that maps a name to a column index would read the wrong column --
# which is worse than having no new columns at all. Old rows are left short on
# purpose: a reader already has to handle a missing field for the days that
# predate the column, and padding them with zeros would assert that the
# traffic was measured and was nil.
migrate_header() {
  [[ -f "$SUMMARY" ]] || return 0
  local want have
  want="$(summary_header)"
  have="$(grep -m1 '^date	' "$SUMMARY" 2>/dev/null || true)"
  [[ -z "$have" || "$have" == "$want" ]] && return 0
  # Only ever widen, and only when the existing header is a prefix of this
  # one. Anything else is a file declaring columns this version does not
  # write -- a newer archiver wrote it, or the counting helper is missing
  # today and was present when the header was made. Rewriting it either way
  # would throw away columns that have data under them.
  case "$want" in
    "$have"*) ;;
    *) printf 'igate-archive: summary declares columns this run cannot write; header left alone\n' >&2
       return 0 ;;
  esac
  # Comments, then the header, then the rows, in that order. The old comment
  # block is replaced rather than kept: it said the console log has no
  # per-line timestamps, which stopped being true on 2026-11-08, and a stale
  # caveat at the top of a data file is worse than none.
  local tmp="${SUMMARY}.migrate"
  {
    summary_comments
    printf '%s\n' "$want"
    grep -v -e '^#' -e '^date	' "$SUMMARY" || true
  } > "$tmp" && mv -- "$tmp" "$SUMMARY"
  printf 'igate-archive: summary header widened to %d columns\n' \
    "$(awk -F'\t' '{print NF; exit}' <<<"$want")"
}

write_summary() {
  local saved="$1"
  if [[ ! -f "$SUMMARY" ]]; then
    { summary_comments; summary_header; } > "$SUMMARY"
  else
    migrate_header
  fi
  # Idempotent: a second run on the same day replaces that row rather than
  # appending a duplicate, so a manual run and the timer cannot both land.
  if grep -q "^${DAY}	" "$SUMMARY" 2>/dev/null; then
    grep -v "^${DAY}	" "$SUMMARY" > "${SUMMARY}.tmp" && mv -- "${SUMMARY}.tmp" "$SUMMARY"
  fi

  # rx_stats and tx_stats print space-separated; the two helpers print tabs
  # already, and print nothing when they could not run.
  local row extra
  row="$(printf '%s\t%s\t%s\t%s' "$DAY" "$(rx_stats)" "$(tx_stats)" "$saved" \
         | tr ' ' '\t')"
  extra="$(day_stats)";     [[ -n "$extra" ]] && row+=$'\t'"$extra"
  extra="$(station_stats)"; [[ -n "$extra" ]] && row+=$'\t'"$extra"

  # Square the row against the FILE's header, not against this version's.
  # Short rows are padded with empty fields so every row has the same width;
  # a row wider than the header is truncated, which can only happen if a
  # newer archiver wrote the header and migrate_header declined to touch it.
  # Empty is deliberate and means "not measured" -- a zero would claim the
  # traffic was counted and was nil.
  local width
  width="$(awk -F'\t' '/^date\t/{print NF; exit}' "$SUMMARY")"
  if [[ -n "$width" ]]; then
    printf '%s\n' "$row" | awk -F'\t' -v n="$width" 'BEGIN { OFS = "\t" }
      { if (NF > n) NF = n; for (i = NF + 1; i <= n; i++) $i = ""; print }' \
      >> "$SUMMARY"
  else
    printf '%s\n' "$row" >> "$SUMMARY"
  fi
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
