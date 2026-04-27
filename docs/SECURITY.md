# Security

## Threat model (brief)

The backend accepts **authenticated** requests from the web app, calls **OpenAI (or OpenRouter)**, **Pinecone**, and may send traces to **Langfuse**. The product handles **sensitive legal content** in memory and in the application database. Treat the stack as **Internet-facing** with standard SaaS hardening.

## Authentication and authorization

- **Clerk (JWT):** The API validates **RS256** tokens using Clerk’s **JWKS** (`CLERK_JWKS_URL`). The JWKS response is **cached 5 minutes** in process (`backend/src/core/security.py`).
- **Subject claim:** The backend uses the JWT `sub` as the user id. Ensure the frontend only sends **session** tokens from your Clerk app.
- **Dev bypass:** When `app_env` is not `production`, the API may accept `X-User-Id` without a Bearer token (`backend/src/api/dependencies.py`). **Disable this path in production** by running with `app_env=production` and only validated Clerk traffic.

**Note:** The `.env.example` may list `CLERK_ISSUER` for your own reference; the current validator in `validate_clerk_jwt` decodes the token without a separate issuer check — align deployment hardening (issuer/audience) with your org’s security policy if required.

## Secrets

| Secret | Where used |
|--------|------------|
| `OPENAI_API_KEY` / `OPENROUTER_API_KEY` | Backend only |
| `PINECONE_API_KEY` | Backend only (RAG) |
| `CLERK_SECRET_KEY` | Frontend (server-side Clerk); never expose to the client |
| `NEXT_PUBLIC_*` | Embedded in the browser; **not** for secrets |
| `DATABASE_URL` | Backend; store in a secrets manager in production |
| `LANGFUSE_SECRET_KEY` | Backend |

Rotate keys on a schedule. Use **separate** Clerk, Pinecone, and LLM projects for dev/staging/prod where possible.

## CORS

`ALLOWED_ORIGINS` restricts browser origins. List **exact** production origins; avoid `*` with credentials. Parsed in `backend/src/core/config.py` (string list or comma-separated form).

## Transport and headers

- Serve the API and app over **HTTPS** in production.
- Standard practice: restrict admin endpoints (none in this app beyond normal API) and rate limit at the edge (API Gateway, WAF, or App Runner integration) for abuse protection; not all are configured in this repo and should be added per your threat model.

## Data

- **PDF uploads:** Text is extracted server-side (`backend/src/services/case_file_text.py`); do not run untrusted binaries as privileged users in production; keep dependencies patched.
- **Pinecone:** Vectors and metadata (including text snippets) sit in your Pinecone project. Apply **namespace** or project isolation per environment.
- **Database:** Enforce **encryption at rest** in Aurora, least-privilege DB users, and network constraints (private subnets, security groups).

## Dependency and image hygiene

- Pin versions via `uv.lock` and `package-lock.json` in the repo; rebuild images on security updates.
- Scan container images in CI (not included by default) if your policy requires it.

## Legal and responsible use

This is an **assistive** tool, not a substitute for a qualified professional. Add product disclaimers in the UI where appropriate; avoid storing unnecessary PII; define retention in line with your jurisdiction and client obligations.

For API details, see [API.md](./API.md). For production deployment, see [DEPLOYMENT.md](./DEPLOYMENT.md).
