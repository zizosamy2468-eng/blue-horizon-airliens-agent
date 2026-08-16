# planning/decomposition.py
#
# DECOMPOSITION-FIRST concern.
#
# Adapted from the reference toolkit's algorithms/decomposition.py: the
# whole plan is generated in ONE shot by the LLM, turned into a TaskDAG
# (dag.py enforces acyclicity), then executed straight through in
# topological order. No sub-task's outcome can change what a LATER
# sub-task in the plan is -- that reactivity is dynamic_decomposition.py's
# job, not this file's. This file is the "commit to the plan" baseline
# the comparison table (planning_eval/) measures dynamic decomposition
# against on the SAME real request type: resolving a flight disruption.
#
# Real domain wiring (not the toolkit's generic demo prompts): the LLM is
# told exactly which domain actions exist (planning/domain_actions.py's
# ACTIONS registry) and must only propose sub-tasks that map to one of
# them, so every node in the resulting DAG is executable against the real
# MCP tools/DB, not a vague free-text step.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dag import CycleError, TaskDAG          # noqa: E402
from domain_actions import ACTIONS, run_action  # noqa: E402
from llm_client import call_llm_json          # noqa: E402

DECOMPOSITION_SYSTEM_PROMPT = """You are the planning module for Blue Horizon Airlines' \
IROPS disruption-response agent. Given a disruption-handling request, break it into a \
DAG of sub-tasks using ONLY the available domain actions listed below. Each sub-task must \
map to exactly one action.

Available actions:
{actions_list}

Respond ONLY with JSON in this exact shape:
{{
  "tasks": [
    {{"id": "short_snake_case_id", "description": "...", "action": "one_of_the_action_names", "depends_on": ["id", ...]}}
  ]
}}

Rules:
- depends_on must only reference task ids that appear earlier in the "tasks" list.
- Every action name must be one of the ones listed above, exactly as written.
- Keep the plan minimal: only include sub-tasks genuinely needed to resolve the request.
- The plan must be acyclic -- do not create a dependency loop.
"""


def _actions_list_text() -> str:
    lines = []
    for name, action in ACTIONS.items():
        lines.append(f"- {name} (shape={action.shape}, write={action.is_write})")
    return "\n".join(lines)


def resolve_kwargs(task_id: str, action_name: str, dag: TaskDAG) -> dict:
    """
    Supplies the arguments each domain action needs for the BH202 demo.
    Read actions are fully runnable. Write actions that require MCP Context
    (assign_reserve_crew, issue_compensation) still need a real ctx and
    will fail gracefully until the planner runs inside an MCP session.
    """
    if action_name == "get_flight_status":
        return {"flight_number": "BH202"}

    if action_name == "get_affected_bookings":
        return {"flight_number": "BH202"}

    if action_name == "get_candidate_replacement_flights":
        return {"origin": "CAI", "destination": "LHR"}

    if action_name == "rebook_passenger":
        return {
            "booking_id": 2,
            "new_flight_number": "BH101",
            "requested_by": "agent_014",
        }

    if action_name == "assign_reserve_crew":
        return {
            "flight_number": "BH202",
            "crew_id": 1,
            "requested_by": "agent_014",
            # ctx is required by the real tool; omitted here on purpose
        }

    if action_name == "issue_compensation":
        return {
            "passenger_email": "mona.khaled@example.com",
            "flight_number": "BH202",
            "amount": 150.0,
            "currency": "USD",
            "reason": "flight delayed due to mechanical issue",
            "issued_by": "agent_007",
            # ctx is required by the real tool; omitted here on purpose
        }

    return {}


def build_plan(request_description: str) -> tuple[TaskDAG, dict]:
    """
    Single LLM call: ask for the full sub-task list + dependencies up
    front, then materialize it into a TaskDAG. Returns (dag, llm_call_stats)
    so callers (planning_eval/run_eval.py) can total up cost without
    re-deriving it from logs.
    """
    prompt = DECOMPOSITION_SYSTEM_PROMPT.format(actions_list=_actions_list_text())
    result = call_llm_json(system_prompt=prompt, user_prompt=request_description)

    if result["parsed"] is None:
        raise ValueError(f"Decomposition LLM call did not return valid JSON: {result['parse_error']}")

    dag = TaskDAG(request_description)
    tasks = result["parsed"]["tasks"]

    # First pass: create every node before any edges, since add_edge
    # requires both endpoints to already exist.
    for t in tasks:
        if t["action"] not in ACTIONS:
            raise ValueError(f"LLM proposed an unknown action '{t['action']}' for task '{t['id']}'")
        dag.add_node(t["id"], t["description"], assigned_action=t["action"])

    # Second pass: wire dependencies. Any cycle raises CycleError here --
    # a plan that can deadlock is rejected before a single sub-task runs.
    for t in tasks:
        for dep_id in t.get("depends_on", []):
            dag.add_edge(dep_id, t["id"])

    stats = {
        "llm_calls": 1,
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "latency_seconds": result["latency_seconds"],
    }
    return dag, stats


def execute_plan(dag: TaskDAG, action_kwargs: dict[str, dict] | None = None) -> dict:
    """
    Executes every node in topological order, blindly -- this is exactly
    the property decomposition-first is being measured against: a
    sub-task's real-world result is recorded but NEVER changes what the
    next node in the (already-fixed) order is or does.

    action_kwargs: optional override map {task_id: {kwarg: value}}.
    If omitted, resolve_kwargs() is used for every node.
    """
    order = dag.topological_order()
    execution_log = []

    for task_id in order:
        node = dag.nodes[task_id]
        node.status = "running"

        if action_kwargs is not None and task_id in action_kwargs:
            kwargs = action_kwargs[task_id]
        else:
            kwargs = resolve_kwargs(task_id, node.assigned_action, dag)

        try:
            result = run_action(node.assigned_action, **kwargs)
            node.result = result
            node.status = "done"
            execution_log.append({"task_id": task_id, "status": "done", "result": str(result)[:300]})
        except Exception as e:
            node.result = str(e)
            node.status = "failed"
            execution_log.append({"task_id": task_id, "status": "failed", "error": str(e)})

    return {
        "order": order,
        "execution_log": execution_log,
        "dag_snapshot": dag.snapshot(),
    }


if __name__ == "__main__":
    request = (
        "Flight BH202 is disrupted due to a mechanical issue. Find every affected "
        "passenger, check whether reserve crew is needed, and draft the passenger notice."
    )

    dag, stats = build_plan(request)
    print("=== Decomposition-first plan ===")
    print(f"LLM calls: {stats['llm_calls']}, tokens: in={stats['input_tokens']} "
          f"out={stats['output_tokens']}, latency={stats['latency_seconds']:.2f}s\n")

    for task_id in dag.topological_order():
        node = dag.nodes[task_id]
        deps = sorted(dag.edges[task_id])
        print(f"  [{task_id}] action={node.assigned_action} depends_on={deps}")
        print(f"      {node.description}")

    print("\n=== Executing plan against real domain actions ===")
    outcome = execute_plan(dag)
    for entry in outcome["execution_log"]:
        print(f"  {entry}")