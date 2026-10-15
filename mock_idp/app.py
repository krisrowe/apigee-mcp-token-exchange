"""
Generic Enterprise STS (Security Token Service) Mock
Implements:
1. /.well-known/openid-configuration
2. /oauth2/v1/keys (JWKS)
3. /oauth2/v1/token (Client Credentials and RFC 8693 Token Exchange)
"""
import time
from typing import Optional
from fastapi import FastAPI, Form, HTTPException, Response
from jose import jwt

app = FastAPI(title="Enterprise STS Mock", version="1.0.0")

SECRET_KEY = "enterprise-reference-secret-key-for-jwt"
ALGORITHM = "HS256"

AUTH_CODES = {}

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
@app.get("/.well-known/jwks.json")
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

@app.get("/oauth2/v1/authorize", response_class=Response)
def authorize_page(
    client_id: str = "enterprise-ai-agent",
    redirect_uri: str = "http://localhost:9999/callback",
    state: str = "init-state",
    scope: str = "openid profile mcp:tools:execute"
):
    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <title>Corporate Identity Provider — Single Sign-On</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 10px; width: 400px; padding: 28px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
    .logo {{ font-size: 18px; font-weight: 700; color: #38bdf8; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
    h2 {{ font-size: 20px; margin: 0 0 8px; }}
    p {{ color: #94a3b8; font-size: 13.5px; margin: 0 0 20px; }}
    .field {{ margin-bottom: 16px; }}
    label {{ display: block; font-size: 12px; font-weight: 600; text-transform: uppercase; color: #94a3b8; margin-bottom: 6px; }}
    input {{ width: 100%; box-sizing: border-box; background: #0f172a; border: 1px solid #475569; border-radius: 6px; padding: 10px 12px; color: #fff; font-size: 14px; }}
    .btn {{ width: 100%; background: #0284c7; color: #fff; font-weight: 600; padding: 12px; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; margin-top: 8px; }}
    .btn:hover {{ background: #0369a1; }}
    .badge {{ background: #334155; color: #cbd5e1; font-size: 11px; padding: 3px 8px; border-radius: 4px; font-family: monospace; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">🔒 Corporate Identity Provider (SSO)</div>
    <h2>Sign In to Developer Tools</h2>
    <p>Client application: <span class="badge">{client_id}</span></p>
    <form method="POST" action="/oauth2/v1/authorize">
      <input type="hidden" name="client_id" value="{client_id}" />
      <input type="hidden" name="redirect_uri" value="{redirect_uri}" />
      <input type="hidden" name="state" value="{state}" />
      <input type="hidden" name="scope" value="{scope}" />
      <div class="field">
        <label>Corporate Username / Email</label>
        <input type="text" name="username" value="developer@example.com" required />
      </div>
      <div class="field">
        <label>Password</label>
        <input type="password" name="password" value="demo123" required />
      </div>
      <button type="submit" class="btn">Authenticate with SSO</button>
    </form>
  </div>
</body>
</html>"""
    return Response(content=html_content, media_type="text/html")

@app.post("/oauth2/v1/authorize")
def process_authorize(
    username: str = Form(...),
    password: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    scope: str = Form("mcp:tools:execute")
):
    import uuid
    code = f"code_{uuid.uuid4().hex[:12]}"
    AUTH_CODES[code] = {
        "username": username,
        "client_id": client_id,
        "scope": scope
    }
    target = f"{redirect_uri}?code={code}&state={state}"
    return Response(
        status_code=303,
        headers={"Location": target}
    )

@app.get("/oauth2/v1/callback", response_class=Response)
def callback_page(code: str, state: str = ""):
    html = f"""<!DOCTYPE html>
<html>
<body>
  <script>
    if (window.opener) {{
      window.opener.postMessage({{ type: 'sso_auth_code', code: '{code}' }}, '*');
      window.close();
    }} else if (window.parent && window.parent !== window) {{
      window.parent.postMessage({{ type: 'sso_auth_code', code: '{code}' }}, '*');
    }} else {{
      document.body.innerHTML = '<h3>Authentication successful! You can close this window.</h3>';
    }}
  </script>
</body>
</html>"""
    return Response(content=html, media_type="text/html")

@app.post("/oauth2/v1/token")
def token_endpoint(
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    client_secret: Optional[str] = Form(None),
    subject_token: Optional[str] = Form(None),
    subject_token_type: Optional[str] = Form(None),
    audience: Optional[str] = Form(None),
    scope: Optional[str] = Form("mcp:tools:execute")
):
    now = int(time.time())

    # --- GRANT 0: Authorization Code (Interactive User SSO Login) ---
    if grant_type == "authorization_code":
        auth_code = code or subject_token
        auth_data = AUTH_CODES.pop(auth_code, None) if auth_code else None
        user_email = auth_data["username"] if auth_data else "developer@example.com"
        payload = {
            "iss": "http://localhost:8081",
            "sub": user_email,
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

    # --- GRANT 1: Client Credentials / User Token ---
    elif grant_type == "client_credentials":
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
