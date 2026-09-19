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

# The packet log the gateway writes. Used only as a liveness signal for the
# monitor pipeline: if this file is growing while the pipeline has produced
# nothing for a long time, the pipeline is broken rather than the band quiet.
LOG_FILE = os.environ.get("IGATE_LOG_FILE", os.path.join(HERE, "run", "direwolf.log"))
# How long the pipeline may produce nothing, while the log is still growing,
# before it is treated as stalled and restarted.
STALL_SECONDS = int(os.environ.get("IGATE_STALL_SECONDS", "600"))

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "17:23:49  RF RX      AA3BR>SYRV6V,...:`h@dl#GYY"
EVENT = re.compile(r"^(\d\d:\d\d:\d\d)\s\s(\S.*?)\s\s+(.*)$")
# Direwolf's decode of the frame above, indented by monitor_filter.
DETAIL = re.compile(r"^\s{6,}(\S.*)$")


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

    def __init__(self, cmd, is_running):
        self.cmd = cmd
        self.is_running = is_running
        self.history = deque(maxlen=HISTORY)
        self.subscribers = set()
        self.lock = threading.Lock()
        self.proc = None
        # Monotonic timestamps for the stall check; see _supervise.
        self.last_line_at = time.monotonic()
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._supervise, daemon=True).start()

    def _publish(self, event):
        with self.lock:
            # A note identical to the last thing published tells a viewer
            # nothing new, and repeated it would push real packets out of the
            # bounded history.
            if event.get("kind") == "note" and self.history and self.history[-1] == event:
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
                self.subscribers.discard(sub)

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
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
            except OSError as exc:
                self._publish({"kind": "note", "text": f"cannot start monitor: {exc}"})
                time.sleep(10)
                continue

            for raw in self.proc.stdout:
                self.last_line_at = time.monotonic()
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
                # Anything else is the monitor's legend or blank lines; skipped.

            # The monitor ends when the gateway stops or restarts. The next pass
            # finds out which: a restart resumes silently, a stop is announced
            # once.
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
            if proc is None or proc.poll() is not None:
                continue  # not running; _run's own retry covers it
            quiet_for = time.monotonic() - self.last_line_at
            if quiet_for < STALL_SECONDS:
                continue
            # The gateway has written to the log more recently than the monitor
            # has produced a line: the pipeline, not the band, is the quiet one.
            if last_size_at <= self.last_line_at:
                continue
            mins = int(quiet_for // 60)
            how_long = f"{mins} minutes" if mins else f"{int(quiet_for)} seconds"
            self._publish({"kind": "note",
                           "text": f"monitor feed stalled for {how_long} while the gateway "
                                   "kept logging — restarting the feed"})
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
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
        sub = Subscriber(QUEUE_DEPTH)
        with self.lock:
            if len(self.subscribers) >= MAX_CLIENTS:
                return None, []
            self.subscribers.add(sub)
            backlog = list(self.history)
        return sub, backlog

    def unsubscribe(self, sub):
        with self.lock:
            self.subscribers.discard(sub)


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

    # Only GET exists. Anything that could change the station's behaviour is
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
        self._send(200, "application/json; charset=utf-8",
                   json.dumps(SETTINGS.get()).encode())

    def _events(self):
        sub, backlog = STREAM.subscribe()
        if sub is None:
            self._send(503, "text/plain; charset=utf-8", b"too many viewers\n")
            return

        self._head(200, "text/event-stream; charset=utf-8", stream=True)
        try:
            for event in backlog:
                self._send_event(event)
            while True:
                try:
                    self._send_event(sub.queue.get(timeout=20))
                except queue.Empty:
                    if sub.dropped.is_set():
                        # Cut loose for falling too far behind. Closing the
                        # response is what makes EventSource reconnect, which
                        # re-subscribes and replays the history.
                        break
                    # Comment frame: keeps proxies and phone radios from
                    # dropping an idle connection on a quiet band.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            STREAM.unsubscribe(sub)

    def _send_event(self, event):
        self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
        self.wfile.flush()


def main():
    global STREAM, SETTINGS
    script_dir = HERE
    cmd = os.environ.get("IGATE_MONITOR_CMD")
    cmd = shlex.split(cmd) if cmd else [
        os.path.join(script_dir, "deploy_igate.sh"), "monitor",
    ]

    # Bound before the monitor starts, so a busy port fails without having
    # started a pipeline that would then need cleaning up.
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    httpd.daemon_threads = True

    SETTINGS = Settings(script_dir)

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
