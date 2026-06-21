#!/usr/bin/env bash
# Boot the GST reconciliation backend (FastAPI, :8011) and frontend (Next.js, :3000).
# First run sets up the Python venv, installs deps and generates sample files.
set -euo pipefail
cd "$(dirname "$0")"

BACKEND_PORT=8011
FRONTEND_PORT=3000

echo "==> Backend setup (port $BACKEND_PORT)"
cd backend
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi
if [ ! -f sample_data/sample_gst_portal.xlsx ]; then
  ./.venv/bin/python scripts/generate_samples.py
fi
./.venv/bin/uvicorn app.main:app --reload --port "$BACKEND_PORT" &
BACKEND_PID=$!
cd ..

# Stop the backend when this script exits.
trap 'echo; echo "Shutting down..."; kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM

echo "==> Frontend setup (port $FRONTEND_PORT)"
cd frontend
if [ ! -d node_modules ]; then
  npm install
fi

echo
echo "Backend:  http://localhost:$BACKEND_PORT"
echo "Frontend: http://localhost:$FRONTEND_PORT"
echo
npm run dev
