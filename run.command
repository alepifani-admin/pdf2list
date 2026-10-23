#!/usr/bin/env bash
# Avvio da sorgente su macOS/Linux (crea un venv dedicato la prima volta).
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.11 python3.12 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "Python 3.10-3.12 non trovato."
  exit 1
fi
if [ ! -d .venv-app ]; then
  "$PYTHON" -m venv .venv-app
  .venv-app/bin/python -m pip install --upgrade pip
  .venv-app/bin/python -m pip install -r requirements.txt
fi
exec .venv-app/bin/python -m pdf2list "$@"
