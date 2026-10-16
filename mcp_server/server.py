"""
Generic External MCP Server Mock
Requires:
- Authorization: Bearer <exchanged_partner_token>
- Verifies audience: matches either the service's own base URL or MCP_EXPECTED_AUDIENCE
- Optional /client-setup page controlled by CLIENT_SETUP_PAGE=OFF|SAFE|UNSAFE (default OFF)
"""
import collections
import os
import time
from typing import Any, Deque, Dict, List
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from jose import jwt

app = FastAPI(title="External Partner MCP Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_KEY = os.environ.get("MCP_JWT_SECRET", "enterprise-reference-secret-key-for-jwt")
ALGORITHM = "HS256"
EXPECTED_AUDIENCE = os.environ.get("MCP_EXPECTED_AUDIENCE", "https://mcp.partner-service.example.com")

# POC FinOps & Abuse Guardrails (Configurable via environment variables)
RATE_LIMIT_RPM = int(os.environ.get("MCP_RATE_LIMIT_RPM", "60"))
MAX_BODY_BYTES = int(os.environ.get("MCP_MAX_BODY_BYTES", "16384"))
_REQUEST_TIMESTAMPS: Deque[float] = collections.deque()


@app.middleware("http")
async def poc_abuse_guard_middleware(request: Request, call_next):
    # 1. Enforce max request payload size (default 16 KB)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Payload too large. POC limit is {MAX_BODY_BYTES} bytes."}
        )

    # 2. Enforce global sliding-window rate limit (default 60 req/min on single POC container)
    rpm_limit = int(os.environ.get("MCP_RATE_LIMIT_RPM", str(RATE_LIMIT_RPM)))
    if rpm_limit > 0:
        now = time.monotonic()
        while _REQUEST_TIMESTAMPS and (now - _REQUEST_TIMESTAMPS[0]) > 60.0:
            _REQUEST_TIMESTAMPS.popleft()
        if len(_REQUEST_TIMESTAMPS) >= rpm_limit:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60"},
                content={"detail": f"POC rate limit exceeded ({rpm_limit} requests/minute)."}
            )
        _REQUEST_TIMESTAMPS.append(now)

    return await call_next(request)


def get_public_base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}".rstrip("/")


def get_allowed_audiences(request: Request) -> List[str]:
    base_url = get_public_base_url(request)
    audiences = [EXPECTED_AUDIENCE]
    if base_url and base_url not in audiences:
        audiences.insert(0, base_url)
    return audiences


def verify_partner_token(request: Request, authorization: str = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected 'Bearer <token>'"
        )
    token = authorization.split(" ", 1)[1]
    allowed_audiences = get_allowed_audiences(request)

    try:
        # Decode without built-in single-string audience check so we can validate against allowed_audiences
        claims = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_aud": False}
        )
        token_aud = claims.get("aud")
        aud_list = [token_aud] if isinstance(token_aud, str) else (token_aud or [])
        if not any(a in allowed_audiences for a in aud_list):
            raise ValueError(f"Invalid audience '{token_aud}'. Expected one of: {allowed_audiences}")

        if claims.get("token_use") != "exchanged_partner_token":
            raise HTTPException(
                status_code=403,
                detail="Token is not an exchanged partner token. Direct corporate user tokens rejected."
            )
        return claims
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid partner token: {str(e)}")


@app.get("/")
@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "catalog-mcp-backend", "runtime": "cloud-run"}


@app.post("/mcp/v1/tools/list")
async def list_tools(request: Request, authorization: str = Header(None)):
    claims = verify_partner_token(request, authorization)
    return {
        "jsonrpc": "2.0",
        "id": "list-1",
        "result": {
            "tools": [
                {
                    "name": "search_catalog",
                    "description": "Search partner enterprise product catalog",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "limit": {"type": "integer", "default": 10}
                        },
                        "required": ["query"]
                    }
                }
            ],
            "_meta": {
                "caller_actor": claims.get("act", {}).get("sub"),
                "delegated_subject": claims.get("sub")
            }
        }
    }


