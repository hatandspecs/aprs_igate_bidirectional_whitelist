FROM fedora:43

RUN dnf install -y --setopt=install_weak_deps=False \
        direwolf hamlib alsa-utils \
    && dnf clean all

# Dedicated non-root, non-login user. It has no fixed UID/GID beyond what
# useradd assigns by default — the host's dialout/audio group GIDs are added
# at `docker run` time via --group-add so this user can open the serial and
# audio device nodes passed through from the host without ever being root.
RUN useradd --system --create-home --home-dir /home/igate --shell /sbin/nologin igate

COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# The rendered direwolf.conf is bind-mounted here at runtime. deploy_igate.sh
# actually runs the container as the invoking host user (--user), not
# `igate`, so the file's host-side ownership lines up and its restrictive
# 600 permissions (it embeds the APRS-IS passcode) still keep it unreadable
# to everyone else. That means the mount point can't live under /home/igate
# (mode 700, owned by `igate`) — an arbitrary host UID couldn't even
# traverse into it. /etc/direwolf is 755 so any UID can reach the file.
RUN mkdir -p /etc/direwolf && chmod 755 /etc/direwolf

USER igate
WORKDIR /home/igate

# entrypoint.sh starts rigctld (bridging the FTX-1's separate CAT/PTT serial
# ports) on loopback, then execs direwolf so it becomes PID 1.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
