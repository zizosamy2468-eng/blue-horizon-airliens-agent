# planning_eval/test_suite.py
#
# THE FIXED TEST SUITE for the planning agent's comparison table.
#
# Per the lab's guardrail: "keep your planning test suite fixed once you
# start evaluating." Every case below is written once, up front, and
# planning_eval/run_eval.py must run every method against every
# applicable case unchanged -- no editing test cases between runs.
#
# The lab requires the suite to include, at minimum:
#   - a case that should favor decomposition-first over dynamic decomposition
#   - a case that should favor dynamic decomposition over decomposition-first
#   - a case that needs lookahead search (routed to LATS/ToT)
#   - a case where a single retry isn't enough and only Reflexion's
#     cross-trial memory helps
#
# Every case is a real request shape against the real Blue Horizon
# schema/seed data (mcp_server/db.sql) -- not a synthetic three-item
# to-do, per the lab's own warning against that anti-pattern.

from dataclasses import dataclass, field


@dataclass
class PlanningTestCase:
    case_id: str
    request_text: str                 # the actual prompt sent to the agent
    flight_number: str
    category: str                     # see CATEGORY DEFINITIONS below
    favors: str                       # which method this case is designed to favor, and why
    notes: str = ""
    extra_context: dict = field(default_factory=dict)


# =============================================================
# CATEGORY DEFINITIONS
# =============================================================
# "decomposition_first_favored": the request is fully mechanical once
#   scoped -- every sub-task's inputs are known up front and nothing
#   about it can change mid-plan. A fixed plan costs less (fewer LLM
#   calls) and there's no real branching to react to.
#
# "dynamic_favored": the request genuinely can't be planned correctly up
#   front -- an early sub-task's real result changes what's needed later
#   (e.g. zero affected passengers means rebooking/compensation sub-tasks
#   should never even be proposed). A fixed decomposition-first plan
#   would blindly execute stale steps; dynamic decomposition reacts.
#
# "lookahead_needed": picking badly among several valid options is
#   costly and worth comparing candidates before committing (routed to
#   ToT for the ungrounded self-eval version, LATS for the grounded
#   version) -- reserve-crew selection near the duty-hour limit is the
#   canonical case in this system.
#
# "reflexion_needed": a single retry is not enough -- the correct output
#   requires learning across MULTIPLE real failures within one run (a
#   batch compensation proposal with more than one policy trap baked in,
#   so fixing the first-caught mistake surfaces a second one on retry).
#
# "self_refine_sufficient": cheap-to-redraft, single-critique-pass output
#   where a full multi-trial Reflexion loop would be overkill -- the
#   passenger notice.


