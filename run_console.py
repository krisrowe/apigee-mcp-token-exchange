#!/usr/bin/env python3
"""
Enterprise Apigee MCP Gateway — Interactive Studio Runner
Spins up the 3 local mock services on loopback ports:
- Gateway Emulator: http://127.0.0.1:8080
- Enterprise STS:   http://127.0.0.1:8081
- SaaS MCP Backend: http://127.0.0.1:8082

And automatically opens studio.html in the default system browser.
"""
import os
import sys
import time
import socket
import signal
import subprocess
import webbrowser
from multiprocessing import Process
import uvicorn
from fastapi.middleware.cors import CORSMiddleware

from mock_idp.app import app as idp_app
from mock_gateway.app import app as gw_app
from mcp_server.server import app as mcp_app

# Enable CORS for local browser console
for app_instance in [idp_app, gw_app, mcp_app]:
    app_instance.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

def free_ports(ports=(8080, 8081, 8082)):
    """Idempotently terminates any lingering processes listening on the required ports."""
    cleaned = False
    for port in ports:
        try:
            out = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True).strip()
            if out:
                pids = [int(p) for p in out.split() if p.isdigit()]
                for pid in pids:
                    if pid != os.getpid():
                        print(f"  • Port {port} occupied by PID {pid}. Cleaning up...")
                        cleaned = True
                        try:
                            os.kill(pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    if cleaned:
        time.sleep(0.5)

    # Force kill if still lingering
    for port in ports:
        try:
            out = subprocess.check_output(["lsof", "-ti", f":{port}"], text=True).strip()
            if out:
                pids = [int(p) for p in out.split() if p.isdigit()]
                for pid in pids:
                    if pid != os.getpid():
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

    # Verify ports are released
    for port in ports:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) != 0:
                    break
            time.sleep(0.1)

def run_idp():
    uvicorn.run(idp_app, host="127.0.0.1", port=8081, log_level="warning")

def run_gw():
    uvicorn.run(gw_app, host="127.0.0.1", port=8080, log_level="warning")

def run_mcp():
    uvicorn.run(mcp_app, host="127.0.0.1", port=8082, log_level="warning")

def main():
    print("Preparing local environment...")
    free_ports([8080, 8081, 8082])

    p_idp = Process(target=run_idp, daemon=True)
    p_gw = Process(target=run_gw, daemon=True)
    p_mcp = Process(target=run_mcp, daemon=True)

    p_idp.start()
    p_gw.start()
    p_mcp.start()

    # Wait for services to bind and validate HTTP responses
    import urllib.request
    import urllib.error

    endpoints = [
        ("Apigee Gateway Emulator (:8080)", "http://127.0.0.1:8080/.well-known/oauth-protected-resource", "GET"),
        ("Enterprise STS / IdP   (:8081)", "http://127.0.0.1:8081/.well-known/openid-configuration", "GET"),
        ("SaaS MCP Backend       (:8082)", "http://127.0.0.1:8082/mcp/v1/tools/list", "POST"),
    ]

    print("\nValidating service HTTP endpoints...")
    time.sleep(0.5)
    for name, url, method in endpoints:
        deadline = time.time() + 6.0
        verified = False
        while time.time() < deadline:
            try:
                req = urllib.request.Request(url, method=method)
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    print(f"  ✓ {name}: HTTP {resp.status} OK")
                    verified = True
                    break
            except urllib.error.HTTPError as e:
                if e.code in [401, 403]:
                    print(f"  ✓ {name}: HTTP {e.code} (Active Security Challenge)")
                    verified = True
                    break
                time.sleep(0.2)
            except Exception:
                time.sleep(0.2)

        if not verified:
            print(f"  ✗ {name}: Service did not respond within timeout.")

    console_path = os.path.abspath("console.html")
    console_url = f"file://{console_path}"
    print(f"\n🚀 Opening Interactive Test Console in browser: {console_url}")
    webbrowser.open(console_url)
    print("\nPress Ctrl+C to stop all services.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down console services...")
        p_idp.terminate()
        p_gw.terminate()
        p_mcp.terminate()
        p_idp.join(timeout=1)
        p_gw.join(timeout=1)
        p_mcp.join(timeout=1)
        free_ports([8080, 8081, 8082])
        print("Done.")

if __name__ == "__main__":
    main()
