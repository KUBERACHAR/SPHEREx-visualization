#!/usr/bin/env sh
# Planet X - SPHEREx Explorer: one-command start on macOS / Linux.
# First run creates a private virtual environment (.venv) and installs 5 small packages.
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  PY=$(command -v python3 || command -v python || true)
  if [ -z "$PY" ]; then echo "Python 3.10+ is required: https://www.python.org/downloads/"; exit 1; fi
  echo "Creating virtual environment..."
  "$PY" -m venv .venv
fi

if ! .venv/bin/python -c "import astropy, numpy, fastapi, uvicorn, httpx" 2>/dev/null; then
  echo "Installing dependencies (one time)..."
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
fi

exec .venv/bin/python -m backend "$@"
