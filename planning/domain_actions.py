# planning/domain_actions.py
#
# ADAPTER LAYER between the planning agent and the real MCP tools/DB.
# This file does NOT reimplement any tool logic -- it wraps the existing
# functions from tools_read.py / tools_write.py so the planner can call
# real actions, and it classifies each action by "shape" so router.py can
# decide PS vs ToT vs LATS without guessing.
#
# Import path assumes this sits at <repo_root>/planning/domain_actions.py
# and mcp_server/ sits at <repo_root>/mcp_server/.

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

from tools_read import get_flight_status, get_passenger_booking          # noqa: E402
from tools_write import assign_reserve_crew, issue_compensation, rebook_passenger  # noqa: E402
from dbase import get_connection                                         # noqa: E402

ActionShape = Literal["deterministic", "reasoning", "retrieval"]


@dataclass
class DomainAction:
    name: str
    fn: Callable
    shape: ActionShape   # deterministic -> PS, reasoning -> ToT, retrieval -> LATS
    is_write: bool        # True = changes real DB state, needs grounded check after


def get_affected_bookings(flight_number: str) -> list[dict]:
    """Real DB read: every confirmed booking on a disrupted flight.
    This is what sub-task decomposition anchors on -- the DAG's first
    node is always 'find out who is actually affected'."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT b.booking_id, b.passenger_id, b.fare_class, p.full_name,
               p.loyalty_tier, f.flight_id, f.status
        FROM bookings b
        JOIN passengers p ON b.passenger_id = p.passenger_id
        JOIN flights f ON b.flight_id = f.flight_id
        WHERE f.flight_number = %s AND b.booking_status = 'confirmed'
        """,
        (flight_number,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


def get_candidate_replacement_flights(origin: str, destination: str) -> list[dict]:
    """Real DB read: scheduled/delayed flights on the same route, used
    as branching candidates for the rebooking sub-task."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT flight_id, flight_number, status, scheduled_departure
        FROM flights
        WHERE origin_airport = %s AND destination_airport = %s
              AND status IN ('scheduled', 'delayed')
        """,
        (origin, destination),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows


# -----------------------------------------------------------
# The action registry the planner routes sub-tasks through.
# -----------------------------------------------------------
ACTIONS: dict[str, DomainAction] = {
    "get_flight_status": DomainAction("get_flight_status", get_flight_status, "deterministic", False),
    "get_affected_bookings": DomainAction("get_affected_bookings", get_affected_bookings, "deterministic", False),
    "get_candidate_replacement_flights": DomainAction(
        "get_candidate_replacement_flights", get_candidate_replacement_flights, "retrieval", False
    ),
    "rebook_passenger": DomainAction("rebook_passenger", rebook_passenger, "deterministic", True),
    "assign_reserve_crew": DomainAction("assign_reserve_crew", assign_reserve_crew, "reasoning", True),
    "issue_compensation": DomainAction("issue_compensation", issue_compensation, "reasoning", True),
}


def run_action(name: str, **kwargs) -> Any:
    if name not in ACTIONS:
        raise KeyError(f"Unknown domain action: {name}")
    return ACTIONS[name].fn(**kwargs)


if __name__ == "__main__":
    # Smoke test: confirm the adapter actually reaches real data, no mocks.
    bookings = get_affected_bookings("BH202")
    print(f"Affected bookings on BH202: {len(bookings)}")
    for b in bookings:
        print(f"  - {b['full_name']} ({b['loyalty_tier']}) seat class {b['fare_class']}")

    candidates = get_candidate_replacement_flights("CAI", "LHR")
    print(f"\nCandidate replacement flights CAI->LHR: {len(candidates)}")