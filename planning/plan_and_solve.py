# planning/plan_and_solve.py
#
# PLAN-AND-SOLVE concern.
#
# Adapted from the reference toolkit's algorithms/plan_and_solve.py:
# one explicit plan phase (a single LLM call that lays out the ordered
# steps), then execution step by step against the REAL domain action,
# single pass, no branching, no re-planning mid-way. This is what
# router.py routes deterministic-shaped sub-tasks to -- there is one
# correct order once the inputs are known, so generating and comparing
# alternatives (ToT) or searching with backtracking (LATS) would just be
# paying extra LLM calls for nothing.
#
# Concrete use in this system: "propose_rebooking" -- given an already-
# ranked list of affected passengers and a list of candidate replacement
# flights, PS produces the ordered assignment plan in one shot, then
# executes rebook_passenger() for real, one passenger at a time, in that
# fixed order. Wang et al. (ACL 2023)'s two-phase plan/solve prompt is
# followed directly: PLAN prompt first, SOLVE prompt (here: real tool
# execution) second, no interleaving between them.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from domain_actions import run_action    # noqa: E402
from llm_client import call_llm_json      # noqa: E402

PLAN_SYSTEM_PROMPT = """You are solving one sub-task of an IROPS disruption-response \
plan for Blue Horizon Airlines: proposing which affected passenger goes on which \
replacement flight. Produce the COMPLETE ordered assignment plan in a single pass -- \
you will not see intermediate results before finishing this plan, so reason through \
the whole thing now.

Rebooking priority order (do not deviate): platinum tier, then gold, then silver, then \
none-tier. Within the same tier, earlier passengers in the input list go first. Business \
and premium fare-class passengers should go to replacement flights with earlier \
departure where possible.

Respond ONLY with JSON in this exact shape:
{
  "plan": [
    {"step": 1, "passenger_id": <int>, "booking_id": <int>, "target_flight_number": "...", "reasoning": "..."},
    ...
  ]
}
"""


def plan(affected_passengers: list[dict], candidate_flights: list[dict]) -> dict:
    """
    PHASE 1: PLAN. One LLM call, produces the entire ordered assignment
    plan up front. Returns the parsed plan plus call stats -- no domain
    action has been executed yet at this point.
    """
    user_prompt = (
        f"Affected passengers (already ranked by policy priority):\n{affected_passengers}\n\n"
        f"Candidate replacement flights:\n{candidate_flights}\n\n"
        "Produce the full rebooking plan now."
    )
    result = call_llm_json(system_prompt=PLAN_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.2)

    if result["parsed"] is None:
        raise ValueError(f"Plan-and-Solve planning phase returned invalid JSON: {result['parse_error']}")

    return {
        "plan_steps": result["parsed"]["plan"],
        "llm_calls": 1,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }


def solve(plan_steps: list[dict], requested_by: str) -> dict:
    """
    PHASE 2: SOLVE. Executes the plan from phase 1 against the REAL
    rebook_passenger domain action, one step at a time, in the fixed
    order the plan already committed to. No step's result changes a
    LATER step's target -- that reactivity belongs to dynamic
    decomposition or LATS, not to Plan-and-Solve.
    """
    execution_log = []
    for step in sorted(plan_steps, key=lambda s: s["step"]):
        try:
            result = run_action(
                "rebook_passenger",
                booking_id=step["booking_id"],
                new_flight_number=step["target_flight_number"],
                requested_by=requested_by,
            )
            execution_log.append({
                "step": step["step"], "booking_id": step["booking_id"],
                "target_flight_number": step["target_flight_number"],
                "status": "executed", "result": result,
            })
        except Exception as e:
            execution_log.append({
                "step": step["step"], "booking_id": step["booking_id"],
                "status": "failed", "error": str(e),
            })

    return {"execution_log": execution_log}


def plan_and_solve(affected_passengers: list[dict], candidate_flights: list[dict], requested_by: str) -> dict:
    """Top-level entry point router.py / the orchestrator calls for a
    sub-task routed to Plan-and-Solve. Returns both phases' output plus
    combined cost stats for the comparison table."""
    plan_result = plan(affected_passengers, candidate_flights)
    solve_result = solve(plan_result["plan_steps"], requested_by)

    return {
        "plan_steps": plan_result["plan_steps"],
        "execution_log": solve_result["execution_log"],
        "llm_calls": plan_result["llm_calls"],
        "input_tokens": plan_result["input_tokens"],
        "output_tokens": plan_result["output_tokens"],
        "latency_seconds": plan_result["latency_seconds"],
    }


if __name__ == "__main__":
    # Demo with realistic shapes matching the BH202 seed data -- Mona
    # Khaled (none-tier) is the only confirmed booking on BH202 in the
    # seed data, so this also exercises the single-passenger case.
    fake_affected = [
        {"passenger_id": 2, "booking_id": 2, "full_name": "Mona Khaled",
         "loyalty_tier": "none", "fare_class": "business"},
    ]
    fake_candidates = [
        {"flight_number": "BH101", "scheduled_departure": "2026-08-01 10:00:00"},
    ]

    outcome = plan_and_solve(fake_affected, fake_candidates, requested_by="agent_014")

    print("=== Plan-and-Solve: propose_rebooking ===")
    print(f"LLM calls: {outcome['llm_calls']}, tokens: in={outcome['input_tokens']} "
          f"out={outcome['output_tokens']}, latency={outcome['latency_seconds']:.2f}s\n")

    print("Plan:")
    for step in outcome["plan_steps"]:
        print(f"  {step}")

    print("\nExecution log (against real rebook_passenger):")
    for entry in outcome["execution_log"]:
        print(f"  {entry}")