# planning/tree_of_thoughts.py
#
# TREE OF THOUGHTS concern.
#
# Adapted from the reference toolkit's algorithms/tree_of_thoughts.py
# (itself modeled on Yao et al. 2023's generate/evaluate/search loop):
# at each level, generate several candidate next-steps, self-evaluate
# each one, keep the best (or best-N) via BFS, and only expand further
# from survivors. router.py sends "reasoning"-shaped sub-tasks here --
# genuinely different valid approaches exist and a wrong pick is costly.
#
# Concrete use in this system: "assign_reserve_crew" -- given a disrupted
# flight needing a reserve crew member, there are usually several
# candidate crew members (different base airports, different current
# duty-hour totals). Which one to propose is exactly a "several valid
# paths, wrong pick is costly" decision (a bad pick means either an
# avoidable duty-hour override request or a slower-to-position crew
# member) -- ToT generates candidates, self-evaluates them against the
# real policy criteria (IROPS-CREW-2: prefer base-matched crew; IROPS-
# DUTY-4: avoid unnecessary overrides), and keeps the best-scoring one
# before it's ever sent to the real assign_reserve_crew tool call.
#
# NOTE ON GROUNDING: the self-evaluation here is the model's own opinion
# of each candidate -- deliberately ungrounded, matching what ToT is
# actually supposed to be (fast, cheap, ranked-by-self-belief). The
# lab's "grounded vs ungrounded" contrast is instead demonstrated on the
# LATS sub-task (lats.py + environment.py), where the same kind of
# scoring decision gets checked against a real validator instead.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from llm_client import call_llm_json    # noqa: E402

GENERATE_SYSTEM_PROMPT = """You are proposing candidate reserve-crew assignments for a \
disrupted Blue Horizon Airlines flight. Given the flight and a list of eligible crew \
members with their current duty hours, propose {n_candidates} DIFFERENT candidate crew \
members to assign, each with brief reasoning for why they might be the right pick.

Respond ONLY with JSON:
{{
  "candidates": [
    {{"crew_id": <int>, "full_name": "...", "reasoning": "..."}},
    ...
  ]
}}
"""

EVALUATE_SYSTEM_PROMPT = """You are scoring ONE candidate reserve-crew assignment against \
Blue Horizon Airlines policy. Score it 0-10 on two policy criteria:
- Base-airport match preferred (IROPS-CREW-2): matching the flight's origin airport avoids \
positioning delay.
- Avoid unnecessary duty-hour overrides (IROPS-DUTY-4): a candidate already near/over the \
duty-hour limit should score lower, since an override request should only happen when no \
better-rested option exists.

Respond ONLY with JSON:
{"score": <0-10>, "reasoning": "..."}
"""


def generate_candidates(flight_number: str, eligible_crew: list[dict], n_candidates: int = 3) -> dict:
    """GENERATE step: propose several different candidate crew members
    in one LLM call, rather than committing to the first one that comes
    to mind."""
    prompt = GENERATE_SYSTEM_PROMPT.format(n_candidates=n_candidates)
    user_prompt = f"Disrupted flight: {flight_number}\nEligible crew:\n{eligible_crew}"
    result = call_llm_json(system_prompt=prompt, user_prompt=user_prompt, temperature=0.8)

    if result["parsed"] is None:
        raise ValueError(f"ToT candidate generation returned invalid JSON: {result['parse_error']}")

    return {
        "candidates": result["parsed"]["candidates"],
        "llm_calls": 1,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }


def evaluate_candidate(flight_number: str, candidate: dict, crew_duty_info: dict) -> dict:
    """EVALUATE step: self-score ONE candidate. Called once per candidate
    so each scoring call stays focused and the resulting scores are
    directly comparable."""
    user_prompt = (
        f"Flight: {flight_number}\nCandidate: {candidate}\n"
        f"Candidate's current duty info: {crew_duty_info}"
    )
    result = call_llm_json(system_prompt=EVALUATE_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.0)

    if result["parsed"] is None:
        raise ValueError(f"ToT candidate evaluation returned invalid JSON: {result['parse_error']}")

    parsed = result["parsed"]

    # Gemini occasionally returns a single-item list instead of a bare object
    if isinstance(parsed, list):
        if not parsed:
            raise ValueError("ToT evaluation returned an empty list")
        parsed = parsed[0]

    if not isinstance(parsed, dict) or "score" not in parsed:
        raise ValueError(f"ToT evaluation returned unexpected shape: {parsed!r}")

    return {
        "score": parsed["score"],
        "reasoning": parsed.get("reasoning", ""),
        "llm_calls": 1,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }

