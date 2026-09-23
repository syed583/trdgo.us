#!/usr/bin/env bash
# Pull, rebuild, restart. Run from the checkout root.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> pulling"
git pull --ff-only

echo "==> python dependencies"
backend/.venv/bin/pip install -q -r backend/requirements.txt

echo "==> frontend"
(cd frontend && npm ci --silent && npm run build)

echo "==> tests"
(cd backend && .venv/bin/python -m pytest -q)

echo "==> restarting"
sudo systemctl restart us-stock-reader
sleep 2
systemctl is-active --quiet us-stock-reader \
  && echo "running" \
  || { echo "failed to start:"; journalctl -u us-stock-reader -n 30 --no-pager; exit 1; }
