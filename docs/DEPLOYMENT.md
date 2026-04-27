# Deployment

## Overview

The reference deployment uses **AWS**: container images in **ECR**, **App Runner** for the API and the web app, and **Aurora Serverless v2** (PostgreSQL) for production data. Infrastructure is codified under `terraform/`.

| Terraform module | Purpose |
|------------------|--------|
| `terraform/backend/` | Backend ECR, App Runner, GitHub OIDC role, related IAM |
| `terraform/frontend/` | Frontend ECR, App Runner, build-time env wiring |
| `terraform/database/` | Aurora, secrets outputs for connection strings |

Outputs include ECR repository URLs and App Runner URLs (e.g. `app_runner_url` in `terraform/backend/outputs.tf`).

## Container images

- **Backend:** `backend/Dockerfile` — Uvicorn on port **8000**
- **Frontend:** `frontend/Dockerfile` — production Next.js on port **3000**; `NEXT_PUBLIC_*` variables are supplied as **build args** so they are baked into the client bundle

Manual helper scripts: `backend/deploy.sh`, `frontend/deploy.sh` (read before use; align with your AWS account and tags).

## CI/CD (GitHub Actions)

- **`.github/workflows/backend-deploy.yml`:** On pull requests, runs compile check, ruff, mypy, pytest with **≥70%** coverage. On **push to `main`**, builds and pushes `linux/amd64` to ECR `litigation-backend` (requires `AWS_ROLE_ARN` and working OIDC; see repository secrets).
- **`.github/workflows/frontend-deploy.yml`:** ESLint, Vitest, `next build`; on `main` push, builds and pushes to ECR `litigation-frontend` with `NEXT_PUBLIC_*` and Clerk secrets from GitHub.
- **`.github/workflows/evals.yml`:** Optional extraction evals on code/data path changes; LLM-judge is manual.

Required secrets (typical; confirm against each workflow file):

- `AWS_ROLE_ARN` — IAM role for GitHub Actions OIDC
- `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `NEXT_PUBLIC_API_URL` — frontend
- `OPENAI_API_KEY` — eval workflows when enabled

**Region:** Workflows use `us-east-2` — change consistently if you relocate resources.

## Runtime configuration

- **Database:** Set `DATABASE_URL` for the backend to Aurora (asyncpg DSN). Credentials often come from **Secrets Manager** in Terraform; wire as App Runner environment or secrets.
- **Pinecone:** Backend needs `PINECONE_*` at **runtime** (not baked into the frontend). Protect keys per environment.
- **Clerk:** Same Clerk instance for frontend and backend; JWKS and issuer in backend env must match the app issuing tokens.
- **Langfuse:** Optional; set `LANGFUSE_*` for production tracing.

## Pre-deploy checklist

1. Migrations: ensure DB schema matches models (this repo uses `init_db` on startup; confirm you have a migration story before scaling teams).
2. CORS: `ALLOWED_ORIGINS` must list the **production** frontend origin(s).
3. RAG: run ingestion in a secure environment and verify Pinecone **namespace/index** for prod.
4. Smoke: `GET /health`, sign-in, one full analyze flow with SSE.

## Rollback

App Runner can roll back to a **previous ECR image tag** if you tag releases instead of only `latest`. Document your tagging policy; for production, avoid immutable `latest`-only rollbacks for critical fixes.

See [SECURITY.md](./SECURITY.md) for secret handling and [ARCHITECTURE.md](./ARCHITECTURE.md) for system context.
