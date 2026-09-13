#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "[SETUP.SH] Checking Python 3..."
if [ -n "${VIRTUAL_ENV:-}${CONDA_PREFIX:-}" ]; then
    PYTHON_BIN="$(command -v python || command -v python3)"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    echo "Error: Python is not installed or not in PATH."
    exit 1
fi

echo "[SETUP.SH] Dispatching setup.py..."
"$PYTHON_BIN" setup.py
echo "[SETUP.SH] Setup complete."