TEST_CASES: list[PlanningTestCase] = [
    # ------------------------------------------------------------
    # decomposition-first favored: no real branching once scoped
    # ------------------------------------------------------------
    PlanningTestCase(
        case_id="df_01_status_check_only",
        request_text="Just check BH101's current status and confirm whether anything needs handling.",
        flight_number="BH101",
        category="decomposition_first_favored",
        favors="decomposition_first",
        notes=(
            "BH101 is 'scheduled' in the seed data -- a single deterministic lookup with "
            "nothing to react to. Decomposition-first's one-shot plan (1 node, 1 LLM call) "
            "should match dynamic's outcome at a fraction of the LLM calls, since there is "
            "no real surprise for dynamic decomposition to react to."
        ),
    ),
    PlanningTestCase(
        case_id="df_02_known_single_passenger_notice",
        request_text=(
            "Flight BH303 is cancelled due to weather. Draft the passenger disruption "
            "notice for it -- no rebooking or compensation needed since weather cancellations "
            "don't trigger standard compensation."
        ),
        flight_number="BH303",
        category="decomposition_first_favored",
        favors="decomposition_first",
        notes=(
            "The scope is fully known up front (draft notice only, explicitly excluding "
            "rebooking/compensation) -- a fixed 1-2 node plan is proportionate; dynamic "
            "decomposition would spend extra LLM calls re-deriving the same scope step by step."
        ),
    ),

    # ------------------------------------------------------------
    # dynamic decomposition favored: early result changes the plan
    # ------------------------------------------------------------
    PlanningTestCase(
        case_id="dyn_01_zero_affected_passengers",
        request_text=(
            "Flight BH202 is disrupted due to a mechanical issue. Resolve the disruption: "
            "find affected passengers, rebook or compensate them as needed, and draft the "
            "passenger notice."
        ),
        flight_number="BH202",
        category="dynamic_favored",
        favors="dynamic",
        notes=(
            "If BH202's confirmed-booking count is small or drops to zero (e.g. after all "
            "prior bookings on it were already rebooked in an earlier run), rebooking and "
            "compensation sub-tasks become unnecessary. Decomposition-first commits to a "
            "fixed plan BEFORE seeing that result and would blindly attempt rebooking/"
            "compensation against an empty or shrinking passenger list; dynamic decomposition "
            "observes the real get_affected_bookings result first and skips the now-pointless "
            "sub-tasks -- this is the required 'two methods diverge' case."
        ),
    ),
    PlanningTestCase(
        case_id="dyn_02_flight_status_changes_scope",
        request_text=(
            "Check on flight BH303 and handle whatever it needs -- rebooking, compensation, "
            "crew reassignment, or just a status confirmation, whatever applies."
        ),
        flight_number="BH303",
        category="dynamic_favored",
        favors="dynamic",
        notes=(
            "BH303 is 'cancelled' due to weather in the seed data, which per policy "
            "(IROPS-COMP-2) means NO standard compensation applies, only free rebooking. "
            "A decomposition-first plan generated from the vague request text alone (before "
            "seeing the real status/reason) is likely to include a compensation sub-task that "
            "should never run; dynamic decomposition checks the real status first and "
            "correctly omits it."
        ),
    ),

    # ------------------------------------------------------------
    # lookahead search needed: PS vs ToT vs LATS comparison
    # ------------------------------------------------------------
    PlanningTestCase(
        case_id="look_01_crew_near_duty_limit",
        request_text=(
            "Flight BH202 needs a reserve crew member assigned. Pick the best candidate "
            "from the eligible CAI-based crew."
        ),
        flight_number="BH202",
        category="lookahead_needed",
        favors="tree_of_thoughts_or_lats",
        notes=(
            "Multiple valid candidates exist (Karim Mostafa, Laila Hassan, Nourhan Fathy), "
            "one of whom (crew_id=1) is close to the real duty-hour limit in the seed data. "
            "Plan-and-Solve would commit to the first plausible candidate with no comparison; "
            "this case is specifically designed to show ToT's ungrounded self-score "
            "potentially passing a candidate that LATS's grounded duty_time_logs check "
            "correctly rejects -- the deliberate grounded-vs-ungrounded swap case."
        ),
        extra_context={
            "eligible_crew": [
                {"crew_id": 1, "full_name": "Capt. Karim Mostafa", "role": "pilot", "base_airport": "CAI"},
                {"crew_id": 2, "full_name": "Capt. Laila Hassan", "role": "co_pilot", "base_airport": "CAI"},
                {"crew_id": 3, "full_name": "Nourhan Fathy", "role": "flight_attendant", "base_airport": "CAI"},
            ]
        },
    ),
    PlanningTestCase(
        case_id="look_02_replacement_route_search",
        request_text=(
            "Flight BH202 (CAI to LHR) is disrupted. Find the best way to get its "
            "passengers to LHR, considering direct and indirect replacement options."
        ),
        flight_number="BH202",
        category="lookahead_needed",
        favors="lats",
        notes=(
            "Only BH101 (a different route, CAI->JFK) and no other CAI->LHR flight exist "
            "in the seed data at evaluation time -- a real search has to explore and reject "
            "at least one dead-end path before concluding no direct replacement exists, which "
            "is exactly the shape LATS's multi-iteration search (not a single ToT pass) is for."
        ),
    ),

    # ------------------------------------------------------------
    # Reflexion needed: single retry insufficient, cross-trial memory required
    # ------------------------------------------------------------
    PlanningTestCase(
        case_id="refl_01_duplicate_plus_tier_multiplier",
        request_text=(
            "Propose compensation for every affected passenger on BH202: Mona Khaled "
            "(none-tier) and Youssef Adel (platinum-tier, currently on a cancelled booking "
            "for a different flight but treat him as also affected here for this test)."
        ),
        flight_number="BH202",
        category="reflexion_needed",
        favors="reflexion",
        notes=(
            "TWO independent real traps are baked in: (1) Mona Khaled already has an "
            "APPROVED compensation on BH202 in the seed data -- a duplicate-claim rejection "
            "on trial 1. (2) Youssef Adel is platinum-tier, so IROPS-COMP-5's 1.25x multiplier "
            "must be applied -- a naive first proposal commonly gets only ONE of these two "
            "traps right per attempt (fixes the duplicate on trial 2, still misses the "
            "multiplier, needs trial 3). This is the case where a single Self-Refine-style "
            "revision is not enough and Reflexion's capped cross-trial reflection buffer is "
            "what actually gets both traps fixed simultaneously by the final trial."
        ),
        extra_context={
            "affected_passengers": [
                {"passenger_id": 2, "passenger_email": "mona.khaled@example.com",
                 "full_name": "Mona Khaled", "loyalty_tier": "none"},
                {"passenger_id": 3, "passenger_email": "youssef.adel@example.com",
                 "full_name": "Youssef Adel", "loyalty_tier": "platinum"},
            ]
        },
    ),

    # ------------------------------------------------------------
    # Self-Refine sufficient: cheap single-pass correction
    # ------------------------------------------------------------
    PlanningTestCase(
        case_id="sr_01_overstated_mechanical_cause",
        request_text=(
            "Draft the passenger disruption notice for BH202, describing the cause as a "
            "confirmed mechanical fault."
        ),
        flight_number="BH202",
        category="self_refine_sufficient",
        favors="self_refine",
        notes=(
            "A single grounded-critique pass (IROPS-MECH-1: unconfirmed mechanical cause "
            "must not be asserted as fact) plus one revision is proportionate and sufficient "
            "-- there's no multi-trial learning needed for a single, well-defined factual "
            "correction like this one."
        ),
    ),

    # ------------------------------------------------------------
    # Additional plentiful cases per the lab's "make them plentiful" instruction
    # ------------------------------------------------------------
    PlanningTestCase(
        case_id="df_03_confirmed_no_action_needed",
        request_text="Confirm flight BH101 does not need any disruption handling right now.",
        flight_number="BH101",
        category="decomposition_first_favored",
        favors="decomposition_first",
    ),
    PlanningTestCase(
        case_id="dyn_03_crew_already_within_limits",
        request_text=(
            "Flight BH202 needs a reserve crew member. Assign whichever crew member is "
            "actually eligible right now without requesting any supervisor override."
        ),
        flight_number="BH202",
        category="dynamic_favored",
        favors="dynamic",
        notes=(
            "If the real duty_time_logs check (run at evaluation time, not the seed date) "
            "shows every eligible candidate well within limits, dynamic decomposition can "
            "stop after one grounded check succeeds; decomposition-first's fixed plan may "
            "include an unnecessary override-request sub-task regardless."
        ),
    ),
    PlanningTestCase(
        case_id="look_03_ranked_priority_ordering",
        request_text=(
            "Multiple passengers are affected on BH202 with different loyalty tiers -- "
            "determine the correct rebooking priority order per policy."
        ),
        flight_number="BH202",
        category="lookahead_needed",
        favors="tree_of_thoughts",
        notes="Several valid-looking orderings exist; comparing candidates against IROPS-REBOOK-1 before committing is worth the extra LLM calls over a single Plan-and-Solve guess.",
    ),
    PlanningTestCase(
        case_id="refl_02_amount_and_currency_mismatch",
        request_text=(
            "Propose compensation for Mona Khaled on BH202, being careful this time to "
            "check whether she already has a claim before proposing an amount."
        ),
        flight_number="BH202",
        category="reflexion_needed",
        favors="reflexion",
        notes="Simpler single-passenger variant of refl_01, used to confirm Reflexion converges in fewer trials when only one trap is present -- a useful contrast row in the comparison table.",
        extra_context={
            "affected_passengers": [
                {"passenger_id": 2, "passenger_email": "mona.khaled@example.com",
                 "full_name": "Mona Khaled", "loyalty_tier": "none"},
            ]
        },
    ),
    PlanningTestCase(
        case_id="sr_02_missing_next_step",
        request_text="Draft a very short passenger notice for BH202 that doesn't mention what happens next.",
        flight_number="BH202",
        category="self_refine_sufficient",
        favors="self_refine",
        notes="Deliberately prompts an incomplete draft (missing next-step per IROPS-COMM-1) so the rubric-critique half of Self-Refine has something concrete to catch, separate from the grounded half's mechanical-cause check.",
    ),
]


def get_cases_by_category(category: str) -> list[PlanningTestCase]:
    return [c for c in TEST_CASES if c.category == category]


if __name__ == "__main__":
    by_category: dict[str, list[PlanningTestCase]] = {}
    for c in TEST_CASES:
        by_category.setdefault(c.category, []).append(c)

    print(f"Total test cases: {len(TEST_CASES)}\n")
    for category, cases in by_category.items():
        print(f"=== {category} ({len(cases)} cases) ===")
        for c in cases:
            print(f"  [{c.case_id}] favors={c.favors}")
            print(f"      {c.request_text[:90]}...")
        print()