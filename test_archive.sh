#!/usr/bin/env bash
#
# Tests for igate-archive.sh and the LOG_DIR rendering.
#
# There is no test suite for deploy_igate.sh — 3,200 lines of bash and
# `bash -n` is the whole safety net. This file exists because the archiver
# runs unattended for a month and a silent failure is not discovered until the
# data is wanted, which is the one time it cannot be recovered.
#
# Everything here runs locally against synthetic fixtures and, where the real
# archive is present, against the 2026-10-30 capture — 378,997 real frames, a
# corpus no fixture can imitate. Nothing here needs a Pi, a radio or a network.
#
#   ./test_archive.sh
#
# Prints one line per check and exits non-zero on the first failure.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n         got: %s\n' "$1" "${2:-}"; }
is()   { [[ "$2" == "$3" ]] && ok "$1" || bad "$1" "'$2' != '$3'"; }
has()  { [[ "$2" == *"$3"* ]] && ok "$1" || bad "$1" "missing '$3'"; }
yes_() { [[ -n "$1" ]] && ok "$2" || bad "$2" "empty"; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# A fake station directory: the archiver resolves everything relative to its
# own location, so a copy of the script into a scratch tree is a whole station.
mkstation() {
  local d="$1"
  mkdir -p "$d/run" "$d/logs"
  cp "$HERE/igate-archive.sh" "$d/"
  cat > "$d/igate.conf" <<EOF
MYCALL = KD3CCO-10
LOG_DIR = $d/logs
ARCHIVE_DIR = $d/archive
ARCHIVE_KEEP_DAYS = 400
EOF
}

# A Direwolf LOGDIR CSV. `heard` equal to `source` is a direct copy; different
# is a copy that arrived through somebody's digipeater.
mklog() {  # file  then rows of "source,heard"
  local f="$1"; shift
  echo 'chan,utime,isotime,source,heard,level,error,dti,name,symbol,latitude,longitude,speed,course,altitude,frequency,offset,tone,system,status,telemetry,comment' > "$f"
  local row
  for row in "$@"; do
    local src="${row%%,*}" hrd="${row##*,}"
    echo "0,1700000000,2026-11-01T00:00:00Z,${src},${hrd},40,0,!,,,,,,,,,,,,,," >> "$f"
  done
}

echo "the summary row counts direct and via-digipeater separately"
S="$WORK/a"; mkstation "$S"
mklog "$S/logs/2026-11-01.log" \
  "WO3T-1,WO3T-1" "WO3T-1,WO3T-1" "W3YA-1,W3YA-1" \
  "K3TOW-13,K3DNA" "N8GD-1,WO3T-1" "KB3EWY-9,KB3EWY-9"
IGATE_ARCHIVE_DAY=2026-11-01 "$S/igate-archive.sh" "$S/igate.conf" >/dev/null
ROW="$(grep '^2026-11-01' "$S/archive/daily-summary.tsv")"
is "six packets counted"            "$(cut -f2 <<<"$ROW")" "6"
is "four of them heard direct"      "$(cut -f3 <<<"$ROW")" "4"
is "five distinct stations"         "$(cut -f4 <<<"$ROW")" "5"
is "three heard direct"             "$(cut -f5 <<<"$ROW")" "3"

echo
echo "an uncertain heard-station still counts as direct"
# Direwolf writes `WO3T-1?` when it is not sure which station it copied.
# Seen within a minute of real logging starting on 2026-10-30. Comparing the
# raw strings counts every uncertain DIRECT copy as digipeated.
Q="$WORK/q"; mkstation "$Q"
mklog "$Q/logs/2026-11-03.log" \
  "K3DNA,K3DNA?" "W3YA-1,W3YA-1" "NJ3T-4,WO3T-1?" "WO3T-1,WO3T-1?"
IGATE_ARCHIVE_DAY=2026-11-03 "$Q/igate-archive.sh" "$Q/igate.conf" >/dev/null
QROW="$(grep '^2026-11-03' "$Q/archive/daily-summary.tsv")"
is "four packets"                        "$(cut -f2 <<<"$QROW")" "4"
is "three direct despite the question marks" "$(cut -f3 <<<"$QROW")" "3"
is "and three distinct direct stations"  "$(cut -f5 <<<"$QROW")" "3"

echo
echo "a missing day is zero, not a crash or a gap"
IGATE_ARCHIVE_DAY=2026-11-02 "$S/igate-archive.sh" "$S/igate.conf" >/dev/null
is "the row still appears" "$(grep -c '^2026-11-02' "$S/archive/daily-summary.tsv")" "1"
is "with no packets"       "$(grep '^2026-11-02' "$S/archive/daily-summary.tsv" | cut -f2)" "0"

echo
echo "running twice on one day replaces the row rather than duplicating it"
IGATE_ARCHIVE_DAY=2026-11-01 "$S/igate-archive.sh" "$S/igate.conf" >/dev/null
is "still exactly one row for 11-01" \
   "$(grep -c '^2026-11-01' "$S/archive/daily-summary.tsv")" "1"
is "and the header survived" \
   "$(grep -c '^date	rx_packets' "$S/archive/daily-summary.tsv")" "1"

echo
echo "rotated chunks are stored once, by content, not by name"
T="$WORK/b"; mkstation "$T"
printf 'chunk one\n' | gzip > "$T/run/direwolf.log.1.gz"
printf 'chunk two\n' | gzip > "$T/run/direwolf.log.2.gz"
IGATE_ARCHIVE_DAY=2026-11-01 "$T/igate-archive.sh" "$T/igate.conf" >/dev/null
is "two distinct chunks saved" "$(ls "$T/archive/frames" | wc -l)" "2"
# The next day, .1 has become .2 under rotation. Same bytes, different name.
cp "$T/run/direwolf.log.1.gz" "$T/run/direwolf.log.2.gz"
IGATE_ARCHIVE_DAY=2026-11-02 "$T/igate-archive.sh" "$T/igate.conf" >/dev/null
is "the renamed chunk is not stored again" "$(ls "$T/archive/frames" | wc -l)" "2"
printf 'chunk three\n' | gzip > "$T/run/direwolf.log.1.gz"
IGATE_ARCHIVE_DAY=2026-11-03 "$T/igate-archive.sh" "$T/igate.conf" >/dev/null
is "but genuinely new content is"           "$(ls "$T/archive/frames" | wc -l)" "3"

echo
echo "the transmit columns count this station's own frames"
U="$WORK/c"; mkstation "$U"
{
  echo '[0L] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&RX iGate'
  echo '[0L] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&RX iGate'
  echo '[0.1] KD3CCO-10>APDW17,W3YA-1*:!4046.25NR07747.50W&RX iGate'
  echo '[0L] KD3CCO-10>APDW17,WIDE1-1,WIDE2-2:}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello'
  echo '[0.1] KD3CCO-10>APDW17,W3TM-10*,WIDE2-1:}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello'
  echo '[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!4029.80NS07807.92W#somebody else'
} > "$U/run/direwolf.log"
IGATE_ARCHIVE_DAY=2026-11-01 "$U/igate-archive.sh" "$U/igate.conf" >/dev/null
ROW="$(grep '^2026-11-01' "$U/archive/daily-summary.tsv")"
is "two beacons sent"              "$(cut -f7 <<<"$ROW")" "2"
is "one beacon repeat heard"       "$(cut -f8 <<<"$ROW")" "1"
is "one gated message sent"        "$(cut -f9 <<<"$ROW")" "1"
is "one gated repeat heard"        "$(cut -f10 <<<"$ROW")" "1"

echo
echo "a third-party payload does not count the gateway as its own digipeater"
# `}...TCPIP,KD3CCO-10*::` contains a used hop AFTER the colon. Matching the
# whole line instead of the path is the bug this guards: it turned the
# station into its own repeater in an earlier version of `reach`.
V="$WORK/d"; mkstation "$V"
echo '[0L] KD3CCO-10>APDW17,WIDE1-1,WIDE2-2:}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello' > "$V/run/direwolf.log"
IGATE_ARCHIVE_DAY=2026-11-01 "$V/igate-archive.sh" "$V/igate.conf" >/dev/null
ROW="$(grep '^2026-11-01' "$V/archive/daily-summary.tsv")"
is "the sent frame is counted once" "$(cut -f9 <<<"$ROW")"  "1"
is "and no repeat is invented"      "$(cut -f10 <<<"$ROW")" "0"

echo
echo "LOG_DIR becomes a command-line flag, not a config directive"
# Direwolf 1.7 ignores unknown CONFIG keywords silently -- proved on the
# station 2026-10-30 with `NOTAREALSETTING 42`, which drew no complaint. So a
# `LOGDIR` line in the rendered conf would be indistinguishable from one being
# thrown away. `-l` is documented in `direwolf -h` and is what gets passed.
FLAG_ON="$(cd "$HERE" && bash -c '
  declare -A CFG=([LOG_DIR]="/tmp/igate-logs"); HOME=/tmp
  source <(sed -n "/^build_logdir_flag()/,/^}/p" deploy_igate.sh)
  mapfile -t f < <(build_logdir_flag); printf "%s " "${f[@]}"' 2>/dev/null)"
is "the flag and its directory are two separate argv entries" \
   "$FLAG_ON" "-l /tmp/igate-logs "
FLAG_OFF="$(cd "$HERE" && bash -c '
  declare -A CFG=([LOG_DIR]=""); HOME=/tmp
  source <(sed -n "/^build_logdir_flag()/,/^}/p" deploy_igate.sh)
  mapfile -t f < <(build_logdir_flag); printf "%s" "${f[@]}"' 2>/dev/null)"
is "and an unset LOG_DIR contributes no argument at all" "$FLAG_OFF" ""
# The rendered conf must NOT carry a LOGDIR line any more.
CONF="$(cd "$HERE" && grep -c 'LOGDIR' deploy_igate.sh || true)"
is "no LOGDIR directive is rendered into the conf" \
   "$(cd "$HERE" && grep -cE '^LOGDIR|build_logdir\b' deploy_igate.sh || true)" "0"

echo
echo "the scripts parse"
bash -n "$HERE/igate-archive.sh" && ok "igate-archive.sh" || bad "igate-archive.sh"
bash -n "$HERE/deploy_igate.sh"  && ok "deploy_igate.sh"  || bad "deploy_igate.sh"

# ---------------------------------------------------------------------------
# Against the real capture, when it is present. Fixtures cannot imitate raw
# bytes in a packet, which is what made GNU grep treat the whole log as binary
# and print nothing on 2026-10-30.
# ---------------------------------------------------------------------------
REAL="$HERE/archive-2026-10/frames-15ft-2026-10-30.log.gz"
if [[ -f "$REAL" ]]; then
  echo
  echo "against the real 2026-10-30 capture"
  W="$WORK/e"; mkstation "$W"
  zcat "$REAL" > "$W/run/direwolf.log"
  IGATE_ARCHIVE_DAY=2026-10-30 "$W/igate-archive.sh" "$W/igate.conf" >/dev/null
  ROW="$(grep '^2026-10-30' "$W/archive/daily-summary.tsv")"
  # `reach` reported 394 beacons and 50 gated messages over this window.
  is "beacons match what reach counted"        "$(cut -f7 <<<"$ROW")" "394"
  is "gated messages match what reach counted" "$(cut -f9 <<<"$ROW")" "50"
  yes_ "$(cut -f6 <<<"$ROW")" "the window frame count is not empty (binary-safe grep)"
  # The same definition `reach` uses -- `^\[0`, frames that touched the radio
  # -- so the column can be compared against a `reach` run directly.
  is "the frame count matches reach exactly" "$(cut -f6 <<<"$ROW")" "30247"
else
  echo
  echo "  SKIP  real-capture checks (archive-2026-10/ not present)"
fi

echo
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]] && echo "ALL PASS"
exit $(( FAIL > 0 ? 1 : 0 ))
