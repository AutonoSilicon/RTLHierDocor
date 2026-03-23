#!/bin/bash
# WebUI launcher for RTLHierDocor (WSL-compatible)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "[WebUI Launcher] Project directory: $PROJECT_DIR"

# Detect if running in WSL
IS_WSL=false
if grep -qE "(Microsoft|WSL)" /proc/version 2>/dev/null; then
    IS_WSL=true
    echo "[WebUI Launcher] WSL environment detected"
fi

# Check Python dependencies
echo "[WebUI Launcher] Checking Python dependencies..."
if ! python3 -c "import fastapi, uvicorn, websockets, pydantic" 2>/dev/null; then
    echo "[WebUI Launcher] Installing Python dependencies..."
    pip install fastapi uvicorn websockets pydantic
fi

# Check if webui dependencies are installed
if [ ! -d "$PROJECT_DIR/webui/node_modules" ]; then
    echo "[WebUI Launcher] Installing frontend dependencies..."
    cd "$PROJECT_DIR/webui"
    npm install
fi

# Function to cleanup processes on exit
cleanup() {
    echo "[WebUI Launcher] Shutting down..."
    if [ -n "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
    fi
    if [ -n "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
    fi
}
trap cleanup EXIT

# Start backend
echo "[WebUI Launcher] Starting backend server on 0.0.0.0:8080..."
cd "$PROJECT_DIR"
source env.sh 2>/dev/null || echo "[WebUI Launcher] Warning: env.sh not found or failed"

# Run backend in background and capture output
python3 -m webui.server > /tmp/webui-backend.log 2>&1 &
BACKEND_PID=$!
echo "[WebUI Launcher] Backend PID: $BACKEND_PID"

# Wait for backend to be ready
echo "[WebUI Launcher] Waiting for backend to start..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:8080/api/status > /dev/null 2>&1; then
        echo "[WebUI Launcher] Backend is ready!"
        break
    fi
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "[WebUI Launcher] Backend failed to start!"
        echo "[WebUI Launcher] Backend log:"
        cat /tmp/webui-backend.log
        exit 1
    fi
    sleep 1
done

# Check if backend actually started
if ! curl -s http://127.0.0.1:8080/api/status > /dev/null 2>&1; then
    echo "[WebUI Launcher] Backend failed to start within timeout!"
    echo "[WebUI Launcher] Backend log:"
    cat /tmp/webui-backend.log
    exit 1
fi

# Start frontend
echo "[WebUI Launcher] Starting frontend dev server..."
cd "$PROJECT_DIR/webui"

# In WSL, we need to bind to 0.0.0.0 to allow Windows browser access
npm run dev -- --host 0.0.0.0 > /tmp/webui-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "[WebUI Launcher] Frontend PID: $FRONTEND_PID"

# Wait for frontend
sleep 3

if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo "[WebUI Launcher] Frontend failed to start!"
    echo "[WebUI Launcher] Frontend log:"
    cat /tmp/webui-frontend.log
    exit 1
fi

echo ""
echo "=============================================="
echo "WebUI is running!"
echo ""
echo "Frontend: http://localhost:3000"
echo "Backend API: http://localhost:8080"
echo "WebSocket: ws://localhost:8080/ws"

if [ "$IS_WSL" = true ]; then
    # Get WSL IP for Windows access
    WSL_IP=$(hostname -I | awk '{print $1}')
    echo ""
    echo "WSL detected - You can also access from Windows at:"
    echo "  http://$WSL_IP:3000"
fi

echo ""
echo "Press Ctrl+C to stop"
echo "=============================================="
echo ""

# Wait for processes
wait
