"""RAG reranker — LLM judge + contextual compression (Retrieval Improvements).

Two-stage quality filter applied after Pinecone retrieval:

1. judge_chunks()    — GPT-4o-mini scores each candidate chunk for legal
                       relevance and returns a ranked subset. Targets ~6
                       chunks but returns more if the case warrants it.

2. compress_chunks() — For each selected chunk, GPT-4o-mini extracts only
                       the sentences directly relevant to the case. All
                       compressions run in parallel.

Both functions fall back gracefully on LLM failure: judge returns all input
chunks, compress returns the original chunk.
"""

import asyncio
import time

import instructor
from pydantic import BaseModel

from src.core.logging import get_logger
from src.core.openai_client import get_async_client

logger = get_logger(__name__)

_MODEL = "gpt-4o-mini"
_MAX_COMPRESS_CONCURRENCY = 6

_JUDGE_SYSTEM_TEMPLATE = """You are a senior Kenyan advocate reviewing retrieved legal text.

Given a case description and a numbered list of retrieved chunks, select the
chunks that are genuinely useful for analysing this specific case.

Scoring guide:
  8–10  directly on point — cites the applicable statute, doctrine, or test that
        governs the specific legal issue in dispute
  5–7   useful context — related area of law, analogous principle, or a provision
        that reveals what the opposing party is entitled to argue
  0–4   not useful — score 0–4 for any of the following:
        • Wrong branch of law: a criminal provision (Penal Code, Criminal Procedure
          Code) in a civil case, or a civil provision in a criminal case, unless
          criminal liability is explicitly in dispute in this specific case — a
          bad-cheque penal provision in a civil wrongful-dishonour claim is 0–4
          even though both involve cheques
        • Wrong statute context: an arbitration clause, venue or jurisdiction rule,
          or procedural provision from a statute whose subject matter is not central
          to the dispute — Arbitration Act sections in a res judicata case are 0–4
        • Boilerplate: statute citation clauses ("This Act may be cited as …"),
          application clauses ("This Act applies to proceedings in …"), publisher
          headers, or index entries
        • Interpretation sections: blocks of statutory definitions ("X means Y",
          "Y includes Z") that list term meanings without stating a substantive rule
        • Surface keyword match only: a provision that shares a keyword with the
          case (e.g. "cheque", "employer", "court") but governs a different legal
          question — score on the legal question the provision addresses, not on
          shared surface words

Return the 0-based indices of all chunks scoring 5 or above, ordered from most
to least relevant. Aim for {target} indices, but return more if the case genuinely
involves multiple distinct legal issues that each require separate authority.
Do not return duplicates or near-identical provisions."""

_COMPRESS_SYSTEM = """You are a senior Kenyan advocate.

Extract only the sentence(s) from the CHUNK below that are directly relevant
to the case described. Rules:
- Return ONLY text that appears word-for-word in the CHUNK. Never use or adapt
  text from the CASE description.
- Preserve exact statutory language — do not paraphrase or summarise.
- If the entire chunk is relevant, return it unchanged.
- If nothing in the chunk is relevant, return an empty string."""


class _JudgeResult(BaseModel):
    selected_indices: list[int]


class _CompressResult(BaseModel):
    relevant_text: str


async def judge_chunks(case_text: str, chunks: list[str], target: int = 8) -> list[str]:
    """Score and select the most legally relevant chunks for this case.

    Falls back to returning all input chunks if the LLM call fails or if the
    LLM returns no selected indices (e.g. overly conservative scoring).
    """
    if not chunks:
        return chunks

    client = instructor.from_openai(get_async_client(), mode=instructor.Mode.JSON)
    numbered = "\n\n".join(f"[{i}] {chunk}" for i, chunk in enumerate(chunks))
    user_msg = f"CASE:\n{case_text}\n\nCHUNKS:\n{numbered}"
    system_msg = _JUDGE_SYSTEM_TEMPLATE.format(target=target)

    logger.info("rerank_judge_start", n_candidates=len(chunks), target=target)
    start = time.monotonic()

    try:
        result = await client.chat.completions.create(
            model=_MODEL,
            response_model=_JudgeResult,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
        )
        # dict.fromkeys preserves order while deduplicating any repeated indices
        indices = list(dict.fromkeys(i for i in result.selected_indices if 0 <= i < len(chunks)))
        selected = [chunks[i] for i in indices]
        if not selected:
            logger.warning("rerank_judge_empty", fallback="all_chunks")
            return chunks
    except Exception as exc:
        logger.warning("rerank_judge_failed", reason=str(exc), fallback="all_chunks")
        return chunks

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("rerank_judge_complete", selected=len(selected), duration_ms=duration_ms)
    return selected


async def _compress_one(
    client: instructor.AsyncInstructor,
    semaphore: asyncio.Semaphore,
    case_text: str,
    chunk: str,
    index: int,
) -> str:
    """Compress a single chunk to its relevant sentences. Returns original on failure."""
    async with semaphore:
        try:
            result = await client.chat.completions.create(
            model=_MODEL,
            response_model=_CompressResult,
            messages=[
                {"role": "system", "content": _COMPRESS_SYSTEM},
                {"role": "user", "content": f"CASE:\n{case_text}\n\nCHUNK:\n{chunk}"},
            ],
            temperature=0.0,
            )
            return result.relevant_text.strip()
        except Exception as exc:
            logger.warning("rerank_compress_chunk_failed", index=index, reason=str(exc))
            return chunk


async def compress_chunks(case_text: str, chunks: list[str]) -> list[str]:
    """Extract only the relevant sentences from each chunk in parallel.

    Chunks that compress to an empty string are dropped entirely.
    Falls back to the original chunk on individual compression failure.
    """
    if not chunks:
        return chunks

    client = instructor.from_openai(get_async_client(), mode=instructor.Mode.JSON)
    semaphore = asyncio.Semaphore(_MAX_COMPRESS_CONCURRENCY)

    logger.info("rerank_compress_start", n_chunks=len(chunks))
    start = time.monotonic()

    results = await asyncio.gather(
        *[_compress_one(client, semaphore, case_text, chunk, i) for i, chunk in enumerate(chunks)],
        return_exceptions=True,
    )

    output: list[str] = []
    for original, res in zip(chunks, results, strict=False):
        if isinstance(res, BaseException):
            logger.warning("rerank_compress_gather_failed", reason=str(res))
            output.append(original)
        else:
            # Judge already vetted this chunk; if compressor finds nothing,
            # keep the full chunk rather than silently discarding a relevant source.
            output.append(res if res else original)

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("rerank_compress_complete", n_chunks=len(output), duration_ms=duration_ms)
    return output
