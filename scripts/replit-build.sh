#!/usr/bin/env bash
# Replit deployment build: python deps + Next.js production build.
set -euo pipefail
cd "$(dirname "$0")/.."

pip install -r api/requirements.txt

export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-/api}"
cd web
npm ci
npm run build   # prebuild copies the graph fixtures into web/public/
