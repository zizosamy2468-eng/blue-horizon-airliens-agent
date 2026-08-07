# CONTEXT MANAGEMENT TEST SUITE.
#
# The scenario this simulates: an ops agent handling BH202's disruption
# ends up making a lot of tool calls in one session -- checking on other
# flights, pulling passenger bookings, checking duty logs -- while one
# early fact matters a lot later: crew_id=1 (Karim Mostafa) already got a
# supervisor override for exceeding duty hours today. If that gets buried
# and forgotten, a later reserve-crew assignment could illegally double
# up his hours with nobody noticing -- a real cost, not a toy example.
#
# Each test case is a transcript of ~40 turns:
#   - turn 0-1: session setup (user asks about BH202)
#   - turn 2:   THE CRITICAL FACT (the override)
#   - turns 3-38: unrelated tool-call noise (other flights, other passengers,
#                 other duty checks) -- this is deliberately large/realistic
#                 input, which is the cheap way to stress-test pruning
#                 (see the lab's cost note: input tokens are cheap, lean on
#                 big realistic transcripts rather than generating lots of
#                 model output).
#   - turn 39:  the final user question that NEEDS the critical fact
#
# We generate 10 seeded variations (varying which flights/passengers show
# up in the noise, and where in the noise block the critical fact sits
# relative to a couple of near-duplicate but NON-critical override
# mentions for other crew) so results aren't a fluke of one transcript.

import random
from dataclasses import dataclass

CRITICAL_FACT = (
    "SUPERVISOR APPROVAL NEEDED: Karim Mostafa (crew_id=1) already at 13.0 duty "
    "hours today. Approved by sup_001 as an override for flight BH202."
)

FINAL_QUERY = (
    "Before assigning any more reserve crew to BH202 today, are there any "
    "existing duty-hour overrides on record for any crew member we should know about?"
)

# What "correctly recalled" means for the deterministic checker in run_eval.py:
# the pruned context must still contain the crew member's name/id AND the
# fact that an override happened AND who approved it. Losing any one of
# those would produce an incomplete/wrong answer in a real system.
REQUIRED_MARKERS = ["crew_id=1", "sup_001", "override"]

OTHER_FLIGHTS = ["BH101", "BH303", "BH404", "BH505", "BH606", "BH707"]
OTHER_PASSENGERS = [
    "ahmed.samir@example.com", "mona.khaled@example.com", "youssef.adel@example.com",
    "sara.mahmoud@example.com", "omar.tarek@example.com",
]
NOISE_STATUSES = ["scheduled", "delayed", "on time", "boarding"]


@dataclass
class TestTurn:
    role: str      # "user" | "tool_call" | "tool_result"
    content: str


@dataclass
class TestCase:
    variation_id: int
    transcript: list[TestTurn]
    critical_index: int   # position of the critical fact in the transcript
    final_query: str
    required_markers: list[str]


def _noise_turn(rng: random.Random) -> list[TestTurn]:
    """One realistic unrelated tool_call/tool_result pair -- the bulk of the transcript."""
    kind = rng.choice(["flight_status", "booking", "duty_log", "unrelated_override"])

    if kind == "flight_status":
        flight = rng.choice(OTHER_FLIGHTS)
        status = rng.choice(NOISE_STATUSES)
        return [
            TestTurn("tool_call", f"get_flight_status(flight_number='{flight}')"),
            TestTurn("tool_result", f"Flight {flight}: Status: {status} - Reason: None"),
        ]

    if kind == "booking":
        email = rng.choice(OTHER_PASSENGERS)
        flight = rng.choice(OTHER_FLIGHTS)
        return [
            TestTurn("tool_call", f"get_passenger_booking(passenger_email='{email}')"),
            TestTurn("tool_result", f"- Flight {flight} | Seat 14C | economy | Booking status: confirmed | Flight status: scheduled"),
        ]

    if kind == "duty_log":
        crew_id = rng.choice([2, 3, 4, 5])
        hours = round(rng.uniform(1.0, 6.0), 1)
        return [
            TestTurn("tool_call", f"check_duty_hours(crew_id={crew_id})"),
            TestTurn("tool_result", f"Crew {crew_id} has logged {hours} duty hours today. No override needed."),
        ]

    # A near-duplicate but NON-critical override, about a different crew
    # member, to make sure strategies aren't just pattern-matching the word
    # "override" -- they need to keep the RIGHT one (crew_id=1 / sup_001).
    other_crew_id = rng.choice([3, 4, 5])
    other_sup = rng.choice(["sup_002"])
    return [
        TestTurn("tool_call", f"assign_reserve_crew(flight_number='{rng.choice(OTHER_FLIGHTS)}', crew_id={other_crew_id})"),
        TestTurn("tool_result", f"Approved: crew {other_crew_id} assigned. (Supervisor override: Approved by {other_sup}.)"),
    ]


def build_test_case(variation_id: int, noise_pairs: int = 18) -> TestCase:
    """
    noise_pairs=18 -> 36 noise turns (tool_call + tool_result each), plus
    2 setup turns + 1 critical fact turn + 1 final query = 40 turns total,
    matching the lab's "40-turn transcript, critical detail at turn 3"
    shape from the worked example.
    """
    rng = random.Random(variation_id)  # seeded so each variation is reproducible

    transcript: list[TestTurn] = [
        TestTurn("user", "Can you help me manage today's disruptions, starting with BH202?"),
        TestTurn("tool_call", "get_flight_status(flight_number='BH202')"),
    ]

    critical_index = len(transcript)
    transcript.append(TestTurn("tool_result", CRITICAL_FACT))

    while len(transcript) < 2 + 1 + noise_pairs * 2:
        transcript.extend(_noise_turn(rng))

    transcript.append(TestTurn("user", FINAL_QUERY))

    return TestCase(
        variation_id=variation_id,
        transcript=transcript,
        critical_index=critical_index,
        final_query=FINAL_QUERY,
        required_markers=REQUIRED_MARKERS,
    )


def build_all_variations(n: int = 10) -> list[TestCase]:
    return [build_test_case(i) for i in range(n)]


if __name__ == "__main__":
    cases = build_all_variations(n=3)  # small preview, run_eval.py uses all 10
    for case in cases:
        print(f"--- Variation {case.variation_id} ---")
        print(f"Total turns: {len(case.transcript)}, critical fact at index {case.critical_index}")
        print(f"Turn {case.critical_index}: {case.transcript[case.critical_index].content}")
        print(f"Final query (turn {len(case.transcript) - 1}): {case.final_query}")
        print()