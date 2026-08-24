# state_graph/compensation/appeal_strategies.py
#
# TREE OF THOUGHTS concern for the Compensation Appeal graph.
#
# This is one of Zizo's two required LLM-call additions (RAG is the
# other one, in retrieve_compensation_policy). Adapted from the same
# generate/evaluate/keep-best-N shape as planning/tree_of_thoughts.py --
# reuses planning/llm_client.py's call_llm_json directly instead of
# standing up a second Gemini client, matching this repo's own rule of
# not duplicating an LLM client per file.
#
# WHY ToT and not a single Plan-and-Solve guess: given one appeal case,
# there are usually several genuinely different, valid ways to argue it
# (cite a documented service failure, lean on loyalty-tier history, argue
# the original amount misapplied policy, request a goodwill gesture
# instead of a policy-based payout). Picking badly here is costly -- a
# weak argument can get the whole appeal rejected outright instead of
# revised, and rejecting wastes a real appeal window (same cost shape as
# a wrong first submission on an insurance claim). So: generate several
# candidate strategies, self-evaluate each against the REAL retrieved
# policy sections (not vibes), keep the best one before it ever reaches
# constrained_action.
#
# GROUNDING NOTE: like planning/tree_of_thoughts.py, the evaluation step
# here is the model's own self-scored opinion -- deliberately ungrounded,
# which is what ToT actually is (fast, cheap, ranked by self-belief). The
# genuinely grounded check in this graph is constrained_action's real
# comparison against the numeric auto-approve cap in nodes.py, not this
# file. That split mirrors how the planning lab kept ToT's self-eval
# separate from LATS's grounded eval.

import sys
from pathlib import Path

PLANNING_DIR = Path(__file__).resolve().parents[2] / "planning"
if str(PLANNING_DIR) not in sys.path:
    sys.path.insert(0, str(PLANNING_DIR))

from llm_client import call_llm_json  # noqa: E402  (real Gemini call, shared client)


GENERATE_SYSTEM_PROMPT = """You are proposing candidate argument strategies for a Blue \
Horizon Airlines passenger's compensation appeal. Given the appeal's facts and the real \
compensation policy sections retrieved for this case, propose {n_candidates} DIFFERENT \
candidate strategies for how to argue this appeal, each with a short reasoning.

Respond ONLY with JSON:
{{
  "candidates": [
    {{
      "strategy_name": "short_snake_case_id",
      "argument_summary": "1-2 sentences making the actual case to present",
      "recommended_amount": <float>,
      "reasoning": "why this strategy could work for this specific appeal"
    }}
  ]
}}
"""

EVALUATE_SYSTEM_PROMPT = """You are scoring ONE candidate appeal strategy for a Blue \
Horizon Airlines compensation appeal, against the REAL policy sections retrieved for this \
case. Score it 0-10 on two criteria:
- Policy alignment: does the argument and recommended_amount actually follow from what the \
retrieved policy sections say, rather than contradicting or ignoring them?
- Persuasiveness given the appeal's real facts: is this a genuinely strong case to lead \
with, or a weak/generic one that risks outright rejection?

Respond ONLY with JSON:
{"score": <0-10>, "reasoning": "..."}
"""


def generate_candidate_strategies(
    appeal_context: dict,
    policy_text: str,
    n_candidates: int = 3,
) -> dict:
    """
    GENERATE step: propose several different argument strategies in one
    LLM call, instead of committing to the first plausible one.

    appeal_context: {"flight_number", "passenger_email", "appeal_reason",
        "original_amount", "requested_amount", "loyalty_tier", ...}
    policy_text: the real text retrieved by retrieve_compensation_policy
        (search_policy_manual) -- this is what keeps candidates grounded
        in real policy instead of generic negotiation tactics.
    """
    prompt = GENERATE_SYSTEM_PROMPT.format(n_candidates=n_candidates)
    user_prompt = (
        f"Appeal facts:\n{appeal_context}\n\n"
        f"Retrieved compensation policy sections:\n{policy_text}"
    )

    result = call_llm_json(system_prompt=prompt, user_prompt=user_prompt, temperature=0.8)

    if result["parsed"] is None:
        raise ValueError(
            f"Appeal strategy generation returned invalid JSON: {result['parse_error']}"
        )

    candidates = result["parsed"].get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("Appeal strategy generation did not return a non-empty candidate list.")

    return {
        "candidates": candidates,
        "llm_calls": 1,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }


