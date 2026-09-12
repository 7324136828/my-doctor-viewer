#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -f ".venv/bin/activate" ]; then
    echo "[RUN.SH] Virtual environment not found. Running setup.sh first..."
    ./setup.sh
fi

if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

source .venv/bin/activate

echo "========================================================"
echo "Starting My Doctor Viewer Full-Stack Services"
echo "Backend:  http://localhost:8000 (API and Docs: /docs)"
echo "Frontend: http://localhost:5173"
echo "========================================================"

# Graceful cleanup on SIGINT / SIGTERM / EXIT
cleanup() {
    echo ""
    echo "[RUN.SH] Stopping background services..."
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
    echo "[RUN.SH] All services stopped."
}
trap cleanup SIGINT SIGTERM EXIT

# Start backend in background
(cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000) &

# Start frontend in background
(cd frontend && npm run dev) &

# Wait for processes
wait
