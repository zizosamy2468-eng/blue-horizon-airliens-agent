# RETRIEVAL ARCHITECTURE 1/3: NAIVE RAG.
#
# The baseline pipeline: embed the query, retrieve top-k chunks from the
# vector store (pure semantic similarity, no keyword boost, no multi-hop
# reasoning), then generate an answer grounded ONLY in what was retrieved.
#
# This is the architecture that should do fine on general/conceptual
# questions ("does weather count for compensation?") but struggle on
# questions with an exact identifier ("what does 4.2b say?") because a
# policy CODE doesn't embed distinctively -- semantic similarity sees
# "4.2b" as just noise, not a meaningful token. That weakness is exactly
# what retrieval_eval/ later measures hybrid search against.
#
# Uses Google Gemini (google-genai SDK, Google AI Studio's free-tier API)
# for generation instead of OpenAI.
#
# Setup: pip install google-genai python-dotenv  (chromadb already required by vector_store.py)

import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from vector_store import PolicyVectorStore, SearchResult

load_dotenv()

GENERATION_MODEL = "gemini-3.1-flash-lite"

_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

SYSTEM_PROMPT = (
    "You are the Blue Horizon IROPS policy assistant. Answer the ops agent's "
    "question using ONLY the policy sections provided below. If the provided "
    "sections do not contain enough information to answer confidently, say so "
    "explicitly instead of guessing. Always cite the policy code(s) you used, "
    "e.g. (IROPS-COMP-1)."
)


def _build_prompt(query: str, chunks: list[SearchResult]) -> str:
    context_block = "\n\n".join(
        f"[{c.section.code}] {c.section.title}\n{c.section.text}" for c in chunks
    )
    return (
        f"Policy sections retrieved:\n\n{context_block}\n\n"
        f"Ops agent question: {query}"
    )


def naive_rag_answer(query: str, store: PolicyVectorStore, k: int = 3) -> dict:
    start = time.perf_counter()

    retrieved = store.search(query, k=k)

    response = _client.models.generate_content(
        model=GENERATION_MODEL,
        contents=_build_prompt(query, retrieved),
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )

    latency = time.perf_counter() - start
    answer = response.text
    usage = response.usage_metadata

    return {
        "architecture": "naive_rag",
        "query": query,
        "retrieved_codes": [c.section.code for c in retrieved],
        "answer": answer,
        "input_tokens": usage.prompt_token_count,
        "output_tokens": usage.candidates_token_count,
        "total_tokens": usage.total_token_count,
        "latency_seconds": round(latency, 3),
    }


if __name__ == "__main__":
    store = PolicyVectorStore()  # rebuild=False by default -- reuses the collection built in vector_store.py

    test_questions = [
        "What's the standard duty-time limit for a pilot in one day?",
        "What does policy 4.2b say about the compensation auto-approve threshold?",
    ]

    for q in test_questions:
        result = naive_rag_answer(q, store)
        print(f"Q: {q}")
        print(f"Retrieved: {result['retrieved_codes']}")
        print(f"A: {result['answer']}")
        print(f"tokens={result['total_tokens']} latency={result['latency_seconds']}s\n")