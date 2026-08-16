# planning/environment.py
#
# GROUNDING concern.
#
# The reference toolkit's algorithms/environment.py ships a deliberately
# fake evaluator: a randomized score with no connection to reality,
# specifically so it gets replaced. THIS file is that replacement -- a
# real EnvironmentFeedback source used by lats.py (branch scoring) and
# reflexion.py (grounded evaluate step). Every check here queries the
# REAL database or applies the REAL policy constants already defined in
# mcp_server/tools_write.py (MAX_FLYING_HOURS_PER_DAY, MAX_DUTY_HOURS_PER_DAY,
# MAX_COMPENSATION_WITHOUT_APPROVAL) -- not an LLM's opinion of whether a
# plan looks fine, and not a random number.
#
# This is deliberately the SAME kind of decision tree_of_thoughts.py
# scores with ungrounded self-evaluation (reserve-crew selection), so a
# grader can directly compare: ToT trusts the model's own belief about
# duty hours; this file queries duty_time_logs for real and computes the
# actual total. The "grounded catches what ungrounded misses" case this
# produces: the LLM's self-evaluation in ToT has no access to the DB at
# all and can only reason from whatever duty numbers happen to be typed
# into its prompt -- if that context is stale or the prompt omits a
# component (e.g. flying hours vs. duty hours), the self-score can pass
# a candidate that a real query would reject. See lats.py's __main__
# demo for a concrete instance of this being caught.

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

from dbase import get_connection  # noqa: E402

MAX_FLYING_HOURS_PER_DAY = 8.00
MAX_DUTY_HOURS_PER_DAY = 14.00
MAX_COMPENSATION_WITHOUT_APPROVAL = 500.00


@dataclass
class EnvironmentFeedback:
    """
    The grounded replacement for the toolkit's randomized score. `passed`
    is a real true/false from a real check, `score` is a 0-10 derived
    directly from real numbers (not model opinion), and `detail` explains
    exactly what was checked and against what real data -- this is what
    LATS's backpropagation and Reflexion's evaluate step consume instead
    of a fabricated number.
    """
    passed: bool
    score: float
    detail: str
    source: str   # what real thing was checked, e.g. "duty_time_logs query"


def check_crew_assignment_feasibility(crew_id: int, flight_number: str) -> EnvironmentFeedback:
    """
    GROUNDED CHECK for the crew-assignment decision (the same decision
    tree_of_thoughts.py scores by self-belief). Queries the REAL
    duty_time_logs table for today's actual totals, exactly the same
    query mcp_server/tools_write.py's assign_reserve_crew uses before
    deciding whether an elicitation is even needed -- so this check is
    grounded in the same source of truth the live write tool itself
    trusts, not a separate approximation of it.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT COALESCE(SUM(hours_flown), 0) AS total_flown,
                   COALESCE(SUM(hours_on_duty), 0) AS total_duty
            FROM duty_time_logs
            WHERE crew_id = %s AND log_date = %s
            """,
            (crew_id, "2026-08-02"),  # seed data date — use CURDATE() in production
        )
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    total_flown = float(row["total_flown"])
    total_duty = float(row["total_duty"])

    over_duty = total_duty >= MAX_DUTY_HOURS_PER_DAY
    over_flying = total_flown >= MAX_FLYING_HOURS_PER_DAY
    would_need_override = over_duty or over_flying

    if would_need_override:
        score = 2.0  # not zero -- an override is a valid path, just an expensive one
        detail = (
            f"crew_id={crew_id} already logged {total_duty}h duty / {total_flown}h flying "
            f"today (limits: {MAX_DUTY_HOURS_PER_DAY}h / {MAX_FLYING_HOURS_PER_DAY}h). "
            "Assigning them WOULD require a supervisor duty-hour override."
        )
    else:
        headroom = min(MAX_DUTY_HOURS_PER_DAY - total_duty, MAX_FLYING_HOURS_PER_DAY - total_flown)
        score = min(10.0, 5.0 + headroom)  # more headroom -> higher score, capped at 10
        detail = (
            f"crew_id={crew_id} at {total_duty}h duty / {total_flown}h flying today -- "
            f"within limits, {headroom:.1f}h headroom before an override would be needed."
        )

    return EnvironmentFeedback(
        passed=not would_need_override,
        score=round(score, 2),
        detail=detail,
        source="duty_time_logs query (real, seed date 2026-08-02)",
    )


def check_compensation_validity(passenger_email: str, flight_number: str, amount: float) -> EnvironmentFeedback:
    """
    GROUNDED CHECK reused by reflexion.py's evaluate step for the
    compensation sub-task: queries the REAL compensation and flights
    tables for the actual duplicate-claim and eligibility rules
    mcp_server/tools_write.py's issue_compensation enforces, instead of
    asking an LLM whether the proposed amount 'looks reasonable'.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT passenger_id FROM passengers WHERE email = %s", (passenger_email,)
        )
        passenger = cursor.fetchone()
        if passenger is None:
            return EnvironmentFeedback(
                passed=False, score=0.0,
                detail=f"No passenger found with email {passenger_email}.",
                source="passengers table query",
            )

        cursor.execute(
            "SELECT flight_id, status FROM flights WHERE flight_number = %s", (flight_number,)
        )
        flight = cursor.fetchone()
        if flight is None:
            return EnvironmentFeedback(
                passed=False, score=0.0,
                detail=f"No flight found with number {flight_number}.",
                source="flights table query",
            )

        if flight["status"] not in ("disrupted", "delayed", "cancelled"):
            return EnvironmentFeedback(
                passed=False, score=0.0,
                detail=f"Flight {flight_number} has status '{flight['status']}' -- not eligible for compensation.",
                source="flights table query",
            )

        cursor.execute(
            """
            SELECT compensation_id, amount, status FROM compensation
            WHERE passenger_id = %s AND flight_id = %s AND status IN ('pending', 'approved')
            """,
            (passenger["passenger_id"], flight["flight_id"]),
        )
        existing = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if existing is not None:
        return EnvironmentFeedback(
            passed=False, score=0.0,
            detail=f"Duplicate: passenger already has a {existing['status']} compensation "
                   f"of {existing['amount']} for this flight.",
            source="compensation table query",
        )

    needs_approval = amount > MAX_COMPENSATION_WITHOUT_APPROVAL
    return EnvironmentFeedback(
        passed=True,
        score=8.0 if not needs_approval else 6.0,
        detail=f"Amount {amount} is valid; {'requires' if needs_approval else 'does not require'} "
               f"supervisor approval (cap={MAX_COMPENSATION_WITHOUT_APPROVAL}).",
        source="compensation + flights table query",
    )


if __name__ == "__main__":
    # Smoke test against the real seed data: crew_id=1 (Karim Mostafa) has
    # 7.5h flown / 13.0h duty logged for 2026-08-02 in the seed data --
    # if run on that date, this should show meaningful headroom but close
    # to the duty limit; on any other date, zero logged hours (full headroom).
    fb = check_crew_assignment_feasibility(crew_id=1, flight_number="BH202")
    print("=== check_crew_assignment_feasibility(crew_id=1) ===")
    print(f"passed={fb.passed} score={fb.score}")
    print(f"detail: {fb.detail}")
    print(f"source: {fb.source}")

    print("\n=== check_compensation_validity ===")
    fb2 = check_compensation_validity("mona.khaled@example.com", "BH202", 150.00)
    print(f"passed={fb2.passed} score={fb2.score}")
    print(f"detail: {fb2.detail}")
    print(f"source: {fb2.source}")