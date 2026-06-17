#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

PORT=8097

# Kill any process already listening on the port
PIDS=$(lsof -ti tcp:"$PORT" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "Killing existing process(es) on port $PORT: $PIDS"
    kill -9 $PIDS
fi

# Activate virtual environment
source .venv/bin/activate

echo "Starting ZKTeco Web UI on http://localhost:$PORT"
python -m web.run_web --port "$PORT"
