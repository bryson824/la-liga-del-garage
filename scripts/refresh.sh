#!/bin/bash
# Starts the publish workflow during NFL game windows, because GitHub's own
# scheduler skips most of its runs. launchd calls this every 10 minutes; outside
# the windows it does nothing. The windows match .github/workflows/publish.yml.
#
#   scripts/refresh.sh          run if inside a game window
#   scripts/refresh.sh --force  run now regardless
#
# Not installed by default; scripts/refresh-install.sh sets it up as a backup.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
REPO="bryson824/la-liga-del-garage"
stamp() { date "+%Y-%m-%d %H:%M:%S"; }

if [ "${1:-}" != "--force" ]; then
  dow=$(date -u +%u)      # 1 = Monday ... 7 = Sunday, UTC
  hour=$((10#$(date -u +%H)))
  in_window=no
  if [ "$dow" = 7 ] && [ "$hour" -ge 17 ]; then in_window=yes; fi
  case "$dow" in 1|2|5) [ "$hour" -le 5 ] && in_window=yes ;; esac
  [ "$in_window" = yes ] || exit 0

  # Skip if a run (scheduled, pushed or ours) started in the last 8 minutes.
  age=$(gh run list -R "$REPO" -w publish.yml -L 1 --json createdAt \
        --jq '.[0].createdAt | fromdateiso8601 | now - . | floor' 2>/dev/null || echo 9999)
  if [ "${age:-9999}" -lt 480 ]; then
    echo "$(stamp) skipped, last run ${age}s ago"
    exit 0
  fi
fi

gh workflow run publish.yml -R "$REPO" >/dev/null
echo "$(stamp) started publish"
