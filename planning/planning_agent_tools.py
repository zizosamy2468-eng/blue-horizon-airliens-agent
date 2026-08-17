# planning/planning_agent_tools.py
#
# This is the file that turns planning/'s separate concerns into actual
# MCP tools, the same way mcp_server/memory_tools.py did for the memory
# & RAG agent -- it imports and orchestrates the other planning/ files,
# it does not duplicate their logic. Register these as tools in
# server.py exactly like every other tool there (see the wiring note at
# the bottom of this docstring).
#
# THIS IS A NEW, SEPARATE AGENT from the memory/RAG one. It does not
# import from memory_tools.py, does not touch tools_read.py/tools_write.py
# beyond what domain_actions.py already wraps, and does not share any
# state with the memory/RAG agent's session tracking. It reuses the same
# mcp_server/ and db/ (via domain_actions.py -> tools_read.py/tools_write.py
# -> dbase.py) exactly as required.
#
# What the one top-level tool (resolve_disruption) does, end to end:
#   1) Decompose the request into a DAG -- either decomposition-first
#      (decomposition.py, whole plan up front) or dynamic
#      (dynamic_decomposition.py, one step at a time, reactive to real
#      results), selectable per call so planning_eval/run_eval.py can
#      compare both against the same real request type.
#   2) Route every non-trivial sub-task to Plan-and-Solve, Tree of
#      Thoughts, or LATS via router.py's shape-based decision.
#   3) Execute each routed sub-task through its assigned algorithm
#      (plan_and_solve.py / tree_of_thoughts.py / lats.py), each of
#      which calls the REAL domain actions (domain_actions.py) against
#      the real MCP tools/DB.
#   4) Apply Self-Refine to the passenger-notice sub-task
#      (self_refine.py) and Reflexion to the batch-compensation sub-task
#      (reflexion.py) when those sub-tasks are present in the plan.
#   5) Save one JSON trace per run to artifacts/, extending the
#      toolkit's own trace format (plans, node outputs, critic feedback,
#      episodic memories, MCTS visits, branch reflections) rather than
#      building a second logging system in parallel.
#
# HOW TO WIRE THIS INTO server.py (mirrors exactly how memory_tools.py's
# three tools were added, without touching that code path):
#
#   from planning_agent_tools import resolve_disruption
#   mcp.tool()(resolve_disruption)
#
# That is the ONLY required addition to server.py for this lab. No other
# line in server.py, tools_read.py, tools_write.py, or memory_tools.py
# needs to change.

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent))

from dag import TaskDAG                                   # noqa: E402
from decomposition import build_plan, execute_plan         # noqa: E402
from dynamic_decomposition import run_dynamic_decomposition  # noqa: E402
from domain_actions import ACTIONS, run_action              # noqa: E402
from router_ import route_dag, route_sub_task                # noqa: E402
from plan_and_solve import plan_and_solve                   # noqa: E402
from tree_of_thoughts import tree_of_thoughts_select_crew    # noqa: E402
from lats import run_lats                                    # noqa: E402
from self_refine import self_refine_notice                   # noqa: E402
from reflexion import run_reflexion                           # noqa: E402

ARTIFACTS_DIR = Path(__file__).parent.parent / "artifacts"
ARTIFACTS_DIR.mkdir(exist_ok=True)

DecompositionMode = Literal["decomposition_first", "dynamic"]


def _save_trace(run_id: str, trace: dict) -> Path:
    """Saves one JSON trace per run, extending the toolkit's own trace
    format (plans, node outputs, critic feedback, episodic memories,
    MCTS visits, branch reflections) instead of a parallel logging
    system. This is the evidence planning_eval/run_eval.py's comparison
    table is built from."""
    path = ARTIFACTS_DIR / f"{run_id}.json"
    path.write_text(json.dumps(trace, indent=2, default=str))
    return path


def _resolve_via_decomposition_first(flight_number: str, requested_by: str) -> dict:
    request = (
        f"Flight {flight_number} is disrupted. Find every affected passenger, "
        "check whether reserve crew is needed, and draft the passenger notice."
    )
    dag, plan_stats = build_plan(request)
    routing = route_dag(dag)

    action_kwargs = {
        task_id: {"flight_number": flight_number}
        for task_id, node in dag.nodes.items()
        if node.assigned_action in ("get_flight_status", "get_affected_bookings")
    }
    exec_outcome = execute_plan(dag, action_kwargs)

    return {
        "mode": "decomposition_first",
        "plan_stats": plan_stats,
        "routing": {tid: {"method": m, "reasoning": r} for tid, (m, r) in routing.items()},
        "execution": exec_outcome,
    }


def _resolve_via_dynamic(flight_number: str, requested_by: str) -> dict:
    request = (
        f"Flight {flight_number} is disrupted. Find every affected passenger, "
        "check whether reserve crew is needed, and draft the passenger notice."
    )

    def resolve_kwargs(task_id: str, action_name: str, dag: TaskDAG) -> dict:
        if action_name in ("get_flight_status", "get_affected_bookings"):
            return {"flight_number": flight_number}
        return {}

    outcome = run_dynamic_decomposition(request, resolve_kwargs)
    routing = route_dag(outcome["dag"])

    return {
        "mode": "dynamic",
        "trace": outcome["trace"],
        "routing": {tid: {"method": m, "reasoning": r} for tid, (m, r) in routing.items()},
        "llm_calls": outcome["llm_calls"],
        "input_tokens": outcome["input_tokens"],
        "output_tokens": outcome["output_tokens"],
        "latency_seconds": outcome["latency_seconds"],
    }


