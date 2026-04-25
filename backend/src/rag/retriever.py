"""RAG retriever — embed query with OpenAI, then similarity-search Pinecone.

Retrieval Improvements pipeline (applied in order):

  1. Query expansion      — GPT-4o-mini generates 5 focused legal queries
  2. Statute filter       — scopes each Pinecone search to relevant source
                            files when an explicit statute name is detected
  3. Score threshold      — drops matches below cosine similarity 0.70
  4. Deduplication        — first-occurrence order preserved across queries
  5. LLM judge            — GPT-4o-mini selects the most relevant subset
  6. Contextual compress  — each selected chunk is trimmed to relevant
                            sentences only

The function signature ``(query: str) -> list[str]`` is the integration
contract with the orchestrator.  Do not change it; only this body should
need to change when swapping vector backends.
"""

import asyncio
import re
import time

from src.core.config import settings
from src.core.logging import get_logger
from src.core.openai_client import get_async_client
from src.rag.pinecone_store import get_pinecone_index, pinecone_configured
from src.rag.query_expansion import expand_query
from src.rag.reranker import compress_chunks, judge_chunks
from src.rag.vector_store import EMBED_MODEL, PINECONE_METADATA_TEXT_KEY

logger = get_logger(__name__)

_openai = get_async_client()

_DEFAULT_N_RESULTS = 5
_SCORE_THRESHOLD = 0.70

# Maps lowercase keyword phrases found in expanded queries to their source
# filenames in the Pinecone index.  Includes explicit Act names, common
# short-form references (e.g. "law of contract"), and well-known section
# groupings (e.g. "bill of rights").  General legal concepts such as
# "negligence" or "damages" are intentionally excluded to avoid
# over-scoping searches that span multiple Acts.
_STATUTE_MAP: dict[str, str] = {
    "arbitration act": "arbitration_act_cap49.txt",
    "civil procedure act": "civil_procedure_act_cap21.txt",
    "constitution of kenya": "constitution_of_kenya_2010.txt",
    "bill of rights": "constitution_of_kenya_2010.txt",
    "contract act": "contract_act_cap_23.txt",
    "law of contract act": "contract_act_cap_23.txt",
    "law of contract": "contract_act_cap_23.txt",
    "criminal procedure code": "criminal_procedure_code_cap75.txt",
    "penal code": "penal_code_cap63.txt",
    "employment act": "employment_act_2007.txt",
    "judicature act": "judicature_act_cap_8.txt",
    "land act": "land_act_2012.txt",
    "law of torts": "law_of_torts_basic.txt",
    "limitation of actions act": "limitation_of_actions_act.txt",
    "limitation of actions": "limitation_of_actions_act.txt",
    "marriage act": "marriage_act_cap150.txt",
    "succession act": "succession_act_cap_160.txt",
    "public procurement": "public_procurement_and_asset_disposal_act_cap412c.txt",
    "small claims court act": "small_claims_court_act.txt",
}


def _source_filter_for_query(query: str) -> list[str] | None:
    """Return source filenames to filter Pinecone by, or None for unfiltered search."""
    q_lower = query.lower()
    sources = {
        fname for kw, fname in _STATUTE_MAP.items()
        if re.search(r"\b" + re.escape(kw) + r"\b", q_lower)
    }
    return list(sources) if sources else None


def _matches_from_response(resp: object) -> list:
    """Normalize Pinecone query response to a list of match objects."""
    matches = getattr(resp, "matches", None)
    if matches is not None:
        return list(matches)
    if isinstance(resp, dict):
        return list(resp.get("matches") or [])
    return []


async def _retrieve_for_query(
    query: str,
    n_results: int,
    source_filter: list[str] | None = None,
) -> list[str]:
    """Embed one query and return scored, threshold-filtered chunks from Pinecone."""
    start = time.monotonic()
    embed_resp = await _openai.embeddings.create(model=EMBED_MODEL, input=query)
    query_embedding: list[float] = embed_resp.data[0].embedding
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("rag_embed_complete", model=EMBED_MODEL, duration_ms=duration_ms, query_len=len(query))

    def _query_pinecone() -> list[str]:
        index = get_pinecone_index()
        q_kwargs: dict = {
            "vector": query_embedding,
            "top_k": n_results,
            "include_metadata": True,
        }
        if settings.pinecone_namespace.strip():
            q_kwargs["namespace"] = settings.pinecone_namespace.strip()
        if source_filter:
            q_kwargs["filter"] = {"source": {"$in": source_filter}}

        resp = index.query(**q_kwargs)
        docs: list[str] = []
        for m in _matches_from_response(resp):
            raw_score = getattr(m, "score", None)
            score: float = raw_score if isinstance(raw_score, (int, float)) else 1.0
            if score < _SCORE_THRESHOLD:
                continue
            meta = getattr(m, "metadata", None) or {}
            if not isinstance(meta, dict):
                meta = dict(meta) if meta else {}
            text = meta.get(PINECONE_METADATA_TEXT_KEY)
            if isinstance(text, str) and text.strip():
                docs.append(text.strip())
        return docs

    return await asyncio.to_thread(_query_pinecone)


async def rag_retrieve(query: str, n_results: int = _DEFAULT_N_RESULTS) -> list[str]:
    """Return reranked, compressed chunks from Pinecone for the given case text.

    Pipeline:
      expand_query → statute-filtered parallel Pinecone searches (score-thresholded)
      → dedup → LLM judge → contextual compression

    Returns an empty list if the query is blank or Pinecone is not configured.
    """
    if not query.strip():
        return []

    if not pinecone_configured():
        logger.warning("rag_pinecone_not_configured", hint="Set PINECONE_* env vars")
        return []

    queries = await expand_query(query)
    logger.info("rag_retrieve_start", n_queries=len(queries))

    results = await asyncio.gather(
        *[
            _retrieve_for_query(q, n_results, _source_filter_for_query(q))
            for q in queries
        ],
        return_exceptions=True,
    )

    # Deduplicate while preserving order — first occurrence wins
    seen: set[str] = set()
    candidates: list[str] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("rag_query_failed", reason=str(result))
            continue
        for chunk in result:
            if chunk not in seen:
                seen.add(chunk)
                candidates.append(chunk)

    logger.info("rag_candidates_collected", n_candidates=len(candidates))

    if not candidates:
        return []

    # Judge: select the most legally relevant subset
    selected = await judge_chunks(query, candidates)

    # Compress: trim each selected chunk to its relevant sentences
    chunks = await compress_chunks(query, selected)

    logger.info("rag_retrieve_complete", chunks_returned=len(chunks), n_queries=len(queries))
    return chunks
