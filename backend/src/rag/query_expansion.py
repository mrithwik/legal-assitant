"""Query expansion for RAG retrieval.

Instead of embedding the full case text as a single query (which dilutes
specific legal signals), this module asks GPT-4o-mini to generate several
short, focused legal search queries from the case description.  Each query
targets one specific legal issue, producing sharper embedding vectors and
more relevant Pinecone results.
"""

import time

import instructor
from pydantic import BaseModel

from src.core.logging import get_logger
from src.core.openai_client import get_async_client

logger = get_logger(__name__)

_MODEL = "gpt-4o-mini"
_N_QUERIES = 5

_PROMPT = f"""You are a Kenyan legal research assistant.

Given a case description, generate exactly {_N_QUERIES} short, focused search queries
to retrieve relevant Kenyan legal precedents and statutes from a vector database.

Rules:
- Each query must target ONE specific legal issue in the case.
- Keep each query concise (5-10 words).
- Use precise legal terminology (statute names, legal doctrines, Kenyan-specific terms).
- Do not repeat concepts across queries — each should cover a distinct angle.
- Do not include the party names or dates — focus on legal concepts only.

Return ONLY a JSON object with a single key "queries" containing a list of strings.
"""


class _ExpandedQueries(BaseModel):
    queries: list[str]


async def expand_query(case_text: str) -> list[str]:
    """Return focused legal search queries derived from the raw case text.

    Falls back to a single-element list containing the original text if the
    LLM call fails, so the caller always gets at least one query.
    """
    if not case_text.strip():
        return [case_text]

    client = instructor.from_openai(get_async_client(), mode=instructor.Mode.JSON)

    logger.info("query_expansion_start", model=_MODEL, n_queries=_N_QUERIES)
    start = time.monotonic()

    try:
        result = await client.chat.completions.create(
            model=_MODEL,
            response_model=_ExpandedQueries,
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": case_text},
            ],
            temperature=0.2,
        )
        queries = [q.strip() for q in result.queries if q.strip()]
    except Exception as exc:
        logger.warning("query_expansion_failed", reason=str(exc), fallback="original_text")
        return [case_text]

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("query_expansion_complete", n_queries=len(queries), duration_ms=duration_ms)

    return queries if queries else [case_text]
