"""RAG retriever — embed query with OpenAI, then similarity-search Pinecone.

Retrieval Improvements pipeline (applied in order):

  1. Query expansion      — GPT-4o-mini generates 7 focused legal queries
                            and returns applicable_statutes for the case
  2. Statute filter       — scopes each Pinecone search to relevant source
                            files when an explicit statute name is detected
                            in the query text
  3. Statute-scoped       — one supplemental search per LLM-identified
     supplemental search    statute not already covered by (2), using the
                            structured case summary as the query
  4. Score threshold      — drops matches below cosine similarity 0.60
  5. Deduplication        — first-occurrence order preserved across queries
  6. LLM judge            — GPT-4o-mini selects the most relevant subset
  7. Contextual compress  — each selected chunk is trimmed to relevant
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
_SCORE_THRESHOLD = 0.60
# Source-filtered searches are scoped to a single statute file, so the universe
# of vectors is small and cosine similarity naturally runs lower than an
# unfiltered search.  A lower threshold avoids false negatives on targeted lookups.
_SCORE_THRESHOLD_FILTERED = 0.45

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
    "work injury benefits act": "work_injury_benefits_act_cap236.txt",
    "work injuries benefits act": "work_injury_benefits_act_cap236.txt",
    "wiba": "work_injury_benefits_act_cap236.txt",
    "workmen's compensation": "work_injury_benefits_act_cap236.txt",
    "workmen compensation": "work_injury_benefits_act_cap236.txt",
    "workplace injury": "work_injury_benefits_act_cap236.txt",
    "workplace safety": "work_injury_benefits_act_cap236.txt",
    "work accident": "work_injury_benefits_act_cap236.txt",
    "personal protective equipment": "work_injury_benefits_act_cap236.txt",
    "occupational safety": "work_injury_benefits_act_cap236.txt",
    "employer liability injury": "work_injury_benefits_act_cap236.txt",
}


# Hard-coded queries guaranteed to run against specific statute files when
# the statute is identified as applicable by expand_query.  Used for critical
# provisions whose statutory text is semantically distant from case-description
# language and therefore cannot be reliably surfaced by embedding the
# rag_context as the supplemental search query.
#
# Each entry maps a source filename to one or more short query strings that
# embed close to the exact statutory text of the critical provision.
_STATUTE_GUARANTEE_QUERIES: dict[str, list[str]] = {
    # WIBA s.16: "No action shall lie by an employee against an employer for
    # damages for personal injury except as provided under this Act."
    # Injury/PPE/compensation query language has a large semantic gap from
    # "no action shall lie" — the tort bar only surfaces with explicit
    # tort-exclusion vocabulary.
    "work_injury_benefits_act_cap236.txt": [
        "no action employee employer tort damages personal injury Kenya",
    ],
}


def _source_filter_for_query(query: str) -> list[str] | None:
    """Return source filenames to filter Pinecone by, or None for unfiltered search."""
    q_lower = query.lower()
    sources = {
        fname for kw, fname in _STATUTE_MAP.items()
        if re.search(r"\b" + re.escape(kw) + r"\b", q_lower)
    }
    return list(sources) if sources else None


def _filename_for_statute(statute_name: str) -> str | None:
    """Map an LLM-returned statute name (e.g. 'Employment Act 2007') to a source filename."""
    s_lower = statute_name.lower()
    for kw, fname in _STATUTE_MAP.items():
        if kw in s_lower:
            return fname
    return None


_FUZZY_DEDUP_THRESHOLD = 0.85


def _dedup_fuzzy(candidates: list[str]) -> list[str]:
    """Remove near-duplicate chunks after exact deduplication.

    Two passes per candidate:
      1. Text containment — if normalised A is a substring of normalised B,
         they represent the same provision; keep the longer one.
      2. Jaccard word similarity ≥ 0.85 — catches the same text with minor
         whitespace or formatting differences; keep the longer one.

    Threshold is intentionally high: legal text reuses common words across
    distinct provisions so a lower value produces false positives.
    """
    def _norm(text: str) -> str:
        # Lowercase, strip punctuation, collapse whitespace
        return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())

    kept: list[str] = []
    for chunk in candidates:
        chunk_norm = _norm(chunk)
        is_dup = False
        for i, existing in enumerate(kept):
            existing_norm = _norm(existing)
            # Containment: one chunk is a substring of the other
            if chunk_norm in existing_norm:
                is_dup = True  # existing is longer/equal — keep it
                break
            if existing_norm in chunk_norm:
                kept[i] = chunk  # chunk is longer — replace
                is_dup = True
                break
            # Word-level containment: 90% of shorter chunk's unique words appear
            # in longer chunk. Catches same provision with different headers or
            # minor Unicode differences that break the substring check above.
            chunk_words = set(chunk_norm.split())
            existing_words = set(existing_norm.split())
            intersection = chunk_words & existing_words
            if len(chunk_words) <= len(existing_words):
                short_w, long_is_chunk = chunk_words, False
            else:
                short_w, long_is_chunk = existing_words, True
            if short_w and len(intersection) / len(short_w) >= 0.90:
                if long_is_chunk:
                    kept[i] = chunk
                is_dup = True
                break
            # Jaccard similarity — catches near-identical chunks of similar length
            union = len(chunk_words | existing_words)
            if union and len(intersection) / union >= _FUZZY_DEDUP_THRESHOLD:
                if len(chunk) > len(existing):
                    kept[i] = chunk
                is_dup = True
                break
        if not is_dup:
            kept.append(chunk)
    return kept


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

    threshold = _SCORE_THRESHOLD_FILTERED if source_filter else _SCORE_THRESHOLD

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
            if score < threshold:
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

    rag_context, queries, applicable_statutes = await expand_query(query)
    logger.info("rag_retrieve_start", n_queries=len(queries), n_statutes=len(applicable_statutes))

    # Supplemental statute-scoped searches: one per LLM-identified statute.
    # Run unconditionally — even if a per-query keyword filter already targets
    # the same file, those queries focus on narrow aspects of the case and may
    # miss critical provisions (e.g. a tort-bar section that only surfaces when
    # the rag_context is used as the query).  Dedup removes any overlap.
    extra_statute_files = list(dict.fromkeys(
        f for s in applicable_statutes
        if (f := _filename_for_statute(s)) is not None
    ))
    logger.info("rag_statute_supplemental", extra_files=extra_statute_files)

    # Guarantee searches: one search per hard-coded query for each applicable
    # statute that has entries in _STATUTE_GUARANTEE_QUERIES.  These fire in
    # addition to the rag_context supplemental search for the same file, so
    # critical provisions with large semantic gaps (e.g. WIBA s.16 tort bar)
    # are always presented to the judge as candidates.
    guarantee_searches = [
        _retrieve_for_query(gq, n_results, [fname])
        for fname in extra_statute_files
        for gq in _STATUTE_GUARANTEE_QUERIES.get(fname, [])
    ]
    if guarantee_searches:
        logger.info("rag_guarantee_queries", count=len(guarantee_searches))

    results = await asyncio.gather(
        *[
            _retrieve_for_query(q, n_results, _source_filter_for_query(q))
            for q in queries
        ],
        *[
            _retrieve_for_query(rag_context, n_results, [fname])
            for fname in extra_statute_files
        ],
        *guarantee_searches,
        return_exceptions=True,
    )

    # Exact deduplication — first occurrence wins
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

    # Fuzzy deduplication — removes near-duplicates the exact pass misses
    candidates = _dedup_fuzzy(candidates)

    logger.info("rag_candidates_collected", n_candidates=len(candidates))

    if not candidates:
        return []

    # Judge: select the most legally relevant subset
    selected = await judge_chunks(rag_context, candidates)

    # Compress: trim each selected chunk to its relevant sentences
    chunks = await compress_chunks(rag_context, selected)

    logger.info("rag_retrieve_complete", chunks_returned=len(chunks), n_queries=len(queries))
    return chunks
