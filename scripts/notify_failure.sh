#!/usr/bin/env bash
set -euo pipefail

MESSAGE="⚠️ SWITCHBOARD ALERT: Daily cycle failed on $(hostname) at $(date) Pacific time. 30-day count reset to 0."
echo "$MESSAGE" >> /home/switchboard/switchboard/logs/failure.log
logger -t switchboard "$MESSAGE"

if [ -n "${FAILURE_ALERT_WEBHOOK_URL:-}" ]; then
    curl -s -X POST -H "Content-Type: application/json" \
         -d "{\"content\": \"$MESSAGE\"}" \
         "${FAILURE_ALERT_WEBHOOK_URL}" || true
fi
