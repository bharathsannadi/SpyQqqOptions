#!/bin/bash
# Kill the engine if its heartbeat file is older than 120s; launchd (KeepAlive) restarts it.
# Looks ONLY at the heartbeat — never at ports or browser clients (the old watchdog's kill-loop).
HB=/Users/bsannadi/Desktop/bharath/SpyQqqOptions/state/heartbeat
[ -f "$HB" ] || { echo "$(TZ=America/New_York date '+%F %T %Z') no heartbeat file yet"; exit 0; }
NOW=$(date +%s); LAST=$(cut -d. -f1 "$HB"); AGE=$((NOW-LAST))
if [ "$AGE" -gt 120 ]; then
  PID=$(launchctl list | awk '$3=="com.spyqqq"{print $1}')
  if [ -n "$PID" ] && [ "$PID" != "-" ]; then
    echo "$(TZ=America/New_York date '+%F %T %Z') heartbeat ${AGE}s stale — killing pid $PID"
    kill -TERM "$PID"; sleep 5; kill -KILL "$PID" 2>/dev/null
  else
    echo "$(TZ=America/New_York date '+%F %T %Z') heartbeat ${AGE}s stale but no pid (launchd will restart)"
  fi
fi
