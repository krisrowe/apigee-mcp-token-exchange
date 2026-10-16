"""
Opt-in live integration test suite against a deployed Backend MCP Server.
Requires the MCP_BACKEND_URL environment variable to be set (e.g., https://your-service.run.app).
Excluded from default `pytest` runs via pyproject.toml `addopts = "--ignore=tests/integration"`.
"""
import os
import time
import pytest
import httpx
from jose import jwt

from mcp_server.server import SECRET_KEY, ALGORITHM, EXPECTED_AUDIENCE
from mock_idp.app import token_endpoint

BACKEND_URL = os.environ.get("MCP_BACKEND_URL", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not BACKEND_URL,
    reason="Set MCP_BACKEND_URL=<https://your-deployed-backend> to run live integration tests"
)


@pytest.fixture(scope="module")
def user_jwt() -> str:
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


@pytest.fixture(scope="module")
def exchanged_partner_jwt(user_jwt: str) -> str:
    res = token_endpoint(
        grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
        code=None,
        client_id="enterprise-apigee-gateway",
        client_secret=None,
        subject_token=user_jwt,
        subject_token_type="urn:ietf:params:oauth:token-type:access_token",
        audience=EXPECTED_AUDIENCE,
        scope="tools:read tools:execute"
    )
    return res["access_token"]


async def test_live_health_check_200():
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{BACKEND_URL}/")
        print(f"\n[LIVE GET /] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


async def test_live_negative_missing_auth_header_401():
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{BACKEND_URL}/mcp/v1/tools/list")
        print(f"\n[LIVE POST /mcp/v1/tools/list (No Auth Header)] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 401


async def test_live_negative_tampered_signature_401(exchanged_partner_jwt: str):
    tampered = exchanged_partner_jwt[:-6] + "tamper"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/mcp/v1/tools/list",
            headers={"Authorization": f"Bearer {tampered}"}
        )
        print(f"\n[LIVE POST /mcp/v1/tools/list (Tampered Signature)] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 401


async def test_live_negative_wrong_audience_401(user_jwt: str):
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
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/mcp/v1/tools/list",
            headers={"Authorization": f"Bearer {wrong_aud_token}"}
        )
        print(f"\n[LIVE POST /mcp/v1/tools/list (Wrong Audience)] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 401


async def test_live_negative_unexchanged_corporate_token_403():
    now = int(time.time())
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
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/mcp/v1/tools/list",
            headers={"Authorization": f"Bearer {unexchanged}"}
        )
        print(f"\n[LIVE POST /mcp/v1/tools/list (Unexchanged Corporate Token)] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 403


async def test_live_positive_tools_list_200(exchanged_partner_jwt: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/mcp/v1/tools/list",
            headers={"Authorization": f"Bearer {exchanged_partner_jwt}"},
            json={"jsonrpc": "2.0", "id": "live-list-1"}
        )
        print(f"\n[LIVE POST /mcp/v1/tools/list (Valid Exchanged Token)] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 200
        assert any(t["name"] == "search_catalog" for t in resp.json()["result"]["tools"])


async def test_live_positive_tools_call_search_catalog_200(exchanged_partner_jwt: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/mcp/v1/tools/call",
            headers={"Authorization": f"Bearer {exchanged_partner_jwt}"},
            json={
                "jsonrpc": "2.0",
                "id": "live-call-1",
                "params": {
                    "name": "search_catalog",
                    "arguments": {"query": "sparkling water", "limit": 3}
                }
            }
        )
        print(f"\n[LIVE POST /mcp/v1/tools/call (search_catalog)] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 200
        assert "Found 3 catalog results matching query: 'sparkling water'" in resp.json()["result"]["content"][0]["text"]


async def test_live_negative_unknown_tool_404(exchanged_partner_jwt: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{BACKEND_URL}/mcp/v1/tools/call",
            headers={"Authorization": f"Bearer {exchanged_partner_jwt}"},
            json={
                "jsonrpc": "2.0",
                "id": "live-call-err",
                "params": {
                    "name": "unknown_tool",
                    "arguments": {}
                }
            }
        )
        print(f"\n[LIVE POST /mcp/v1/tools/call (unknown_tool)] -> HTTP {resp.status_code} | {resp.json()}")
        assert resp.status_code == 404
