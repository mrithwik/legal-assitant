# Development guide

## Requirements

| Component | Version / tool |
|-----------|----------------|
| Backend | Python 3.11+, [uv](https://docs.astral.sh/uv/) |
| Frontend | Node.js 20 LTS, npm |
| Local DB (optional) | Docker for Postgres; SQLite needs no extra services |
| External | Clerk app, OpenAI or OpenRouter key, Pinecone (for RAG) |

## Clone and environment

```bash
git clone <your-fork-or-origin-url> legal-assistant
cd legal-assistant
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local
```

Edit `backend/.env` and `frontend/.env.local` using comments in the example files. At minimum: **one LLM key**, **Pinecone** (for RAG), **Clerk** URLs for JWT validation, and `NEXT_PUBLIC_*` for the web app.

### Optional: Postgres (instead of default SQLite)

```bash
cd infra
docker compose up -d
```

Set `DATABASE_URL=postgresql+asyncpg://...` in `backend/.env` to match your compose or cloud database.

## Run the stack

**Terminal 1 — API**

```bash
cd backend
uv sync
uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

**Terminal 2 — web**

```bash
cd frontend
npm install
npm run dev
```

- API docs: <http://127.0.0.1:8000/docs>  
- App: <http://localhost:3000>  

A convenience script exists: `uv run dev` from `backend/` (see `backend/pyproject.toml` / `src/cli.py`).

### First-time RAG index

With Pinecone env vars set and corpus under `data/raw/`:

```bash
cd backend
uv run python -m src.rag.ingestion
```

## Backend: tests and quality (matches CI)

```bash
cd backend
uv run pytest tests/ -q
uv run ruff check src tests
uv run mypy src --ignore-missing-imports
uv run pytest tests/ --cov=src --cov-fail-under=70 -q
```

Tests use mocks; no OpenAI spend in the default suite. Set `OPENAI_API_KEY` in CI to a throwaway or dummy for imports if required by subprocess (see workflow files).

## Frontend: tests and build

```bash
cd frontend
npm run lint
npm run test:run
npm run build
```

## Evaluations (live API, costs money)

Run locally when validating prompt or pipeline changes:

```bash
cd backend
uv run python -m evals.eval_extraction
uv run python -m evals.eval_llm_judge --threshold 3.0
```

`evals.yml` on `main` runs **golden extraction** on path filters with `continue-on-error: true` until you tighten the workflow. The **LLM judge** is intended for **manual** workflow dispatch.

## Common issues

- **401 on API:** Sign in with Clerk, or in dev use the documented `X-User-Id` bypass only when `app_env` is not `production`.
- **503 auth:** Set `CLERK_JWKS_URL` in the backend.
- **Empty RAG sections:** Check Pinecone keys, index dimensions/metric, and that ingestion completed successfully.
- **CORS:** Align `ALLOWED_ORIGINS` with the exact browser origin (scheme + host + port).

See also [../README.md](../README.md) and [RAG.md](./RAG.md).
