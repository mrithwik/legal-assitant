# Frontend (Next.js)

Next.js **App Router** application for the Litigation Prep Assistant: landing, pricing, auth entry, and dashboard flows for case input, **SSE** streaming of the analysis, history, and per-case detail. It integrates with **Clerk** and the FastAPI backend documented under [`../docs/`](../docs/README.md).

## Requirements

- Node.js **20+** (LTS recommended)
- npm (this project uses `package-lock.json`)

## Install

From this directory (`frontend/`):

```bash
npm install
```

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Development server with [Turbopack](https://nextjs.org/docs/app/api-reference/turbopack) (`next dev --turbopack`). |
| `npm run build` | Production build (`next build`). |
| `npm run start` | Serve the production build (`next start`). |
| `npm run lint` | ESLint via Next.js config. |

Default dev URL: `http://localhost:3000`.

**Evals (backend only):** Golden-case extraction is configured in **`../.github/workflows/evals.yml`**. The expensive LLM-judge job is **manual**; see [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `NEXT_PUBLIC_API_URL` | Base URL of the FastAPI backend (e.g. `http://127.0.0.1:8000` locally) |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk **publishable** key (safe in the browser) |
| `CLERK_SECRET_KEY` | Clerk **secret** — server only; never commit to git |

Create `.env.local` from **`frontend/.env.example`**. See [Next.js environment variables](https://nextjs.org/docs/app/building-your-application/configuring/environment-variables).

## App Router routes (scaffold)

| Route | Purpose |
|-------|---------|
| `/` | Public landing; signed-in users are sent to `/dashboard`. |
| `/dashboard` | Authenticated home (dashboard overview). |
| `/dashboard/new-scan` | New case scan with streaming `POST /api/v1/analyze`. |
| `/dashboard/scans` | Case list (`GET /api/v1/cases`). |
| `/dashboard/scans/[id]` | Case detail with agent steps. |
| `/subscriptions` | Plans / Clerk `PricingTable` + premium tools. |
| `/public/*` | Marketing-style public routes (e.g. login, pricing redirect). |

`src/proxy.ts` (Clerk middleware) controls auth at the edge; `/dashboard/*` is also gated in `dashboard/layout.tsx`.

## Components

`src/components/` is organized by domain: `forms`, `dashboard`, `agents`, and `ui` (primitives). Key streaming UI: `pipeline-markdown-panel.tsx` (SSE), case intake: `forms/case-input-form.tsx`.

## Deploying on AWS App Runner

The frontend is containerised and deployed via AWS App Runner. The full infrastructure is managed by Terraform in `terraform/frontend/`.

1. **Build and push** the Docker image (`frontend/Dockerfile`) to the ECR repository created by Terraform.
2. **Apply Terraform** — `cd terraform/frontend && terraform apply`. App Runner pulls the image from ECR and serves it on port 3000.
3. **Environment variables** — `NEXT_PUBLIC_*` values are passed as Docker build args in the Terraform config. `CLERK_SECRET_KEY` is injected at runtime from AWS Secrets Manager.

The App Runner service URL is available as a Terraform output (`frontend_url`).

## TypeScript and paths

`tsconfig.json` maps `@/*` to `src/*` (for example `import { apiBaseUrl } from "@/lib/api"`).