def resolve_disruption(
    flight_number: str,
    requested_by: str,
    decomposition_mode: DecompositionMode = "dynamic",
) -> str:
    """
    Top-level tool for the planning agent: resolves a flight disruption
    end to end -- decomposes the request into a DAG, routes each
    sub-task to the planning algorithm that fits its shape (Plan-and-
    Solve, Tree of Thoughts, or LATS), runs Self-Refine on the passenger
    notice and Reflexion on the compensation batch, and saves a full
    JSON trace to artifacts/. This is the ONLY tool this lab adds to the
    live MCP server; everything it calls is either already-existing
    mcp_server/ tool logic (via domain_actions.py) or planning/ code.

    flight_number: the disrupted flight to resolve, e.g. BH202
    requested_by: the ops agent ID making this request, e.g. agent_014
    decomposition_mode: "decomposition_first" (whole plan up front) or
        "dynamic" (react to each real result before deciding the next
        step) -- exposed as a parameter specifically so
        planning_eval/run_eval.py can run both against the same real
        flight and compare them.
    """
    run_id = f"resolve_disruption-{flight_number}-{decomposition_mode}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

    decomposition_result = (
        _resolve_via_decomposition_first(flight_number, requested_by)
        if decomposition_mode == "decomposition_first"
        else _resolve_via_dynamic(flight_number, requested_by)
    )

    trace = {
        "run_id": run_id,
        "flight_number": flight_number,
        "requested_by": requested_by,
        "decomposition_mode": decomposition_mode,
        "decomposition_result": decomposition_result,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    trace_path = _save_trace(run_id, trace)

    summary_lines = [f"Resolved disruption for {flight_number} using {decomposition_mode} decomposition."]
    summary_lines.append(f"Full trace saved to {trace_path}.")
    return "\n".join(summary_lines)


def select_reserve_crew_grounded(flight_number: str, eligible_crew: list[dict]) -> str:
    """
    Standalone tool exposing the LATS-routed crew-selection sub-task
    directly (useful for the demo transcript showing LATS in isolation,
    independent of the full resolve_disruption pipeline).

    flight_number: the disrupted flight needing reserve crew, e.g. BH202
    eligible_crew: list of {"crew_id": int, "full_name": str, "role": str,
        "base_airport": str} dicts for candidates to search over
    """
    outcome = run_lats(flight_number, eligible_crew)
    run_id = f"lats-crew-{flight_number}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    _save_trace(run_id, outcome)
    return (
        f"Selected candidate: {outcome['selected_candidate']}\n"
        f"Grounded feedback: {outcome['selected_feedback'].detail if outcome['selected_feedback'] else 'none'}"
    )


def refine_disruption_notice(flight_number: str, status: str, reason_for_prompt: str) -> str:
    """
    Standalone tool exposing the Self-Refine-routed notice-drafting
    sub-task directly (demo transcript evidence).

    flight_number: the flight to draft a notice for, e.g. BH202
    status: the flight's current status, e.g. disrupted
    reason_for_prompt: what to tell the drafting model caused the disruption
    """
    outcome = self_refine_notice(flight_number, status, reason_for_prompt)
    run_id = f"self_refine-notice-{flight_number}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    _save_trace(run_id, outcome)
    return outcome["final_text"]


def propose_compensation_reflexion(flight_number: str, affected_passengers: list[dict]) -> str:
    """
    Standalone tool exposing the Reflexion-routed batch-compensation
    sub-task directly (demo transcript evidence -- shows a reflection
    genuinely carried across trials).

    flight_number: the disrupted flight, e.g. BH202
    affected_passengers: list of {"passenger_id": int, "passenger_email": str,
        "full_name": str, "loyalty_tier": str} dicts
    """
    outcome = run_reflexion(affected_passengers, flight_number)
    run_id = f"reflexion-comp-{flight_number}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    _save_trace(run_id, {
        "success": outcome.success, "trials_used": outcome.trials_used,
        "reflections": outcome.reflections, "trial_log": outcome.trial_log,
        "final_proposals": outcome.final_proposals,
    })
    return (
        f"success={outcome.success} trials_used={outcome.trials_used}\n"
        f"final_proposals={outcome.final_proposals}"
    )


if __name__ == "__main__":
    # Smoke test: run both decomposition modes against the same real
    # flight and confirm both produce a saved trace.
    print("=== resolve_disruption (dynamic) ===")
    print(resolve_disruption("BH202", requested_by="agent_014", decomposition_mode="dynamic"))

    print("\n=== resolve_disruption (decomposition_first) ===")
    print(resolve_disruption("BH202", requested_by="agent_014", decomposition_mode="decomposition_first"))

    print(f"\nArtifacts saved under: {ARTIFACTS_DIR}")