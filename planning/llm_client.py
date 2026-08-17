# planning/llm_client.py
#
# Shared Gemini wrapper for everything under planning/ -- decomposition,
# decomposition-first, dynamic decomposition, Plan-and-Solve, Tree of
# Thoughts, LATS, Self-Refine, and Reflexion all call through this one
# file, matching the same google-genai / .env pattern already used by
# context_eval/llm_client.py and rag/naive_rag.py elsewhere in this repo,
# instead of every planning file rolling its own client.
#
# Two entry points on purpose:
#   - call_llm(): plain text generation (used for drafting sub-task
#     descriptions, plan steps, revisions, reflections).
#   - call_llm_json(): forces structured JSON output (used anywhere a
#     planning algorithm needs a parseable list/score/decision back --
#     decomposition's sub-task list, ToT's candidate scores, LATS's
#     expansion, etc.) so those files parse a dict, not free text.

import json
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = "gemini-3.1-flash-lite"
_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.7) -> dict:
    """
    One real Gemini call, plain text. Returns text + real usage/latency
    numbers -- every planning file's LLM-call counters in the eventual
    comparison table (planning_eval/) come from these returned numbers,
    not an estimate.
    """
    time.sleep(7)
    start = time.perf_counter()
    response = _client.models.generate_content(
        model=MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
        ),
    )
    latency = time.perf_counter() - start
    usage = response.usage_metadata

    return {
        "text": response.text,
        "input_tokens": usage.prompt_token_count,
        "output_tokens": usage.candidates_token_count,
        "latency_seconds": latency,
    }


def call_llm_json(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> dict:
    """
    Same as call_llm, but forces JSON output and parses it. Used whenever
    a planning algorithm needs structured data back (a list of sub-tasks,
    a set of scored candidates, an approve/revise verdict) rather than
    prose it would have to parse itself.

    Returns the same shape as call_llm, plus a 'parsed' key holding the
    decoded JSON. If parsing fails, 'parsed' is None and 'parse_error'
    holds the exception message -- callers decide how to handle that
    (retry, fall back) rather than this function silently swallowing it.
    """
    time.sleep(7)
    start = time.perf_counter()
    response = _client.models.generate_content(
        model=MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            response_mime_type="application/json",
        ),
    )
    latency = time.perf_counter() - start
    usage = response.usage_metadata

    result = {
        "text": response.text,
        "input_tokens": usage.prompt_token_count,
        "output_tokens": usage.candidates_token_count,
        "latency_seconds": latency,
        "parsed": None,
        "parse_error": None,
    }

    try:
        result["parsed"] = json.loads(response.text)
    except (json.JSONDecodeError, TypeError) as e:
        result["parse_error"] = str(e)

    return result


if __name__ == "__main__":
    plain = call_llm(
        system_prompt="You are terse.",
        user_prompt="Say hello in exactly three words.",
    )
    print("Plain call:", plain["text"], f"(in={plain['input_tokens']} out={plain['output_tokens']})")

    structured = call_llm_json(
        system_prompt="Respond only with JSON: {\"steps\": [\"...\", \"...\"]}",
        user_prompt="List two steps to make tea.",
    )
    print("\nJSON call parsed:", structured["parsed"])
    if structured["parse_error"]:
        print("Parse error:", structured["parse_error"])