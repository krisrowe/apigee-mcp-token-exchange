"""
Generic External MCP Server Mock
Requires:
- Authorization: Bearer <exchanged_partner_token>
- Verifies audience: aud="https://mcp.partner-service.example.com"
"""
from typing import Any, Dict
from fastapi import FastAPI, Header, HTTPException, Request
from jose import jwt

app = FastAPI(title="External Partner MCP Server", version="1.0.0")

SECRET_KEY = "enterprise-reference-secret-key-for-jwt"
ALGORITHM = "HS256"
EXPECTED_AUDIENCE = "https://mcp.partner-service.example.com"

def verify_partner_token(authorization: str = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected 'Bearer <token>'"
        )
    token = authorization.split(" ")[1]
    try:
        claims = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            audience=EXPECTED_AUDIENCE
        )
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

@app.post("/mcp/v1/tools/list")
async def list_tools(request: Request, authorization: str = Header(None)):
    claims = verify_partner_token(authorization)
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
async def call_tool(payload: Dict[str, Any], authorization: str = Header(None)):
    claims = verify_partner_token(authorization)
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8082)
