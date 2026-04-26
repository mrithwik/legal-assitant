"""Tests for the Retrieval Improvements feature.

Seven test groups:
  1. _is_substantive_chunk    — pure unit tests for the ingestion-time filter
  2. _source_filter_for_query — statute-aware Pinecone metadata filter
  3. _dedup_fuzzy             — near-duplicate removal (containment + Jaccard)
  4. score threshold          — low-score Pinecone matches are dropped
  5. judge_chunks             — LLM judge selects relevant subset
  6. compress_chunks          — LLM contextual compression
  7. rag_retrieve integration — full pipeline with judge + compress called

All LLM and Pinecone calls are mocked; no network, no API cost.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from src.rag.ingestion import _is_substantive_chunk
from src.rag.reranker import compress_chunks, judge_chunks
from src.rag.retriever import (
    _STATUTE_GUARANTEE_QUERIES,
    _dedup_fuzzy,
    _source_filter_for_query,
    rag_retrieve,
)
from src.rag.vector_store import PINECONE_METADATA_TEXT_KEY

# ── 1. _is_substantive_chunk ──────────────────────────────────────────────────


def test_substantive_chunk_accepts_normal_legal_text():
    chunk = (
        "Section 3(3) of the Law of Contract Act provides that no suit shall be "
        "brought upon a contract for the disposition of an interest in land unless "
        "the contract is in writing and signed by all parties thereto. This provision "
        "requires strict compliance and has been interpreted broadly by Kenyan courts "
        "to include agreements for sale, leases, and other dispositions of land."
    )
    assert _is_substantive_chunk(chunk) is True


def test_substantive_chunk_rejects_arrangement_of_sections():
    chunk = (
        "CHAPTER 23\nLAW OF CONTRACT ACT\nARRANGEMENT OF SECTIONS\n"
        "Section\n1. Short title.\n2. English law of contract to apply in Kenya.\n"
        "3. Certain contracts to be in writing.\n4. Application of Indian Act."
    )
    assert _is_substantive_chunk(chunk) is False


def test_substantive_chunk_rejects_arrangement_of_articles():
    chunk = (
        "THE CONSTITUTION OF KENYA, 2010\nARRANGEMENT OF ARTICLES\n"
        "1—Sovereignty of the people.\n2—Supremacy of this Constitution."
    )
    assert _is_substantive_chunk(chunk) is False


def test_substantive_chunk_rejects_kenyalaw_url():
    chunk = (
        "LAWS OF KENYA\nPublished by the National Council for Law Reporting\n"
        "with the Authority of the Attorney-General\nwww.kenyalaw.org\n"
        "Revised Edition 2012 [2002]"
    )
    assert _is_substantive_chunk(chunk) is False


def test_substantive_chunk_rejects_national_council_boilerplate():
    chunk = (
        "National Council for Law Reporting\nwith the authority of the "
        "Attorney-General as gazetted by the Government Printer"
    )
    assert _is_substantive_chunk(chunk) is False


def test_substantive_chunk_rejects_creative_commons_boilerplate():
    chunk = (
        "This PDF copy is licensed under a Creative Commons Attribution "
        "NonCommercial ShareAlike 4.0 License. FRBR URI: /akn/ke/act/1972/14."
    )
    assert _is_substantive_chunk(chunk) is False


def test_substantive_chunk_rejects_toc_heavy_chunk():
    # > 45% of lines are TOC entries
    chunk = (
        "LAND ACT, 2012\n"
        "1—Short title.\n"
        "2—Interpretation.\n"
        "3—Application.\n"
        "4—Guiding values and principles.\n"
        "5—Forms of tenure.\n"
        "6—Land management and administration.\n"
        "7—Methods of acquisition of title to land."
    )
    assert _is_substantive_chunk(chunk) is False


def test_substantive_chunk_rejects_dot_leader_toc():
    chunk = (
        "Contents\n"
        "1. Short title .......................................................................  1\n"
        "2. Application of Act ................................................................  1\n"
        "3. Interpretation ....................................................................  2\n"
        "4. Law applicable to succession .....................................................  4\n"
        "Part II – WILLS ......................................................................  5\n"
        "5. Persons capable of making wills ..................................................  5"
    )
    assert _is_substantive_chunk(chunk) is False


def test_substantive_chunk_rejects_very_short_chunk():
    # Under _MIN_CHUNK_WORDS
    assert _is_substantive_chunk("CAP. 23\nL11 - 3\n[Rev. 2012]") is False


def test_substantive_chunk_accepts_mixed_text_with_some_section_refs():
    # Has a section reference but mostly prose — should not be rejected
    chunk = (
        "The court in Nairobi held that section 38 of the Land Act 2012 applies "
        "to agreements for the sale of public land where part performance has been "
        "demonstrated. The plaintiff had made substantial improvements to the land "
        "and paid a deposit of Kshs 500,000. The court found that equity would "
        "intervene to prevent the defendant from relying on the absence of a written "
        "contract as a defence to the claim for specific performance."
    )
    assert _is_substantive_chunk(chunk) is True


# ── 2. _source_filter_for_query ───────────────────────────────────────────────


def test_source_filter_detects_land_act():
    result = _source_filter_for_query("specific performance Land Act Kenya")
    assert result == ["land_act_2012.txt"]


def test_source_filter_detects_contract_act():
    result = _source_filter_for_query("written contract law of contract act requirement")
    assert result is not None
    assert "contract_act_cap_23.txt" in result


def test_source_filter_detects_employment_act():
    result = _source_filter_for_query("unfair dismissal Employment Act Kenya")
    assert result == ["employment_act_2007.txt"]


def test_source_filter_detects_succession_act():
    result = _source_filter_for_query("intestate succession act distribution of estate")
    assert result == ["succession_act_cap_160.txt"]


def test_source_filter_returns_none_for_generic_query():
    result = _source_filter_for_query("breach of duty negligence damages")
    assert result is None


def test_source_filter_returns_multiple_sources_when_multiple_statutes_mentioned():
    result = _source_filter_for_query("land act and law of contract act overlap")
    assert result is not None
    assert "land_act_2012.txt" in result
    assert "contract_act_cap_23.txt" in result


def test_source_filter_is_case_insensitive():
    result = _source_filter_for_query("LAND ACT section 38 specific performance")
    assert result == ["land_act_2012.txt"]


def test_source_filter_no_false_positive_on_partial_word_match():
    # "contract action" contains the substring "contract act" but is NOT a statute name
    result = _source_filter_for_query("breach of contract action for damages")
    assert result is None


def test_source_filter_detects_wiba_by_occupational_safety():
    result = _source_filter_for_query("occupational safety employer liability injury")
    assert result == ["work_injury_benefits_act_cap236.txt"]


def test_source_filter_detects_wiba_by_work_accident():
    result = _source_filter_for_query("work accident employer compensation Kenya")
    assert result == ["work_injury_benefits_act_cap236.txt"]


def test_source_filter_detects_wiba_by_personal_protective_equipment():
    result = _source_filter_for_query("personal protective equipment employer duty")
    assert result == ["work_injury_benefits_act_cap236.txt"]


# ── 3. _dedup_fuzzy ──────────────────────────────────────────────────────────


def test_dedup_fuzzy_passes_through_distinct_chunks():
    a = "Section 7 of the Civil Procedure Act bars relitigation of the same matter."
    b = "An employer is liable to pay compensation to an employee injured while at work."
    assert _dedup_fuzzy([a, b]) == [a, b]


def test_dedup_fuzzy_removes_exact_contained_substring():
    short = "Any person who draws or issues a cheque is guilty of a misdemeanour."
    long = (
        "Any person who draws or issues a cheque is guilty of a misdemeanour. "
        "Subsection (1)(a) does not apply with respect to a post-dated cheque."
    )
    result = _dedup_fuzzy([short, long])
    assert result == [long]


def test_dedup_fuzzy_keeps_longer_when_shorter_arrives_first():
    short = "No court shall try any suit in which the matter was already in issue."
    long = (
        "No court shall try any suit in which the matter was already in issue "
        "between the same parties litigating under the same title in a court of competent jurisdiction."
    )
    result = _dedup_fuzzy([short, long])
    assert result == [long]


def test_dedup_fuzzy_keeps_longer_when_longer_arrives_first():
    long = (
        "No court shall try any suit in which the matter was already in issue "
        "between the same parties litigating under the same title in a court of competent jurisdiction."
    )
    short = "No court shall try any suit in which the matter was already in issue."
    result = _dedup_fuzzy([long, short])
    assert result == [long]


def test_dedup_fuzzy_removes_high_jaccard_duplicate():
    a = "Every person has the right to administrative action that is expeditious efficient lawful reasonable and procedurally fair."
    b = "Every person has the right to administrative action that is expeditious, efficient, lawful, reasonable and procedurally fair."
    result = _dedup_fuzzy([a, b])
    assert len(result) == 1


def test_dedup_fuzzy_removes_subset_with_extra_header():
    """Chunk with section header should absorb the same chunk without the header,
    even when the substring check fails due to words inserted by the header."""
    with_header = (
        "Unfair termination. 45. No employer shall terminate the employment "
        "of an employee unfairly. A termination is unfair if the employer "
        "fails to prove the reason is valid and procedure was fair."
    )
    without_header = (
        "45. No employer shall terminate the employment of an employee "
        "unfairly. A termination is unfair if the employer fails to prove "
        "the reason is valid and procedure was fair."
    )
    result = _dedup_fuzzy([with_header, without_header])
    assert len(result) == 1
    assert result[0] == with_header


def test_dedup_fuzzy_preserves_distinct_provisions_with_shared_words():
    # Both use common legal words but are clearly different provisions
    a = "No employer shall terminate the employment of an employee unfairly without valid reason."
    b = "No court shall try any suit where the matter was directly in issue between the same parties."
    result = _dedup_fuzzy([a, b])
    assert result == [a, b]


def test_dedup_fuzzy_empty_input():
    assert _dedup_fuzzy([]) == []


def test_dedup_fuzzy_single_chunk():
    chunk = "Section 23 of the Limitation of Actions Act provides for fresh accrual on acknowledgement."
    assert _dedup_fuzzy([chunk]) == [chunk]


# ── 4. Score threshold ────────────────────────────────────────────────────────


def _scored_index_mock(docs_with_scores: list[tuple[str, float]]):
    """Pinecone index mock returning matches with explicit score attributes."""
    matches = []
    for text, score in docs_with_scores:
        m = MagicMock()
        m.score = score
        m.metadata = {PINECONE_METADATA_TEXT_KEY: text}
        matches.append(m)
    idx = MagicMock()
    idx.query.return_value = MagicMock(matches=matches)
    return idx


async def test_score_threshold_drops_low_score_matches():
    idx = _scored_index_mock([("High score chunk.", 0.85), ("Low score chunk.", 0.55)])
    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = AsyncMock(
            return_value=MagicMock(data=[MagicMock(embedding=[0.1] * 1536)])
        )
        result = await rag_retrieve("some query")
    assert "High score chunk." in result
    assert "Low score chunk." not in result


async def test_score_threshold_accepts_match_exactly_at_threshold():
    idx = _scored_index_mock([("Borderline chunk.", 0.60)])
    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = AsyncMock(
            return_value=MagicMock(data=[MagicMock(embedding=[0.1] * 1536)])
        )
        result = await rag_retrieve("some query")
    assert "Borderline chunk." in result


async def test_score_threshold_treats_none_score_as_passing():
    """A match with score=None must not raise — it should be treated as passing."""
    idx = MagicMock()
    m = MagicMock()
    m.score = None
    m.metadata = {PINECONE_METADATA_TEXT_KEY: "Chunk with null score."}
    idx.query.return_value = MagicMock(matches=[m])
    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = AsyncMock(
            return_value=MagicMock(data=[MagicMock(embedding=[0.1] * 1536)])
        )
        result = await rag_retrieve("some query")
    assert result == ["Chunk with null score."]


async def test_score_threshold_drops_all_below_threshold_returns_empty():
    idx = _scored_index_mock([("Bad chunk 1.", 0.50), ("Bad chunk 2.", 0.55)])
    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = AsyncMock(
            return_value=MagicMock(data=[MagicMock(embedding=[0.1] * 1536)])
        )
        result = await rag_retrieve("some query")
    assert result == []


# ── 5. judge_chunks ───────────────────────────────────────────────────────────


def _judge_mock(indices: list[int]):
    mock_result = MagicMock()
    mock_result.selected_indices = indices
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_result)
    return mock_client


async def test_judge_chunks_returns_selected_chunks_in_order():
    chunks = ["Chunk A.", "Chunk B.", "Chunk C.", "Chunk D."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = _judge_mock([2, 0])
        mock_instr.Mode.JSON = "json"
        result = await judge_chunks("some case", chunks)
    assert result == ["Chunk C.", "Chunk A."]


async def test_judge_chunks_deduplicates_repeated_indices():
    chunks = ["Chunk A.", "Chunk B.", "Chunk C."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = _judge_mock([0, 0, 1])
        mock_instr.Mode.JSON = "json"
        result = await judge_chunks("case", chunks)
    assert result == ["Chunk A.", "Chunk B."]


async def test_judge_chunks_filters_out_of_range_indices():
    chunks = ["A.", "B."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = _judge_mock([0, 99, 1])
        mock_instr.Mode.JSON = "json"
        result = await judge_chunks("case", chunks)
    assert result == ["A.", "B."]


async def test_judge_chunks_returns_all_chunks_on_llm_failure():
    chunks = ["Chunk A.", "Chunk B.", "Chunk C."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = MagicMock(
            chat=MagicMock(
                completions=MagicMock(create=AsyncMock(side_effect=Exception("LLM down")))
            )
        )
        mock_instr.Mode.JSON = "json"
        result = await judge_chunks("case", chunks)
    assert result == chunks


async def test_judge_chunks_empty_input_returns_empty():
    with patch("src.rag.reranker.instructor"):
        result = await judge_chunks("case", [])
    assert result == []


async def test_judge_chunks_target_is_sent_to_llm():
    """The target value must appear in the system prompt sent to the LLM."""
    chunks = ["Chunk A.", "Chunk B."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(selected_indices=[0])
        )
        mock_instr.from_openai.return_value = mock_client
        mock_instr.Mode.JSON = "json"
        await judge_chunks("case", chunks, target=10)

    call_messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    system_content = call_messages[0]["content"]
    assert "Aim for 10 indices" in system_content


async def test_judge_chunks_returns_all_chunks_when_llm_returns_empty_indices():
    """If the LLM returns no selected indices, fall back to all input chunks."""
    chunks = ["Chunk A.", "Chunk B.", "Chunk C."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = _judge_mock([])
        mock_instr.Mode.JSON = "json"
        result = await judge_chunks("case", chunks)
    assert result == chunks


async def test_judge_chunks_can_return_more_than_target_when_warranted():
    chunks = [f"Chunk {i}." for i in range(10)]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = _judge_mock(list(range(8)))
        mock_instr.Mode.JSON = "json"
        result = await judge_chunks("complex multi-issue case", chunks, target=6)
    assert len(result) == 8


async def test_judge_prompt_includes_wrong_branch_of_law_guidance():
    """System prompt must explicitly warn that criminal provisions are 0-4 in civil cases."""
    chunks = ["A chunk."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(selected_indices=[0])
        )
        mock_instr.from_openai.return_value = mock_client
        mock_instr.Mode.JSON = "json"
        await judge_chunks("civil case", chunks)

    system_content = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "criminal" in system_content.lower()
    assert "0–4" in system_content or "0-4" in system_content


async def test_judge_prompt_warns_against_surface_keyword_match():
    """System prompt must instruct the judge to score on legal question, not shared words."""
    chunks = ["A chunk."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(selected_indices=[0])
        )
        mock_instr.from_openai.return_value = mock_client
        mock_instr.Mode.JSON = "json"
        await judge_chunks("civil case", chunks)

    system_content = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "keyword" in system_content.lower() or "surface" in system_content.lower()


async def test_judge_prompt_mentions_citation_clause_as_boilerplate():
    """System prompt must flag 'This Act may be cited as' as 0-4 boilerplate."""
    chunks = ["A chunk."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(selected_indices=[0])
        )
        mock_instr.from_openai.return_value = mock_client
        mock_instr.Mode.JSON = "json"
        await judge_chunks("civil case", chunks)

    system_content = mock_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "cited as" in system_content.lower()


# ── 6. compress_chunks ────────────────────────────────────────────────────────


def _compress_mock(texts: list[str]):
    """instructor client mock that returns successive compressed texts."""
    results = [MagicMock(relevant_text=t) for t in texts]
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=results)
    return mock_client


async def test_compress_chunks_returns_compressed_text():
    chunks = ["Long chunk with irrelevant preamble. Relevant sentence here.", "Another chunk."]
    compressed = ["Relevant sentence here.", "Another chunk."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = _compress_mock(compressed)
        mock_instr.Mode.JSON = "json"
        result = await compress_chunks("case text", chunks)
    assert result == compressed


async def test_compress_chunks_falls_back_to_original_on_empty_compression():
    # If the compressor returns "" for a judge-approved chunk, the original is
    # kept rather than dropped — the judge is the gatekeeper, not the compressor.
    chunks = ["Relevant chunk.", "Irrelevant chunk — nothing useful here."]
    compressed = ["Relevant chunk.", ""]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_instr.from_openai.return_value = _compress_mock(compressed)
        mock_instr.Mode.JSON = "json"
        result = await compress_chunks("case text", chunks)
    assert result == ["Relevant chunk.", "Irrelevant chunk — nothing useful here."]


async def test_compress_chunks_falls_back_to_original_on_individual_failure():
    chunks = ["Chunk A.", "Chunk B."]
    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[MagicMock(relevant_text="Compressed A."), Exception("API error")]
        )
        mock_instr.from_openai.return_value = mock_client
        mock_instr.Mode.JSON = "json"
        result = await compress_chunks("case text", chunks)
    assert result[0] == "Compressed A."
    assert result[1] == "Chunk B."


async def test_compress_chunks_runs_all_compressions_in_parallel():
    chunks = ["A.", "B.", "C."]
    call_count = {"n": 0}

    async def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        return MagicMock(relevant_text="compressed")

    with patch("src.rag.reranker.instructor") as mock_instr:
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=_side_effect)
        mock_instr.from_openai.return_value = mock_client
        mock_instr.Mode.JSON = "json"
        result = await compress_chunks("case", chunks)
    assert call_count["n"] == 3
    assert len(result) == 3


async def test_compress_chunks_empty_input_returns_empty():
    with patch("src.rag.reranker.instructor"):
        result = await compress_chunks("case", [])
    assert result == []


# ── 7. rag_retrieve integration ───────────────────────────────────────────────


def _index_mock_for_docs(texts: list[str], score: float = 0.85):
    matches = []
    for t in texts:
        m = MagicMock()
        m.score = score
        m.metadata = {PINECONE_METADATA_TEXT_KEY: t}
        matches.append(m)
    idx = MagicMock()
    idx.query.return_value = MagicMock(matches=matches)
    return idx


def _embed_mock():
    return AsyncMock(return_value=MagicMock(data=[MagicMock(embedding=[0.1] * 1536)]))


async def test_rag_retrieve_calls_judge_with_deduplicated_candidates():
    """judge_chunks receives the post-dedup candidate list, not raw Pinecone results."""
    idx = _index_mock_for_docs(["Chunk A.", "Chunk B."])
    judge_spy = AsyncMock(side_effect=lambda q, c, **kw: c)

    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=judge_spy),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = _embed_mock()
        await rag_retrieve("case text")

    assert judge_spy.called
    _, call_chunks = judge_spy.call_args.args
    assert call_chunks == ["Chunk A.", "Chunk B."]


async def test_rag_retrieve_calls_compress_with_judge_output():
    """compress_chunks receives whatever judge_chunks returned."""
    idx = _index_mock_for_docs(["Chunk A.", "Chunk B.", "Chunk C."])
    compress_spy = AsyncMock(side_effect=lambda q, c: c)

    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(return_value=["Chunk A."])),
        patch("src.rag.retriever.compress_chunks", new=compress_spy),
    ):
        mock_openai.embeddings.create = _embed_mock()
        await rag_retrieve("case text")

    _, call_chunks = compress_spy.call_args.args
    assert call_chunks == ["Chunk A."]


async def test_rag_retrieve_final_result_is_compress_output():
    """The value returned by compress_chunks is what rag_retrieve returns."""
    idx = _index_mock_for_docs(["Raw chunk."])
    final = ["Compressed and relevant sentence."]

    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(return_value=final)),
    ):
        mock_openai.embeddings.create = _embed_mock()
        result = await rag_retrieve("case text")

    assert result == final


async def test_rag_retrieve_skips_judge_and_compress_when_no_candidates():
    """If all Pinecone matches are below the score threshold, judge/compress are not called."""
    idx = _scored_index_mock([("Low score.", 0.50)])
    judge_spy = AsyncMock(side_effect=lambda q, c, **kw: c)
    compress_spy = AsyncMock(side_effect=lambda q, c: c)

    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=judge_spy),
        patch("src.rag.retriever.compress_chunks", new=compress_spy),
    ):
        mock_openai.embeddings.create = AsyncMock(
            return_value=MagicMock(data=[MagicMock(embedding=[0.1] * 1536)])
        )
        result = await rag_retrieve("case text")

    assert result == []
    judge_spy.assert_not_called()
    compress_spy.assert_not_called()


async def test_rag_retrieve_applies_statute_filter_when_statute_detected():
    """Pinecone query must include source filter when a statute name is in the query."""
    idx = _index_mock_for_docs(["Land Act chunk."])
    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = _embed_mock()
        await rag_retrieve("specific performance Land Act Kenya")

    call_kwargs = idx.query.call_args.kwargs
    assert "filter" in call_kwargs
    assert call_kwargs["filter"] == {"source": {"$in": ["land_act_2012.txt"]}}


async def test_rag_retrieve_no_statute_filter_for_generic_query():
    """Pinecone query must NOT include a source filter for generic legal queries."""
    idx = _index_mock_for_docs(["Some chunk."])
    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch("src.rag.retriever.expand_query", new=AsyncMock(side_effect=lambda q: (q, [q], []))),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = _embed_mock()
        await rag_retrieve("breach of duty negligence damages")

    call_kwargs = idx.query.call_args.kwargs
    assert "filter" not in call_kwargs


# ── 8. Guarantee queries ──────────────────────────────────────────────────────


def test_guarantee_queries_wiba_entry_exists():
    """WIBA file must have at least one guarantee query targeting the tort bar."""
    assert "work_injury_benefits_act_cap236.txt" in _STATUTE_GUARANTEE_QUERIES
    queries = _STATUTE_GUARANTEE_QUERIES["work_injury_benefits_act_cap236.txt"]
    assert len(queries) >= 1
    combined = " ".join(queries).lower()
    assert any(kw in combined for kw in ("tort", "no action", "damages"))


def test_guarantee_queries_not_defined_for_civil_procedure_act():
    """Civil Procedure Act has no guarantee queries — its key provisions embed well."""
    assert "civil_procedure_act_cap21.txt" not in _STATUTE_GUARANTEE_QUERIES


def test_guarantee_queries_not_defined_for_land_act():
    """Land Act has no guarantee queries — its key provisions embed well."""
    assert "land_act_2012.txt" not in _STATUTE_GUARANTEE_QUERIES


async def test_guarantee_query_fires_against_wiba_file_when_wiba_in_statutes():
    """When WIBA is in applicable_statutes, the guarantee query must be sent to
    Pinecone filtered to the WIBA source file, in addition to the supplemental search."""
    idx = _index_mock_for_docs(["WIBA chunk."])
    wiba_filter = {"source": {"$in": ["work_injury_benefits_act_cap236.txt"]}}

    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch(
            "src.rag.retriever.expand_query",
            new=AsyncMock(return_value=(
                "employee injured workplace",
                ["employee injured at workplace PPE not provided Kenya"],
                ["Work Injury Benefits Act Cap 236"],
            )),
        ),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = _embed_mock()
        await rag_retrieve("employee fell at construction site and was injured")

    all_filters = [call.kwargs.get("filter") for call in idx.query.call_args_list]
    assert wiba_filter in all_filters, (
        f"Expected a Pinecone call filtered to WIBA file. Actual filters: {all_filters}"
    )


async def test_guarantee_query_fires_in_addition_to_supplemental_search():
    """When WIBA is applicable, both the rag_context supplemental search AND the
    guarantee query search must run — they serve different retrieval purposes."""
    idx = _index_mock_for_docs(["WIBA chunk."])
    wiba_filter = {"source": {"$in": ["work_injury_benefits_act_cap236.txt"]}}

    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch(
            "src.rag.retriever.expand_query",
            new=AsyncMock(return_value=(
                "employee injured workplace",
                ["employee injured at workplace Kenya"],
                ["Work Injury Benefits Act Cap 236"],
            )),
        ),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = _embed_mock()
        await rag_retrieve("employee fell and was injured at work")

    # Count how many Pinecone calls used the WIBA filter
    wiba_calls = [
        call for call in idx.query.call_args_list
        if call.kwargs.get("filter") == wiba_filter
    ]
    # 1 supplemental (rag_context) + 1 guarantee query = at least 2 WIBA-filtered calls
    assert len(wiba_calls) >= 2, (
        f"Expected at least 2 WIBA-filtered calls (supplemental + guarantee). "
        f"Got {len(wiba_calls)}. All filters: {[c.kwargs.get('filter') for c in idx.query.call_args_list]}"
    )


async def test_guarantee_query_does_not_fire_when_statute_not_in_applicable_statutes():
    """Guarantee queries must not run when WIBA is not in expand_query's applicable_statutes."""
    idx = _index_mock_for_docs(["Land chunk."])
    wiba_filter = {"source": {"$in": ["work_injury_benefits_act_cap236.txt"]}}

    with (
        patch("src.rag.retriever._openai") as mock_openai,
        patch("src.rag.retriever.pinecone_configured", return_value=True),
        patch("src.rag.retriever.get_pinecone_index", return_value=idx),
        patch(
            "src.rag.retriever.expand_query",
            new=AsyncMock(return_value=(
                "land dispute context",
                ["specific performance Land Act Kenya"],
                ["Land Act 2012"],
            )),
        ),
        patch("src.rag.retriever.judge_chunks", new=AsyncMock(side_effect=lambda q, c, **kw: c)),
        patch("src.rag.retriever.compress_chunks", new=AsyncMock(side_effect=lambda q, c: c)),
    ):
        mock_openai.embeddings.create = _embed_mock()
        await rag_retrieve("land sale agreement specific performance")

    all_filters = [call.kwargs.get("filter") for call in idx.query.call_args_list]
    assert wiba_filter not in all_filters, (
        f"WIBA guarantee query must not fire for a land dispute. Filters: {all_filters}"
    )
