#!/usr/bin/env python3
"""Diagnostic script for WebUI connectivity issues."""

import sys
import socket
import subprocess
from pathlib import Path

def check_port(host, port, timeout=2):
    """Check if a port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception as e:
        return False

def main():
    print("=" * 60)
    print("RTLHierDocor WebUI Diagnostics")
    print("=" * 60)
    print()

    # Check Python version
    print(f"Python version: {sys.version}")
    print()

    # Check dependencies
    print("Checking dependencies...")
    deps = ['fastapi', 'uvicorn', 'websockets', 'pydantic']
    for dep in deps:
        try:
            __import__(dep)
            print(f"  ✓ {dep} installed")
        except ImportError:
            print(f"  ✗ {dep} NOT installed - run: pip install fastapi uvicorn websockets pydantic")
    print()

    # Check ports
    print("Checking ports...")
    backend_running = check_port('127.0.0.1', 8080)
    frontend_running = check_port('127.0.0.1', 3000)

    print(f"  Backend (127.0.0.1:8080): {'✓ Running' if backend_running else '✗ Not reachable'}")
    print(f"  Frontend (127.0.0.1:3000): {'✓ Running' if frontend_running else '✗ Not reachable'}")
    print()

    # Try to identify WSL IP
    try:
        result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
        ips = result.stdout.strip().split()
        print(f"WSL IP addresses: {', '.join(ips)}")
        print()
    except Exception:
        pass

    # Recommendations
    if not backend_running:
        print("Backend is not running. To start it:")
        print("  1. source env.sh")
        print("  2. python3 -m webui.server")
        print()

    if not frontend_running:
        print("Frontend is not running. To start it:")
        print("  1. cd webui")
        print("  2. npm install  (if not done)")
        print("  3. npm run dev")
        print()

    if backend_running and frontend_running:
        print("Both services appear to be running.")
        print("If you're still having issues:")
        print("  1. Check browser console for CORS errors")
        print("  2. Try accessing http://localhost:3000 from Windows browser")
        print("  3. Or use WSL browser: http://127.0.0.1:3000")
        print()

    print("=" * 60)

if __name__ == '__main__':
    main()