@app.post("/mcp/v1/tools/call")
async def call_tool(request: Request, payload: Dict[str, Any], authorization: str = Header(None)):
    claims = verify_partner_token(request, authorization)
    tool_name = payload.get("params", {}).get("name")
    args = payload.get("params", {}).get("arguments", {})

    if tool_name == "search_catalog":
        query = args.get("query", "")
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id", "call-1"),
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": f"Found 3 catalog results matching query: '{query}'."
                    }
                ],
                "actor_context": {
                    "gateway": claims.get("act", {}).get("sub"),
                    "user_ref": claims.get("sub")
                }
            }
        }
    else:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")


@app.get("/client-setup", response_class=HTMLResponse)
async def client_setup_page(request: Request):
    mode = os.environ.get("CLIENT_SETUP_PAGE", "OFF").strip().upper()
    if mode not in ("SAFE", "UNSAFE"):
        raise HTTPException(
            status_code=404,
            detail="Client setup page is disabled. Set CLIENT_SETUP_PAGE=SAFE or UNSAFE to enable."
        )

    base_url = get_public_base_url(request)
    is_unsafe = (mode == "UNSAFE")
    displayed_secret = SECRET_KEY if is_unsafe else ""
    secret_badge = (
        f'<code style="color:#fbbf24;">{SECRET_KEY}</code> <span class="badge badge-warn">UNSAFE MODE: VISIBLE</span>'
        if is_unsafe
        else '<code style="color:#94a3b8;">&lt;REDACTED (CLIENT_SETUP_PAGE=SAFE)&gt;</code> <span class="badge badge-safe">SAFE MODE</span>'
    )

    sample_jwt = "<EXCHANGED_PARTNER_JWT>"
    if is_unsafe:
        now = int(time.time())
        sample_jwt = jwt.encode(
            {
                "iss": "https://identity.example.com/oauth2/v1",
                "sub": "actor:user_developer@example.com",
                "aud": base_url,
                "act": {"sub": "enterprise-apigee-gateway"},
                "scope": "tools:read tools:execute",
                "iat": now,
                "exp": now + 3600,
                "token_use": "exchanged_partner_token",
            },
            SECRET_KEY,
            algorithm=ALGORITHM,
        )

    warning_banner = ""
    if is_unsafe:
        warning_banner = """
        <div class="alert alert-warn">
          <strong>⚠️ Sandbox / POC Mode Active (<code>CLIENT_SETUP_PAGE=UNSAFE</code>):</strong>
          This page is currently exposing the active <code>HS256</code> JWT signing secret and pre-signed test tokens so gateway engineers can configure and test integration immediately.
          Set <code>CLIENT_SETUP_PAGE=SAFE</code> (to redact secrets) or <code>CLIENT_SETUP_PAGE=OFF</code> (to disable this route) before production use.
        </div>
        """
    else:
        warning_banner = """
        <div class="alert alert-info">
          <strong>🔒 Safe Setup Mode Active (<code>CLIENT_SETUP_PAGE=SAFE</code>):</strong>
          Public protocol contracts and endpoint configurations are shown below, while signing secrets are redacted on the server.
          To run the in-browser test suite below, enter the shared POC signing secret in the test panel (or set <code>CLIENT_SETUP_PAGE=UNSAFE</code> in sandbox environments).
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Mock Backend MCP Server for RFC 8693 Token Exchange ({base_url})</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --muted: #94a3b8;
      --primary: #38bdf8;
      --green: #10b981;
      --amber: #f59e0b;
      --red: #ef4444;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 28px;
      line-height: 1.5;
    }}
    .wrap {{ max-width: 1080px; margin: 0 auto; }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      border-bottom: 1px solid var(--border);
      padding-bottom: 16px;
      margin-bottom: 20px;
    }}
    h1 {{ font-size: 22px; margin: 0 0 6px; }}
    h2 {{ font-size: 16px; margin: 0 0 12px; color: var(--primary); }}
    .sub {{ color: var(--muted); font-size: 13.5px; margin: 0; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px;
      margin-bottom: 20px;
    }}
    .alert {{
      border-radius: 8px;
      padding: 12px 16px;
      margin-bottom: 20px;
      font-size: 13px;
    }}
    .alert-warn {{ background: rgba(245, 158, 11, 0.12); border: 1px solid var(--amber); color: #fde68a; }}
    .alert-info {{ background: rgba(56, 189, 248, 0.12); border: 1px solid var(--primary); color: #bae6fd; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 11px; width: 240px; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      background: #090d16;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 12.5px;
    }}
    pre {{
      background: #090d16;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      overflow-x: auto;
      font-size: 12px;
      margin: 8px 0 16px;
      color: #e2e8f0;
    }}
    .btn {{
      background: #0284c7;
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 9px 16px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }}
    .btn:hover {{ background: #0369a1; }}
    .btn-sec {{
      background: #334155;
      color: #e2e8f0;
      border: 1px solid #475569;
    }}
    .btn-sec:hover {{ background: #475569; }}
    .badge {{
      display: inline-block;
      font-size: 11px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 999px;
      margin-left: 6px;
    }}
    .badge-warn {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid var(--amber); }}
    .badge-safe {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid var(--green); }}
    .test-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 12px;
      background: #090d16;
      border: 1px solid var(--border);
      border-radius: 6px;
      margin-bottom: 6px;
      font-size: 12.5px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .status-pill {{
      padding: 2px 8px;
      border-radius: 4px;
      font-weight: 700;
      font-size: 11.5px;
    }}
    .pass {{ background: rgba(16, 185, 129, 0.2); color: #34d399; }}
    .fail {{ background: rgba(239, 68, 68, 0.2); color: #f87171; }}
    input[type="text"] {{
      background: #090d16;
      border: 1px solid var(--border);
      border-radius: 6px;
      color: #fff;
      padding: 8px 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12.5px;
    }}
    a {{ color: var(--primary); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="header">
      <div>
        <h1>Mock Backend MCP Server for RFC 8693 Token Exchange</h1>
        <p class="sub">
          Live Target Endpoint: <code>{base_url}</code> &middot;
          Part of the <a href="https://github.com/krisrowe/apigee-mcp-token-exchange" target="_blank">Enterprise Apigee MCP Gateway Reference Architecture</a>
        </p>
      </div>
      <div>
        <span class="badge {'badge-warn' if is_unsafe else 'badge-safe'}">CLIENT_SETUP_PAGE={mode}</span>
      </div>
    </div>

    {warning_banner}

    <div class="card">
      <h2>1. Synchronized Gateway &amp; Token Exchange Contract</h2>
      <p class="sub" style="margin-bottom: 12px;">
        Configure your Apigee API Proxy and RFC 8693 Security Token Service (STS) to emit outbound partner tokens matching these exact parameters:
      </p>
      <table>
        <tr>
          <th>Backend MCP Target URL</th>
          <td><code>{base_url}</code></td>
        </tr>
        <tr>
          <th>MCP Endpoints</th>
          <td>
            <code>POST {base_url}/mcp/v1/tools/list</code><br/>
            <code>POST {base_url}/mcp/v1/tools/call</code><br/>
            <code>GET {base_url}/health</code>
          </td>
        </tr>
        <tr>
          <th>Accepted Audience (<code>aud</code>)</th>
          <td>
            <code>{base_url}</code> <em>(Recommended: matches service URL per RFC 8707)</em><br/>
            <span style="color:var(--muted); font-size:12px;">Also accepts fallback: <code>{EXPECTED_AUDIENCE}</code></span>
          </td>
        </tr>
        <tr>
          <th>Required Claim (<code>token_use</code>)</th>
          <td><code>exchanged_partner_token</code> <span style="color:var(--muted); font-size:12px;">(Unexchanged <code>user_identity</code> tokens are rejected with <code>HTTP 403</code>)</span></td>
        </tr>
        <tr>
          <th>Signing Algorithm &amp; Secret</th>
          <td><code>{ALGORITHM}</code> &middot; Secret: {secret_badge}</td>
        </tr>
      </table>
    </div>

    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
        <div>
          <h2 style="margin:0;">2. Live Browser Verification Suite (Same-Origin POST Back to This Server)</h2>
          <p class="sub">Executes all 8 positive (<code>200</code>) and negative (<code>401</code>, <code>403</code>, <code>404</code>) assertions directly against <code>{base_url}</code>.</p>
        </div>
        <button class="btn" onclick="runAllTests()">&#9654; Run All 8 Live Tests</button>
      </div>

      <div style="display:flex; gap:12px; align-items:center; margin-bottom:14px;">
        <label style="font-size:12px; color:var(--muted); font-weight:600;">Signing Secret for Test Token Minting:</label>
        <input id="secretInput" type="text" value="{displayed_secret}" oninput="updateCurlToken()" placeholder="Enter MCP_JWT_SECRET to mint test tokens..." style="flex:1;" />
        <input id="queryInput" type="text" value="organic apples" style="width:180px;" title="Search query for tools/call" />
        <button class="btn btn-sec" onclick="runSingleToolCall()">Invoke <code>search_catalog</code></button>
      </div>

      <div id="testResults"></div>
      <pre id="testLog" style="margin-top:12px; max-height:280px; overflow-y:auto;">// Click "▶ Run All 8 Live Tests" above to verify positive and negative responses against {base_url}</pre>
    </div>

    <div class="card">
      <h2>3. Apigee Proxy Configuration Snippets</h2>
      <p class="sub"><strong>Outbound TargetEndpoint (<code>apiproxy/targets/default.xml</code>):</strong></p>
      <pre>&lt;TargetEndpoint name="default"&gt;
    &lt;PreFlow name="PreFlow"&gt;
        &lt;Request&gt;
            &lt;Step&gt;&lt;Name&gt;AM-SetPartnerAuthHeader&lt;/Name&gt;&lt;/Step&gt;
        &lt;/Request&gt;
    &lt;/PreFlow&gt;
    &lt;HTTPTargetConnection&gt;
        &lt;URL&gt;{base_url}&lt;/URL&gt;
    &lt;/HTTPTargetConnection&gt;
&lt;/TargetEndpoint&gt;</pre>

      <p class="sub"><strong>CLI Verification (from cloned repository):</strong></p>
      <pre>git clone https://github.com/krisrowe/apigee-mcp-token-exchange.git
cd apigee-mcp-token-exchange
./verify {base_url} --signing-key {SECRET_KEY if is_unsafe else '<YOUR_MCP_JWT_SECRET>'}</pre>

      <p class="sub"><strong>Direct <code>curl</code> Verification:</strong></p>
      <pre># 1. Negative Test: Missing Authorization Header (Expected: HTTP 401)
curl -i -X POST {base_url}/mcp/v1/tools/list \\
  -H "Content-Type: application/json" \\
  -d '{{"jsonrpc": "2.0", "id": "1"}}'

# 2. Positive Test: Execute search_catalog with Exchanged Partner Token (Expected: HTTP 200)
curl -i -X POST {base_url}/mcp/v1/tools/call \\
  -H "Authorization: Bearer <span id="curlJwtToken">{sample_jwt}</span>" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "jsonrpc": "2.0",
    "id": "call-1",
    "params": {{
      "name": "search_catalog",
      "arguments": {{"query": "organic apples", "limit": 3}}
    }}
  }}'</pre>
    </div>
  </div>

  <script>
    const BASE_URL = window.location.origin;

    function b64url(uint8) {{
      let bin = '';
      for (let i = 0; i < uint8.length; i++) bin += String.fromCharCode(uint8[i]);
      return btoa(bin).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
    }}

    function strToB64url(str) {{
      return b64url(new TextEncoder().encode(str));
    }}

    async function signJwtHS256(payload, secret) {{
      const header = {{ alg: 'HS256', typ: 'JWT' }};
      const encHeader = strToB64url(JSON.stringify(header));
      const encPayload = strToB64url(JSON.stringify(payload));
      const signingInput = `${{encHeader}}.${{encPayload}}`;
      const key = await crypto.subtle.importKey(
        'raw',
        new TextEncoder().encode(secret),
        {{ name: 'HMAC', hash: 'SHA-256' }},
        false,
        ['sign']
      );
      const sigBuf = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(signingInput));
      return `${{signingInput}}.${{b64url(new Uint8Array(sigBuf))}}`;
    }}

    async function updateCurlToken() {{
      const secret = document.getElementById('secretInput').value.trim();
      const span = document.getElementById('curlJwtToken');
      if (!span) return;
      if (!secret) {{
        span.textContent = '<EXCHANGED_PARTNER_JWT>';
        return;
      }}
      const now = Math.floor(Date.now() / 1000);
      span.textContent = await signJwtHS256({{
        iss: 'https://identity.example.com/oauth2/v1',
        sub: 'actor:user_developer@example.com',
        aud: BASE_URL,
        act: {{ sub: 'enterprise-apigee-gateway' }},
        scope: 'tools:read tools:execute',
        iat: now,
        exp: now + 3600,
        token_use: 'exchanged_partner_token'
      }}, secret);
    }}

    async function runAllTests() {{
      const secret = document.getElementById('secretInput').value.trim();
      const resultsEl = document.getElementById('testResults');
      const logEl = document.getElementById('testLog');
      resultsEl.innerHTML = '';
      logEl.textContent = '';

      if (!secret) {{
        logEl.textContent = 'Please enter the signing secret in the input box above (or set CLIENT_SETUP_PAGE=UNSAFE on the server) to mint test tokens.';
        return;
      }}

      const now = Math.floor(Date.now() / 1000);
      const validPartnerToken = await signJwtHS256({{
        iss: 'https://identity.example.com/oauth2/v1',
        sub: 'actor:user_developer@example.com',
        aud: BASE_URL,
        act: {{ sub: 'enterprise-apigee-gateway' }},
        scope: 'tools:read tools:execute',
        iat: now,
        exp: now + 900,
        token_use: 'exchanged_partner_token'
      }}, secret);

      const unexchangedUserToken = await signJwtHS256({{
        iss: 'https://identity.example.com/oauth2/v1',
        sub: 'developer@example.com',
        aud: BASE_URL,
        iat: now,
        exp: now + 3600,
        token_use: 'user_identity'
      }}, secret);

      const wrongAudToken = await signJwtHS256({{
        iss: 'https://identity.example.com/oauth2/v1',
        sub: 'actor:user_developer@example.com',
        aud: 'https://wrong-audience.example.com',
        iat: now,
        exp: now + 900,
        token_use: 'exchanged_partner_token'
      }}, secret);

      const tamperedToken = validPartnerToken.slice(0, -6) + 'tamper';
      const query = document.getElementById('queryInput').value || 'organic apples';

      const tests = [
        {{
          name: '1. GET /health (Unauthenticated Health Probe)',
          expected: 200,
          run: () => fetch(`${{BASE_URL}}/health`)
        }},
        {{
          name: '2. POST /mcp/v1/tools/list (Missing Authorization Header)',
          expected: 401,
          run: () => fetch(`${{BASE_URL}}/mcp/v1/tools/list`, {{ method: 'POST' }})
        }},
        {{
          name: '3. POST /mcp/v1/tools/list (Tampered Signature)',
          expected: 401,
          run: () => fetch(`${{BASE_URL}}/mcp/v1/tools/list`, {{
            method: 'POST',
            headers: {{ 'Authorization': `Bearer ${{tamperedToken}}` }}
          }})
        }},
        {{
          name: '4. POST /mcp/v1/tools/list (Wrong Audience Claim)',
          expected: 401,
          run: () => fetch(`${{BASE_URL}}/mcp/v1/tools/list`, {{
            method: 'POST',
            headers: {{ 'Authorization': `Bearer ${{wrongAudToken}}` }}
          }})
        }},
        {{
          name: '5. POST /mcp/v1/tools/list (Unexchanged Corporate Token -> Zero-PII Boundary)',
          expected: 403,
          run: () => fetch(`${{BASE_URL}}/mcp/v1/tools/list`, {{
            method: 'POST',
            headers: {{ 'Authorization': `Bearer ${{unexchangedUserToken}}` }}
          }})
        }},
        {{
          name: '6. POST /mcp/v1/tools/list (Valid Exchanged Partner Token)',
          expected: 200,
          run: () => fetch(`${{BASE_URL}}/mcp/v1/tools/list`, {{
            method: 'POST',
            headers: {{ 'Authorization': `Bearer ${{validPartnerToken}}`, 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ jsonrpc: '2.0', id: 'list-1' }})
          }})
        }},
        {{
          name: `7. POST /mcp/v1/tools/call (search_catalog: "${{query}}")`,
          expected: 200,
          run: () => fetch(`${{BASE_URL}}/mcp/v1/tools/call`, {{
            method: 'POST',
            headers: {{ 'Authorization': `Bearer ${{validPartnerToken}}`, 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              jsonrpc: '2.0',
              id: 'call-1',
              params: {{ name: 'search_catalog', arguments: {{ query, limit: 3 }} }}
            }})
          }})
        }},
        {{
          name: '8. POST /mcp/v1/tools/call (Unknown Tool Name)',
          expected: 404,
          run: () => fetch(`${{BASE_URL}}/mcp/v1/tools/call`, {{
            method: 'POST',
            headers: {{ 'Authorization': `Bearer ${{validPartnerToken}}`, 'Content-Type': 'application/json' }},
            body: JSON.stringify({{
              jsonrpc: '2.0',
              id: 'call-err',
              params: {{ name: 'unknown_tool', arguments: {{}} }}
            }})
          }})
        }}
      ];

      let passedCount = 0;
      for (const t of tests) {{
        const res = await t.run();
        const body = await res.json();
        const ok = (res.status === t.expected);
        if (ok) passedCount++;

        const row = document.createElement('div');
        row.className = 'test-row';
        row.innerHTML = `
          <span>${{t.name}}</span>
          <span class="status-pill ${{ok ? 'pass' : 'fail'}}">
            HTTP ${{res.status}} (Expected ${{t.expected}}) ${{ok ? '✓ PASS' : '✗ FAIL'}}
          </span>
        `;
        resultsEl.appendChild(row);
        logEl.textContent += `[${{t.name}}] -> HTTP ${{res.status}}\\n${{JSON.stringify(body)}}\\n\\n`;
      }}
      logEl.textContent = `=== SUMMARY: ${{passedCount}} / ${{tests.length}} LIVE ASSERTIONS PASSED ===\\n\\n` + logEl.textContent;
    }}

    async function runSingleToolCall() {{
      const secret = document.getElementById('secretInput').value.trim();
      const logEl = document.getElementById('testLog');
      if (!secret) {{
        logEl.textContent = 'Please enter the signing secret in the input box above to mint a partner token.';
        return;
      }}
      const now = Math.floor(Date.now() / 1000);
      const query = document.getElementById('queryInput').value || 'organic apples';
      const token = await signJwtHS256({{
        iss: 'https://identity.example.com/oauth2/v1',
        sub: 'actor:user_developer@example.com',
        aud: BASE_URL,
        act: {{ sub: 'enterprise-apigee-gateway' }},
        scope: 'tools:read tools:execute',
        iat: now,
        exp: now + 900,
        token_use: 'exchanged_partner_token'
      }}, secret);

      const res = await fetch(`${{BASE_URL}}/mcp/v1/tools/call`, {{
        method: 'POST',
        headers: {{ 'Authorization': `Bearer ${{token}}`, 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          jsonrpc: '2.0',
          id: 'interactive-call-1',
          params: {{ name: 'search_catalog', arguments: {{ query, limit: 3 }} }}
        }})
      }});
      const data = await res.json();
      logEl.textContent = `[POST ${{BASE_URL}}/mcp/v1/tools/call] -> HTTP ${{res.status}}\\n` + JSON.stringify(data, null, 2);
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082)
