# Operations and observability

## Logging (structlog)

Configuration: `backend/src/core/logging.py`. In **development**, logs are human-readable. In **production** (`app_env=production` or as configured in your deployment), use **JSON** on stdout for ingestion into CloudWatch, Datadog, ELK, or similar.

**HTTP requests** are logged by middleware in `src/main.py` with `method`, `path`, `status_code`, and `duration_ms`.

**Pipeline and agent** logs use structured keys such as `pipeline_start`, `step_complete`, `pipeline_complete`, and `rag_retrieval_failed` — filter on `case_id` to correlate a single run.

**LLM telemetry** events (where implemented) typically include `model`, `agent`, and token or latency fields. Search the codebase for the `llm_` and `http_request` event names in `backend/src/`.

## Langfuse (optional)

When `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and (if needed) `LANGFUSE_HOST` are set, the backend can report traces to Langfuse. If any are blank, tracing is off. This is the primary product-level “trace UI” in addition to raw logs.

## Health

- Liveness: `GET /health` returns `{"status":"ok"}`. Use this in load balancer or App Runner health checks.

There is no dedicated `GET /ready` in the default app; for deep readiness, consider checking DB and Pinecone if your SLOs require it (add an endpoint with timeouts).

## What is not built in (extensions)

- **No Prometheus /metrics** endpoint in the stock app: derive latency from logs or APM.
- **No distributed tracing (OpenTelemetry)** in the default tree: add if you need cross-service trace IDs.

## Runbook snippets

- **RAG empty for many users:** Verify Pinecone quota, index name/host, and recent ingestion. Check logs for `rag_retrieval_failed`.
- **403/401 spikes:** Confirm Clerk key rotation, JWKS URL, and that mobile/web clients use the same published URL as `ALLOWED_ORIGINS` for CORS.
- **Slow first request:** Cold start for App Runner or Pinecone — expected; consider min instances and warming.

## Backups and retention

- **Aurora:** Rely on AWS automated backups and point-in-time recovery; document RPO/RTO.
- **Pinecone:** Rebuild from `data/raw/` plus ingestion if you need a reproducible state.
- **Case data:** Business policy — define retention in your compliance framework.

## Cost visibility

Log token fields where available; aggregate by day per environment. The root README includes a rough per-run order of magnitude; watch OpenAI and Pinecone bills after prompt or retrieval changes.

See [ARCHITECTURE.md](./ARCHITECTURE.md) and [RAG.md](./RAG.md) for where LLM and retrieval costs accrue.
