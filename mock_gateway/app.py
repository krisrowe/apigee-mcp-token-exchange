"""
Generic Apigee Gateway Emulator
Emulates Apigee API Proxy behavior:
1. RFC 9728 Discovery challenge (HTTP 401 + WWW-Authenticate)
2. Tool execution flow with RFC 8693 Token Exchange
"""
from typing import Any, Dict
from fastapi import FastAPI, Header, HTTPException, Request, Response
import httpx
from jose import jwt

app = FastAPI(title="Apigee MCP Gateway", version="1.0.0")

SECRET_KEY = "enterprise-reference-secret-key-for-jwt"
ALGORITHM = "HS256"

STS_TOKEN_ENDPOINT = "http://localhost:8081/oauth2/v1/token"
EXTERNAL_MCP_BACKEND = "http://localhost:8082"

@app.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource_metadata(response: Response):
    response.status_code = 401
    response.headers["WWW-Authenticate"] = (
        'Bearer realm="enterprise-ai-gateway", '
        'authorization_uri="http://localhost:8081/oauth2/v1/authorize", '
        'token_uri="http://localhost:8081/oauth2/v1/token"'
    )
    return {
        "resource": "https://gateway.example.com/mcp/v1",
        "authorization_servers": ["http://localhost:8081"],
        "scopes_supported": ["mcp:tools:execute", "mcp:tools:read"],
        "bearer_methods_supported": ["header"],
        "documentation": "https://portal.example.com/docs/ai-gateway/mcp"
    }

async def execute_token_exchange(user_jwt: str, target_audience: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STS_TOKEN_ENDPOINT,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "enterprise-apigee-gateway",
                "subject_token": user_jwt,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "audience": target_audience,
                "scope": "tools:read tools:execute"
            },
            timeout=10.0
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Apigee ServiceCallout (RFC 8693 Token Exchange) failed: {resp.text}"
            )
        data = resp.json()
        return data.get("access_token")

@app.api_route("/mcp/v1/{path:path}", methods=["GET", "POST"])
async def apigee_proxy_route(path: str, request: Request, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization: Bearer <user_token>"
        )
    user_token = authorization.split(" ")[1]
    try:
        user_claims = jwt.decode(
            user_token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience="enterprise-api-gateway"
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Apigee VerifyJWT failed: {str(e)}")

    target_partner_aud = "https://mcp.partner-service.example.com"
    partner_token = await execute_token_exchange(user_token, target_partner_aud)

    outbound_headers = {
        "Authorization": f"Bearer {partner_token}",
        "Content-Type": "application/json",
        "X-Apigee-Proxy": "mcp-external-gateway-v1",
        "X-Correlation-Id": "trace-12345"
    }

    backend_url = f"{EXTERNAL_MCP_BACKEND}/mcp/v1/{path}"
    body = await request.body()

    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method=request.method,
            url=backend_url,
            headers=outbound_headers,
            content=body,
            timeout=30.0
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json")
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
