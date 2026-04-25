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

_JUDGE_SYSTEM_TEMPLATE = """You are a senior Kenyan advocate reviewing retrieved legal text.

Given a case description and a numbered list of retrieved chunks, select the
chunks that are genuinely useful for analysing this specific case.

Scoring guide:
  8–10  directly on point — cites the applicable statute, doctrine, or test
  5–7   useful context — related area of law or analogous principle
  0–4   not useful — wrong area of law, index entry, or procedural boilerplate

Return the 0-based indices of all chunks scoring 5 or above, ordered from most
to least relevant. Aim for {target} indices, but return more if the case genuinely
involves multiple distinct legal issues that each require separate authority.
Do not return duplicates or near-identical provisions."""

_COMPRESS_SYSTEM = """You are a senior Kenyan advocate.

Extract only the sentence(s) from the following legal text chunk that are
directly relevant to the case described. Preserve exact statutory language —
do not paraphrase or summarise. If the entire chunk is relevant, return it
unchanged. If nothing is relevant, return an empty string."""


class _JudgeResult(BaseModel):
    selected_indices: list[int]


class _CompressResult(BaseModel):
    relevant_text: str


async def judge_chunks(case_text: str, chunks: list[str], target: int = 6) -> list[str]:
    """Score and select the most legally relevant chunks for this case.

    Falls back to returning all input chunks if the LLM call fails.
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
    except Exception as exc:
        logger.warning("rerank_judge_failed", reason=str(exc), fallback="all_chunks")
        return chunks

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("rerank_judge_complete", selected=len(selected), duration_ms=duration_ms)
    return selected


async def _compress_one(
    client: instructor.AsyncInstructor, case_text: str, chunk: str, index: int
) -> str:
    """Compress a single chunk to its relevant sentences. Returns original on failure."""
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

    logger.info("rerank_compress_start", n_chunks=len(chunks))
    start = time.monotonic()

    results = await asyncio.gather(
        *[_compress_one(client, case_text, chunk, i) for i, chunk in enumerate(chunks)],
        return_exceptions=True,
    )

    output: list[str] = []
    for original, res in zip(chunks, results, strict=False):
        if isinstance(res, BaseException):
            logger.warning("rerank_compress_gather_failed", reason=str(res))
            output.append(original)
        elif res:
            output.append(res)
        # empty string → drop the chunk

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("rerank_compress_complete", n_chunks=len(output), duration_ms=duration_ms)
    return output
