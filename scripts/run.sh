#!/usr/bin/env bash
# Run the Still Counts server (phone app at /, board at /board) and the tests.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
case "${1:-serve}" in
  test)  .venv/bin/python -m pytest -q && node --test client/test/ ;;
  oracle) .venv/bin/python -m oracle.oracle --seed "${2:-0xA3}" --ops "${3:-240}" --perms "${4:-200}" ;;
  *)     # A fresh join token per run: the board's QR code carries it, and writes without it get a 401.
         # STILLCOUNTS_OPEN=1 turns that off (an isolated network, or a quick look).
         if [ -z "${STILLCOUNTS_OPEN:-}" ] && [ -z "${STILLCOUNTS_TOKEN:-}" ]; then
           STILLCOUNTS_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(9))')"; export STILLCOUNTS_TOKEN
         fi
         if [ -n "${STILLCOUNTS_TOKEN:-}" ]; then
           echo "Open the board on THIS laptop with its token: http://localhost:${PORT:-8000}/board?t=${STILLCOUNTS_TOKEN}"
         fi
         # HTTPS (needed for the phone app to be saved for offline use): STILLCOUNTS_TLS_CERT and STILLCOUNTS_TLS_KEY,
         # for example from `mkcert <laptop-ip> localhost`. See docs/DEMO.md.
         TLS=()
         if [ -n "${STILLCOUNTS_TLS_CERT:-}" ] && [ -n "${STILLCOUNTS_TLS_KEY:-}" ]; then
           TLS=(--ssl-certfile "$STILLCOUNTS_TLS_CERT" --ssl-keyfile "$STILLCOUNTS_TLS_KEY")
         fi
         .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}" ${TLS[@]+"${TLS[@]}"} ;;
esac
