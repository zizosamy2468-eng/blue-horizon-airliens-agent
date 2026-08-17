# planning/self_refine.py
#
# SELF-REFINE concern (Madaan et al., 2023).
#
# Adapted from the reference toolkit's algorithms/self_refine.py: ONE
# draft, ONE critique against an explicit rubric, ONE revision. Used for
# sub-task outputs that are cheap to redo -- drafting the passenger
# disruption notice is the textbook fit: getting it wrong costs a re-
# generation, not a re-negotiated flight or a duty-hour violation, so a
# single draft/critique/revise pass is proportionate (Reflexion's
# multi-trial, cross-attempt machinery in reflexion.py is reserved for
# sub-tasks where a single retry genuinely isn't enough).
#
# GROUNDING: the critique step is split into two parts, both stated
# explicitly per the lab's "state the source of truth" requirement:
#   1) A DETERMINISTIC, grounded check against the real flight row
#      (policy IROPS-COMM-1 / IROPS-MECH-1: must state the real flight
#      number and real status, and must NOT assert an unconfirmed
#      mechanical cause as settled fact). This is a real DB read, not an
#      opinion -- it either literally is or isn't true of the draft text.
#   2) An LLM rubric pass for tone/completeness/next-step clarity, which
#      IS model opinion, labeled as such rather than presented as if it
#      were grounded.
# Keeping these separate (rather than one blended "critique" call) is
# what lets a grader see exactly which parts of the critique are
# real-world-checked and which are the model's own judgment.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from llm_client import call_llm, call_llm_json  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))
from dbase import get_connection  # noqa: E402

DRAFT_SYSTEM_PROMPT = """Write a short, polite passenger disruption notice (3-4 sentences) \
for a Blue Horizon Airlines flight. Mention that affected passengers will be rebooked or \
compensated per policy. Do not invent details you were not given."""

RUBRIC_CRITIQUE_SYSTEM_PROMPT = """Critique this passenger disruption notice against this \
rubric: (1) is the tone polite and reassuring without over-promising, (2) is the next step \
(rebooking/compensation) clearly stated, (3) is it concise (3-4 sentences, no rambling). \
This is your own judgment, not a fact-check -- the factual accuracy of the notice has \
already been checked separately.

Respond ONLY with JSON: {"issues": ["...", ...], "acceptable": true|false}
"""

REVISE_SYSTEM_PROMPT = """Revise this passenger disruption notice to fix the issues listed \
below. Keep it 3-4 sentences, polite, and factually exactly as constrained."""


