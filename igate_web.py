#!/usr/bin/env python3
"""Read-only LAN web monitor for the bidirectional APRS iGate.

Serves one page showing live packet flow and the resolved whitelist. Python
standard library only — no Flask, no pip, nothing to install on a 512 MB Pi.

Deliberately read-only. There is no POST handler and no code path that writes
igate.conf, restarts the service, or reads the raw Direwolf log. The whitelist is
the only thing between APRS-IS and the transmitter, so editing it stays on SSH
where there is authentication; see README.md.

Two design points worth keeping:

  * The packet annotation is NOT reimplemented here. This streams the output of
    "deploy_igate.sh monitor" as a subprocess, so there is one implementation of
    the [ig>tx]/[0L] pairing rather than two that can drift. That logic has been
    wrong twice already.
  * Streaming `monitor` rather than the log also inherits its passcode
    redaction. Serving run/direwolf.log would publish the APRS-IS passcode to
    everyone on the network.

  IGATE_WEB_BIND    interface to bind (default 0.0.0.0)
  IGATE_WEB_PORT    port (default 8080)
  IGATE_MONITOR_CMD override the monitor command, for testing
  IGATE_LIVENESS_CMD override the is-the-gateway-running check (exit 0 = running),
                    for testing
"""

import json
import os
import queue
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "web", "index.html")

BIND = os.environ.get("IGATE_WEB_BIND", "0.0.0.0")
PORT = int(os.environ.get("IGATE_WEB_PORT", "8080"))

# Bounded so a long-running page load cannot accumulate memory, and so a newly
# opened browser gets useful context instead of an empty screen.
HISTORY = 300
MAX_CLIENTS = 8
# Per-viewer buffer. Deep enough to ride out a browser stalling for a few
# seconds on a busy band, bounded so one stuck viewer cannot grow without limit.
QUEUE_DEPTH = int(os.environ.get("IGATE_QUEUE_DEPTH", "500"))
# When that buffer is full, how many of the viewer's oldest events to discard to
# make room. Falling behind should cost the middle of the feed, not the feed.
DROP_OLDEST = int(os.environ.get("IGATE_DROP_OLDEST", "50"))
# How long a write to a viewer may block before the connection is abandoned.
#
# Without this a viewer that goes away WITHOUT closing — a laptop that sleeps,
# a phone that backgrounds the tab, WiFi dropping — leaves a connection the
# server has no reason to think is dead. Keepalives go on being written into a
# socket that may not error for many minutes, or the write blocks forever
# because the peer's receive window never opens again. The slot stays taken,
# and after MAX_CLIENTS of those the page returns "too many viewers" to
# everyone until the service is restarted. That happened in service.
#
# Longer than the keepalive interval, so a healthy but briefly slow viewer is
# not cut off; short enough that a dead one is reclaimed the same day.
WRITE_TIMEOUT = float(os.environ.get("IGATE_WRITE_TIMEOUT", "30"))
# How often to write a comment frame on an idle connection, and how often the
# streaming loop wakes to look at anything other than the queue.
KEEPALIVE_SECONDS = 20.0
DROPPED_POLL_SECONDS = 5.0

# The packet log the gateway writes. Used only as a liveness signal for the
# monitor pipeline: if this file is growing while the pipeline has produced
# nothing for a long time, the pipeline is broken rather than the band quiet.
LOG_FILE = os.environ.get("IGATE_LOG_FILE", os.path.join(HERE, "run", "direwolf.log"))
# How long the pipeline may produce nothing, while the log is still growing,
# before it is treated as stalled and restarted.
STALL_SECONDS = int(os.environ.get("IGATE_STALL_SECONDS", "600"))

# The label-to-metric map lives in igate_counts.py so the page's live
# counters and the archiver's nightly columns cannot come to mean different
# things. A deployment missing the helper still serves the page; it just has
# no live counters, which is better than a second copy of the mapping here
# quietly drifting from the first.
try:
    sys.path.insert(0, HERE)
    from igate_counts import LABEL_METRIC
except Exception:                                    # pragma: no cover
    LABEL_METRIC = {}

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "17:23:49  RF RX      AA3BR>SYRV6V,...:`h@dl#GYY"
EVENT = re.compile(r"^(\d\d:\d\d:\d\d)\s\s(\S.*?)\s\s+(.*)$")
# Direwolf's decode of the frame above, indented by monitor_filter.
DETAIL = re.compile(r"^\s{6,}(\S.*)$")


def _log(message):
    """Say it where it survives.

    The journal is persistent on the Pi now, so anything written here outlives
    the reboot that might otherwise have been the only record. stderr, because
    systemd captures it and nothing here should depend on a page being open to
    be diagnosable.
    """
    print(f"igate-web: {message}", file=sys.stderr, flush=True)


class Subscriber:
    """One viewer's event queue, plus a flag saying it was cut off.

    Without the flag, a viewer that falls behind is dropped from the fan-out
    while its connection stays open: the handler blocks on an empty queue,
    keeps sending keepalives, and the browser sees a healthy stream that never
    delivers anything again. EventSource only reconnects on an error, so the
    page sits there reading "live" and frozen.
    """

    __slots__ = ("queue", "dropped")

    def __init__(self, maxsize):
        self.queue = queue.Queue(maxsize=maxsize)
        self.dropped = threading.Event()


