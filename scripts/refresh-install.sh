#!/bin/bash
# Installs (or with --remove, removes) the launchd job that runs refresh.sh
# every 10 minutes while this Mac is awake. Log: ~/Library/Logs/la-liga-refresh.log
set -euo pipefail
LABEL="local.la-liga.refresh"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$(cd "$(dirname "$0")" && pwd)/refresh.sh"
LOG="$HOME/Library/Logs/la-liga-refresh.log"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SCRIPT</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL, running $SCRIPT every 10 minutes"
