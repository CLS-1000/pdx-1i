#!/usr/bin/env bash
set -euo pipefail

echo "== 1) Force live mode in .env =="
sed -i 's/^PDX1_ENVIRONMENT=.*/PDX1_ENVIRONMENT=production/' .env || true
sed -i 's/^PDX1_LIVE=.*/PDX1_LIVE=true/' .env || true
grep -E '^PDX1_ENVIRONMENT=|^PDX1_LIVE=' .env

echo
echo "== 2) Run pipeline once (log saved) =="
python -m pdx1.pipeline 2>&1 | tee live-run.log

echo
echo "== 3) Short summary of failures =="
grep -E 'harvest .* ok=|adapter .* failed|run pdx1_|written|brief' live-run.log || true

echo
echo "== 4) Start API check (5s) =="
(timeout 5s pdx1-api >/tmp/pdx1-api.log 2>&1 || true)
grep -E 'PDX-1i API started' /tmp/pdx1-api.log || true

echo
echo "Done. Files:"
ls -lh pdx1_signals.jsonl pdx1.db 2>/dev/null || true
