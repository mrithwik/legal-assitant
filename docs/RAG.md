# RAG: retrieval augmented generation

The application grounds **legal strategy** in a curated **Kenyan statute corpus** under `data/raw/` (plain `.txt` and `.md` files). Embeddings and search use **OpenAI** `text-embedding-3-small` and **Pinecone** (cosine similarity, 1536 dimensions). Implementation details live in `backend/src/rag/`.

## Prerequisites

1. **Pinecone** serverless index: **1536** dimensions, **cosine** metric (must match `text-embedding-3-small`).
2. Environment variables (see `backend/.env.example`):
   - `PINECONE_API_KEY`
   - `PINECONE_INDEX_HOST` (preferred for serverless) **or** `PINECONE_INDEX_NAME`
   - Optional `PINECONE_NAMESPACE` to isolate environments in one index
3. **OpenAI** (or OpenRouter) API key for embeddings and LLM calls.

If Pinecone is not configured, `rag_retrieve` returns **no chunks**; the API still starts and the strategy step runs without RAG context.

## Ingestion

From the `backend/` directory, with statute files in `data/raw/`:

```bash
uv run python -m src.rag.ingestion
```

The ingestor chunks text (roughly 800 characters with overlap), embeds in batches, and upserts to Pinecone. Re-run when you add or change source documents.

## Retrieval pipeline (high level)

The retriever (`backend/src/rag/retriever.py`) applies multiple stages, including **query expansion** (LLM-generated sub-queries and statute hints), **metadata filtering** where applicable, **score thresholds**, **deduplication**, and **LLM-assisted selection / compression** of chunks (`query_expansion.py`, `reranker.py`). Exact thresholds and behavior are defined in code and may evolve; read those modules when tuning for production.

## Tuning and operations

- **Cost:** RAG adds embedding calls and often extra LLM calls for expansion and judging. Monitor token usage in logs (see [OPERATIONS.md](./OPERATIONS.md)).
- **Quality:** Improving the **source corpus** (cleaner text, better chunk boundaries) often beats only tweaking similarity scores.
- **Security:** Ingested text is stored in your Pinecone project; use **separate indexes or namespaces** per environment and restrict API keys.

## File reference

| File | Purpose |
|------|--------|
| `vector_store.py` | Embedding model id, dimension, Pinecone metadata key for chunk text |
| `pinecone_store.py` | Pinecone client and query helpers |
| `ingestion.py` | File walk, chunk, embed, upsert |
| `retriever.py` | End-to-end `rag_retrieve` used by the orchestrator |
| `query_expansion.py` | Sub-queries and statute scoping |
| `reranker.py` | LLM-based selection and trimming |
