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
  # igate_counts.py too: the archiver asks it for the day_* and rx_stations_*
  # columns, and without it those fields come out EMPTY rather than wrong.
  # A station missing the helper is a case worth having covered -- see
  # "the row survives a missing helper" below -- so it is copied here and
  # removed there, rather than being absent everywhere by accident.
  cp "$HERE/igate-archive.sh" "$HERE/igate_counts.py" "$d/"
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
  # Deliberately a MIX of the two bracket formats. Direwolf's -T flag puts a
  # timestamp inside the bracket, and on 2026-11-08 that broke twelve parsing
  # sites for six days while every test passed, because every fixture here
  # used the old shape. Both have to work: the archive holds logs from either
  # side of the change.
  echo '[0L] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&RX iGate'
  echo '[0L 2026-11-14T13:49:16-0500] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&RX iGate'
  echo '[0.1 2026-11-14T13:49:16-0500] KD3CCO-10>APDW17,W3YA-1*:!4046.25NR07747.50W&RX iGate'
  echo '[0L] KD3CCO-10>APDW17,WIDE1-1,WIDE2-2:}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello'
  echo '[0.1 2026-11-14T13:49:16-0500] KD3CCO-10>APDW17,W3TM-10*,WIDE2-1:}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hello'
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
echo "the day_* columns are exact for the UTC day, unlike the win_* ones"
# The distinction the whole feature rests on. One console log, two column
# groups: win_* counts everything in the file, day_* counts only 2026-11-16.
# 19:30 Eastern on the 16th is 00:30 UTC on the 17th and belongs to the 17th.
DAYS="$WORK/f"; mkstation "$DAYS"
mklog "$DAYS/logs/2026-11-16.log" "WO3T-1,WO3T-1" "W3YA-1,W3YA-1" "KD3CCO-10,W3YA-1"
mklog "$DAYS/logs/2026-11-15.log" "WO3T-1,WO3T-1" "N3DXC,N3DXC"
{
  echo '[0.3 2026-11-15T18:50:00-0500] WO3T-1>APNU3B,WIDE1-1*:!4029.80NS07807.92W#'
  echo '[0.3 2026-11-16T09:00:00-0500] WO3T-1>APNU3B,WIDE1-1*:!4029.80NS07807.92W#'
  echo '[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!4029.80NS07807.92W#'
  echo '[rx>ig] #'
  echo '[ig>tx] N8GD-1>APRS,TCPIP*,qAC,T2NALA:!4100.00N/08000.00W#'
  echo '[0L 2026-11-16T09:30:00-0500] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&'
  echo '[0L 2026-11-16T10:00:00-0500] KD3CCO-10>APDW17,WIDE1-1:}KD3CCO-1>APRS,TCPIP,KD3CCO-10*::KD3CCO-7 :hi'
  echo '[0.1 2026-11-16T10:00:05-0500] KD3CCO-10>APDW17,W3YA-1*:!4046.25NR07747.50W&'
  echo '[0.3 2026-11-16T19:30:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x'
} > "$DAYS/run/direwolf.log"
IGATE_ARCHIVE_DAY=2026-11-16 "$DAYS/igate-archive.sh" "$DAYS/igate.conf" >/dev/null
SUM="$DAYS/archive/daily-summary.tsv"
# Read by NAME, which is how the monitor reads it and the only safe way once
# the header can widen.
val() { python3 - "$SUM" "$1" "$2" <<'PY'
import sys
rows=[l.rstrip("\n").split("\t") for l in open(sys.argv[1]) if not l.startswith("#")]
hdr=rows[0]
for r in rows[1:]:
    if r[0]==sys.argv[2]:
        d=dict(zip(hdr,r)); print(d.get(sys.argv[3],"<absent>")); break
else: print("<no row>")
PY
}
is "win_frames counts the whole log"      "$(val 2026-11-16 win_frames)" "6"
is "day_frames counts only that UTC day"  "$(val 2026-11-16 day_frames)" "4"
is "and matches rx plus tx for the day"   "$(val 2026-11-16 day_rx_frames)/$(val 2026-11-16 day_tx_frames)" "2/2"
is "one beacon that day"                  "$(val 2026-11-16 day_beacons)" "1"
is "one gated UP to APRS-IS"              "$(val 2026-11-16 day_gated_up)" "1"
is "the keepalive is not gated traffic"   "$(val 2026-11-16 day_is_offered)" "1"
is "one gated DOWN to RF"                 "$(val 2026-11-16 day_gated_down)" "1"
is "nothing undated"                      "$(val 2026-11-16 day_undated)" "0"
is "the log spanned the day"              "$(val 2026-11-16 day_partial)" "0"
is "own beacon returns are broken out"    "$(val 2026-11-16 rx_own)" "1"
# 11-16 has WO3T-1, W3YA-1, KD3CCO-10; 11-15 has WO3T-1, N3DXC. Excluding
# MYCALL the union is WO3T-1, W3YA-1, N3DXC = 3, while the daily counts sum
# to 5. Summing them is the error this column exists to prevent.
is "the station union is a union, not a sum" "$(val 2026-11-16 rx_stations_win)" "3"
is "rx_stations still includes MYCALL, unchanged" "$(val 2026-11-16 rx_stations)" "3"

