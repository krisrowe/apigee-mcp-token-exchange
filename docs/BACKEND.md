# Backend MCP Server Integration Guide

> **Part of the [Enterprise Apigee MCP Gateway Reference Architecture](../README.md):**
> In the full end-to-end architecture, an internal developer IDE authenticates with the corporate Identity Provider (IdP) and sends requests to **Apigee**, which performs an **[IETF RFC 8693 OAuth 2.0 Token Exchange](https://datatracker.ietf.org/doc/html/rfc8693)** and forwards a down-scoped partner token to this backend service.
>
> **Using This Document Standalone:**
> If the Backend MCP Server has **already been deployed** and you have been provided its HTTPS URL (`<BACKEND_MCP_URL>`), you can use this document directly as your integration specification to wire up your API Gateway, verify positive/negative security behavior, and execute MCP tools.
> *(Need to deploy your own instance first? See the optional [Cloud Run Deployment Guide](CLOUD-RUN.md) or run locally via the [Main README](../README.md#quickstart-testing-live-verification--client-setup-portal).)*

---

## Quick Start: If You Already Have a Deployed `<BACKEND_MCP_URL>`

If someone shared a live Backend MCP URL with you (for example, `https://your-mcp-backend.example.com`), you can immediately verify or integrate with it in four ways:

1. **Open the Hosted Client & Gateway Setup Page (`/client-setup`):**
   If enabled on the deployment (`CLIENT_SETUP_PAGE=SAFE` or `UNSAFE`), open **`<BACKEND_MCP_URL>/client-setup`** directly in your browser. It displays the exact synchronized configuration for that live instance (URL, accepted `aud`, `token_use`, copy-paste Apigee XML snippets) and includes an **in-browser Live Verification Suite** that posts all 8 positive and negative test cases directly to the server.
2. **Run the Automated Live Verification CLI (`./verify`):**
   Clone this repository and run `./verify <BACKEND_MCP_URL>` from the root directory (it will save the URL in your local XDG cache so future runs only require `./verify`):
   ```bash
   git clone https://github.com/krisrowe/apigee-mcp-token-exchange.git
   cd apigee-mcp-token-exchange
   ./verify <BACKEND_MCP_URL>
   ```
3. **Wire Your Apigee Proxy Target directly to `<BACKEND_MCP_URL>`:**
   Jump to **[Section 3: Configuring the Apigee Proxy Target](#3-configuring-the-apigee-proxy-target)** for the exact `HTTPTargetConnection` and header requirements.
4. **Test with Standalone `curl` Commands (Zero Repo Clone Required):**
   Jump to **[Section 4B: Manual `curl` Verification](#option-b-manual-curl-verification)** to test the live endpoints directly from any terminal.

---

## 1. Where This Service Fits in the Architecture

```mermaid
flowchart LR
    IDE["Developer IDE / AI Agent"] -- "1. Corporate User JWT\n(aud: enterprise-api-gateway)" --> Apigee["Apigee API Gateway"]
    Apigee -- "2. RFC 8693 Token Exchange" --> IdP["Enterprise IdP / STS"]
    IdP -- "3. Issues Partner Token\n(token_use: exchanged_partner_token)" --> Apigee
    Apigee -- "4. POST /mcp/v1/tools/*\nAuthorization: Bearer <partner_token>" --> Backend["Backend MCP Server\n(<BACKEND_MCP_URL>)"]
```

The backend MCP server enforces a strict **Zero-PII Partner Boundary**:
- It **never** accepts direct corporate employee tokens (`token_use: "user_identity"`).
- Every MCP tool request must carry an **RFC 8693 Exchanged Partner Token** issued for the target partner audience (`https://mcp.partner-service.example.com`).

---

## 2. Security & Token Verification Contract

### Expected JWT Claims

| Claim | Required Value / Format | Enforcement Behavior |
| :--- | :--- | :--- |
| **`Authorization` Header** | `Bearer <exchanged_partner_jwt>` | Missing or non-Bearer header returns **`HTTP 401 Unauthorized`**. |
| **`aud` (Audience)** | `<BACKEND_MCP_URL>` *(or `https://mcp.partner-service.example.com`)* | Matches the service's own base URL per [RFC 8707](https://datatracker.ietf.org/doc/html/rfc8707) (or `MCP_EXPECTED_AUDIENCE`). Mismatched audience or invalid signature returns **`HTTP 401 Unauthorized`**. |
| **`token_use`** | `exchanged_partner_token` | Any other value (e.g., `user_identity` from an unexchanged corporate SSO token) returns **`HTTP 403 Forbidden`**. |
| **`sub` (Subject)** | Pseudonymous or delegated actor reference (e.g., `actor:user_developer@example.com`) | Recorded in `_meta.delegated_subject` and `actor_context.user_ref`. |
| **`act` (Actor)** | `{"sub": "enterprise-apigee-gateway"}` | Recorded in `_meta.caller_actor` and `actor_context.gateway`. |

> **Reference Signing Key (Demo / Test Harness):**
> By default, the reference implementation validates HS256 signatures using `SECRET_KEY = "enterprise-reference-secret-key-for-jwt"` (defined in [`mcp_server/server.py`](../mcp_server/server.py) and matched by [`mock_idp/app.py`](../mock_idp/app.py)). In production with Okta, Entra ID, or PingFederate, replace this with your IdP's asymmetric RS256 JWKS endpoint.

### HTTP Endpoints & Status Code Matrix

Base URL placeholder: **`<BACKEND_MCP_URL>`** (for example, `https://your-mcp-backend.example.com`)

| Endpoint | Method | Auth Required | Scenario | Expected HTTP Status |
| :--- | :--- | :--- | :--- | :--- |
| `/` or `/health` | `GET` | No | Liveness / readiness probe | **`200 OK`** |
| `/client-setup` | `GET` | No | Interactive Client & Gateway Setup Page (`CLIENT_SETUP_PAGE=SAFE\|UNSAFE`; disabled `404` when `OFF`) | **`200 OK`** *(or `404`)* |
| `/mcp/v1/tools/list` | `POST` | Yes (`Bearer`) | Valid exchanged partner token (`token_use="exchanged_partner_token"`) | **`200 OK`** |
| `/mcp/v1/tools/call` | `POST` | Yes (`Bearer`) | Valid exchanged partner token + `name="search_catalog"` | **`200 OK`** |
| `/mcp/v1/tools/list` | `POST` | Yes (`Bearer`) | Missing `Authorization` header | **`401 Unauthorized`** |
| `/mcp/v1/tools/list` | `POST` | Yes (`Bearer`) | Tampered JWT signature or expired token | **`401 Unauthorized`** |
| `/mcp/v1/tools/list` | `POST` | Yes (`Bearer`) | Wrong `aud` claim (not `https://mcp.partner-service.example.com`) | **`401 Unauthorized`** |
| `/mcp/v1/tools/list` | `POST` | Yes (`Bearer`) | Unexchanged corporate user token (`token_use="user_identity"`) | **`403 Forbidden`** |
| `/mcp/v1/tools/call` | `POST` | Yes (`Bearer`) | Valid exchanged partner token + unknown tool name | **`404 Not Found`** |

---

## 3. Configuring the Apigee Proxy Target

To connect your Apigee API Proxy to the deployed backend at `<BACKEND_MCP_URL>`:

1. **In your Apigee Token Exchange policy ([`apiproxy/policies/SC-RFC8693-TokenExchange.xml`](../apiproxy/policies/SC-RFC8693-TokenExchange.xml)):**
   Ensure the requested `audience` parameter matches `https://mcp.partner-service.example.com` and the issued token includes `token_use: "exchanged_partner_token"`.
2. **In your Apigee AssignMessage policy ([`apiproxy/policies/AM-SetPartnerAuthHeader.xml`](../apiproxy/policies/AM-SetPartnerAuthHeader.xml)):**
   Set `Authorization: Bearer {exchanged_partner_token}` and strip any internal employee headers.
3. **In your Apigee TargetEndpoint ([`apiproxy/targets/default.xml`](../apiproxy/targets/default.xml)):**
   Point `<URL>` inside `<HTTPTargetConnection>` to `<BACKEND_MCP_URL>`:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<TargetEndpoint name="default">
    <Description>Outbound Partner SaaS MCP Server</Description>
    <PreFlow name="PreFlow">
        <Request>
            <!-- Injects exchanged partner token & strips corporate employee headers -->
            <Step>
                <Name>AM-SetPartnerAuthHeader</Name>
            </Step>
        </Request>
        <Response/>
    </PreFlow>
    <HTTPTargetConnection>
        <URL>https://your-mcp-backend.example.com</URL>
    </HTTPTargetConnection>
</TargetEndpoint>
```

You can also point the interactive visual protocol console ([`console.html`](../console.html)) directly at your live backend using the `?backend=` query parameter:

```text
console.html?backend=https://your-mcp-backend.example.com
```

---

## 4. Verifying a Live Backend Deployment

### Option A: Automated Positive & Negative Verification CLI (`./verify`)

The repository includes a standalone executable [`./verify`](../verify) CLI script at the project root that runs all 9 positive (`200`) and negative (`401`, `403`, `404`) HTTP checks against your deployed backend URL, prints the `/client-setup` URL and full request/response details for each call, and caches the URL (and optional `--signing-key`) in `~/.cache/apigee-mcp-token-exchange/context.json` for subsequent runs:

```bash
# First run: pass your Backend MCP URL (or omit it to be prompted interactively)
./verify https://your-mcp-backend.example.com

# Optional: if deployed with a custom MCP_JWT_SECRET, pass --signing-key once (also cached)
./verify https://your-mcp-backend.example.com --signing-key <YOUR_MCP_JWT_SECRET>

# Subsequent runs: automatically reuses your cached URL and signing key
./verify
```

*(For `pytest` integration suites, you can also run `.venv/bin/pytest tests/integration`, which automatically reads the cached URL or `MCP_BACKEND_URL`.)*

**Expected Output (`9/9 LIVE CHECKS PASSED` across positive and negative HTTP assertions):**
```text
====================================================================================
APIGEE MCP BACKEND - LIVE ENDPOINT VERIFICATION
====================================================================================
Target Backend URL     : https://your-mcp-backend.example.com (command-line argument (saved to cache))
Client Setup Portal    : https://your-mcp-backend.example.com/client-setup
Health Check Endpoint  : https://your-mcp-backend.example.com/health
Cached Context File    : ~/.cache/apigee-mcp-token-exchange/context.json
Active Signing Secret  : enterprise-reference-secret-key-for-jwt (repo POC default)
------------------------------------------------------------------------------------

[1/9] Health Probe (Unauthenticated Liveness)
  Invoked  : GET https://your-mcp-backend.example.com/health
  Auth     : None (public health probe)
  Status   : HTTP 200 (expected 200) -> [PASS]
  Response : {"status": "ok", "service": "catalog-mcp-backend", "runtime": "cloud-run"}

[2/9] Negative: Missing Authorization Header
  Invoked  : POST https://your-mcp-backend.example.com/mcp/v1/tools/list
  Auth     : None (omitted Authorization header)
  Status   : HTTP 401 (expected 401) -> [PASS]
  Response : {"detail": "Missing or malformed Authorization header. Expected 'Bearer <token>'"}
...
[7/9] Positive: Execute MCP Tool Call (search_catalog: 'sparkling water')
  Invoked  : POST https://your-mcp-backend.example.com/mcp/v1/tools/call
  Auth     : Bearer <exchanged_partner_token, aud='https://your-mcp-backend.example.com'>
  Status   : HTTP 200 (expected 200) -> [PASS]
  Response : {"jsonrpc": "2.0", "id": "pos-call-1", "result": {"content": [{"type": "text", "text": "Found 3 catalog results matching query: 'sparkling water'."}], "actor_context": {"gateway": "enterprise-apigee-gateway", "user_ref": "actor:user_developer@example.com"}}}
...
====================================================================================
RESULT: 9/9 LIVE CHECKS PASSED AGAINST https://your-mcp-backend.example.com
====================================================================================
  * Open Client & Gateway Setup Page : https://your-mcp-backend.example.com/client-setup
  * Re-run this live verification    : ./verify
  * Switch target backend URL        : ./verify https://new-url.example.com
====================================================================================
```

### Option B: Manual `curl` Verification

1. **Health Check (`HTTP 200`):**
   ```bash
   curl -i https://your-mcp-backend.example.com/
   ```

2. **Negative Test: Missing Authorization Header (`HTTP 401`):**
   ```bash
   curl -i -X POST https://your-mcp-backend.example.com/mcp/v1/tools/list \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc": "2.0", "id": "1"}'
   ```

3. **Positive Test: List Tools with Exchanged Partner Token (`HTTP 200`):**
   ```bash
   curl -i -X POST https://your-mcp-backend.example.com/mcp/v1/tools/list \
     -H "Authorization: Bearer <EXCHANGED_PARTNER_JWT>" \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc": "2.0", "id": "list-1"}'
   ```

4. **Positive Test: Execute `search_catalog` Tool Call (`HTTP 200`):**
   ```bash
   curl -i -X POST https://your-mcp-backend.example.com/mcp/v1/tools/call \
     -H "Authorization: Bearer <EXCHANGED_PARTNER_JWT>" \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0",
       "id": "call-1",
       "params": {
         "name": "search_catalog",
         "arguments": {"query": "organic apples", "limit": 3}
       }
     }'
   ```

---

## Related Guides in This Repository

- **[Main Architecture & Local Test Harness](../README.md):** End-to-end sequence diagrams, RFC 9728 OAuth discovery, interactive browser console (`make console`), and IDE/CLI agent bridge.
- **[Optional Cloud Run Deployment Guide](CLOUD-RUN.md):** How to publish the backend MCP server to Cloud Run using standard `gcloud run deploy`.
- **[Apigee API Proxy Bundle](../apiproxy/):** Production XML policies for `<VerifyJWT>`, `<ServiceCallout>` (RFC 8693 Token Exchange), and `<AssignMessage>`.
