#!/usr/bin/env bash
# gf — Goldfinger Engine control script
# Usage: ./gf [start|stop|restart|status|logs|open]

SERVICE="com.goldfinger.engine"
PLIST="$HOME/Library/LaunchAgents/$SERVICE.plist"
LOG="/Users/rukiverr/goldfinger-engine/data/logs/engine.log"
URL="http://localhost:8050"

case "${1:-status}" in
  start)
    launchctl load "$PLIST" 2>/dev/null || launchctl kickstart -k "gui/$(id -u)/$SERVICE"
    echo "Goldfinger started → $URL"
    ;;
  stop)
    launchctl unload "$PLIST"
    echo "Goldfinger stopped"
    ;;
  restart)
    launchctl unload "$PLIST" 2>/dev/null; sleep 1
    launchctl load "$PLIST"
    echo "Goldfinger restarted → $URL"
    ;;
  status)
    info=$(launchctl list "$SERVICE" 2>/dev/null)
    if echo "$info" | grep -q '"PID"'; then
      pid=$(echo "$info" | grep '"PID"' | awk '{print $3}' | tr -d ',')
      echo "RUNNING  PID $pid  →  $URL"
    else
      echo "STOPPED"
    fi
    ;;
  logs)
    tail -f "$LOG"
    ;;
  open)
    open "$URL"
    ;;
  *)
    echo "Usage: ./gf [start|stop|restart|status|logs|open]"
    ;;
esac
