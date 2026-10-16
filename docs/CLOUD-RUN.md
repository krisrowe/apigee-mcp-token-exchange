# Optional Guide: Publishing the Backend MCP Server to Cloud Run

While the [Backend MCP Server Integration Guide](BACKEND.md) is completely cloud-agnostic, hosting [`mcp_server/server.py`](../mcp_server/server.py) on **Cloud Run** provides an instant, serverless HTTPS endpoint that Apigee X or Apigee Hybrid can call over the internet without managing virtual machines or Kubernetes clusters.

Below are two ways to deploy the backend service to Cloud Run:
1. **Option A: Standard `gcloud run deploy` (Recommended for most users)**: Uses the standard `gcloud` CLI with zero extra tooling required.
2. **Option B: Declarative `gapp` CLI**: Uses the included [`gapp.yaml`](../gapp.yaml) manifest.

---

## Prerequisites

- A GCP project (`<YOUR_GCP_PROJECT_ID>`) with billing enabled.
- `gcloud` CLI installed and authenticated:
  ```bash
  gcloud auth login
  gcloud config set project <YOUR_GCP_PROJECT_ID>
  ```

---

## Option A: Deploy with Standard `gcloud` Commands (No Extra Tools Needed)

Cloud Run can build and deploy the Python application directly from source using Cloud Buildpacks.

### 1. Enable Required APIs
```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project=<YOUR_GCP_PROJECT_ID>
```

### 2. Deploy the Service from Source
From the root of this repository, run:
```bash
gcloud run deploy catalog-mcp-backend \
  --source . \
  --project=<YOUR_GCP_PROJECT_ID> \
  --region=us-central1 \
  --allow-unauthenticated \
  --set-build-env-vars="GOOGLE_ENTRYPOINT=uvicorn mcp_server.server:app --host 0.0.0.0 --port \$PORT"
```

> **Why `--allow-unauthenticated`?**
> Cloud Run's IAM layer (`roles/run.invoker`) checks for cloud IAM tokens, whereas our MCP server validates **OAuth 2.0 RFC 8693 Exchanged Partner JWTs** inside the application layer ([`verify_partner_token`](../mcp_server/server.py)). Allowing unauthenticated ingress at the Cloud Run infrastructure layer allows Apigee to pass the `Authorization: Bearer <exchanged_partner_token>` header directly to the FastAPI application, which strictly rejects unauthenticated or unexchanged requests with `401` / `403`.

### 3. Retrieve Your Service URL
```bash
gcloud run services describe catalog-mcp-backend \
  --project=<YOUR_GCP_PROJECT_ID> \
  --region=us-central1 \
  --format="value(status.url)"
```

---

## Option B: Deploy with `gapp` (`gapp.yaml`)

This repository includes a declarative [`gapp.yaml`](../gapp.yaml) manifest pre-configured with `entrypoint: mcp_server.server:app` and `public: true`.

If you use [`gapp`](https://github.com/krisrowe/gapp), you can provision and deploy in two commands:

```bash
# 1. One-time project setup (enables APIs, Artifact Registry, and service accounts)
gapp setup <YOUR_GCP_PROJECT_ID>

# 2. Build and deploy to Cloud Run
gapp deploy --project <YOUR_GCP_PROJECT_ID>
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

Once deployed, run `./verify --live` with your Cloud Run HTTPS URL to execute all 8 positive and negative HTTP assertions (and save the URL in your local XDG cache for subsequent runs):

```bash
./verify --live https://catalog-mcp-backend-<hash>-uc.a.run.app
```

Then follow the **[Backend MCP Server Integration Guide (`docs/BACKEND.md`)](BACKEND.md)** to wire the URL into your Apigee proxy's [`apiproxy/targets/default.xml`](../apiproxy/targets/default.xml) or test it in the visual protocol console (`console.html?backend=https://...`).

