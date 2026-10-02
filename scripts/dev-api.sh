#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x .venv/bin/python ]]; then
  exec .venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
fi

exec python3 -m uvicorn api.main:app --host 127.0.0.1 --port 8000