class MonitorStream:
    """One `deploy_igate.sh monitor` subprocess, fanned out to every client.

    One subprocess regardless of how many browsers are open: five viewers should
    not mean five `tail -f | gawk` pipelines on a single-board computer.
    """

    # How many lines after a pipeline restart are checked against recently
    # published events. Larger than `docker logs --tail 30`, which is the
    # bigger of the two replay windows.
    REPLAY_GUARD = 40

    def __init__(self, cmd, is_running):
        self.cmd = cmd
        self.is_running = is_running
        self.history = deque(maxlen=HISTORY)
        # A list, not a set: admission evicts the OLDEST viewer when full, and
        # a set has no oldest.
        self.subscribers = []
        # Which pipeline the current stall escalation is about, and how many
        # attempts have been made on it.
        self._stall_proc = None
        self._stall_attempts = 0
        self._stall_restarts = 0
        # Last time round the loop, per thread. See _heartbeat.
        self._beats = {}
        self.lock = threading.Lock()
        self.proc = None
        # Monotonic timestamps for the stall check; see _supervise.
        self.last_line_at = time.monotonic()
        # Live counters, by the metric each monitor label belongs to. These
        # are the only numbers on the page that are not read from a file, and
        # they are honest about one thing only: traffic seen since this
        # process started following the log. They are NOT a day. See counts().
        self._counts = dict.fromkeys(set(LABEL_METRIC.values()), 0)
        self._counts_since = time.time()
        self._counted = 0
        # `tail -F` with no -n starts at the last 10 lines and `docker logs
        # -f --tail 30` at the last 30, so every pipeline restart re-reads
        # frames that have already been counted. Signatures of the most
        # recent events, consulted only in the first REPLAY_GUARD lines after
        # a restart, so a genuinely repeated beacon later in the day still
        # counts.
        self._recent = deque(maxlen=self.REPLAY_GUARD)
        self._recent_set = set()
        self._since_restart = None
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._supervise, daemon=True).start()

    def counts(self):
        """The live counters, with the one caveat they need, as data.

        `since` is a wall-clock time rather than a day on purpose. These reset
        when the service restarts and they begin at whatever `tail -F` was
        following, so they are a figure for "while this page's feed has been
        up" and nothing else. The exact per-day numbers come from the archive
        and are the ones to quote.
        """
        with self.lock:
            out = dict(self._counts)
            out["since"] = self._counts_since
            out["events"] = self._counted
            return out

    def _count(self, event):
        """Fold one event into the live counters. Caller holds the lock.

        Returns False if this event is a replayed line already counted, in
        which case it is not published either: a viewer should not see the
        same frame twice because the pipeline restarted.
        """
        metric = LABEL_METRIC.get(event.get("label", "").strip())
        sig = (event.get("time"), event.get("label"), event.get("text"))
        if self._since_restart is not None:
            if self._since_restart >= self.REPLAY_GUARD:
                self._since_restart = None
            else:
                self._since_restart += 1
                if sig in self._recent_set:
                    return False
        if len(self._recent) == self._recent.maxlen and self._recent:
            self._recent_set.discard(self._recent[0])
        self._recent.append(sig)
        self._recent_set.add(sig)
        if metric:
            self._counts[metric] = self._counts.get(metric, 0) + 1
            self._counted += 1
        return True

    def _publish(self, event):
        with self.lock:
            # A note identical to the last thing published tells a viewer
            # nothing new, and repeated it would push real packets out of the
            # bounded history.
            if event.get("kind") == "note" and self.history and self.history[-1] == event:
                return
            if event.get("kind") == "event" and not self._count(event):
                return
            self.history.append(event)
            dead = []
            for sub in self.subscribers:
                try:
                    sub.queue.put_nowait(event)
                    continue
                except queue.Full:
                    pass
                # Behind, but not necessarily hopeless. A monitor should show
                # the most recent traffic, so make room by discarding this
                # viewer's oldest events rather than its connection. On a busy
                # band a browser can fall behind briefly and catch up.
                try:
                    for _ in range(DROP_OLDEST):
                        sub.queue.get_nowait()
                    sub.queue.put_nowait(event)
                    continue
                except (queue.Empty, queue.Full):
                    pass
                # Still cannot take it: cut it loose, and say so, so the
                # handler closes the connection and the browser reconnects.
                sub.dropped.set()
                dead.append(sub)
            for sub in dead:
                if sub in self.subscribers:
                    self.subscribers.remove(sub)

    def _run(self):
        # Whether the gateway was running at the last check; None before the
        # first. The monitor is only started while it runs: with the gateway
        # stopped, `monitor` exits at once, and restarting it every few seconds
        # filled the page with "monitor ended" notes that never said why.
        gateway_up = None
        while True:
            try:
                running = bool(self.is_running())
            except Exception:  # a failed check must not kill the feed thread
                running = False
            if not running:
                if gateway_up is not False:
                    self._publish({"kind": "note",
                                   "text": "gateway is not running — packets will appear here when it starts"})
                gateway_up = False
                time.sleep(10)
                continue
            if gateway_up is False:
                self._publish({"kind": "note", "text": "gateway is running — following packets"})
            gateway_up = True

            try:
                # Its own session, so stop() can signal the whole pipeline
                # `monitor` runs (`tail -f | gawk`, or `docker logs -f | gawk`)
                # rather than only the shell at its head.
                self.proc = subprocess.Popen(
                    self.cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    # errors="replace", NOT strict. The log carries other
                    # people's APRS comment text verbatim, and plenty of
                    # stations send a Latin-1 degree sign (0xB0) in a bearing:
                    #   DX: W8QT-1 71.9mi 286\xb0 03:43
                    # Decoded strictly, that raises UnicodeDecodeError — which
                    # is a ValueError, so the read loop's own handler caught it
                    # and ended the loop. One malformed byte from a stranger
                    # three states away took the monitor down. It cost days to
                    # find because stderr was discarded and the orphans were
                    # cleaned up by systemd before anyone went looking.
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
            except OSError as exc:
                self._publish({"kind": "note", "text": f"cannot start monitor: {exc}"})
                time.sleep(10)
                continue

            _log(f"monitor pipeline started, pid {self.proc.pid}")
            # Arm the replay guard: the first REPLAY_GUARD lines of a fresh
            # pipeline are whatever tail was told to start with, and those
            # frames have already been counted and shown.
            with self.lock:
                self._since_restart = 0
            # The supervisor closes this stdout to break the deadlock described
            # in _supervise. That raises here, which is the intended escape and
            # not a fault: it is the only way to leave a blocking read on a
            # pipe whose writers have been orphaned.
            try:
                for raw in self.proc.stdout:
                    self.last_line_at = time.monotonic()
                    # A line arriving is the only evidence a restart achieved
                    # anything, so it is the only thing that clears the count.
                    self._stall_restarts = 0
                    line = ANSI.sub("", raw.rstrip("\n"))
                    m = EVENT.match(line)
                    if m:
                        self._publish({
                            "kind": "event",
                            "time": m.group(1),
                            "label": m.group(2).strip(),
                            "text": m.group(3),
                        })
                        continue
                    d = DETAIL.match(line)
                    if d:
                        self._publish({"kind": "detail", "text": d.group(1)})
                    # Anything else is the legend or blank lines; skipped.
            except (ValueError, OSError) as exc:
                _log(f"read loop released: {exc!r}")

            # The monitor ends when the gateway stops or restarts. The next pass
            # finds out which: a restart resumes silently, a stop is announced
            # once.
            #
            # Kill it before looping. `rc=None` here means the pipeline is
            # still alive and only the reader stopped, and starting a second
            # one without ending the first is how a single bad byte became
            # hundreds of orphaned `tail -f | gawk` pipelines over several
            # days, until the Pi ran out of threads and the page died with
            # RuntimeError("can't start new thread"). The pipeline is its own
            # session, so nothing reaps it for us.
            rc = self.proc.poll()
            if rc is None:
                _log("monitor pipeline still alive after the reader stopped; "
                     "killing it so it cannot be orphaned")
                self.stop()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
            _log(f"monitor pipeline ended, rc={rc}")
            time.sleep(5)

    def _log_size(self):
        try:
            return os.stat(LOG_FILE).st_size
        except OSError:
            return None

    def _supervise(self):
        """Restart the monitor pipeline if it stops producing while the gateway
        is still writing packets.

        The read loop above blocks on the pipeline's stdout with no timeout, so
        a pipeline that stays alive while producing nothing — a `tail` left
        following a rotated-away log, say — leaves the page frozen while the
        gateway carries on gating perfectly well. That happened in service, and
        looked from the browser exactly like a quiet band.

        Silence alone is not the signal: on a quiet band the filter legitimately
        emits nothing for hours. The log still growing while the pipeline says
        nothing is the signal.
        """
        last_size = self._log_size()
        last_size_at = time.monotonic()
        # Often enough to notice within a fraction of the threshold, never more
        # than twice a minute.
        interval = min(30, max(2, STALL_SECONDS // 4))
        while True:
            time.sleep(interval)
            size = self._log_size()
            if size is not None and last_size is not None and size != last_size:
                last_size = size
                last_size_at = time.monotonic()
            elif size is not None and last_size is None:
                last_size = size
                last_size_at = time.monotonic()

            proc = self.proc
            if proc is None:
                continue
            # Deliberately NOT "if proc.poll() is not None: continue".
            #
            # That guard deadlocked the monitor for hours at a time. SIGTERM to
            # the process group kills bash — `deploy_igate.sh monitor` — while
            # `tail` and `gawk` can outlive it, still holding the write end of
            # the pipe. So poll() reports the leader dead while _run is still
            # blocked on a stdout that will never reach EOF, because the
            # orphans keep it open. The supervisor then skipped every
            # subsequent check on the grounds that "_run's own retry covers
            # it", and _run *was the thing that was stuck*. Each half waited
            # for the other, silently, until someone restarted the service.
            #
            # A dead leader with a stalled feed is therefore not a reason to
            # stand down. It is the signal to escalate: SIGKILL the group to
            # take the orphans with it, and close stdout to unblock the
            # reader.
            quiet_for = time.monotonic() - self.last_line_at
            if quiet_for < STALL_SECONDS:
                continue
            # The gateway has written to the log more recently than the monitor
            # has produced a line: the pipeline, not the band, is the quiet one.
            if last_size_at <= self.last_line_at:
                continue
            mins = int(quiet_for // 60)
            how_long = f"{mins} minutes" if mins else f"{int(quiet_for)} seconds"

            # Escalate. The first version sent SIGTERM, announced a restart and
            # assumed one followed — and in service it announced a restart that
            # never happened, leaving the page frozen for three and a half
            # hours while the gateway ran perfectly. Announcing a fix that does
            # not occur is worse than announcing nothing: it reads as handled.
            #
            # So: term, then kill, then stop trying to be clever and exit.
            # igate-web.service is Restart=on-failure, so leaving is a ten
            # second outage rather than an afternoon, and it works whatever the
            # underlying cause turns out to be — which is the point, because
            # after two attempts the cause is still unknown.
            # Two counters, because they answer different questions. Per
            # pipeline: has SIGTERM been tried on THIS one yet. Across
            # pipelines: has restarting actually helped at all. Without the
            # second, a pipeline that dies obediently and comes back just as
            # silent is killed and restarted forever, announcing a fix every
            # interval — which is the same lie as before, only busier.
            if proc is not self._stall_proc:
                self._stall_proc, self._stall_attempts = proc, 0
            self._stall_attempts += 1
            attempt = self._stall_attempts
            self._stall_restarts += 1

            if self._stall_restarts > 4 or attempt > 2:
                _log(f"pipeline pid {proc.pid}: {self._stall_restarts} restarts "
                     f"have not fixed {int(quiet_for)}s of silence "
                     f"(attempt {attempt} on this one); exiting so systemd "
                     f"restarts the service")
                self._publish({"kind": "note",
                               "text": f"monitor feed stalled for {how_long} and could not "
                                       "be restarted — restarting the web monitor itself"})
                time.sleep(1)          # let the note reach anyone watching
                os._exit(1)

            # A leader that is already dead needs no SIGTERM; the problem is
            # whatever is still holding the pipe.
            leader_alive = proc.poll() is None
            sig, what = ((signal.SIGTERM, "restarting the feed")
                         if attempt == 1 and leader_alive
                         else (signal.SIGKILL, "restarting the feed (forcing it)"))

            self._publish({"kind": "note",
                           "text": f"monitor feed stalled for {how_long} while the gateway "
                                   f"kept logging — {what}"})
            try:
                os.killpg(proc.pid, sig)
                _log(f"sent {sig.name} to pipeline group {proc.pid} "
                     f"(attempt {attempt}, silent {int(quiet_for)}s)")
            except OSError as exc:
                _log(f"could not signal pipeline group {proc.pid}: {exc!r}")

            # Killing the group is not enough on its own: the reader is blocked
            # on a file object, and nothing wakes it but EOF or an exception.
            # Closing stdout raises in the read loop, which is exactly the
            # escape it needs.
            if not leader_alive or sig is signal.SIGKILL:
                try:
                    if proc.stdout is not None:
                        proc.stdout.close()
                        _log(f"closed stdout of pipeline {proc.pid} to unblock the reader")
                except OSError as exc:
                    _log(f"could not close stdout of {proc.pid}: {exc!r}")

            self.last_line_at = time.monotonic()

    def stop(self):
        """Signal the monitor pipeline. Without this, `tail -f` outlives the
        server and follows the log forever, because nothing ever writes to the
        pipe that would tell it the reader has gone."""
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    def subscribe(self):
        """Register a viewer, making room if the limit is reached.

        The oldest viewer is evicted rather than the newest refused. Refusing
        is the wrong way round: the person refused is the one sitting in front
        of the page right now, and the eight holding the slots may be ghosts —
        connections whose browsers are asleep or gone. An evicted viewer that
        is still really there reconnects by itself, because closing the
        response is what makes EventSource retry.
        """
        sub = Subscriber(QUEUE_DEPTH)
        with self.lock:
            while len(self.subscribers) >= MAX_CLIENTS:
                oldest = self.subscribers[0]
                oldest.dropped.set()
                del self.subscribers[0]
            self.subscribers.append(sub)
            backlog = list(self.history)
        return sub, backlog

    def unsubscribe(self, sub):
        with self.lock:
            if sub in self.subscribers:
                self.subscribers.remove(sub)


class Settings:
    """Cached view of `deploy_igate.sh config` plus liveness from the pidfiles.

    `config` is used rather than `status` because `status` also rewrites
    run/status.html; a page refresh should not have side effects.
    """

    TTL = 10.0
    HIDE = {"IGLOGIN_PASSCODE"}

    def __init__(self, script_dir):
        self.script_dir = script_dir
        self.lock = threading.Lock()
        self.at = 0.0
        self.cache = {}

    def _liveness(self):
        """Ask the script, because liveness is mode-specific.

        Docker mode has a container and no pidfiles; bare-metal has pidfiles and
        no container. Reading run/*.pid directly would report every healthy
        Docker deployment as stopped. `is-running` answers for whichever mode is
        configured and, unlike `status`, does not rewrite run/status.html — which
        matters when something polls it every 15 seconds.
        """
        try:
            p = subprocess.run(
                [os.path.join(self.script_dir, "deploy_igate.sh"), "is-running"],
                capture_output=True, text=True, timeout=20,
            )
            return p.returncode == 0, p.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return False, "state unavailable"

    def get(self):
        with self.lock:
            if time.time() - self.at < self.TTL and self.cache:
                return self.cache

            settings, filt = {}, ""
            try:
                out = subprocess.run(
                    [os.path.join(self.script_dir, "deploy_igate.sh"), "config"],
                    capture_output=True, text=True, timeout=20,
                ).stdout
                for line in out.splitlines():
                    if line.startswith("Resolved Direwolf FILTER:"):
                        filt = line.split(":", 1)[1].strip()
                    elif "=" in line and not line.startswith(" "):
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        # Masked by `config` already; dropped as well, so the
                        # page has no field that could ever carry it.
                        if k and k.isupper() and k not in self.HIDE:
                            settings[k] = v
            except (OSError, subprocess.SubprocessError):
                pass

            running, detail = self._liveness()
            self.cache = {
                "running": running,
                "detail": detail,
                "filter": filt,
                "settings": settings,
                "host": os.uname().nodename,
            }
            self.at = time.time()
            return self.cache


class LoggingStatus:
    """What the dated logs and the daily archive are doing, read from disk.

    Exists so the morning check -- did last night's timer run, is today's
    dated log filling, is rotation outpacing the archiver -- does not need an
    ssh session.

    **Everything here is bounded, because this is polled forever and the files
    it reads only grow.** The monitor has been brought down before by work
    that was cheap on day one. The rules this follows:

      * No full-file scan whose size is unbounded. The dated CSV grows all
        day; it is line-counted only below ROW_COUNT_LIMIT and reported by
        size above it.
      * The summary is read from the END, a fixed TAIL_BYTES, so a file with
        four hundred rows in it costs the same as one with four.
      * The frames directory is walked with a hard stop at SCAN_LIMIT
        entries, using scandir so the size comes without a second syscall.
      * Nothing is recomputed that igate-archive.sh already wrote down.
        Counting beacons out of the 20 MB packet log takes seconds; the
        summary TSV holds the answer and costs a few hundred bytes.
    """

    TTL = 60.0                 # the morning check does not need 15-second news
    ROW_COUNT_LIMIT = 2 << 20  # 2 MB: above this, report bytes and not rows
    TAIL_BYTES = 16384          # enough for the last several summary rows
    SCAN_LIMIT = 5000          # frames/ entries examined before giving up
    SUMMARY_ROWS = 8           # a 7-day window, plus the day before it
    WINDOW_DAYS = 7            # must match SUMMARY_WINDOW_DAYS in the archiver

    def __init__(self, script_dir):
        self.script_dir = script_dir
        self.lock = threading.Lock()
        self.at = 0.0
        self.cache = {}

    def _conf(self, key, default=""):
        """One key out of the config files, without running the script."""
        out = default
        pat = re.compile(r"\s*%s\s*=\s*(.*)" % re.escape(key))
        for name in ("igate.conf", "igate.local.conf"):
            try:
                with open(os.path.join(self.script_dir, name),
                          encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        m = pat.match(line)
                        if m:
                            out = m.group(1).split("#")[0].strip()
            except OSError:
                continue
        return out

    @classmethod
    def _day_file(cls, log_dir, day):
        """Size and age always; row count only while it is cheap.

        Size and mtime are two stat fields and answer the question that
        matters -- is it filling. The row count is a convenience and is not
        worth reading a file that grows every day without limit.
        """
        path = os.path.join(log_dir, day + ".log")
        try:
            st = os.stat(path)
        except OSError:
            return {"day": day, "present": False, "rows": None,
                    "bytes": 0, "age_s": None}
        rows = None
        if st.st_size <= cls.ROW_COUNT_LIMIT:
            try:
                with open(path, "rb") as fh:
                    rows = max(0, sum(1 for _ in fh) - 1)   # less the header
            except OSError:
                pass
        return {"day": day, "present": True, "rows": rows,
                "bytes": st.st_size,
                "age_s": max(0, int(time.time() - st.st_mtime))}

    @classmethod
    def _summary_tail(cls, path):
        """The last few rows, read from the end of the file.

        Reading the whole thing would be fine today and quietly linear in the
        number of days the station has ever run.
        """
        header, rows = [], []
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                if size > cls.TAIL_BYTES:
                    fh.seek(size - cls.TAIL_BYTES)
                    fh.readline()           # discard a partial first line
                chunk = fh.read().decode("utf-8", "replace")
            for line in chunk.splitlines():
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if parts[0] == "date":
                    header = parts
                else:
                    rows.append(parts)
            # The header lives at the top and a tail read will usually miss
            # it, so fetch it separately -- one short read, not the file.
            if not header:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("date\t"):
                            header = line.rstrip("\n").split("\t")
                            break
                        if not line.startswith("#"):
                            break
        except OSError:
            pass
        return header, rows[-cls.SUMMARY_ROWS:]

    @classmethod
    def _window(cls, header, rows):
        """The last WINDOW_DAYS complete days, summed where summing is valid.

        Three rules this follows, each of which is a way of being wrong that
        is easy to reach:

        **Unique stations are not summable.** Seven daily counts added up is
        not the number of stations heard in a week, because most stations are
        heard on more than one day, and the sum cannot be corrected afterwards
        without the callsigns. The union is computed by the archiver, which
        has the dated logs, and arrives as `rx_stations_win` on the most
        recent row; this reads that number rather than adding anything up.

        **A sum over an incomplete window is an undercount, not a total.** So
        the result carries how many days were found, how many carry the
        console-log columns at all, and how many of those are flagged partial.
        A caller that shows the sum without those is reporting a reboot as a
        quiet week.

        **Only days inside the window count.** The tail may hold more rows
        than that, and may be missing days entirely if the station was down.
        """
        idx = {name: i for i, name in enumerate(header)}

        def val(row, name):
            i = idx.get(name)
            if i is None or i >= len(row):
                return None
            text = row[i].strip()
            if not text:
                return None
            try:
                return int(text)
            except ValueError:
                return None

        # The window is the WINDOW_DAYS days ending with the most recent row,
        # which is the last COMPLETE day. Today is not in the summary at all.
        dated = [r for r in rows if r and re.fullmatch(r"\d{4}-\d\d-\d\d", r[0])]
        if not dated:
            return None
        last = dated[-1][0]
        try:
            end = date.fromisoformat(last)
        except ValueError:
            return None
        # Date arithmetic, not seconds. Stepping back six times 86400 from a
        # local midnight lands at 23:00 of the day before across a DST
        # boundary, which moves the window edge by a day twice a year.
        first = (end - timedelta(days=cls.WINDOW_DAYS - 1)).isoformat()
        inside = [r for r in dated if first <= r[0] <= last]

        sums = {}
        for name in ("rx_packets", "rx_direct", "day_gated_up",
                     "day_gated_down", "day_is_offered", "day_beacons",
                     "day_beacon_repeats", "day_frames"):
            got = [val(r, name) for r in inside]
            got = [g for g in got if g is not None]
            sums[name] = sum(got) if got else None
            sums[name + "_days"] = len(got)

        partial = sum(1 for r in inside if (val(r, "day_partial") or 0) == 1)
        undated = sum(val(r, "day_undated") or 0 for r in inside)
        # The union as of the newest row. Taken from the row rather than
        # summed, for the reason in the docstring.
        stations = val(inside[-1], "rx_stations_win") if inside else None
        new_stations = [val(r, "rx_stations_new") for r in inside]
        new_stations = [n for n in new_stations if n is not None]

        return {
            "first": first,
            "last": last,
            "days_expected": cls.WINDOW_DAYS,
            "days_present": len(inside),
            "days_partial": partial,
            "undated": undated,
            "stations": stations,
            "stations_new": sum(new_stations) if new_stations else None,
            **sums,
        }

    @classmethod
    def _frames(cls, frames_dir):
        """Count and total size, with a hard stop. scandir, not listdir+stat."""
        chunks = total = 0
        newest = 0.0
        truncated = False
        try:
            with os.scandir(frames_dir) as it:
                for entry in it:
                    if chunks >= cls.SCAN_LIMIT:
                        truncated = True
                        break
                    if not entry.name.endswith(".log.gz"):
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    chunks += 1
                    total += st.st_size
                    if st.st_mtime > newest:
                        newest = st.st_mtime
        except OSError:
            pass
        return chunks, total, newest, truncated

    def _timer(self):
        """State of the archive timer, with 'never installed' told apart.

        `LoadState` is not decoration. `systemctl show` on a unit that does
        not exist prints `ActiveState=inactive` and exits 0, so without it a
        timer that was never installed -- every Docker deployment, and any Pi
        where the unit was not enabled -- reports as installed and stopped.
        Those want opposite actions from whoever is reading the page.
        """
        try:
            p = subprocess.run(
                ["systemctl", "show", "igate-archive.timer",
                 "--property=LastTriggerUSec",
                 "--property=NextElapseUSecRealtime",
                 "--property=ActiveState",
                 "--property=LoadState"],
                capture_output=True, text=True, timeout=10,
            )
            out = {}
            for line in p.stdout.splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
            load = out.get("LoadState", "")
            if load in ("not-found", "bad-setting", "error"):
                return {"state": "not installed", "load": load,
                        "last": "", "next": ""}
            return {"state": out.get("ActiveState", "unknown"),
                    "load": load,
                    "last": out.get("LastTriggerUSec", "") or "never",
                    "next": out.get("NextElapseUSecRealtime", "") or "unknown"}
        except (OSError, subprocess.SubprocessError):
            # No systemctl at all: a container, or a non-systemd host. Not the
            # same as a timer that is installed and broken.
            return {"state": "unknown", "load": "", "last": "", "next": ""}

    def get(self):
        with self.lock:
            if time.time() - self.at < self.TTL and self.cache:
                return self.cache
            try:
                self.cache = self._collect()
            except Exception as exc:                 # never take /api with it
                _log(f"logging status unavailable: {exc!r}")
                self.cache = {"error": "unavailable"}
            self.at = time.time()
            return self.cache

    def _resolve(self, key, default):
        """A config path, resolved the way the station resolves it.

        A relative path in the config is resolved against the install
        directory. That is the only form that works from here: on the Pi
        `~/aprs-igate` is a symlink to `/opt/aprs-igate`, this unit runs from
        /opt with ProtectHome=yes, and an absolute /home/... path in the
        config is simply not visible. Found 2026-10-30, when every count read
        zero while the station was writing normally.
        """
        val = os.path.expanduser(self._conf(key, default))
        return val if os.path.isabs(val) else os.path.join(self.script_dir, val)

    def log_dir(self):
        return self._resolve("LOG_DIR", "logs")

    def mycall(self):
        return self._conf("MYCALL", "KD3CCO-10")

    def _collect(self):
        here = self.script_dir
        resolve = self._resolve

        log_dir = self.log_dir()
        arch_dir = resolve("ARCHIVE_DIR", "archive")

        today = time.strftime("%Y-%m-%d", time.gmtime())
        yday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))

        chunks, chunk_bytes, newest, truncated = self._frames(
            os.path.join(arch_dir, "frames"))
        header, rows = self._summary_tail(
            os.path.join(arch_dir, "daily-summary.tsv"))

        # The live packet log and which rotated generations exist. Four stats.
        run_log = os.path.join(here, "run", "direwolf.log")
        live, gens = 0, []
        for suffix in ("", ".1", ".1.gz", ".2.gz"):
            try:
                st = os.stat(run_log + suffix)
            except OSError:
                continue
            if suffix:
                gens.append(suffix.lstrip("."))
            else:
                live = st.st_size

        return {
            "log_dir": log_dir,
            "archive_dir": arch_dir,
            "today": self._day_file(log_dir, today),
            "yesterday": self._day_file(log_dir, yday),
            "chunks": chunks,
            "chunks_truncated": truncated,
            "chunk_bytes": chunk_bytes,
            "newest_chunk_age_s": int(time.time() - newest) if newest else None,
            "summary_header": header,
            "summary_rows": rows,
            "window": self._window(header, rows),
            "window_days": self.WINDOW_DAYS,
            "live_log_bytes": live,
            "rotations_held": gens,
            "timer": self._timer(),
        }


class TodayTraffic:
    """Packets and distinct stations heard so far today, read incrementally.

    Two problems with doing this the obvious way. Counting rows in the dated
    CSV on every poll is linear in the day, which is the shape of work that
    has taken this page down before; and `LoggingStatus` therefore refuses to
    line-count above 2 MB, which at this station's ~9,000 packets a day means
    the count vanishes from the panel late in the busiest days -- exactly when
    somebody is looking.

    So this remembers a byte offset and reads only what has been appended
    since the last poll. A day's work costs one pass in total however often
    the page is refreshed, and distinct stations become possible at all: a set
    of a few hundred callsigns is cheap to hold and impossible to get from a
    row count.

    **The file is parsed by splitting on commas, not with the csv module**, and
    that is safe here rather than sloppy. Direwolf's comment field can contain
    commas and quotes, but `source` and `heard` are columns 4 and 5, ahead of
    every free-text field, so nothing in a stranger's comment text can shift
    them. The csv module would also need the whole logical row, which is the
    one thing an incremental reader of a file being appended to does not have.

    Resets when the UTC day rolls over, and when the file shrinks or its inode
    changes, which covers rotation, a hand-edit, and a card remount.
    """

    TTL = 20.0
    # A single appended line that never terminates would otherwise grow this
    # without limit. Longer than any real CSV row; a fragment past it is
    # dropped and the offset advanced, so one malformed write costs one line.
    MAX_FRAGMENT = 64 * 1024

    def __init__(self, script_dir, log_dir_fn, mycall_fn):
        self.script_dir = script_dir
        self._log_dir = log_dir_fn
        self._mycall = mycall_fn
        self.lock = threading.Lock()
        self.at = 0.0
        self.cache = {}
        self._reset(None)

    def _reset(self, day):
        self.day = day
        self.offset = 0
        self.inode = None
        self.frag = ""
        self.packets = 0
        self.direct = 0
        self.own = 0
        self.stations = set()
        self.stations_direct = set()
        self.header_seen = False

    def get(self):
        with self.lock:
            if time.time() - self.at < self.TTL and self.cache:
                return self.cache
            try:
                self.cache = self._collect()
            except Exception as exc:
                _log(f"today traffic unavailable: {exc!r}")
                self.cache = {"error": "unavailable"}
            self.at = time.time()
            return self.cache

    def _collect(self):
        day = time.strftime("%Y-%m-%d", time.gmtime())
        if day != self.day:
            self._reset(day)
        path = os.path.join(self._log_dir(), day + ".log")
        try:
            st = os.stat(path)
        except OSError:
            return {"day": day, "present": False, "packets": None,
                    "stations": None, "stations_direct": None,
                    "direct": None, "own": None}
        if self.inode is not None and (st.st_ino != self.inode
                                       or st.st_size < self.offset):
            # A different file, or the same one truncated. Everything counted
            # so far may belong to bytes that are gone; start over rather than
            # carry a total that cannot be checked.
            keep = self.day
            self._reset(keep)
            try:
                st = os.stat(path)
            except OSError:
                return {"day": day, "present": False, "packets": None,
                        "stations": None, "stations_direct": None,
                        "direct": None, "own": None}
        self.inode = st.st_ino

        if st.st_size > self.offset:
            mycall = self._mycall()
            with open(path, "rb") as fh:
                fh.seek(self.offset)
                chunk = fh.read(st.st_size - self.offset)
            self.offset += len(chunk)
            text = self.frag + chunk.decode("utf-8", "replace")
            # Only whole lines. The tail of the read is almost always a
            # partial row, because direwolf is appending while this runs.
            lines = text.split("\n")
            self.frag = lines.pop()
            if len(self.frag) > self.MAX_FRAGMENT:
                self.frag = ""
            for line in lines:
                if not line:
                    continue
                parts = line.split(",", 5)
                if len(parts) < 5:
                    continue
                if parts[0] == "chan":
                    self.header_seen = True
                    continue
                src = parts[3].strip().rstrip("?")
                heard = parts[4].strip().rstrip("?")
                if not src:
                    continue
                self.packets += 1
                if src == mycall:
                    # Own beacons returning off the air after a digipeater
                    # repeats them. Counted, but not a station this receiver
                    # reached: see igate_counts.sources().
                    self.own += 1
                else:
                    self.stations.add(src)
                if heard == src or heard == "":
                    self.direct += 1
                    if src != mycall:
                        self.stations_direct.add(src)

        return {"day": day, "present": True,
                "packets": self.packets,
                "direct": self.direct,
                "own": self.own,
                "stations": len(self.stations),
                "stations_direct": len(self.stations_direct),
                "bytes": st.st_size,
                "age_s": max(0, int(time.time() - st.st_mtime))}


class Handler(BaseHTTPRequestHandler):
    server_version = "igate-web"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass  # journald already records the unit; per-request noise is not useful

    def _send(self, code, ctype, body):
        """Send a complete response. Length is derived, never counted by hand.

        Under HTTP/1.1 keep-alive a Content-Length that is one byte too large
        makes the client wait forever for a byte that never arrives, so hand
        counting is not a style question.
        """
        self._head(code, ctype, len(body))
        self.wfile.write(body)

    def _head(self, code, ctype, length=None, stream=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # Read-only page, but there is no reason to let it be framed or sniffed.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if stream:
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
        elif length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._page()
        elif path == "/api/status":
            self._status()
        elif path == "/events":
            self._events()
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found\n")

    # Only GET exists. Anything that could change the station's behavior is
    # absent by construction rather than by permission check.
    def do_POST(self):
        self._send(405, "text/plain; charset=utf-8", b"read-only monitor\n")

    do_PUT = do_DELETE = do_PATCH = do_POST

    def _page(self):
        try:
            with open(PAGE, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(500, "text/plain; charset=utf-8", b"web/index.html is missing\n")
            return
        self._send(200, "text/html; charset=utf-8", body)

    def _status(self):
        payload = dict(SETTINGS.get())
        # Separate caches and TTLs. Liveness is wanted every 15 s; the logging
        # view is filesystem reads and is not; today's traffic is an
        # incremental read and sits between the two. Each is guarded on its
        # own so one bad directory cannot take /api/status with it.
        try:
            payload["logging"] = LOGGING.get()
        except Exception:                      # never let the panel break /api
            payload["logging"] = None
        try:
            payload["today"] = TODAY.get()
        except Exception:
            payload["today"] = None
        try:
            payload["live"] = STREAM.counts()
        except Exception:
            payload["live"] = None
        self._send(200, "application/json; charset=utf-8",
                   json.dumps(payload).encode())

    def _events(self):
        # subscribe() always succeeds now: at the limit it evicts the oldest
        # viewer rather than refusing this one. The 503 that used to live here
        # turned out to be the wrong answer — it refused the person actually
        # looking at the page in favor of connections that were often already
        # dead.
        sub, backlog = STREAM.subscribe()

        # A deadline on every write. Without one, a viewer that went away
        # without closing holds its slot indefinitely: the write neither
        # succeeds nor fails, and MAX_CLIENTS of those make the page return
        # "too many viewers" to everyone. socket.timeout is an OSError, so the
        # handler below already treats it as the disconnection it is.
        try:
            self.connection.settimeout(WRITE_TIMEOUT)
        except OSError:
            pass

        self._head(200, "text/event-stream; charset=utf-8", stream=True)
        try:
            for event in backlog:
                self._send_event(event)
            last_keepalive = time.monotonic()
            while True:
                # Checked before blocking, not only after a timeout. A viewer
                # cut loose while its queue still held events used to be sent
                # all of them first — up to QUEUE_DEPTH of them — before the
                # connection closed.
                if sub.dropped.is_set():
                    # Closing the response is what makes EventSource
                    # reconnect, which re-subscribes and replays the history.
                    break
                try:
                    self._send_event(sub.queue.get(timeout=DROPPED_POLL_SECONDS))
                    continue
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_keepalive >= KEEPALIVE_SECONDS:
                    # Comment frame: keeps proxies and phone radios from
                    # dropping an idle connection on a quiet band.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_keepalive = now
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            STREAM.unsubscribe(sub)
            # Close the socket, do not return it to the keep-alive loop.
            #
            # The whole drop-and-reconnect design rests on this: EventSource
            # only retries when the response ends. The streaming header says
            # Connection: keep-alive with no Content-Length, so without this
            # the base class simply waited for another request on the same
            # socket — the viewer's slot was freed while its connection stayed
            # open and silent, and the page sat there reading "live" and
            # frozen. That is indistinguishable from a quiet band, and it is
            # the symptom that sent us looking at the gateway instead.
            self.close_connection = True

    def _send_event(self, event):
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.flush()


def main():
    global STREAM, SETTINGS, LOGGING, TODAY
    script_dir = HERE
    cmd = os.environ.get("IGATE_MONITOR_CMD")
    cmd = shlex.split(cmd) if cmd else [
        os.path.join(script_dir, "deploy_igate.sh"), "monitor",
    ]

    # Bound before the monitor starts, so a busy port fails without having
    # started a pipeline that would then need cleaning up.
    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            """One line, not a traceback, when a viewer disconnects.

            socketserver prints a full traceback for any exception out of a
            handler, and a browser closing a tab or a phone sleeping raises
            ConnectionResetError every time. On a page left open all day that
            is hundreds of tracebacks — noise in itself, and actively harmful
            now the journal is persistent and capped at 32M, because it
            rotates away the entries worth keeping.

            A disconnection is not an error here and is not reported. Anything
            else is, in one line, which is enough to know something happened
            and to go looking.
            """
            exc = sys.exc_info()[1]
            if isinstance(exc, (BrokenPipeError, ConnectionResetError,
                                ConnectionAbortedError, TimeoutError)):
                return
            _log(f"request from {client_address[0]} failed: {exc!r}")

    httpd = Server((BIND, PORT), Handler)

    SETTINGS = Settings(script_dir)
    LOGGING = LoggingStatus(script_dir)
    # Shares LoggingStatus's config reader, so LOG_DIR and MYCALL are resolved
    # in exactly one place and the two panels cannot disagree about which file
    # today's traffic is in.
    TODAY = TodayTraffic(script_dir, LOGGING.log_dir, LOGGING.mycall)

    liveness_cmd = os.environ.get("IGATE_LIVENESS_CMD")
    if liveness_cmd:
        def is_running():
            try:
                return subprocess.run(shlex.split(liveness_cmd), capture_output=True,
                                      timeout=20).returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False
    else:
        def is_running():
            return SETTINGS._liveness()[0]

    STREAM = MonitorStream(cmd, is_running)

    # SIGTERM is how both `deploy_igate.sh down` and systemd stop this. Python's
    # default for it exits without running any cleanup, which would leave the
    # monitor pipeline running; raising SystemExit reaches the finally below.
    def _terminate(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _terminate)

    print(f"igate-web listening on http://{BIND}:{PORT}/  (read-only)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STREAM.stop()


if __name__ == "__main__":
    main()
