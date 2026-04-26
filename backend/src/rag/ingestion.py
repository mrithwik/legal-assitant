"""Ingest Kenyan legal corpus from data/raw/ into Pinecone.

Run once to build the index:
    uv run python -m src.rag.ingestion

Re-run whenever documents are added to data/raw/.
Requires PINECONE_* and OPENAI_API_KEY (see .env.example).
"""

import asyncio
import re
import uuid
from pathlib import Path

from src.core.config import settings
from src.core.logging import configure_logging, get_logger
from src.core.openai_client import get_async_client
from src.rag.pinecone_store import get_pinecone_index, pinecone_configured
from src.rag.vector_store import EMBED_MODEL, PINECONE_METADATA_TEXT_KEY

logger = get_logger(__name__)

RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "raw"

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
_EMBED_BATCH_SIZE = 256
_PINECONE_UPSERT_BATCH = 100

# ── Substantive-chunk filter ───────────────────────────────────────────────────
# Kenyan legal documents all share the same publisher header + ARRANGEMENT OF
# SECTIONS table-of-contents structure. These blocks produce chunks that embed
# superficially well but contain no usable legal substance. Filtering them at
# ingest time keeps the Pinecone index clean.

_BOILERPLATE_EXACT = frozenset({
    "ARRANGEMENT OF SECTIONS",
    "ARRANGEMENT OF ARTICLES",
})

_BOILERPLATE_CONTAINS = (
    "www.kenyalaw.org",
    "National Council for Law Reporting",
    "Creative Commons",
    "FRBR URI",
)

# TOC entry: "1. Short title", "1—Short title", "1 – Short title", with any dash variant
_TOC_ENTRY_RE = re.compile(r"^\s*\d+[A-Z]?[\.\-—―–]\s*\S")
# Part header: "PART I", "PART II", "Part I" etc.
_PART_HEADER_RE = re.compile(r"^\s*PART\s+[IVX\d]+", re.IGNORECASE)
# Dot-leader line: "Short title .......................................... 5"
_DOT_LEADER_RE = re.compile(r"\.{4,}")
# Definition line: '”term” means/includes ...' pattern found in interpretation sections
_DEFINITION_LINE_RE = re.compile(r'[“””]?\w[\w\s\-]*[“””]?\s+(?:means|includes)\b', re.IGNORECASE)
# Interpretation section header: marks the start of a statutory definitions block
_INTERP_HEADER_RE = re.compile(
    r'\b(?:unless\s+the\s+context\s+otherwise\s+requires?|'
    r'in\s+this\s+(?:Act|Part|section|Schedule|Code|Constitution|Rules?))\b',
    re.IGNORECASE,
)
# Threshold: header + 3+ definition lines → interpretation section (not a substantive provision)
_MAX_DEFINITION_LINES = 2

_MIN_CHUNK_WORDS = 25
_MAX_TOC_LINE_RATIO = 0.45


