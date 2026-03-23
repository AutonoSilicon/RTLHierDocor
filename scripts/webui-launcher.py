#!/usr/bin/env python3
"""Simple WebUI launcher with proper error handling."""

import subprocess
import sys
import time
import signal
import os
from pathlib import Path

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'

def log(msg, color=RESET):
    print(f"{color}{msg}{RESET}")

def check_port(port, host='127.0.0.1'):
    """Check if a port is in use."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

def start_backend():
    """Start the backend server."""
    log("Starting backend server...", YELLOW)

    # Change to project directory
    project_dir = Path(__file__).parent.parent
    os.chdir(project_dir)

    # Source env.sh if exists
    env_script = project_dir / "env.sh"
    if env_script.exists():
        log(f"Loading {env_script}")
        # Export env vars from env.sh
        result = subprocess.run(
            ["bash", "-c", f"source {env_script} && env"],
            capture_output=True,
            text=True
        )
        for line in result.stdout.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

    # Start uvicorn
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "webui.server:app", "--host", "0.0.0.0", "--port", "8080"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Wait for server to be ready
    for i in range(30):
        if proc.poll() is not None:
            # Process exited
            stdout, stderr = proc.communicate()
            log("Backend failed to start!", RED)
            log(f"stdout: {stdout}", RED)
            log(f"stderr: {stderr}", RED)
            return None

        if check_port(8080):
            log("Backend started successfully on http://localhost:8080", GREEN)
            return proc

        time.sleep(0.5)

    log("Backend startup timeout", RED)
    proc.terminate()
    return None

def start_frontend():
    """Start the frontend dev server."""
    log("Starting frontend dev server...", YELLOW)

    project_dir = Path(__file__).parent.parent
    webui_dir = project_dir / "webui"

    if not webui_dir.exists():
        log(f"webui directory not found: {webui_dir}", RED)
        return None

    # Check if node_modules exists
    if not (webui_dir / "node_modules").exists():
        log("node_modules not found. Running npm install...", YELLOW)
        result = subprocess.run(
            ["npm", "install"],
            cwd=webui_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            log(f"npm install failed: {result.stderr}", RED)
            return None

    # Start npm dev server
    proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "0.0.0.0"],
        cwd=webui_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # Wait for server to be ready
    for i in range(30):
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            log("Frontend failed to start!", RED)
            log(f"stdout: {stdout}", RED)
            log(f"stderr: {stderr}", RED)
            return None

        if check_port(3000):
            log("Frontend started successfully on http://localhost:3000", GREEN)
            return proc

        time.sleep(0.5)

    log("Frontend startup timeout", RED)
    proc.terminate()
    return None

def get_wsl_ip():
    """Get WSL IP address."""
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True
        )
        return result.stdout.strip().split()[0]
    except:
        return None

def main():
    log("=" * 60)
    log("RTLHierDocor WebUI Launcher")
    log("=" * 60)
    print()

    backend_proc = None
    frontend_proc = None

    def cleanup(sig=None, frame=None):
        log("\nShutting down...", YELLOW)
        if backend_proc:
            backend_proc.terminate()
            log("Backend stopped", GREEN)
        if frontend_proc:
            frontend_proc.terminate()
            log("Frontend stopped", GREEN)
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Check ports
    if check_port(8080):
        log("Port 8080 already in use (backend may already be running)", YELLOW)
    else:
        backend_proc = start_backend()
        if not backend_proc:
            sys.exit(1)

    time.sleep(1)

    if check_port(3000):
        log("Port 3000 already in use (frontend may already be running)", YELLOW)
    else:
        frontend_proc = start_frontend()
        if not frontend_proc:
            if backend_proc:
                backend_proc.terminate()
            sys.exit(1)

    print()
    log("=" * 60, GREEN)
    log("WebUI is running!", GREEN)
    log("=" * 60, GREEN)
    print()
    log("Frontend URLs:")
    log("  - http://localhost:3000 (WSL)")
    wsl_ip = get_wsl_ip()
    if wsl_ip:
        log(f"  - http://{wsl_ip}:3000 (Windows browser)")
    print()
    log("Backend URLs:")
    log("  - http://localhost:8080")
    log("  - ws://localhost:8080/ws (WebSocket)")
    print()
    log("Press Ctrl+C to stop")
    print()

    # Wait for processes
    try:
        while True:
            if backend_proc and backend_proc.poll() is not None:
                log("Backend exited unexpectedly", RED)
                break
            if frontend_proc and frontend_proc.poll() is not None:
                log("Frontend exited unexpectedly", RED)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()

if __name__ == "__main__":
    main()
