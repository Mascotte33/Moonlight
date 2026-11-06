#!/bin/sh
# MOONLIGHT GUI launcher for Linux (X11 session). Opens the GUI detached from
# the terminal; the log is on the Dashboard and Diagnostics tabs and in
# logs/bot.log.
cd "$(dirname "$0")" || exit 1
nohup python3 -m moonlight "$@" >/dev/null 2>&1 &