def evaluate_candidate_strategy(
    appeal_context: dict,
    candidate: dict,
    policy_text: str,
) -> dict:
    """
    EVALUATE step: self-score ONE candidate strategy. Called once per
    candidate so scores stay directly comparable, same pattern as
    planning/tree_of_thoughts.py's evaluate_candidate.
    """
    user_prompt = (
        f"Appeal facts:\n{appeal_context}\n\n"
        f"Candidate strategy:\n{candidate}\n\n"
        f"Retrieved compensation policy sections:\n{policy_text}"
    )

    result = call_llm_json(system_prompt=EVALUATE_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.0)

    if result["parsed"] is None:
        raise ValueError(
            f"Appeal strategy evaluation returned invalid JSON: {result['parse_error']}"
        )

    parsed = result["parsed"]

    # Gemini occasionally returns a single-item list instead of a bare object.
    if isinstance(parsed, list):
        if not parsed:
            raise ValueError("Appeal strategy evaluation returned an empty list.")
        parsed = parsed[0]

    if not isinstance(parsed, dict) or "score" not in parsed:
        raise ValueError(f"Appeal strategy evaluation returned unexpected shape: {parsed!r}")

    return {
        "score": parsed["score"],
        "reasoning": parsed.get("reasoning", ""),
        "llm_calls": 1,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }


def compare_appeal_strategies(
    appeal_context: dict,
    policy_text: str,
    n_candidates: int = 3,
    excluded_strategy_names: list[str] | None = None,
) -> dict:
    """
    Full ToT loop for this sub-task: generate N candidates, self-evaluate
    every one against the real retrieved policy, keep the top-1 highest
    scoring. Single level is enough -- picking ONE argument to lead with
    is not a multi-hop search problem the way LATS's route-finding is.

    excluded_strategy_names: strategy names already tried and rejected in
    an earlier revision round of THIS SAME appeal (see nodes.py's revised
    appeal loop) -- passed so the model does not just re-propose the same
    rejected argument again on a retry.

    Returns the winning candidate, the full scored list (for the
    checkpointed state / eventual trace), and combined LLM-call stats.
    """
    excluded_strategy_names = excluded_strategy_names or []

    context_for_generation = dict(appeal_context)
    if excluded_strategy_names:
        context_for_generation["previously_rejected_strategies"] = excluded_strategy_names

    gen = generate_candidate_strategies(context_for_generation, policy_text, n_candidates)
    total_llm_calls = gen["llm_calls"]
    total_input_tokens = gen["input_tokens"]
    total_output_tokens = gen["output_tokens"]
    total_latency = gen["latency_seconds"]

    scored = []
    for candidate in gen["candidates"]:
        if candidate.get("strategy_name") in excluded_strategy_names:
            continue

        ev = evaluate_candidate_strategy(appeal_context, candidate, policy_text)
        total_llm_calls += ev["llm_calls"]
        total_input_tokens += ev["input_tokens"]
        total_output_tokens += ev["output_tokens"]
        total_latency += ev["latency_seconds"]

        scored.append({**candidate, "score": ev["score"], "eval_reasoning": ev["reasoning"]})

    if not scored:
        raise ValueError(
            "Every generated candidate strategy was already tried and rejected "
            "in a previous revision round."
        )

    scored.sort(key=lambda c: c["score"], reverse=True)
    winner = scored[0]

    return {
        "winner": winner,
        "all_scored": scored,
        "llm_calls": total_llm_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "latency_seconds": total_latency,
    }


if __name__ == "__main__":
    # Smoke test with a realistic shape matching the seed data: Youssef
    # Adel (platinum-tier) appealing for a higher amount than a first,
    # rejected offer -- platinum tier + IROPS-COMP-5's multiplier is a
    # real policy lever a good strategy should actually use.
    appeal_context = {
        "flight_number": "BH202",
        "passenger_email": "youssef.adel@example.com",
        "loyalty_tier": "platinum",
        "original_amount": 100.0,
        "requested_amount": 200.0,
        "appeal_reason": (
            "The original compensation did not apply the platinum-tier "
            "multiplier for a mechanical disruption."
        ),
    }

    policy_text = (
        "[IROPS-COMP-5] Loyalty tier compensation multiplier\n"
        "Gold and platinum tier passengers receive a 25% compensation multiplier on top of "
        "the standard amount for mechanical or crew-related disruptions.\n\n"
        "[IROPS-COMP-4.2b] Auto-approve cap and supervisor override threshold\n"
        "Compensation amounts up to the auto-approve cap may be issued by any authenticated "
        "ops agent without further approval. Amounts above the cap require explicit "
        "supervisor approval via elicitation before the payout is recorded."
    )

    outcome = compare_appeal_strategies(appeal_context, policy_text, n_candidates=3)

    print("=== Tree of Thoughts: compare appeal strategies ===")
    print(
        f"LLM calls: {outcome['llm_calls']}, tokens: in={outcome['input_tokens']} "
        f"out={outcome['output_tokens']}, latency={outcome['latency_seconds']:.2f}s\n"
    )

    print("All scored candidates:")
    for c in outcome["all_scored"]:
        print(f"  {c['strategy_name']} score={c['score']} -- {c['eval_reasoning']}")

    print("\nWinning strategy:")
    print(outcome["winner"])