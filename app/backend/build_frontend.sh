#!/bin/bash
# Render builds the Docker image from app/backend, so the screens must be built and copied in first.
# (Run before `docker build`, or let the Dockerfile's `frontend_dist` come from the repo.)
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../frontend"
npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund
npm run build
rm -rf "$HERE/frontend_dist"
cp -R dist "$HERE/frontend_dist"
echo "screens built into app/backend/frontend_dist"