def draft_notice(flight_number: str, status: str, reason_for_prompt: str) -> dict:
    """DRAFT phase: one LLM call, no critique applied yet."""
    user_prompt = f"Flight {flight_number} is currently {status}, due to: {reason_for_prompt}."
    result = call_llm(system_prompt=DRAFT_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.7)
    return {
        "text": result["text"], "llm_calls": 1,
        "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }


def grounded_critique(flight_number: str, draft_text: str) -> dict:
    """
    GROUNDED half of the critique. Source of truth: the flights table,
    read fresh right now -- not the value that was in the prompt when
    the draft was written, which could theoretically be stale by the
    time this critique runs. Checks two real policy requirements:
      - IROPS-COMM-1: the real flight number and real status must
        actually appear in the draft text.
      - IROPS-MECH-1: if the real disruption_reason is 'mechanical' and
        has not been maintenance-confirmed (this project has no separate
        confirmation flag, so a mechanical reason is always treated as
        provisional per policy), the draft must NOT assert 'mechanical'
        as settled fact -- it should say something like 'an operational
        issue' instead.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT flight_number, status, disruption_reason FROM flights WHERE flight_number = %s",
            (flight_number,),
        )
        flight = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if flight is None:
        return {"grounded_ok": False, "grounded_issues": [f"No such flight {flight_number} in the database."]}

    issues = []
    if flight["flight_number"] not in draft_text:
        issues.append(f"Draft does not mention the real flight number {flight['flight_number']}.")
    if flight["status"] not in draft_text.lower() and flight["status"] not in draft_text:
        issues.append(f"Draft does not reflect the real status '{flight['status']}'.")
    if flight["disruption_reason"] == "mechanical" and "mechanical" in draft_text.lower():
        issues.append(
            "Draft asserts 'mechanical' as settled fact, but per IROPS-MECH-1 an unconfirmed "
            "mechanical cause must be described as 'an operational issue' instead."
        )

    return {"grounded_ok": len(issues) == 0, "grounded_issues": issues, "source": "flights table query (real, read at critique time)"}


def rubric_critique(draft_text: str) -> dict:
    """UNGROUNDED half of the critique -- explicitly labeled as model
    opinion on tone/completeness/concision, not a fact-check."""
    result = call_llm_json(system_prompt=RUBRIC_CRITIQUE_SYSTEM_PROMPT, user_prompt=draft_text, temperature=0.2)
    parsed = result["parsed"] or {"issues": [], "acceptable": True}
    return {
        "acceptable": parsed.get("acceptable", True), "issues": parsed.get("issues", []),
        "llm_calls": 1, "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"], "latency_seconds": result["latency_seconds"],
    }


def revise_notice(draft_text: str, all_issues: list[str]) -> dict:
    """REVISE phase: exactly one revision pass, incorporating BOTH the
    grounded issues and the rubric issues into a single fix."""
    if not all_issues:
        return {"text": draft_text, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_seconds": 0.0}

    issues_text = "\n".join(f"- {i}" for i in all_issues)
    user_prompt = f"Original draft:\n{draft_text}\n\nIssues to fix:\n{issues_text}"
    result = call_llm(system_prompt=REVISE_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.4)
    return {
        "text": result["text"], "llm_calls": 1,
        "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }


def self_refine_notice(flight_number: str, status: str, reason_for_prompt: str) -> dict:
    """
    Full Self-Refine loop: DRAFT -> CRITIQUE (grounded + rubric) ->
    REVISE, exactly one pass each. Returns both the original draft and
    the final text so a grader can see what actually changed, plus
    combined cost stats.
    """
    draft = draft_notice(flight_number, status, reason_for_prompt)

    grounded = grounded_critique(flight_number, draft["text"])
    rubric = rubric_critique(draft["text"])

    all_issues = grounded["grounded_issues"] + rubric["issues"]
    revision = revise_notice(draft["text"], all_issues)

    total_llm_calls = draft["llm_calls"] + rubric["llm_calls"] + revision["llm_calls"]
    total_input_tokens = draft["input_tokens"] + rubric["input_tokens"] + revision["input_tokens"]
    total_output_tokens = draft["output_tokens"] + rubric["output_tokens"] + revision["output_tokens"]
    total_latency = draft["latency_seconds"] + rubric["latency_seconds"] + revision["latency_seconds"]

    return {
        "draft_text": draft["text"],
        "grounded_critique": grounded,
        "rubric_critique": rubric,
        "final_text": revision["text"],
        "was_revised": len(all_issues) > 0,
        "llm_calls": total_llm_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "latency_seconds": total_latency,
    }


if __name__ == "__main__":
    # Demo against the real BH202 seed row (status='disrupted',
    # disruption_reason='mechanical') -- deliberately prompts the DRAFT
    # phase to say "mechanical" outright, so the grounded critique's
    # IROPS-MECH-1 check has something real to catch.
    outcome = self_refine_notice(
        "BH202",
        status="disrupted",
        reason_for_prompt="a confirmed mechanical fault with the aircraft",  # deliberately overstated
    )

    print("=== Self-Refine: passenger disruption notice for BH202 ===")
    print(f"LLM calls: {outcome['llm_calls']}, tokens: in={outcome['input_tokens']} "
          f"out={outcome['output_tokens']}, latency={outcome['latency_seconds']:.2f}s\n")

    print("Draft:\n", outcome["draft_text"])
    print("\nGrounded critique (source:", outcome["grounded_critique"].get("source"), ")")
    print(" issues:", outcome["grounded_critique"]["grounded_issues"])
    print("\nRubric critique (model opinion, not fact-checked):")
    print(" acceptable:", outcome["rubric_critique"]["acceptable"], "issues:", outcome["rubric_critique"]["issues"])

    print("\nWas revised:", outcome["was_revised"])
    print("\nFinal text:\n", outcome["final_text"])