echo
echo "a log that does not span the day says so"
# The reboot case. run/ is a tmpfs, so a reboot starts the console log from
# empty and that day's day_* columns are an undercount -- while the rx_*
# columns, which come from the card, are intact for the same day. A row that
# cannot tell those apart is unusable.
PART="$WORK/g"; mkstation "$PART"
mklog "$PART/logs/2026-11-16.log" "WO3T-1,WO3T-1"
{
  echo '[0.3 2026-11-16T08:00:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x'
  echo '[0.3 2026-11-16T19:00:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x'
} > "$PART/run/direwolf.log"
IGATE_ARCHIVE_DAY=2026-11-16 "$PART/igate-archive.sh" "$PART/igate.conf" >/dev/null
SUM="$PART/archive/daily-summary.tsv"
is "partial is set when the log starts mid-day" "$(val 2026-11-16 day_partial)" "1"
is "but the receive column is still exact"      "$(val 2026-11-16 rx_packets)"  "1"

echo
echo "a pre-2026-11-08 log reports undated rather than zero"
# Logs from before -T carry no stamps at all. Crediting their whole contents
# to one day would be wrong by about eight times; reporting zero would read as
# a quiet day. Neither: undated, and partial.
OLD="$WORK/h"; mkstation "$OLD"
mklog "$OLD/logs/2026-11-16.log" "WO3T-1,WO3T-1"
{
  echo '[0L] KD3CCO-10>APDW17,WIDE1-1:!4046.25NR07747.50W&'
  echo '[0.3] WO3T-1>APNU3B,WIDE1-1*:!x'
  echo '[rx>ig] WO3T-1>APNU3B,qAR,KD3CCO-10:!x'
} > "$OLD/run/direwolf.log"
IGATE_ARCHIVE_DAY=2026-11-16 "$OLD/igate-archive.sh" "$OLD/igate.conf" >/dev/null
SUM="$OLD/archive/daily-summary.tsv"
is "three unstamped lines are undated" "$(val 2026-11-16 day_undated)" "3"
is "none of them is credited to the day" "$(val 2026-11-16 day_frames)" "0"
is "and the row is flagged partial"      "$(val 2026-11-16 day_partial)" "1"
is "while win_* still counts them"       "$(val 2026-11-16 win_frames)" "2"

echo
echo "an older summary file is widened, not corrupted"
MIG="$WORK/i"; mkstation "$MIG"
mklog "$MIG/logs/2026-11-16.log" "WO3T-1,WO3T-1"
{
  echo '[0.3 2026-11-15T18:50:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x'
  echo '[0L 2026-11-16T09:30:00-0500] KD3CCO-10>APDW17,WIDE1-1:!x'
  echo '[0.3 2026-11-16T19:30:00-0500] WO3T-1>APNU3B,WIDE1-1*:!x'
} > "$MIG/run/direwolf.log"
mkdir -p "$MIG/archive"
{
  printf '# Receive columns are EXACT for the named UTC day, from Direwolf LOGDIR.\n'
  printf '# Transmit columns are a ROLLING WINDOW over whatever the packet log still\n'
  printf '# holds (about 8 days) -- the console log has no per-line timestamps.\n'
  printf 'date\trx_packets\trx_direct\trx_stations\trx_stations_direct\twin_frames\twin_beacons\twin_beacon_repeats\twin_gated\twin_gated_repeats\tchunks_saved\n'
  printf '2026-11-10\t9047\t2100\t310\t14\t30247\t394\t160\t50\t2\t1\n'
} > "$MIG/archive/daily-summary.tsv"
IGATE_ARCHIVE_DAY=2026-11-16 "$MIG/igate-archive.sh" "$MIG/igate.conf" >/dev/null
SUM="$MIG/archive/daily-summary.tsv"
is "exactly one header line remains"   "$(grep -c '^date	' "$SUM")" "1"
is "the old row's own columns survive" "$(val 2026-11-10 rx_packets)" "9047"
is "and its win_beacons is untouched"  "$(val 2026-11-10 win_beacons)" "394"
# Left SHORT on purpose: an empty field says "not measured", a zero would
# claim the traffic was counted and was nil.
is "the old row has no day_* values invented for it" \
   "$(val 2026-11-10 day_beacons)" "<absent>"
