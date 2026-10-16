"""
Sociable unit tests for the Apigee MCP Token Exchange protocol flow.
Runs completely in-process via httpx.ASGITransport with zero port bindings or network access.
"""
import pytest
import httpx

from mock_idp.app import app as idp_app, token_endpoint
from mcp_server.server import app as mcp_app
from mock_gateway.app import app as gateway_app


@pytest.fixture
def idp_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=idp_app),
        base_url="http://localhost:8081"
    )


@pytest.fixture
def mcp_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp_app),
        base_url="http://localhost:8082"
    )


@pytest.fixture
def gateway_client():
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway_app),
        base_url="http://localhost:8080"
    )


@pytest.fixture
def user_jwt() -> str:
    """Issue a real corporate user JWT using the IdP token endpoint."""
    res = token_endpoint(
        grant_type="client_credentials",
        code=None,
        client_id="enterprise-ai-agent",
        client_secret=None,
        subject_token=None,
        subject_token_type=None,
        audience=None,
        scope="mcp:tools:execute"
    )
    return res["access_token"]


@pytest.fixture
def exchanged_partner_jwt(user_jwt: str) -> str:
    """Perform a real RFC 8693 Token Exchange using the IdP token endpoint."""
    res = token_endpoint(
        grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
        code=None,
        client_id="enterprise-apigee-gateway",
        client_secret=None,
        subject_token=user_jwt,
        subject_token_type="urn:ietf:params:oauth:token-type:access_token",
        audience="https://mcp.partner-service.example.com",
        scope="tools:read tools:execute"
    )
    return res["access_token"]


