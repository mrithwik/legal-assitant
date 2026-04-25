#!/usr/bin/env python3
"""Inspect persisted ``rag_retrieval`` outcomes (status + chunk counts).

Run from the ``backend`` directory so ``.env`` loads:

    uv run python scripts/audit_rag_steps.py
    uv run python scripts/audit_rag_steps.py --limit 50

Point at another database (e.g. production read replica):

    DATABASE_URL='postgresql+asyncpg://...' uv run python scripts/audit_rag_steps.py

Interpretation:
  - status FAILED  → exception/timeout in ``rag_retrieve`` (see server logs:
    ``rag_retrieval_failed``).
  - status COMPLETED + 0 chunks → pipeline ran but returned no precedents
    (Pinecone/threshold/filter, or judge/compress emptied the list).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure ``backend`` is on sys.path when invoked as ``python scripts/audit_rag_steps.py``
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _chunk_count(result: dict | None) -> int | None:
    if not result or not isinstance(result, dict):
        return None
    chunks = result.get("chunks")
    if isinstance(chunks, list):
        return len(chunks)
    return None


async def _main(limit: int) -> int:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from src.database.models import AgentStep, Case
    from src.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        stmt = (
            select(AgentStep)
            .where(AgentStep.step_name == "rag_retrieval")
            .options(selectinload(AgentStep.case))
            .join(Case, AgentStep.case_id == Case.id)
            .order_by(Case.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()

    if not rows:
        print("No rag_retrieval steps found (empty DB or no scans yet).")
        return 0

    print(f"{'case_id':<36}  {'status':<12}  {'chunks':>6}  title")
    print("-" * 100)
    for step in rows:
        case = step.case
        title = (case.title[:48] + "…") if case and len(case.title) > 48 else (case.title if case else "?")
        n = _chunk_count(step.result)
        chunks_s = str(n) if n is not None else "—"
        print(f"{step.case_id}  {step.status:<12}  {chunks_s:>6}  {title}")

    failed = sum(1 for s in rows if s.status == "FAILED")
    empty_ok = sum(
        1 for s in rows if s.status != "FAILED" and _chunk_count(s.result) == 0
    )
    print("-" * 100)
    print(f"Rows shown: {len(rows)}  |  FAILED: {failed}  |  completed with 0 chunks: {empty_ok}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=30, help="Max rag_retrieval rows (default 30)")
    args = p.parse_args()
    try:
        raise SystemExit(asyncio.run(_main(args.limit)))
    except Exception as exc:  # noqa: BLE001 — script entrypoint
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
