# RETRIEVAL ARCHITECTURE 2/3: HYBRID SEARCH.
#
# Combines vector similarity (semantic) with BM25 (exact keyword overlap)
# in the same query. This is what should win on exact-identifier questions
# like "what does 4.2b say" -- BM25 matches "4.2b" as a literal token even
# though it means nothing to an embedding model.
#
# Reuses the exact same technique already in this codebase's
# mcp_server/keyword_search.py (rank_bm25.BM25Plus) rather than inventing a
# different keyword scorer, so both the live MCP server's
# search_knowledge_base tool and this evaluation stay consistent.
#
# Setup: pip install rank_bm25 google-genai   (plus everything vector_store.py needs)

import re
import time

from rank_bm25 import BM25Plus

from naive_rag import GENERATION_MODEL, SYSTEM_PROMPT, _build_prompt, _client
from policy_corpus import get_manual
from vector_store import PolicyVectorStore, SearchResult


def _tokenize(text: str) -> list[str]:
    # Keep alphanumerics AND dotted codes like "4.2b" as single tokens --
    # this is exactly the detail naive vector search loses and BM25 needs
    # to keep in order to win on citation-heavy questions.
    return re.findall(r"[a-z0-9]+(?:\.[a-z0-9]+)?", text.lower())


class BM25PolicyIndex:
    def __init__(self):
        self.sections = get_manual()
        corpus_tokens = [
            _tokenize(f"{s.code} {s.title} {s.text}") for s in self.sections
        ]
        self.bm25 = BM25Plus(corpus_tokens)

    def score(self, query: str) -> dict[str, float]:
        tokens = _tokenize(query)
        scores = self.bm25.get_scores(tokens)
        return {s.code: float(score) for s, score in zip(self.sections, scores)}


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return scores
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 0.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def hybrid_search(
    query: str,
    vector_store: PolicyVectorStore,
    bm25_index: BM25PolicyIndex,
    k: int = 3,
    vector_weight: float = 0.5,
) -> list[SearchResult]:
    """
    Retrieves a wider vector candidate pool, scores the whole corpus with
    BM25, normalizes both score sets to [0, 1], and combines them with a
    weighted sum so an exact-code match can outrank a merely-plausible
    semantic neighbor.
    """
    vector_hits = vector_store.search(query, k=10)  # wide net before re-ranking
    vector_scores = _normalize({r.section.code: r.score for r in vector_hits})
    bm25_scores = _normalize(bm25_index.score(query))

    sections_by_code = {s.code: s for s in bm25_index.sections}
    all_codes = set(vector_scores) | {c for c, s in bm25_scores.items() if s > 0}

    combined = []
    for code in all_codes:
        v_score = vector_scores.get(code, 0.0)
        b_score = bm25_scores.get(code, 0.0)
        combined_score = vector_weight * v_score + (1 - vector_weight) * b_score
        combined.append((combined_score, code))

    combined.sort(key=lambda x: -x[0])
    top = combined[:k]

    return [
        SearchResult(chunk_id=code, section=sections_by_code[code], score=score)
        for score, code in top
    ]


def hybrid_rag_answer(
    query: str,
    vector_store: PolicyVectorStore,
    bm25_index: BM25PolicyIndex,
    k: int = 3,
) -> dict:
    from google.genai import types

    start = time.perf_counter()

    retrieved = hybrid_search(query, vector_store, bm25_index, k=k)

    response = _client.models.generate_content(
        model=GENERATION_MODEL,
        contents=_build_prompt(query, retrieved),
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )

    latency = time.perf_counter() - start
    usage = response.usage_metadata

    return {
        "architecture": "hybrid_rag",
        "query": query,
        "retrieved_codes": [c.section.code for c in retrieved],
        "answer": response.text,
        "input_tokens": usage.prompt_token_count,
        "output_tokens": usage.candidates_token_count,
        "total_tokens": usage.total_token_count,
        "latency_seconds": round(latency, 3),
    }


if __name__ == "__main__":
    vector_store = PolicyVectorStore()
    bm25_index = BM25PolicyIndex()

    test_questions = [
        "What does policy 4.2b say about the compensation auto-approve threshold?",
        "How does the base airport of a reserve crew member affect assignment?",
    ]

    for q in test_questions:
        result = hybrid_rag_answer(q, vector_store, bm25_index)
        print(f"Q: {q}")
        print(f"Retrieved: {result['retrieved_codes']}")
        print(f"A: {result['answer']}")
        print(f"tokens={result['total_tokens']} latency={result['latency_seconds']}s\n")