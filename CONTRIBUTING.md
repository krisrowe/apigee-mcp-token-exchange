# Contributing & Maintainer Guide

This document covers architecture boundaries, testing conventions, and optional deployment tooling for maintainers and contributors working on `apigee-mcp-token-exchange`.

---

## 1. Architecture & Zero-Coupling Principles

- **Vendor & Runtime Neutrality:**
  The reference implementation ([`mock_idp/`](mock_idp/), [`mock_gateway/`](mock_gateway/), [`mcp_server/`](mcp_server/), and [`apiproxy/`](apiproxy/)) must remain standard Python/FastAPI and standard Apigee XML with zero runtime dependencies on proprietary deployment wrappers.
- **No Hardcoded Deployment URLs or Environment Identities:**
  Never hardcode live Cloud Run URLs, project IDs, organization IDs, or personal/corporate email domains in source files, tests, or documentation. Always use generic RFC 2606 placeholders (`example.com`) in docs and pass live target URLs at runtime via `./verify --live <URL>` or environment variables.
- **Role of [`gapp.yaml`](gapp.yaml) (Optional Declarative Manifest Only):**
  The root [`gapp.yaml`](gapp.yaml) file is included strictly as an optional convenience manifest for contributors who use [`gapp`](https://github.com/krisrowe/gapp) to deploy Python ASGI services to Cloud Run (`gapp setup <PROJECT_ID>` and `gapp deploy --project <PROJECT_ID>`).
  - **Strict Decoupling Rule:** Aside from the declarative [`gapp.yaml`](gapp.yaml) file, this repository must have **zero code, test, or workflow coupling** to `gapp`. All user-facing deployment documentation ([`docs/CLOUD-RUN.md`](docs/CLOUD-RUN.md)) must present standard `gcloud run deploy` commands as the primary, self-contained path.

---

## 2. Testing Architecture

Tests are organized into two tiers under [`tests/`](tests/):

1. **Sociable Unit Tests ([`tests/unit/test_protocol_flow.py`](tests/unit/test_protocol_flow.py)):**
   - **Default (`./verify` or `make test`):** Runs completely in-process via `httpx.ASGITransport` with zero local TCP port bindings, zero background `uvicorn` subprocesses, and zero external network calls.
   - Verifies all positive (`200`) and negative (`401`, `403`, `404`, `413`, `429`) protocol assertions, dynamic RFC 8707 audience matching, and `CLIENT_SETUP_PAGE` modes (`OFF`, `SAFE`, `UNSAFE`).
2. **Opt-In Live Integration Tests ([`tests/integration/real-user/test_live_backend.py`](tests/integration/real-user/test_live_backend.py)):**
   - Excluded from default `pytest` runs via `addopts = "--ignore=tests/integration"` in [`pyproject.toml`](pyproject.toml).
   - Invoked via `./verify --live <BACKEND_URL>` (which caches the last-used URL in `~/.cache/apigee-mcp-token-exchange/context.json` so subsequent runs only require `./verify --live`).

---

## 3. Pre-Commit Checks

Before committing or opening a pull request, ensure both the offline unit test suite and repository hygiene checks pass with exit code `0`:

```bash
./verify
```
