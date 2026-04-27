# Contributing

## Workflow

1. **Open an issue** or work from an assigned task so scope and acceptance criteria are clear.
2. **Branch** from `main` using a short, descriptive name (`fix/sse-trailing-newline`, `feature/history-filters`).
3. **Keep changes focused** — one concern per pull request when possible.
4. **Update docs** if you change public API behavior, environment variables, or operations assumptions.

## Quality bar

- **Backend:** Ruff, mypy (as configured), pytest with **coverage ≥70%** on `src/`, matching CI.
- **Frontend:** ESLint, Vitest, and `next build` must pass.
- **No secrets in git** — use `.env` locally; rely on platform secrets in CI.
- **Evaluations:** Run `evals.eval_extraction` for prompt or schema changes; use `eval_llm_judge` sparingly (costs real money).

## Code style

- Match existing patterns: async SQLAlchemy sessions, Pydantic models, structlog for new backend logs, TypeScript strictness in the frontend.
- Avoid breaking SSE contract without a versioned API or coordinated frontend update.

## Reviews

Request review when:

- Auth, data retention, or external API keys are involved.
- You change the default model, chunking, or RAG behavior that affects user-visible quality.

Thank you for keeping the product safe, auditable, and easy to run.