def tree_of_thoughts_select_crew(
    flight_number: str,
    eligible_crew: list[dict],
    crew_duty_lookup: dict[int, dict],
    n_candidates: int = 3,
    keep_top_k: int = 1,
) -> dict:
    """
    Full BFS-style ToT loop for this sub-task: generate N candidates at
    one level, self-evaluate every one of them, keep the top-K highest
    scoring. Single level is enough for this sub-task's shape (picking
    ONE crew member is not a multi-hop search problem the way finding a
    replacement route can be) -- lats.py is where multi-level MCTS
    search actually happens.

    crew_duty_lookup: {crew_id: {"total_duty": float, "base_airport": str}}
    used to feed real duty-hour context into each evaluation instead of
    letting the model guess at it.

    Returns the winning candidate(s), the full scored tree (for the
    artifacts/ trace), and combined LLM-call/token/latency stats.
    """
    gen = generate_candidates(flight_number, eligible_crew, n_candidates)
    total_llm_calls = gen["llm_calls"]
    total_input_tokens = gen["input_tokens"]
    total_output_tokens = gen["output_tokens"]
    total_latency = gen["latency_seconds"]

    scored = []
    for candidate in gen["candidates"]:
        duty_info = crew_duty_lookup.get(candidate["crew_id"], {"total_duty": 0.0, "base_airport": "unknown"})
        ev = evaluate_candidate(flight_number, candidate, duty_info)
        total_llm_calls += ev["llm_calls"]
        total_input_tokens += ev["input_tokens"]
        total_output_tokens += ev["output_tokens"]
        total_latency += ev["latency_seconds"]
        scored.append({**candidate, "score": ev["score"], "eval_reasoning": ev["reasoning"]})

    scored.sort(key=lambda c: c["score"], reverse=True)
    kept = scored[:keep_top_k]
    pruned = scored[keep_top_k:]

    return {
        "kept": kept,
        "pruned": pruned,
        "all_scored": scored,
        "llm_calls": total_llm_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "latency_seconds": total_latency,
    }


if __name__ == "__main__":
    # Demo with shapes matching the BH202 seed data: Capt. Karim Mostafa
    # (crew_id=1, CAI base) already has original duty on BH202; Capt.
    # Laila Hassan (crew_id=2, CAI base) is on reserve for the same flight.
    eligible_crew = [
        {"crew_id": 1, "full_name": "Capt. Karim Mostafa", "role": "pilot", "base_airport": "CAI"},
        {"crew_id": 2, "full_name": "Capt. Laila Hassan", "role": "co_pilot", "base_airport": "CAI"},
        {"crew_id": 3, "full_name": "Nourhan Fathy", "role": "flight_attendant", "base_airport": "CAI"},
    ]
    crew_duty_lookup = {
        1: {"total_duty": 13.0, "base_airport": "CAI"},   # near the 14h limit -- should score lower
        2: {"total_duty": 4.0, "base_airport": "CAI"},    # well within limits -- should score higher
        3: {"total_duty": 2.0, "base_airport": "CAI"},
    }

    outcome = tree_of_thoughts_select_crew("BH202", eligible_crew, crew_duty_lookup, n_candidates=3, keep_top_k=1)

    print("=== Tree of Thoughts: select reserve crew for BH202 ===")
    print(f"LLM calls: {outcome['llm_calls']}, tokens: in={outcome['input_tokens']} "
          f"out={outcome['output_tokens']}, latency={outcome['latency_seconds']:.2f}s\n")

    print("All scored candidates:")
    for c in outcome["all_scored"]:
        print(f"  crew_id={c['crew_id']} {c['full_name']} score={c['score']} -- {c['eval_reasoning']}")

    print("\nKept (winning) candidate(s):")
    for c in outcome["kept"]:
        print(f"  {c}")