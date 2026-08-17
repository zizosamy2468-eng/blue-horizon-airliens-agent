# planning/reflexion.py
#
# REFLEXION concern (Shinn et al., 2023).
#
# Adapted from the reference toolkit's algorithms/reflexion.py: unlike
# Self-Refine (self_refine.py -- one draft, one critique, one revision),
# Reflexion retries the ENTIRE task across multiple trials, carrying a
# CAPPED episodic buffer of verbal reflections from every prior failed
# trial into the next attempt's prompt. Used for the sub-task where a
# single retry genuinely isn't enough: proposing compensation for a
# BATCH of affected passengers under real policy constraints (loyalty-
# tier multiplier IROPS-COMP-5, duplicate-claim rejection, auto-approve
# cap). A first attempt commonly gets one or two passengers wrong (wrong
# multiplier applied, or a passenger who already has a claim) -- a single
# revision often fixes the loudest problem but reintroduces or misses
# another, which is exactly the shape Reflexion is for: learn across
# attempts within the same run, not just once.
#
# GROUNDING: the evaluate step calls environment.py's REAL
# check_compensation_validity() per proposed passenger -- the same
# grounded source lats.py uses for crew assignment -- not the model's own
# opinion of whether its proposal looks fair.

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from environment import EnvironmentFeedback, check_compensation_validity  # noqa: E402
from llm_client import call_llm_json                                       # noqa: E402

PROPOSE_SYSTEM_PROMPT = """You are proposing compensation amounts for passengers affected \
by a Blue Horizon Airlines disruption. Apply these REAL policy rules exactly:
- Base compensation for a mechanical/crew disruption: 100 USD equivalent per passenger.
- Loyalty tier multiplier (IROPS-COMP-5): gold and platinum tier get 1.25x the base amount. \
Silver and none-tier get the base amount with no multiplier.
- A passenger who already has a pending/approved compensation claim for this flight cannot \
receive a second one -- do not propose an amount for them, note them as already covered instead.

If you are given REFLECTIONS from previous failed attempts this run, they describe REAL \
mistakes your past proposals made -- do not repeat them.

Respond ONLY with JSON:
{
  "proposals": [
    {"passenger_email": "...", "amount": <float>, "currency": "USD", "reasoning": "..."}
  ],
  "already_covered": ["passenger_email", ...]
}
"""

REFLECT_SYSTEM_PROMPT = """A batch compensation proposal was evaluated against real policy \
checks and some proposals failed. Write ONE short, concrete verbal reflection (2-3 \
sentences) summarizing the REAL mistakes made this trial, phrased as a lesson for the next \
attempt. Be specific about which passenger(s) and which rule was violated.

Respond ONLY with JSON: {"reflection": "..."}
"""


@dataclass
class ReflexionResult:
    success: bool
    final_proposals: list[dict]
    trials_used: int
    reflections: list[str]
    trial_log: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_seconds: float = 0.0


def propose_compensation(
    affected_passengers: list[dict],
    flight_number: str,
    reflections: list[str],
) -> dict:
    """ONE trial's proposal step. Informed by every reflection carried
    over from earlier failed trials in THIS run (capped buffer, see
    run_reflexion below)."""
    reflections_text = (
        "\n".join(f"- {r}" for r in reflections) if reflections
        else "(none yet -- this is the first trial)"
    )
    user_prompt = (
        f"Flight: {flight_number}\nAffected passengers:\n{affected_passengers}\n\n"
        f"Reflections from previous failed trials:\n{reflections_text}"
    )
    result = call_llm_json(system_prompt=PROPOSE_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.4)
    if result["parsed"] is None:
        raise ValueError(f"Reflexion propose step returned invalid JSON: {result['parse_error']}")

    return {
        "proposals": result["parsed"].get("proposals", []),
        "already_covered": result["parsed"].get("already_covered", []),
        "llm_calls": 1, "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"], "latency_seconds": result["latency_seconds"],
    }


def grounded_evaluate_batch(proposals: list[dict], flight_number: str) -> list[tuple[dict, EnvironmentFeedback]]:
    """EVALUATE step, grounded: runs environment.py's REAL
    check_compensation_validity() against the database for every
    proposed passenger, one real check per proposal -- no LLM call, no
    self-opinion involved in this step at all."""
    return [
        (p, check_compensation_validity(p["passenger_email"], flight_number, p["amount"]))
        for p in proposals
    ]


def reflect_on_trial(failed: list[tuple[dict, EnvironmentFeedback]]) -> dict:
    """REFLECT step: turn this trial's real grounded failures into one
    verbal lesson for the next trial's propose step."""
    failures_text = "\n".join(f"- {p['passenger_email']}: proposed {p['amount']} -- {fb.detail}" for p, fb in failed)
    result = call_llm_json(system_prompt=REFLECT_SYSTEM_PROMPT, user_prompt=failures_text, temperature=0.3)
    reflection = (
        result["parsed"]["reflection"] if result["parsed"]
        else f"{len(failed)} proposal(s) failed grounded validation: {failures_text[:200]}"
    )
    return {
        "reflection": reflection, "llm_calls": 1, "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"], "latency_seconds": result["latency_seconds"],
    }


