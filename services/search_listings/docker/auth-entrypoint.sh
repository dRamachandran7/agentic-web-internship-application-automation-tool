#!/usr/bin/env bash
# Interactive Handshake login inside a container.
#
# scripts/save_auth.py needs a browser a human can actually see and click.
# There isn't one in a container, so this starts a virtual X display, exposes
# it over VNC, and bridges that to a websocket noVNC can serve — the login
# then happens in a normal browser tab on the host, at localhost:7900.
#
# The saved session lands on the bind-mounted .auth/ directory, which the
# scraping service reads read-only.
set -euo pipefail

DISPLAY_NUM=99
export DISPLAY=":${DISPLAY_NUM}"

Xvfb "$DISPLAY" -screen 0 1600x1000x24 -nolisten tcp &

# Wait for the X socket rather than sleeping a fixed amount — x11vnc exits
# immediately if it starts before the display is accepting connections.
for _ in $(seq 1 100); do
  [ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ] && break
  sleep 0.1
done

x11vnc -display "$DISPLAY" -forever -shared -nopw -quiet -rfbport 5900 &
websockify --web=/usr/share/novnc 7900 localhost:5900 >/dev/null 2>&1 &

cat <<'BANNER'

====================================================================
  Open this in your browser to see the login window:

      http://localhost:7900/vnc.html?autoconnect=1&resize=scale

  Log into Handshake there (including your school's SSO and MFA)
  until you reach your dashboard, then come back to THIS terminal
  and press Enter to save the session.
====================================================================

BANNER

exec python -m scripts.save_auth
