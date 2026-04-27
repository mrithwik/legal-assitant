# API reference

Base URL: configure the frontend with `NEXT_PUBLIC_API_URL` (e.g. `http://127.0.0.1:8000` locally). All authenticated routes expect Clerk-issued tokens unless noted.

Interactive docs: `GET /docs` (Swagger UI) and `GET /redoc` on the running API.

## Authentication

- **Production:** `Authorization: Bearer <Clerk session JWT>` (RS256, validated against `CLERK_JWKS_URL` in `backend/src/core/security.py`). JWKS is cached in memory for five minutes.
- **Non-production:** If `Authorization` is omitted and `app_env` is not `production`, the backend accepts `X-User-Id` as a **development fallback** for tests and local tools (`backend/src/api/dependencies.py`). **Do not rely on this in production.**

If `clerk_jwks_url` is empty, protected routes that need JWT return **503** (auth not configured).

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Liveness: `{"status":"ok"}` |
| `GET` | `/api/v1/me` | Yes | Current user profile |
| `POST` | `/api/v1/analyze` | Yes | Multipart case intake; returns **SSE** stream |
| `GET` | `/api/v1/cases` | Yes | List cases for the user; optional `?q=` title substring |
| `GET` | `/api/v1/cases/{id}` | Yes | Case detail including agent steps |
| `DELETE` | `/api/v1/cases/{id}` | Yes | Delete case and related steps |

### `POST /api/v1/analyze`

- **Content-Type:** `multipart/form-data`
- **Fields:** `title` (required, truncated server-side to 255 chars), `case_text` (optional), `case_file` (optional: `.txt`, `.md`, `.pdf`)
- **Constraint:** Merged text from `case_text` and extracted file must be non-empty.
- **Response:** `text/event-stream` (SSE). Each event is a line `data: <json>\n\n`.

#### SSE event types

| `type` | Fields | Meaning |
|--------|--------|--------|
| `markdown_section` | `section_id`, `heading`, `markdown` | One rendered section for the UI |
| `complete` | `case_id` | Pipeline finished successfully |
| `error` | `detail` | Pipeline failed; human-readable message |

`section_id` values: `extraction`, `rag_retrieval`, `strategy`, `drafting`, `qa`.

Example (abbreviated):

```http
data: {"type": "markdown_section", "section_id": "extraction", "heading": "Fact extraction", "markdown": "..."}

data: {"type": "complete", "case_id": "550e8400-e29b-41d4-a716-446655440000"}
```

## Error handling

- Validation errors: **422** with FastAPI’s default body.
- Missing/invalid token: **401** on protected routes.
- Unhandled server errors: **500** with `{"detail":"Internal server error"}` (no stack trace to client).

CORS: configured from `ALLOWED_ORIGINS` (list or comma-separated string in `backend/src/core/config.py`).

## Versioning

Routes are under `/api/v1`. Increment the prefix when making breaking changes to response shapes or auth.
