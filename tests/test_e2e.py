"""
End-to-End Test Suite for Generic Apigee MCP Token Exchange
"""
import pytest
import httpx
from multiprocessing import Process
import time
import uvicorn

from mock_idp.app import app as idp_app
from mcp_server.server import app as mcp_app
from mock_gateway.app import app as gateway_app

def run_idp():
    uvicorn.run(idp_app, host="127.0.0.1", port=8081, log_level="warning")

def run_mcp():
    uvicorn.run(mcp_app, host="127.0.0.1", port=8082, log_level="warning")

def run_gateway():
    uvicorn.run(gateway_app, host="127.0.0.1", port=8080, log_level="warning")

@pytest.fixture(scope="session", autouse=True)
def setup_services():
    p_idp = Process(target=run_idp, daemon=True)
    p_mcp = Process(target=run_mcp, daemon=True)
    p_gw = Process(target=run_gateway, daemon=True)

    p_idp.start()
    p_mcp.start()
    p_gw.start()

    time.sleep(1.5)
    yield

    p_gw.terminate()
    p_mcp.terminate()
    p_idp.terminate()

@pytest.mark.asyncio
async def test_rfc_9728_discovery():
    async with httpx.AsyncClient() as client:
        resp = await client.get("http://localhost:8080/.well-known/oauth-protected-resource")
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert "authorization_uri" in resp.headers["WWW-Authenticate"]

@pytest.mark.asyncio
async def test_token_exchange_flow():
    async with httpx.AsyncClient() as client:
        # 1. Get User JWT
        token_resp = await client.post(
            "http://localhost:8081/oauth2/v1/token",
            data={"grant_type": "client_credentials"}
        )
        assert token_resp.status_code == 200
        user_jwt = token_resp.json()["access_token"]

        # 2. List tools through Gateway
        list_resp = await client.post(
            "http://localhost:8080/mcp/v1/tools/list",
            headers={"Authorization": f"Bearer {user_jwt}"}
        )
        assert list_resp.status_code == 200
        tools = list_resp.json()["result"]["tools"]
        assert any(t["name"] == "search_catalog" for t in tools)

        # 3. Call tool through Gateway
        call_resp = await client.post(
            "http://localhost:8080/mcp/v1/tools/call",
            headers={"Authorization": f"Bearer {user_jwt}"},
            json={
                "jsonrpc": "2.0",
                "id": "req-1",
                "params": {
                    "name": "search_catalog",
                    "arguments": {"query": "sample-item"}
                }
            }
        )
        assert call_resp.status_code == 200
        res = call_resp.json()["result"]
        assert "Found 3 catalog results" in res["content"][0]["text"]
