# A small, self-contained Gemini wrapper just for context_eval/, so this
# folder doesn't need to depend on rag/ to make real LLM calls (matches
# the project's own preference for each concern's folder being independently
# runnable/gradable). Uses the same google-genai SDK and .env pattern as
# everything in rag/ -- GOOGLE_API_KEY, same free Google AI Studio key.

import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = "gemini-3.1-flash-lite"
_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


def call_llm(system_prompt: str, user_prompt: str) -> dict:
    """
    One real Gemini call. Returns the text plus real usage/latency numbers.
    Retries automatically on free-tier rate limits (429).
    """
    import time
    from google.genai import errors

    max_retries = 5
    for attempt in range(max_retries):
        try:
            start = time.perf_counter()
            response = _client.models.generate_content(
                model=MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(system_instruction=system_prompt),
            )
            latency = time.perf_counter() - start
            usage = response.usage_metadata

            return {
                "text": response.text,
                "input_tokens": usage.prompt_token_count,
                "output_tokens": usage.candidates_token_count,
                "latency_seconds": latency,
            }
        except errors.ClientError as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = 25 + attempt * 10  # 25s, 35s, 45s...
                print(f"Rate limit hit, waiting {wait}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError("Failed after max retries due to rate limits")


SUMMARIZE_SYSTEM_PROMPT = (
    "You compress a block of IROPS ops-agent tool-call transcript into a short "
    "summary. CRITICAL: if the transcript contains any decision, approval, "
    "compensation amount, supervisor ID, duty-hour figure, or root-cause reason "
    "(e.g. mechanical, weather), you MUST preserve that specific detail verbatim "
    "in your summary -- do not generalize it away. Routine/repetitive tool calls "
    "with no such detail can be compressed heavily. Keep the summary under 3 sentences."
)

ANSWER_SYSTEM_PROMPT = (
    "You are an IROPS ops assistant. Answer the question using ONLY the context "
    "transcript provided below. If the context does not contain the answer, say "
    "so explicitly instead of guessing."
)


def summarize_chunk(chunk_text: str) -> dict:
    return call_llm(SUMMARIZE_SYSTEM_PROMPT, chunk_text)


def answer_from_context(context_text: str, query: str) -> dict:
    user_prompt = f"Context transcript:\n{context_text}\n\nQuestion: {query}"
    return call_llm(ANSWER_SYSTEM_PROMPT, user_prompt)


if __name__ == "__main__":
    result = summarize_chunk(
        "get_flight_status(flight_number='BH303')\n"
        "Flight BH303: Status: scheduled - Reason: None\n"
        "get_flight_status(flight_number='BH404')\n"
        "Flight BH404: Status: delayed - Reason: None\n"
        "SUPERVISOR APPROVAL NEEDED: Karim Mostafa (crew_id=1) already at 13.0 duty "
        "hours today. Approved by sup_001 as an override for flight BH202."
    )
    print("Summary:", result["text"])
    print(f"tokens: in={result['input_tokens']} out={result['output_tokens']} "
          f"latency={result['latency_seconds']:.2f}s")