# planning/router.py
#
# ROUTING concern.
#
# Every sub-task in a DAG (built by decomposition.py or
# dynamic_decomposition.py) that needs more than a single deterministic
# tool call gets routed here to ONE of the three planning algorithms.
# The routing decision is NOT a guess or an LLM call -- it's read
# directly off the shape already recorded on the domain action in
# domain_actions.py's ACTIONS registry, so it's cheap, deterministic, and
# traceable back to a real property of the action instead of a hidden
# judgment call:
#
#   shape="deterministic" -> Plan-and-Solve
#     A fixed sequence with one correct order and no real branching
#     (e.g. propose_rebooking: rank passengers, then assign them to
#     replacement flights one at a time in priority order). A single
#     explicit plan is enough; there's nothing worth exploring several
#     versions of.
#
#   shape="reasoning" -> Tree of Thoughts
#     Several genuinely different valid approaches exist and picking
#     badly is costly (e.g. assign_reserve_crew: which crew member to
#     propose, at what duty-hour risk, matters enough to generate and
#     compare a few candidates before committing to one).
#
#   shape="retrieval" -> LATS
#     Needs real tool-use/search against live data where the search
#     itself can go down unproductive paths and benefits from MCTS-style
#     exploration with grounded external feedback, not just one lookup
#     (e.g. find_candidate_replacement_flights when the direct route is
#     unavailable and multi-hop/alternate-airport options must be
#     explored and scored against real seat availability).
#
# This mirrors the lab's own diagram exactly: Logical/Deterministic -> PS,
# Complex Reasoning/Search -> ToT, Knowledge/Retrieval -> LATS.

import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent))

from dag import TaskDAG, TaskNode        # noqa: E402
from domain_actions import ACTIONS        # noqa: E402

PlanningMethod = Literal["plan_and_solve", "tree_of_thoughts", "lats", "direct"]

_SHAPE_TO_METHOD: dict[str, PlanningMethod] = {
    "deterministic": "plan_and_solve",
    "reasoning": "tree_of_thoughts",
    "retrieval": "lats",
}


def route_sub_task(node: TaskNode) -> tuple[PlanningMethod, str]:
    """
    Returns (method, reasoning) for a single DAG node. 'direct' is
    returned only for actions that are read-only, single-call, and
    have no decision content at all (e.g. get_flight_status) -- those
    don't need ANY of the three planning algorithms, they're just
    executed directly by decomposition.py / dynamic_decomposition.py.
    Everything else in ACTIONS is deliberately shaped to need PS, ToT,
    or LATS -- that's what makes this router non-trivial to write, per
    the lab's own warning against a DAG over fake three-item to-dos.
    """
    if node.assigned_action not in ACTIONS:
        raise KeyError(f"No registered domain action for node '{node.task_id}': {node.assigned_action}")

    action = ACTIONS[node.assigned_action]

    # Plain reads with no decision content stay direct -- routing them
    # through a planning algorithm would be exactly the "fake to-do
    # item" anti-pattern the lab warns against.
    if action.name in ("get_flight_status",) and not action.is_write:
        return "direct", f"'{action.name}' is a single deterministic lookup with no decision to plan."

    method = _SHAPE_TO_METHOD[action.shape]

    reasoning = {
        "plan_and_solve": (
            f"'{action.name}' has one correct execution order once inputs are known "
            "(e.g. assign ranked passengers to replacement flights in priority order) -- "
            "a single explicit plan is sufficient, no branch is worth exploring."
        ),
        "tree_of_thoughts": (
            f"'{action.name}' has several genuinely different valid choices "
            "(which crew member, what duty-hour risk to accept) where a wrong pick is "
            "costly enough to justify generating and comparing candidates before committing."
        ),
        "lats": (
            f"'{action.name}' requires real search over live data (alternate routes, "
            "candidate flights) where early paths can be dead ends -- MCTS-style "
            "exploration with grounded feedback fits better than a single-shot lookup."
        ),
    }[method]

    return method, reasoning


def route_dag(dag: TaskDAG) -> dict[str, tuple[PlanningMethod, str]]:
    """Routes every node in a DAG at once -- used by the orchestrator
    (planning_agent_tools.py) right after a plan is built, before
    execution starts, so the routing decision is visible in the trace
    alongside the plan itself."""
    return {task_id: route_sub_task(node) for task_id, node in dag.nodes.items()}


if __name__ == "__main__":
    from dag import TaskDAG

    dag = TaskDAG("resolve disruption for BH202")
    dag.add_node("check_status", "Check BH202's current status", assigned_action="get_flight_status")
    dag.add_node("find_affected", "Find affected bookings", assigned_action="get_affected_bookings")
    dag.add_node("find_candidates", "Find candidate replacement flights", assigned_action="get_candidate_replacement_flights")
    dag.add_node("rebook", "Rebook passengers onto replacements", assigned_action="rebook_passenger")
    dag.add_node("assign_crew", "Assign reserve crew if needed", assigned_action="assign_reserve_crew")
    dag.add_node("compensate", "Issue compensation where eligible", assigned_action="issue_compensation")

    routing = route_dag(dag)
    print("=== Routing decisions ===")
    for task_id, (method, reasoning) in routing.items():
        print(f"[{task_id}] -> {method}")
        print(f"    {reasoning}\n")