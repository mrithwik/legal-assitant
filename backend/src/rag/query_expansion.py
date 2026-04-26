"""Query expansion for RAG retrieval.

Instead of embedding the full case text as a single query (which dilutes specific
legal signals), this module asks GPT-4o-mini to:

  1. Extract a structured legal summary (issues, statutes, key facts)
  2. Generate focused search queries from that summary

The structured summary is returned alongside the queries and used as the context
string for the LLM judge and contextual compressor — giving them a clean,
noise-free view of the case rather than the raw formatted document.

Falls back to a heuristic sentence-scoring approach if the LLM call fails,
selecting sentences with the highest density of legal terminology so the fallback
embeds well against statute chunks.
"""

import re
import time

import instructor
from pydantic import BaseModel

from src.core.logging import get_logger
from src.core.openai_client import get_async_client

logger = get_logger(__name__)

_MODEL = "gpt-4o-mini"
_N_QUERIES = 7

_PROMPT = f"""You are a Kenyan legal research assistant.

Given a case description (which may be a short prose summary or a full formatted
legal document with headers, party lists, and timelines), extract the legally
significant information and generate search queries.

Return a JSON object with exactly these fields:

"legal_issues": list of distinct legal issues in dispute — one entry per issue,
  stated concisely using precise legal terminology. Include every statutory
  provision or doctrine mentioned, even briefly. Do NOT consolidate separate
  issues into one entry.

"applicable_statutes": list of statute names mentioned or clearly implied
  (e.g. "Succession Act Cap 160", "Employment Act 2007"). Include section numbers
  if specified in the case. Key statute mappings to know:
  - Workplace injuries, occupational accidents, employer liability for employee injury
    → "Work Injury Benefits Act Cap 236" (WIBA). This Act bars tort claims against
    employers (s.16) and provides statutory compensation (s.10). Always include it
    for any case involving an employee injured at work, even if OSHA is also cited.
  - Constitutional rights violations, Bill of Rights → "Constitution of Kenya 2010"

"key_facts": list of facts that are legally material to the outcome — dates that
  affect limitation periods, presence or absence of written agreements, capacity
  issues, prior proceedings, etc. Omit party names and monetary amounts unless
  they are legally material (e.g. a limitation period argument requires the date).

"queries": exactly {_N_QUERIES} short, focused search queries (5–10 words each)
  for retrieving relevant Kenyan legal precedents and statutes from a vector
  database. Rules for generating queries:
  - Each query must target ONE specific legal issue using precise legal terminology.
  - Do not include party names.
  - Do not repeat concepts — each query must address a distinct legal question.
  - When WIBA (Work Injury Benefits Act Cap 236) is an applicable statute, one
    query must specifically target the employer's statutory bar to tort claims,
    using language close to the Act's text, for example:
    "no action employee employer tort damages personal injury Kenya"
  - When specific constitutional articles are cited by number in the case
    (e.g. Article 47, Article 33, Article 50), generate one query per cited
    article that includes both the article number and its subject matter, e.g.:
    "Article 47 fair administrative action Constitution Kenya",
    "Article 33 freedom of expression Constitution Kenya",
    "Article 50 fair hearing right Constitution Kenya"
  - For cases spanning multiple distinct statutes (e.g. Employment Act plus the
    Constitution, or WIBA plus OSHA), allocate queries proportionally — do not
    concentrate all queries on one statute and neglect the others."""

_LEGAL_TERM_RE = re.compile(
    r"\b(section|article|act|code|court|liability|breach|contract|negligence|"
    r"damages|tort|statute|offence|petition|plaintiff|defendant|appeal|"
    r"jurisdiction|remedy|injunction|employment|succession|arbitration|"
    r"limitation|constitution|procedure|evidence|capacity|possession|lease|"
    r"tenure|fraud|misrepresentation|estoppel|consideration|termination|"
    r"dismissal|compensation|penalty|forfeiture|indemnity)\b",
    re.IGNORECASE,
)


class _CaseSummary(BaseModel):
    legal_issues: list[str]
    applicable_statutes: list[str]
    key_facts: list[str]
    queries: list[str]


def _format_context(summary: _CaseSummary) -> str:
    """Format structured summary into a context string for the judge and compressor."""
    lines: list[str] = []
    if summary.legal_issues:
        lines.append("Legal issues in dispute:")
        lines.extend(f"  - {issue}" for issue in summary.legal_issues)
    if summary.applicable_statutes:
        lines.append("Applicable statutes:")
        lines.extend(f"  - {s}" for s in summary.applicable_statutes)
    if summary.key_facts:
        lines.append("Key legally material facts:")
        lines.extend(f"  - {f}" for f in summary.key_facts)
    return "\n".join(lines)


def _extract_fallback_context(case_text: str, max_sentences: int = 8) -> str:
    """Extract the most legally relevant sentences without an LLM call.

    Scores each sentence by count of legal term matches and returns the
    top-scoring sentences joined as a single string. Used when the LLM call
    fails so the fallback embeds meaningfully rather than as a diluted
    full-document vector.
    """
    sentences = re.split(r"(?<=[.?!])\s+", case_text.strip())
    scored = sorted(
        [(len(_LEGAL_TERM_RE.findall(s)), s) for s in sentences if s.strip()],
        key=lambda x: -x[0],
    )
    top = [s for _, s in scored[:max_sentences] if s.strip()]
    return " ".join(top) if top else case_text[:800]


async def expand_query(case_text: str) -> tuple[str, list[str], list[str]]:
    """Return (context_string, queries, applicable_statutes) derived from the case text.

    context_string: structured legal summary formatted for the LLM judge and
      contextual compressor. On LLM failure, falls back to a heuristic
      sentence-scored extraction of the most legally relevant sentences.

    queries: focused Pinecone search queries. On LLM failure, falls back to a
      single-element list containing the fallback context string.

    applicable_statutes: statute names identified by the LLM (e.g. "Employment
      Act 2007"). Empty list on LLM failure.
    """
    if not case_text.strip():
        return case_text, [case_text], []

    client = instructor.from_openai(get_async_client(), mode=instructor.Mode.JSON)

    logger.info("query_expansion_start", model=_MODEL, n_queries=_N_QUERIES)
    start = time.monotonic()

    try:
        result = await client.chat.completions.create(
            model=_MODEL,
            response_model=_CaseSummary,
            messages=[
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": case_text},
            ],
            temperature=0.2,
        )
        queries = [q.strip() for q in result.queries if q.strip()][:_N_QUERIES]
        statutes = list(result.applicable_statutes)
        context = _format_context(result)
    except Exception as exc:
        logger.warning("query_expansion_failed", reason=str(exc), fallback="heuristic")
        fallback = _extract_fallback_context(case_text)
        return fallback, [fallback], []

    duration_ms = round((time.monotonic() - start) * 1000, 1)
    logger.info("query_expansion_complete", n_queries=len(queries), duration_ms=duration_ms)

    if not queries:
        fallback = _extract_fallback_context(case_text)
        return context or fallback, [fallback], []

    return context, queries, statutes