is "the new row does have them"        "$(val 2026-11-16 day_beacons)" "1"
is "the stale no-timestamps caveat is gone" \
   "$(grep -c 'no per-line timestamps' "$SUM")" "0"
# Running again must not widen a second time or duplicate anything.
IGATE_ARCHIVE_DAY=2026-11-16 "$MIG/igate-archive.sh" "$MIG/igate.conf" >/dev/null
is "a second run leaves one header"   "$(grep -c '^date	' "$SUM")" "1"
is "and one row for the day"          "$(grep -c '^2026-11-16	' "$SUM")" "1"

echo
echo "the row survives a missing helper"
# python3 or igate_counts.py absent must not cost the rx_* columns, which are
# the ones that cannot be recomputed later from anything else.
NOH="$WORK/j"; mkstation "$NOH"
rm -f "$NOH/igate_counts.py"
mklog "$NOH/logs/2026-11-16.log" "WO3T-1,WO3T-1" "W3YA-1,W3YA-1"
echo '[0L] KD3CCO-10>APDW17,WIDE1-1:!x' > "$NOH/run/direwolf.log"
IGATE_ARCHIVE_DAY=2026-11-16 "$NOH/igate-archive.sh" "$NOH/igate.conf" >/dev/null
SUM="$NOH/archive/daily-summary.tsv"
is "the receive columns are still written" "$(val 2026-11-16 rx_packets)" "2"
is "the win_ columns are still written"    "$(val 2026-11-16 win_beacons)" "1"
# On a station that never had the helper the columns are not declared at all,
# which is honest: the file does not promise a figure it cannot produce.
is "the day_ columns are not declared"     "$(val 2026-11-16 day_frames)" "<absent>"
is "and the file is self-consistent" \
   "$(awk -F'\t' '/^2026-11-16\t/{print NF}' "$SUM")" \
   "$(awk -F'\t' '/^date\t/{print NF}' "$SUM")"

echo
echo "a helper that disappears after the header is wide leaves empty fields"
# The order that matters on a real station: the columns exist from earlier
# runs, and one night python3 is missing or the file is not executable. The
# row must stay the declared width with those fields EMPTY. A zero there
# would read as a measured quiet day and would be averaged in as one.
GON="$WORK/k"; mkstation "$GON"
mklog "$GON/logs/2026-11-16.log" "WO3T-1,WO3T-1" "W3YA-1,W3YA-1"
echo '[0L 2026-11-16T09:30:00-0500] KD3CCO-10>APDW17,WIDE1-1:!x' > "$GON/run/direwolf.log"
IGATE_ARCHIVE_DAY=2026-11-16 "$GON/igate-archive.sh" "$GON/igate.conf" >/dev/null
SUM="$GON/archive/daily-summary.tsv"
is "the first run fills them"  "$(val 2026-11-16 day_beacons)" "1"
rm -f "$GON/igate_counts.py"
IGATE_ARCHIVE_DAY=2026-11-17 "$GON/igate-archive.sh" "$GON/igate.conf" >/dev/null
is "the next row is still the declared width" \
   "$(awk -F'\t' '/^2026-11-17\t/{print NF}' "$SUM")" \
   "$(awk -F'\t' '/^date\t/{print NF}' "$SUM")"
is "with the day_ field empty rather than zero" \
   "$(val 2026-11-17 day_beacons)" ""
is "and the header not narrowed behind it" \
   "$(val 2026-11-16 day_beacons)" "1"

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
