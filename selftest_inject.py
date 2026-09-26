#!/usr/bin/env python3
"""Put one APRS message onto APRS-IS, for `deploy_igate.sh selftest`.

Separate from the shell script because a login handshake with a timeout, and a
read loop that distinguishes "server rejected the login" from "server said
nothing", is not something to write in bash and then trust.

It injects and exits. Whether the message comes back to the gateway, gets
transmitted, and is acknowledged is decided by reading the gateway's own log,
which the shell script does — deliberately, so that a broken monitor cannot
make a working gateway look failed.

Every value arrives in the environment rather than on the command line: the
passcode would otherwise be visible in the process list, and this is a program
whose whole job is to be run repeatedly and unattended.
"""
import os
import socket
import sys
import time

TIMEOUT = 20


def need(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"selftest: {name} is not set")
    return value


def main():
    server = need("SELFTEST_SERVER")
    port = int(os.environ.get("SELFTEST_PORT", "14580"))
    login = need("SELFTEST_LOGIN")
    passcode = need("SELFTEST_PASS")
    source = need("SELFTEST_FROM")
    target = need("SELFTEST_TO")
    text = need("SELFTEST_TEXT")
    msgid = need("SELFTEST_ID")

    # The addressee field is exactly nine characters, space padded. A server
    # will forward a malformed one and the addressee will never see it, which
    # would present as a radio fault.
    if len(target) > 9:
        sys.exit(f"selftest: {target} is longer than the 9-character addressee field")
    packet = f"{source}>APRS,TCPIP*::{target:<9}:{text}{{{msgid}"

    try:
        sock = socket.create_connection((server, port), timeout=TIMEOUT)
    except OSError as exc:
        sys.exit(f"selftest: cannot reach {server}:{port} — {exc}")

    sock.settimeout(TIMEOUT)
    try:
        # Read the server's banner before logging in. A server that is not
        # speaking APRS-IS at all is worth distinguishing from a bad passcode.
        banner = sock.recv(512).decode(errors="replace")
        if not banner.startswith("#"):
            sys.exit(f"selftest: {server}:{port} did not answer as an APRS-IS server")

        sock.sendall(
            f"user {login} pass {passcode} vers igate-selftest 1.0\r\n".encode())

        # "verified" is the word that matters: an unverified login is accepted
        # and then silently refuses to accept anything transmitted, which would
        # look like the gateway ignoring the message.
        deadline = time.monotonic() + TIMEOUT
        verified = None
        while time.monotonic() < deadline:
            try:
                line = sock.recv(512).decode(errors="replace")
            except socket.timeout:
                break
            if not line:
                break
            if "logresp" in line:
                verified = "verified" in line and "unverified" not in line
                break
        if verified is False:
            sys.exit(f"selftest: {login} was not verified by {server} — check the passcode")
        if verified is None:
            sys.exit(f"selftest: {server} never answered the login")

        sock.sendall((packet + "\r\n").encode())
        # A moment for the server to take it before the socket closes; an
        # immediate close can discard it.
        time.sleep(2)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    print(f"  sent: {packet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
