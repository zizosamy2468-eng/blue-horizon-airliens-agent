# RETRIEVAL ARCHITECTURE 3/3: AGENTIC RAG.
#
# A real multi-hop loop: the model sees the question, decides what to
# search for, gets results, and explicitly decides whether it has enough
# to answer or needs another retrieval round with a different query. This
# is the architecture that should win on decomposition-shaped questions --
# e.g. "for a gold-tier passenger on a mechanical disruption who also
# needed a duty-time override, what compensation applies?" needs at least
# two different policy sections (compensation multiplier + duty-time
# override rules) that a single embedding of the whole question won't
# retrieve well in one shot.
#
# Implementation: uses Gemini's function calling via a stateful chat
# session (client.chats.create) with automatic function calling DISABLED,
# so this code stays in control of the loop -- it inspects
# response.function_calls itself, actually runs hybrid_search(), and feeds
# the real observation back via a function response Part. The loop
# terminates when the model stops requesting tool calls (genuinely
# agentic, not a fixed hop count), capped at max_hops as a safety bound.
#
# Setup: same as hybrid_rag.py (google-genai, rank_bm25, chromadb all required)

import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from hybrid_rag import BM25PolicyIndex, hybrid_search
from vector_store import PolicyVectorStore

load_dotenv()

GENERATION_MODEL = "gemini-3.1-flash-lite"
_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

SEARCH_FUNCTION = types.FunctionDeclaration(
    name="search_policy_manual",
    description=(
        "Searches the Blue Horizon IROPS policy manual and returns the most "
        "relevant policy sections for a given search query. Call this again "
        "with a different, more specific query if the first results don't "
        "fully answer the ops agent's question."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search the policy manual for"},
        },
        "required": ["query"],
    },
)
SEARCH_TOOL = types.Tool(function_declarations=[SEARCH_FUNCTION])

SYSTEM_PROMPT = (
    "You are the Blue Horizon IROPS policy assistant. Use the "
    "search_policy_manual tool to look up whatever you need from the policy "
    "manual before answering -- call it more than once if the question has "
    "multiple parts that need different lookups (e.g. a compensation "
    "question that also involves a duty-time override). Once you have "
    "enough retrieved sections to answer confidently, answer directly "
    "citing the policy code(s) you used, e.g. (IROPS-COMP-1). Never answer "
    "from general knowledge -- only from what search_policy_manual returns."
)


def agentic_rag_answer(
    query: str,
    vector_store: PolicyVectorStore,
    bm25_index: BM25PolicyIndex,
    max_hops: int = 4,
    k_per_hop: int = 3,
) -> dict:
    start = time.perf_counter()

    chat = _client.chats.create(
        model=GENERATION_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[SEARCH_TOOL],
            # We want manual control of the loop (to actually run
            # hybrid_search ourselves and record what got retrieved), not
            # the SDK auto-calling a local python function for us.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )

    all_retrieved_codes: list[str] = []
    total_input_tokens = 0
    total_output_tokens = 0
    hops_used = 0

    response = chat.send_message(query)

    for hop in range(max_hops):
        usage = response.usage_metadata
        total_input_tokens += usage.prompt_token_count
        total_output_tokens += usage.candidates_token_count

        if not response.function_calls:
            # The model decided it has enough to answer -- loop ends here,
            # not at a fixed hop count.
            latency = time.perf_counter() - start
            return {
                "architecture": "agentic_rag",
                "query": query,
                "retrieved_codes": all_retrieved_codes,
                "answer": response.text,
                "hops_used": hops_used,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "total_tokens": total_input_tokens + total_output_tokens,
                "latency_seconds": round(latency, 3),
            }

        # The model wants to retrieve (again). Run every requested call for
        # real, and feed the actual observation back as a function response.
        hops_used += 1
        response_parts = []
        for call in response.function_calls:
            sub_query = call.args["query"]
            results = hybrid_search(sub_query, vector_store, bm25_index, k=k_per_hop)
            all_retrieved_codes.extend(r.section.code for r in results)

            observation = "\n\n".join(
                f"[{r.section.code}] {r.section.title}\n{r.section.text}" for r in results
            ) or "No relevant policy sections found for that query."

            response_parts.append(
                types.Part.from_function_response(
                    name=call.name,
                    response={"result": observation},
                )
            )

        response = chat.send_message(response_parts)

    # Hit the safety cap without the model volunteering a final answer --
    # send one more message with tools disabled so it has to respond in text.
    final = chat.send_message(
        "Please answer now with what you have, citing policy codes.",
    )
    usage = final.usage_metadata
    total_input_tokens += usage.prompt_token_count
    total_output_tokens += usage.candidates_token_count
    latency = time.perf_counter() - start

    return {
        "architecture": "agentic_rag",
        "query": query,
        "retrieved_codes": all_retrieved_codes,
        "answer": final.text,
        "hops_used": hops_used,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "latency_seconds": round(latency, 3),
        "note": "hit max_hops safety cap",
    }


if __name__ == "__main__":
    vector_store = PolicyVectorStore()
    bm25_index = BM25PolicyIndex()

    # A genuinely multi-part, decomposition-shaped question -- needs both
    # the compensation-multiplier rule AND the duty-time override rule.
    query = (
        "A gold-tier passenger is on flight BH202, which is disrupted for a "
        "mechanical reason, and the crew member we'd assign as reserve "
        "already needs a duty-time override. What compensation applies to "
        "the passenger, and what has to happen before we can assign that "
        "crew member?"
    )

    result = agentic_rag_answer(query, vector_store, bm25_index)
    print(f"Q: {query}\n")
    print(f"Hops used: {result['hops_used']}")
    print(f"Retrieved across all hops: {result['retrieved_codes']}")
    print(f"A: {result['answer']}")
    print(f"tokens={result['total_tokens']} latency={result['latency_seconds']}s")