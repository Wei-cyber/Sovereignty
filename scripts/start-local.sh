#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then cp .env.example .env; fi
uv sync --frozen
uv run python -m backend.manage migrate
printf '%s\n' 'Start the frontend separately: cd frontend && npm ci && npm run dev'
printf '%s\n' 'First use: configure your API key and run python -m backend.manage bootstrap.'
exec uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
