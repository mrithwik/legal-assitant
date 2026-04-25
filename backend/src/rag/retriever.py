"""RAG retriever — embed query with OpenAI, then similarity-search Pinecone.

Feature 2 (Query Expansion): instead of embedding the full case text as one
vector, rag_retrieve() first calls expand_query() to generate several focused
legal search queries, runs each through Pinecone in parallel, then deduplicates
the combined results.  This gives the strategy agent richer, more targeted
context than a single broad embedding.

The function signature ``(query: str) -> list[str]`` is the integration contract
with the orchestrator.  Do not change the signature; only this body should need
to change when swapping vector backends.
"""

import asyncio
import time

from src.core.config import settings
from src.core.logging import get_logger
from src.core.openai_client import get_async_client
from src.rag.pinecone_store import get_pinecone_index, pinecone_configured
from src.rag.query_expansion import expand_query
from src.rag.vector_store import EMBED_MODEL, PINECONE_METADATA_TEXT_KEY

logger = get_logger(__name__)

_openai = get_async_client()

_DEFAULT_N_RESULTS = 5


def _matches_from_response(resp: object) -> list:
    """Normalize Pinecone query response to a list of match objects."""
    matches = getattr(resp, "matches", None)
    if matches is not None:
        return list(matches)
    if isinstance(resp, dict):
        return list(resp.get("matches") or [])
    return []


async def _retrieve_for_query(query: str, n_results: int) -> list[str]:
    """Embed one query and return the top-k matching chunks from Pinecone."""
    start = time.monotonic()
    embed_resp = await _openai.embeddings.create(model=EMBED_MODEL, input=query)
    query_embedding: list[float] = embed_resp.data[0].embedding
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("rag_embed_complete", model=EMBED_MODEL, duration_ms=duration_ms, query=query[:60])

    def _query_pinecone() -> list[str]:
        index = get_pinecone_index()
        q_kwargs: dict = {
            "vector": query_embedding,
            "top_k": n_results,
            "include_metadata": True,
        }
        if settings.pinecone_namespace.strip():
            q_kwargs["namespace"] = settings.pinecone_namespace.strip()
        resp = index.query(**q_kwargs)
        docs: list[str] = []
        for m in _matches_from_response(resp):
            meta = getattr(m, "metadata", None) or {}
            if not isinstance(meta, dict):
                meta = dict(meta) if meta else {}
            text = meta.get(PINECONE_METADATA_TEXT_KEY)
            if isinstance(text, str) and text.strip():
                docs.append(text.strip())
        return docs

    return await asyncio.to_thread(_query_pinecone)


async def rag_retrieve(query: str, n_results: int = _DEFAULT_N_RESULTS) -> list[str]:
    """Return deduplicated chunks from Pinecone for the given case text.

    Uses query expansion: GPT-4o-mini generates several focused legal search
    queries from the raw case text, each is searched in parallel, and the
    combined results are deduplicated before being returned.

    Returns an empty list if the query is blank or Pinecone is not configured.
    """
    if not query.strip():
        return []

    if not pinecone_configured():
        logger.warning("rag_pinecone_not_configured", hint="Set PINECONE_* env vars")
        return []

    # Generate focused queries — falls back to [original text] if LLM fails
    queries = await expand_query(query)
    logger.info("rag_retrieve_start", n_queries=len(queries))

    # Run all Pinecone searches in parallel
    results = await asyncio.gather(
        *[_retrieve_for_query(q, n_results) for q in queries],
        return_exceptions=True,
    )

    # Deduplicate while preserving order — first occurrence of each chunk wins
    seen: set[str] = set()
    chunks: list[str] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("rag_query_failed", reason=str(result))
            continue
        for chunk in result:
            if chunk not in seen:
                seen.add(chunk)
                chunks.append(chunk)

    logger.info("rag_retrieve_complete", chunks_returned=len(chunks), n_queries=len(queries))
    return chunks
