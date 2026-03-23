#!/bin/bash
# Simple WebUI launcher - WSL compatible

cd /home/ling/RTLHierDocor

# Load environment
if [ -f "env.sh" ]; then
    source env.sh
fi

echo "=== RTLHierDocor WebUI Launcher ==="
echo ""

# Check if already running
if lsof -Pi :8080 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Backend already running on port 8080"
else
    echo "Starting backend..."
    python3 -m uvicorn webui.server:app --host 0.0.0.0 --port 8080 --log-level info &
    sleep 3
fi

if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Frontend already running on port 3000"
else
    echo "Starting frontend..."
    cd webui
    npm run dev -- --host 0.0.0.0 &
    sleep 3
fi

echo ""
echo "WebUI should be available at:"
echo "  - http://localhost:3000 (from this WSL)"
echo "  - http://$(hostname -I | awk '{print $1}'):3000 (from Windows)"
echo ""
echo "Press Enter to stop all services"
read

# Kill processes
pkill -f "uvicorn webui.server:app" 2>/dev/null || true
pkill -f "npm run dev" 2>/dev/null || true
echo "Stopped."
