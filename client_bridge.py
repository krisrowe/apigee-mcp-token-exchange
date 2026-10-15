#!/usr/bin/env python3
"""
Enterprise MCP Gateway Client Bridge (Generic Reference)
Enables CLI agents (Antigravity CLI, Claude Code) and IDEs (Cursor, VS Code)
to connect to an Apigee-protected MCP Gateway over standard stdio JSON-RPC.

Workflow:
1. Verifies or acquires corporate SSO user token (opens browser if needed).
2. Speaks standard Model Context Protocol (MCP) JSON-RPC over stdio.
3. Forwards tool calls to the Apigee Gateway with Authorization: Bearer <user_jwt>.
4. Apigee performs in-flight RFC 8693 token exchange with STS and proxies to SaaS partner.
"""
import sys
import json
import os
import webbrowser
import http.server
import socketserver
import urllib.parse
import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8080")
IDP_URL = os.environ.get("IDP_URL", "http://127.0.0.1:8081")
TOKEN_CACHE_FILE = os.path.expanduser("~/.cache/enterprise_mcp_user_token.json")

def get_cached_token():
    if os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE, "r") as f:
                data = json.load(f)
                return data.get("access_token")
        except Exception:
            return None
    return None

def save_token(token_data):
    os.makedirs(os.path.dirname(TOKEN_CACHE_FILE), exist_ok=True)
    with open(TOKEN_CACHE_FILE, "w") as f:
        json.dump(token_data, f)

def acquire_token_via_sso():
    auth_code_container = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                auth_code_container["code"] = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;padding:40px;background:#0f172a;color:#f8fafc;'><h2>&#x2705; Authentication Successful!</h2><p>Your AI assistant is now authenticated.<br>You may close this window and return to your terminal / IDE.</p></body></html>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    port = 9999
    server = socketserver.TCPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 60

    auth_url = f"{IDP_URL}/oauth2/v1/authorize?client_id=enterprise-ai-agent&redirect_uri=http://localhost:{port}/callback"
    sys.stderr.write(f"\n[Enterprise MCP Bridge] 🔒 Corporate SSO Required.\n[Enterprise MCP Bridge] Opening browser: {auth_url}\n")
    webbrowser.open(auth_url)

    while "code" not in auth_code_container:
        server.handle_request()

    server.server_close()
    code = auth_code_container.get("code")
    if not code:
        raise RuntimeError("Failed to acquire authorization code from SSO.")

    resp = httpx.post(
        f"{IDP_URL}/oauth2/v1/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "enterprise-ai-agent"
        },
        timeout=10.0
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {resp.text}")

    token_data = resp.json()
    save_token(token_data)
    sys.stderr.write("[Enterprise MCP Bridge] ✅ Corporate User JWT acquired and cached.\n\n")
    return token_data.get("access_token")

def ensure_token():
    token = get_cached_token()
    if not token:
        token = acquire_token_via_sso()
    return token

def handle_jsonrpc(req: dict, token: str) -> dict:
    method = req.get("method")
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "enterprise-apigee-mcp-gateway",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "notifications/initialized":
        return None

    elif method == "tools/list":
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        resp = httpx.post(f"{GATEWAY_URL}/mcp/v1/tools/list", headers=headers, timeout=15.0)
        if resp.status_code != 200:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"Gateway error: {resp.text}"}
            }
        data = resp.json()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": data.get("result", {})
        }

    elif method == "tools/call":
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        resp = httpx.post(f"{GATEWAY_URL}/mcp/v1/tools/call", headers=headers, json=req, timeout=30.0)
        if resp.status_code != 200:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"Gateway call error: {resp.text}"}
            }
        data = resp.json()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": data.get("result", {})
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        }

def main():
    token = ensure_token()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            res = handle_jsonrpc(req, token)
            if res is not None:
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()
        except Exception as e:
            sys.stderr.write(f"Bridge error: {str(e)}\n")

if __name__ == "__main__":
    main()