async def test_health_check_returns_200(mcp_client: httpx.AsyncClient):
    resp = await mcp_client.get("/")
    print(f"\n[GET /] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_rfc_9728_discovery_returns_401_challenge(gateway_client: httpx.AsyncClient):
    resp = await gateway_client.get("/.well-known/oauth-protected-resource")
    print(f"\n[GET /.well-known/oauth-protected-resource] -> HTTP {resp.status_code} | WWW-Authenticate: {resp.headers.get('WWW-Authenticate')}")
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers
    assert "authorization_uri" in resp.headers["WWW-Authenticate"]


async def test_backend_rejects_missing_auth_header_with_401(mcp_client: httpx.AsyncClient):
    resp = await mcp_client.post("/mcp/v1/tools/list")
    print(f"\n[POST /mcp/v1/tools/list (No Auth Header)] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 401
    assert "Missing or malformed Authorization header" in resp.json()["detail"]


async def test_backend_rejects_tampered_signature_with_401(mcp_client: httpx.AsyncClient, exchanged_partner_jwt: str):
    tampered = exchanged_partner_jwt[:-6] + "tamper"
    resp = await mcp_client.post(
        "/mcp/v1/tools/list",
        headers={"Authorization": f"Bearer {tampered}"}
    )
    print(f"\n[POST /mcp/v1/tools/list (Tampered Signature)] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 401
    assert "Invalid partner token" in resp.json()["detail"]


async def test_backend_rejects_wrong_audience_with_401(mcp_client: httpx.AsyncClient, user_jwt: str):
    wrong_aud_token = token_endpoint(
        grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
        code=None,
        client_id="enterprise-apigee-gateway",
        client_secret=None,
        subject_token=user_jwt,
        subject_token_type="urn:ietf:params:oauth:token-type:access_token",
        audience="https://wrong-audience.example.com",
        scope="tools:read"
    )["access_token"]
    resp = await mcp_client.post(
        "/mcp/v1/tools/list",
        headers={"Authorization": f"Bearer {wrong_aud_token}"}
    )
    print(f"\n[POST /mcp/v1/tools/list (Wrong Audience)] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 401
    assert "Invalid partner token" in resp.json()["detail"]


async def test_backend_rejects_unexchanged_corporate_user_token_with_403(mcp_client: httpx.AsyncClient):
    from jose import jwt
    from mcp_server.server import SECRET_KEY, ALGORITHM, EXPECTED_AUDIENCE
    import time

    now = int(time.time())
    # Token has valid signature and expected audience, but token_use is 'user_identity' (unexchanged)
    unexchanged = jwt.encode(
        {
            "iss": "http://localhost:8081",
            "sub": "developer@example.com",
            "aud": EXPECTED_AUDIENCE,
            "iat": now,
            "exp": now + 3600,
            "token_use": "user_identity"
        },
        SECRET_KEY,
        algorithm=ALGORITHM
    )
    resp = await mcp_client.post(
        "/mcp/v1/tools/list",
        headers={"Authorization": f"Bearer {unexchanged}"}
    )
    print(f"\n[POST /mcp/v1/tools/list (Unexchanged Corporate Token)] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 403
    assert "Direct corporate user tokens rejected" in resp.json()["detail"]


async def test_backend_accepts_exchanged_partner_token_tools_list_200(mcp_client: httpx.AsyncClient, exchanged_partner_jwt: str):
    resp = await mcp_client.post(
        "/mcp/v1/tools/list",
        headers={"Authorization": f"Bearer {exchanged_partner_jwt}"},
        json={"jsonrpc": "2.0", "id": "list-1"}
    )
    print(f"\n[POST /mcp/v1/tools/list (Valid Exchanged Partner Token)] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert any(t["name"] == "search_catalog" for t in tools)
    assert resp.json()["result"]["_meta"]["caller_actor"] == "enterprise-apigee-gateway"


async def test_backend_executes_search_catalog_tool_call_200(mcp_client: httpx.AsyncClient, exchanged_partner_jwt: str):
    resp = await mcp_client.post(
        "/mcp/v1/tools/call",
        headers={"Authorization": f"Bearer {exchanged_partner_jwt}"},
        json={
            "jsonrpc": "2.0",
            "id": "call-1",
            "params": {
                "name": "search_catalog",
                "arguments": {"query": "organic apples", "limit": 3}
            }
        }
    )
    print(f"\n[POST /mcp/v1/tools/call (search_catalog)] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert "Found 3 catalog results matching query: 'organic apples'" in result["content"][0]["text"]
    assert result["actor_context"]["gateway"] == "enterprise-apigee-gateway"


async def test_backend_rejects_unknown_tool_with_404(mcp_client: httpx.AsyncClient, exchanged_partner_jwt: str):
    resp = await mcp_client.post(
        "/mcp/v1/tools/call",
        headers={"Authorization": f"Bearer {exchanged_partner_jwt}"},
        json={
            "jsonrpc": "2.0",
            "id": "call-err",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {}
            }
        }
    )
    print(f"\n[POST /mcp/v1/tools/call (nonexistent_tool)] -> HTTP {resp.status_code} | {resp.json()}")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


async def test_backend_accepts_own_base_url_as_audience(mcp_client: httpx.AsyncClient, user_jwt: str):
    """RFC 8707: Backend accepts its own base URL ('http://localhost:8082') as the 'aud' claim."""
    self_aud_jwt = token_endpoint(
        grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
        code=None,
        client_id="enterprise-apigee-gateway",
        client_secret=None,
        subject_token=user_jwt,
        subject_token_type="urn:ietf:params:oauth:token-type:access_token",
        audience="http://localhost:8082",
        scope="tools:read tools:execute"
    )["access_token"]
    resp = await mcp_client.post(
        "/mcp/v1/tools/list",
        headers={"Authorization": f"Bearer {self_aud_jwt}"},
        json={"jsonrpc": "2.0", "id": "list-self-aud"}
    )
    print(f"\n[POST /mcp/v1/tools/list (aud=self base_url)] -> HTTP {resp.status_code}")
    assert resp.status_code == 200


async def test_client_setup_page_modes(mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from mcp_server.server import SECRET_KEY

    # 1. Default OFF -> 404
    monkeypatch.delenv("CLIENT_SETUP_PAGE", raising=False)
    resp_off = await mcp_client.get("/client-setup")
    print(f"\n[GET /client-setup (CLIENT_SETUP_PAGE=OFF)] -> HTTP {resp_off.status_code}")
    assert resp_off.status_code == 404

    # 2. SAFE -> 200, secret redacted
    monkeypatch.setenv("CLIENT_SETUP_PAGE", "SAFE")
    resp_safe = await mcp_client.get("/client-setup")
    print(f"\n[GET /client-setup (CLIENT_SETUP_PAGE=SAFE)] -> HTTP {resp_safe.status_code} (Secret Redacted)")
    assert resp_safe.status_code == 200
    assert "REDACTED" in resp_safe.text
    assert SECRET_KEY not in resp_safe.text

    # 3. UNSAFE -> 200, secret visible for POC setup
    monkeypatch.setenv("CLIENT_SETUP_PAGE", "UNSAFE")
    resp_unsafe = await mcp_client.get("/client-setup")
    print(f"\n[GET /client-setup (CLIENT_SETUP_PAGE=UNSAFE)] -> HTTP {resp_unsafe.status_code} (Secret Visible)")
    assert resp_unsafe.status_code == 200
    assert SECRET_KEY in resp_unsafe.text


async def test_poc_rate_limit_and_payload_guards(mcp_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch):
    from mcp_server.server import _REQUEST_TIMESTAMPS

    # 1. Payload size guard (> 16 KB -> 413)
    huge_payload = "x" * 20000
    resp_413 = await mcp_client.post(
        "/mcp/v1/tools/list",
        content=huge_payload,
        headers={"Content-Type": "application/json", "Content-Length": str(len(huge_payload))}
    )
    print(f"\n[POST /mcp/v1/tools/list (>16KB Payload)] -> HTTP {resp_413.status_code} | {resp_413.json()}")
    assert resp_413.status_code == 413

    # 2. Rate limit guard (MCP_RATE_LIMIT_RPM=2 -> 3rd call returns 429)
    _REQUEST_TIMESTAMPS.clear()
    monkeypatch.setenv("MCP_RATE_LIMIT_RPM", "2")
    r1 = await mcp_client.get("/health")
    r2 = await mcp_client.get("/health")
    r3 = await mcp_client.get("/health")
    print(f"\n[GET /health (Rate Limit Exceeded)] -> HTTP {r3.status_code} | {r3.json()}")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429
    _REQUEST_TIMESTAMPS.clear()