def run_reflexion(
    affected_passengers: list[dict],
    flight_number: str,
    max_trials: int = 3,
    reflection_buffer_cap: int = 3,
) -> ReflexionResult:
    """
    Full Reflexion loop: PROPOSE -> GROUNDED EVALUATE -> (if any failure)
    REFLECT -> retry PROPOSE with the accumulated reflection buffer, up
    to max_trials. reflection_buffer_cap bounds how many past reflections
    get carried forward at once (oldest dropped first) so the prompt
    doesn't grow unboundedly across trials -- a capped buffer, not an
    ever-growing transcript.
    """
    reflections: list[str] = []
    trial_log = []
    total_llm_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency = 0.0

    for trial in range(max_trials):
        proposal = propose_compensation(affected_passengers, flight_number, reflections)
        total_llm_calls += proposal["llm_calls"]
        total_input_tokens += proposal["input_tokens"]
        total_output_tokens += proposal["output_tokens"]
        total_latency += proposal["latency_seconds"]

        evaluated = grounded_evaluate_batch(proposal["proposals"], flight_number)
        passed = [(p, fb) for p, fb in evaluated if fb.passed]
        failed = [(p, fb) for p, fb in evaluated if not fb.passed]

        trial_entry = {
            "trial": trial,
            "proposed": proposal["proposals"],
            "already_covered": proposal["already_covered"],
            "passed": [{"passenger_email": p["passenger_email"], "amount": p["amount"], "detail": fb.detail} for p, fb in passed],
            "failed": [{"passenger_email": p["passenger_email"], "amount": p["amount"], "detail": fb.detail} for p, fb in failed],
        }

        if not failed:
            trial_log.append(trial_entry)
            return ReflexionResult(
                success=True, final_proposals=proposal["proposals"], trials_used=trial + 1,
                reflections=reflections, trial_log=trial_log,
                llm_calls=total_llm_calls, input_tokens=total_input_tokens,
                output_tokens=total_output_tokens, latency_seconds=total_latency,
            )

        refl = reflect_on_trial(failed)
        total_llm_calls += refl["llm_calls"]
        total_input_tokens += refl["input_tokens"]
        total_output_tokens += refl["output_tokens"]
        total_latency += refl["latency_seconds"]

        reflections.append(refl["reflection"])
        if len(reflections) > reflection_buffer_cap:
            reflections.pop(0)   # capped buffer: drop the oldest reflection first

        trial_entry["reflection"] = refl["reflection"]
        trial_log.append(trial_entry)

    # Exhausted max_trials without a fully passing batch -- return the
    # last trial's passing subset only, never the failing proposals.
    last_passing = [p for p, fb in evaluated if fb.passed]
    return ReflexionResult(
        success=False, final_proposals=last_passing, trials_used=max_trials,
        reflections=reflections, trial_log=trial_log,
        llm_calls=total_llm_calls, input_tokens=total_input_tokens,
        output_tokens=total_output_tokens, latency_seconds=total_latency,
    )


if __name__ == "__main__":
    # Demo shaped so a first-trial mistake is likely: Mona Khaled already
    # has an APPROVED 150.00 USD compensation for BH202 in the seed data
    # -- a naive first proposal that doesn't check for that will get
    # grounded-rejected as a duplicate, forcing a real second trial.
    affected_passengers = [
        {"passenger_id": 2, "passenger_email": "mona.khaled@example.com",
         "full_name": "Mona Khaled", "loyalty_tier": "none"},
    ]

    outcome = run_reflexion(affected_passengers, "BH202", max_trials=3)

    print("=== Reflexion: batch compensation proposal for BH202 ===")
    print(f"success={outcome.success} trials_used={outcome.trials_used}")
    print(f"LLM calls: {outcome.llm_calls}, tokens: in={outcome.input_tokens} "
          f"out={outcome.output_tokens}, latency={outcome.latency_seconds:.2f}s\n")

    for entry in outcome.trial_log:
        print(f"--- Trial {entry['trial']} ---")
        print(f"  proposed: {entry['proposed']}")
        print(f"  already_covered: {entry['already_covered']}")
        print(f"  passed: {entry['passed']}")
        print(f"  failed: {entry['failed']}")
        if "reflection" in entry:
            print(f"  reflection carried to next trial: {entry['reflection']}")
        print()

    print("Reflection buffer at end (capped):", outcome.reflections)
    print("\nFinal accepted proposals:", outcome.final_proposals)