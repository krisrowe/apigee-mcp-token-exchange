"""
Generic Enterprise STS (Security Token Service) Mock
Implements:
1. /.well-known/openid-configuration
2. /oauth2/v1/keys (JWKS)
3. /oauth2/v1/token (Client Credentials and RFC 8693 Token Exchange)
"""
import time
from typing import Optional
from fastapi import FastAPI, Form, HTTPException
from jose import jwt

app = FastAPI(title="Enterprise STS Mock", version="1.0.0")

SECRET_KEY = "enterprise-reference-secret-key-for-jwt"
ALGORITHM = "HS256"

@app.get("/.well-known/openid-configuration")
def openid_configuration():
    return {
        "issuer": "http://localhost:8081",
        "authorization_endpoint": "http://localhost:8081/oauth2/v1/authorize",
        "token_endpoint": "http://localhost:8081/oauth2/v1/token",
        "jwks_uri": "http://localhost:8081/oauth2/v1/keys",
        "grant_types_supported": [
            "authorization_code",
            "client_credentials",
            "urn:ietf:params:oauth:grant-type:token-exchange"
        ]
    }

@app.get("/oauth2/v1/keys")
def jwks():
    return {
        "keys": [
            {
                "kty": "oct",
                "alg": ALGORITHM,
                "kid": "mock-key-1",
                "use": "sig"
            }
        ]
    }

@app.post("/oauth2/v1/token")
def token_endpoint(
    grant_type: str = Form(...),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    subject_token: Optional[str] = Form(None),
    subject_token_type: Optional[str] = Form(None),
    audience: Optional[str] = Form(None),
    scope: Optional[str] = Form("mcp:tools:execute")
):
    now = int(time.time())

    # --- GRANT 1: Client Credentials / User Token ---
    if grant_type == "client_credentials":
        payload = {
            "iss": "http://localhost:8081",
            "sub": "user_developer@example.com",
            "aud": "enterprise-api-gateway",
            "groups": ["engineering", "ai-assistants"],
            "iat": now,
            "exp": now + 3600,
            "token_use": "user_identity"
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": scope
        }

    # --- GRANT 2: RFC 8693 OAuth 2.0 Token Exchange ---
    elif grant_type == "urn:ietf:params:oauth:grant-type:token-exchange":
        if not subject_token:
            raise HTTPException(status_code=400, detail="Missing subject_token")

        try:
            user_claims = jwt.decode(subject_token, SECRET_KEY, algorithms=[ALGORITHM], audience="enterprise-api-gateway")
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Invalid subject_token: {str(e)}")

        target_aud = audience or "https://mcp.partner-service.example.com"
        exchanged_payload = {
            "iss": "http://localhost:8081",
            "sub": f"actor:{user_claims.get('sub')}",
            "aud": target_aud,
            "act": {
                "sub": "enterprise-apigee-gateway"
            },
            "scope": "tools:read tools:execute",
            "iat": now,
            "exp": now + 900,
            "token_use": "exchanged_partner_token"
        }
        exchanged_token = jwt.encode(exchanged_payload, SECRET_KEY, algorithm=ALGORITHM)

        return {
            "access_token": exchanged_token,
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "token_type": "Bearer",
            "expires_in": 900,
            "scope": "tools:read tools:execute"
        }

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported grant_type: {grant_type}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
