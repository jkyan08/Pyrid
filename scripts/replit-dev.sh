#!/usr/bin/env bash
# Replit workspace Run button: dev servers for API + web (first run installs deps).
set -euo pipefail
cd "$(dirname "$0")/.."

pip install -q -r api/requirements.txt
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload &

cd web
[ -d node_modules ] || npm ci
exec npx next dev -p "${PORT:-3000}" -H 0.0.0.0
