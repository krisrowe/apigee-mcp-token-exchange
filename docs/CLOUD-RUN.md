# Optional Guide: Publishing the Backend MCP Server to Cloud Run

While the [Backend MCP Server Integration Guide](BACKEND.md) is completely cloud-agnostic, hosting [`mcp_server/server.py`](../mcp_server/server.py) on **Cloud Run** provides an instant, serverless HTTPS endpoint that Apigee X or Apigee Hybrid can call over the internet without managing virtual machines or Kubernetes clusters.

Below are two ways to deploy the backend service to Cloud Run:
- **Standard `gcloud run deploy` (documented below)**: Uses the standard `gcloud` CLI with zero extra tooling required.
- **Declarative `gapp` CLI (for contributors using `gapp`)**: See [`CONTRIBUTING.md`](../CONTRIBUTING.md#1-architecture--zero-coupling-principles) for details on the optional [`gapp.yaml`](../gapp.yaml) manifest.

---

## Prerequisites

- A GCP project (`<YOUR_GCP_PROJECT_ID>`) with billing enabled.
- `gcloud` CLI installed and authenticated:
  ```bash
  gcloud auth login
  gcloud config set project <YOUR_GCP_PROJECT_ID>
  ```

---

## Deploying from Source with `gcloud` (No Extra Tools Needed)

Cloud Run can build and deploy the Python application directly from source using Cloud Buildpacks.

### 1. Enable Required APIs
```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project=<YOUR_GCP_PROJECT_ID>
```

### 2. Deploy the Service from Source (with POC FinOps Caps)
From the root of this repository, run:
```bash
gcloud run deploy catalog-mcp-backend \
  --source . \
  --project=<YOUR_GCP_PROJECT_ID> \
  --region=us-central1 \
  --allow-unauthenticated \
  --max-instances=1 \
  --min-instances=0 \
  --set-env-vars="CLIENT_SETUP_PAGE=SAFE" \
  --set-build-env-vars="GOOGLE_ENTRYPOINT=uvicorn mcp_server.server:app --host 0.0.0.0 --port \$PORT"
```

> **Why `--allow-unauthenticated` and `--max-instances=1`?**
> - **Application-Layer JWT Verification:** Cloud Run's IAM layer (`roles/run.invoker`) checks for cloud IAM tokens, whereas our MCP server validates **OAuth 2.0 RFC 8693 Exchanged Partner JWTs** inside the application layer ([`verify_partner_token`](../mcp_server/server.py)). Allowing unauthenticated ingress at the Cloud Run infrastructure layer allows Apigee to pass `Authorization: Bearer <exchanged_partner_token>` directly to the FastAPI application, which strictly rejects unauthenticated or unexchanged requests with `401` / `403`.
> - **FinOps & Abuse Protection (`--max-instances=1`):** Setting `--max-instances=1` and `--min-instances=0` ensures the POC container scales to zero ($0 cost) when idle and never scales beyond 1 container. In addition, the FastAPI server enforces built-in POC rate limiting (`MCP_RATE_LIMIT_RPM=60` req/min $\rightarrow$ `HTTP 429`) and payload size limits (`MCP_MAX_BODY_BYTES=16384` bytes $\rightarrow$ `HTTP 413`).
> - **Hosted Client Setup Page (`CLIENT_SETUP_PAGE=OFF|SAFE|UNSAFE`):**
>   - `OFF` (Default): `/client-setup` is disabled (`HTTP 404`).
>   - `SAFE`: Enables `GET /client-setup` with live endpoint contracts, Apigee XML snippets, and an in-browser test suite while keeping the signing secret redacted.
>   - `UNSAFE`: For sandbox/POC sessions only; displays the active `MCP_JWT_SECRET` on `/client-setup` and pre-populates ready-to-run `curl` commands and one-click browser tests.

### 3. Retrieve Your Service URL
```bash
gcloud run services describe catalog-mcp-backend \
  --project=<YOUR_GCP_PROJECT_ID> \
  --region=us-central1 \
  --format="value(status.url)"
```

---

## Troubleshooting: Organization Policy Blocking `allUsers` (`public: true`)

In many enterprise cloud organizations, the **Domain Restricted Sharing** organization policy (`constraints/iam.allowedPolicyMemberDomains`) blocks adding `allUsers` (`--allow-unauthenticated`) to Cloud Run services with the following error:

```text
ERROR: (gcloud.run.services.add-iam-policy-binding) FAILED_PRECONDITION:
One or more users named in the policy do not belong to a permitted customer,
perhaps due to an organization policy.
```

### How to Resolve in Sandbox / POC Environments

If you have Organization Policy Administrator permissions (`roles/orgpolicy.policyAdmin`) in your sandbox project or organization, you can relax the policy for your project so Cloud Run can accept public HTTPS calls from Apigee:

```bash
# Reset or allow all domains on the project (or organization) for sandbox testing
gcloud org-policies reset constraints/iam.allowedPolicyMemberDomains \
  --project=<YOUR_GCP_PROJECT_ID>

# Then apply the public invoker binding
gcloud run services add-iam-policy-binding catalog-mcp-backend \
  --project=<YOUR_GCP_PROJECT_ID> \
  --region=us-central1 \
  --member="allUsers" \
  --role="roles/run.invoker"
```

---

## Next Step: Verify and Integrate with Apigee

Once deployed, run `./verify` with your Cloud Run HTTPS URL to execute all 9 positive and negative HTTP assertions (and save the URL in your local XDG cache for subsequent runs):

```bash
./verify https://catalog-mcp-backend-<hash>-uc.a.run.app
```

If you enabled `CLIENT_SETUP_PAGE=SAFE` or `UNSAFE`, you can also open **`https://catalog-mcp-backend-<hash>-uc.a.run.app/client-setup`** directly in your browser to view the synchronized configuration and run the in-browser test suite.

Then follow the **[Backend MCP Server Integration Guide](BACKEND.md)** to wire the URL into your Apigee proxy's [`apiproxy/targets/default.xml`](../apiproxy/targets/default.xml) or test it in the visual protocol console (`console.html?backend=https://...`).

