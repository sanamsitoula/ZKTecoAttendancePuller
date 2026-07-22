#!/usr/bin/env bash
# ZKTeco Attendance Puller — web UI launcher for Ubuntu/Linux
# Usage: ./start_web.sh [port]
# If port is not given, defaults to 8097.

set -euo pipefail

# ── resolve project root ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${1:-8097}"

echo "=== ZKTeco Web UI ==="
echo "Project : $SCRIPT_DIR"
echo "Port    : $PORT"

# ── check virtual environment ───────────────────────────────────────────────
if [ ! -f ".venv/bin/activate" ]; then
    echo ""
    echo "ERROR: Virtual environment not found at .venv/"
    echo "Create it first:"
    echo "  python3 -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# ── check db_config.json ────────────────────────────────────────────────────
if [ ! -f "db_config.json" ]; then
    echo ""
    echo "ERROR: db_config.json not found."
    echo "Create it from the example:"
    echo "  cp db_config.json.example db_config.json"
    echo "  nano db_config.json"
    exit 1
fi

# ── check users.json ────────────────────────────────────────────────────────
if [ ! -f "users.json" ]; then
    echo ""
    echo "ERROR: users.json not found."
    echo "Create it from the example:"
    echo "  cp users.json.example users.json"
    echo "  nano users.json"
    exit 1
fi

# ── kill anything already on the port ───────────────────────────────────────
# Try fuser first (installed by default on Ubuntu), fall back to lsof/ss
if command -v fuser &>/dev/null; then
    fuser -k "${PORT}/tcp" 2>/dev/null && echo "Killed process on port $PORT" || true
elif command -v lsof &>/dev/null; then
    PIDS=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
    [ -n "$PIDS" ] && kill -9 $PIDS && echo "Killed PIDs: $PIDS" || true
fi

# ── activate venv ────────────────────────────────────────────────────────────
source .venv/bin/activate

# Prefer python3 if python is not available
PYTHON=$(command -v python || command -v python3)

# ── auto-attend scheduler (user 258) ────────────────────────────────────────
# Approved recurring check-in/check-out job for the configured employee
# (default: global user 258). Runs as a background scheduler alongside the
# web UI so it comes up automatically whenever this script is used to start
# the server. Config: auto_attend_config.json (copied from the .example on
# first run if missing).
if [ ! -f "auto_attend_config.json" ] && [ -f "auto_attend_config.json.example" ]; then
    cp auto_attend_config.json.example auto_attend_config.json
    echo "Created auto_attend_config.json from example (default user_id: 258)"
fi

if [ -f "auto_attend/auto_attend.py" ]; then
    if ! pgrep -f "auto_attend/auto_attend.py" >/dev/null 2>&1; then
        mkdir -p logs
        nohup "$PYTHON" auto_attend/auto_attend.py >> logs/auto_attend_nohup.log 2>&1 &
        disown
        echo "Auto-attend scheduler started (PID $!) — logs/auto_attend.log"
    else
        echo "Auto-attend scheduler already running."
    fi
fi

echo ""
echo "Python  : $($PYTHON --version)"
echo "Starting: http://localhost:$PORT"
echo ""

exec "$PYTHON" -m web.run_web --port "$PORT"
