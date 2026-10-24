#!/usr/bin/env bash
# Run the Still Counts server (phone app at /, board at /board) and the tests.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q pytest pytest-cov hypothesis fastapi uvicorn requests httpx jsonschema
case "${1:-serve}" in
  test)  .venv/bin/python -m pytest -q && node --test client/test/ ;;
  oracle) .venv/bin/python -m oracle.oracle --seed "${2:-0xA3}" --ops "${3:-240}" --perms "${4:-200}" ;;
  *)     .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}" ;;
esac
