#!/usr/bin/env bash
# Replit deployment run: FastAPI internal on :8000, Next.js on the exposed port.
# Next rewrites /api/* -> 127.0.0.1:8000 (web/next.config.ts), so the browser
# only ever talks to one origin. If the API dies, the web app's static-fallback
# mode keeps the demo alive (README "fallback mode").
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uvicorn >/dev/null 2>&1 || pip install -r api/requirements.txt
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 &

cd web
exec npx next start -p "${PORT:-3000}" -H 0.0.0.0