def _is_substantive_chunk(chunk: str) -> bool:
    """Return False for table-of-contents, publisher header, and boilerplate chunks."""
    for phrase in _BOILERPLATE_EXACT:
        if phrase in chunk:
            return False
    for phrase in _BOILERPLATE_CONTAINS:
        if phrase in chunk:
            return False
    if len(chunk.split()) < _MIN_CHUNK_WORDS:
        return False
    lines = [ln for ln in chunk.splitlines() if ln.strip()]
    if not lines:
        return False
    toc_count = sum(
        1 for ln in lines
        if _TOC_ENTRY_RE.match(ln)
        or _PART_HEADER_RE.match(ln)
        or _DOT_LEADER_RE.search(ln)
    )
    if toc_count / len(lines) > _MAX_TOC_LINE_RATIO:
        return False
    # Interpretation/definitions sections list term meanings and embed poorly.
    # Require BOTH signals to avoid false positives on substantive provisions
    # that use "means" as a noun/verb (e.g. "means of escape" in OSHA fire-exit
    # sections): a statutory interpretation header AND 3+ "X means Y" lines.
    has_interp_header = bool(_INTERP_HEADER_RE.search(chunk))
    definition_count = sum(1 for ln in lines if _DEFINITION_LINE_RE.search(ln))
    if has_interp_header and definition_count > _MAX_DEFINITION_LINES:
        return False
    return True


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks that end on sentence boundaries.

    Splits the text into sentences first, then accumulates sentences into chunks
    of approximately `size` characters. A new chunk starts by repeating the last
    `overlap` characters worth of sentences from the previous chunk so context
    is preserved across boundaries. Chunks never break mid-sentence.
    """
    if not text.strip():
        return []

    # Split into sentences on ". ", ".\n", "? ", "! " while keeping the delimiter
    import re as _re
    raw_sentences = _re.split(r'(?<=[.?!])\s+', text.strip())
    sentences = [s.strip() for s in raw_sentences if s.strip()]

    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence_len = len(sentence) + 1  # +1 for the space between sentences
        if current and current_len + sentence_len > size:
            chunks.append(" ".join(current))
            # Roll back by overlap: drop sentences from the front until we're under overlap
            while current and current_len > overlap:
                removed = current.pop(0)
                current_len -= len(removed) + 1
        current.append(sentence)
        current_len += sentence_len

    if current:
        chunks.append(" ".join(current))

    return chunks


async def _ingest_documents_async(raw_dir: Path = RAW_DIR) -> dict:
    """Async implementation: embed via ``AsyncOpenAI`` and upsert to Pinecone."""
    txt_files = sorted(raw_dir.glob("*.txt")) + sorted(raw_dir.glob("*.md"))

    if not txt_files:
        logger.warning("ingestion_no_files_found", raw_dir=str(raw_dir))
        return {"detail": "no_files_found", "chunks_added": 0}

    logger.info("ingestion_start", raw_dir=str(raw_dir), file_count=len(txt_files))

    all_docs: list[str] = []
    all_ids: list[str] = []
    all_metadata: list[dict[str, str | int]] = []

    for fpath in txt_files:
        text = fpath.read_text(encoding="utf-8", errors="replace")
        file_chunks = [c for c in chunk_text(text) if _is_substantive_chunk(c)]
        for i, chunk in enumerate(file_chunks):
            all_docs.append(chunk)
            all_ids.append(f"{fpath.stem}_{i}_{uuid.uuid4().hex[:6]}")
            all_metadata.append(
                {
                    PINECONE_METADATA_TEXT_KEY: chunk,
                    "source": fpath.name,
                    "chunk_index": i,
                }
            )
        logger.info("ingestion_file_processed", file=fpath.name, chunks=len(file_chunks))

    if not all_docs:
        logger.warning("ingestion_no_content", raw_dir=str(raw_dir))
        return {"detail": "no_content", "chunks_added": 0}

    if not pinecone_configured():
        raise ValueError(
            "Pinecone is not configured. Set PINECONE_API_KEY and "
            "PINECONE_INDEX_HOST (or PINECONE_INDEX_NAME) in the environment."
        )

    logger.info(
        "ingestion_embed_start",
        total_chunks=len(all_docs),
        batch_size=_EMBED_BATCH_SIZE,
        model=EMBED_MODEL,
    )

    client = get_async_client()
    embeddings: list[list[float]] = []
    for i in range(0, len(all_docs), _EMBED_BATCH_SIZE):
        batch = all_docs[i : i + _EMBED_BATCH_SIZE]
        resp = await client.embeddings.create(model=EMBED_MODEL, input=batch)
        embeddings.extend(item.embedding for item in resp.data)
        logger.info(
            "ingestion_embed_batch_complete",
            batch_start=i,
            batch_end=min(i + _EMBED_BATCH_SIZE, len(all_docs)),
        )

    index = get_pinecone_index()
    ns = settings.pinecone_namespace.strip()

    # Delete existing vectors for each source file before upserting so that
    # re-ingestion is idempotent: updated files get fresh vectors and removed
    # files leave no orphans.  Metadata filter delete requires a paid Pinecone
    # plan (Starter supports it); on free pods this is a no-op and duplicates
    # must be cleared by deleting the namespace manually.
    ingested_sources = {m["source"] for m in all_metadata}
    for source_name in ingested_sources:
        try:
            delete_kwargs: dict = {"filter": {"source": {"$eq": source_name}}}
            if ns:
                delete_kwargs["namespace"] = ns
            index.delete(**delete_kwargs)
            logger.info("ingestion_deleted_existing", source=source_name)
        except Exception as exc:
            logger.warning("ingestion_delete_skipped", source=source_name, reason=str(exc))

    for start in range(0, len(all_docs), _PINECONE_UPSERT_BATCH):
        end = start + _PINECONE_UPSERT_BATCH
        batch_vectors = [
            {"id": all_ids[j], "values": embeddings[j], "metadata": all_metadata[j]}
            for j in range(start, min(end, len(all_docs)))
        ]
        upsert_kwargs: dict = {"vectors": batch_vectors}
        if ns:
            upsert_kwargs["namespace"] = ns
        index.upsert(**upsert_kwargs)
        logger.info(
            "ingestion_upsert_batch_complete",
            batch_start=start,
            batch_end=min(end, len(all_docs)),
        )

    logger.info("ingestion_complete", chunks_added=len(all_docs))
    return {"detail": "ok", "chunks_added": len(all_docs)}


def ingest_documents(raw_dir: Path = RAW_DIR, persist_dir: str | None = None) -> dict:
    """Load all .txt and .md files from raw_dir, chunk, embed, and upsert to Pinecone.

    ``persist_dir`` is accepted for backward compatibility with tests and callers
    that previously pointed at Chroma on disk; it is ignored.

    Synchronous entry point for scripts and tests; runs the async pipeline with
    ``asyncio.run``.
    """
    _ = persist_dir
    return asyncio.run(_ingest_documents_async(raw_dir=raw_dir))


if __name__ == "__main__":
    configure_logging()
    result = ingest_documents()
    print(result)
