# planning/dynamic_decomposition.py
#
# DYNAMIC / INTERLEAVED DECOMPOSITION concern.
#
# Adapted from the reference toolkit's algorithms/dynamic_decomposition.py:
# unlike decomposition.py (whole plan up front), this generates ONE
# sub-task at a time, runs it against the real domain action, feeds the
# REAL observation back to the LLM, and only then asks "what's next".
# This is what lets an early surprise reshape the rest of the plan --
# e.g. if get_affected_bookings comes back with zero passengers, there is
# nothing to rebook or compensate and the plan should stop there instead
# of decomposition-first's fixed node list blindly running rebooking
# logic against an empty list.
#
# Same DAG machinery as decomposition.py (dag.py's TaskDAG, same cycle
# guard) -- the only difference is WHEN nodes get added: incrementally,
# with edges only from already-completed nodes, instead of all at once.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dag import CycleError, TaskDAG            # noqa: E402
from domain_actions import ACTIONS, run_action  # noqa: E402
from llm_client import call_llm_json            # noqa: E402

NEXT_STEP_SYSTEM_PROMPT = """You are the planning module for Blue Horizon Airlines' \
IROPS disruption-response agent, working step by step. You are given the original \
request, the sub-tasks already completed with their REAL results, and must decide the \
SINGLE next sub-task -- or declare the request already resolved.

Available actions:
{actions_list}

Respond ONLY with JSON in this exact shape:
{{
  "done": false,
  "next_task": {{"id": "short_snake_case_id", "description": "...", "action": "one_of_the_action_names"}}
}}
or, if no further sub-task is needed:
{{
  "done": true,
  "reason": "..."
}}

Rules:
- Only propose an action from the list above, exactly as named.
- Base your decision on what has ACTUALLY happened so far, not what you'd expect to happen.
- If a prior result changes what's needed (e.g. no affected passengers, or reserve crew \
already within duty hours), reflect that -- do not propose a sub-task that no longer makes sense.
"""


def _actions_list_text() -> str:
    return "\n".join(f"- {name} (shape={a.shape}, write={a.is_write})" for name, a in ACTIONS.items())


def _format_history(dag: TaskDAG, order: list[str]) -> str:
    if not order:
        return "(no sub-tasks completed yet)"
    lines = []
    for task_id in order:
        node = dag.nodes[task_id]
        lines.append(f"- [{task_id}] {node.description} -> action={node.assigned_action} "
                      f"-> status={node.status} -> result={str(node.result)[:200]}")
    return "\n".join(lines)


def resolve_kwargs(task_id: str, action_name: str, dag: TaskDAG) -> dict:
    """
    Same resolver used by decomposition.py so both methods stay consistent
    on the BH202 demo request.
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
        }

    if action_name == "issue_compensation":
        return {
            "passenger_email": "mona.khaled@example.com",
            "flight_number": "BH202",
            "amount": 150.0,
            "currency": "USD",
            "reason": "flight delayed due to mechanical issue",
            "issued_by": "agent_007",
        }

    return {}


def run_dynamic_decomposition(
    request_description: str,
    action_kwargs_resolver=None,
    max_steps: int = 8,
) -> dict:
    """
    Runs the interleaved loop: propose one step -> execute it for real ->
    feed the real result back -> propose the next step, until the model
    says done or max_steps is hit (safety bound, not a fixed plan length).

    action_kwargs_resolver: callable(task_id, action_name, dag) -> dict of
    kwargs to call the action with. Defaults to resolve_kwargs if omitted.

    Returns the DAG, the completed order, a step-by-step trace (for the
    artifacts/ evidence log), and total LLM-call stats.
    """
    if action_kwargs_resolver is None:
        action_kwargs_resolver = resolve_kwargs

    dag = TaskDAG(request_description)
    completed_order: list[str] = []
    trace = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency = 0.0
    llm_calls = 0

    for step in range(max_steps):
        prompt = NEXT_STEP_SYSTEM_PROMPT.format(actions_list=_actions_list_text())
        user_prompt = (
            f"Original request: {request_description}\n\n"
            f"Sub-tasks completed so far:\n{_format_history(dag, completed_order)}"
        )
        result = call_llm_json(system_prompt=prompt, user_prompt=user_prompt)
        llm_calls += 1
        total_input_tokens += result["input_tokens"]
        total_output_tokens += result["output_tokens"]
        total_latency += result["latency_seconds"]

        if result["parsed"] is None:
            trace.append({"step": step, "error": f"invalid JSON: {result['parse_error']}"})
            break

        decision = result["parsed"]
        if decision.get("done"):
            trace.append({"step": step, "done": True, "reason": decision.get("reason", "")})
            break

        next_task = decision["next_task"]
        if next_task["action"] not in ACTIONS:
            trace.append({"step": step, "error": f"unknown action '{next_task['action']}'"})
            break

        # New node depends on every already-completed node -- dynamic
        # decomposition doesn't know the full dependency shape up front,
        # so it conservatively chains onto whatever came immediately
        # before it. add_edge's cycle guard still applies here exactly
        # like it does in decomposition-first.
        dag.add_node(next_task["id"], next_task["description"], assigned_action=next_task["action"])
        if completed_order:
            dag.add_edge(completed_order[-1], next_task["id"])

        kwargs = action_kwargs_resolver(next_task["id"], next_task["action"], dag)
        node = dag.nodes[next_task["id"]]
        node.status = "running"

        try:
            real_result = run_action(next_task["action"], **kwargs)
            node.result = real_result
            node.status = "done"
            trace.append({
                "step": step, "task_id": next_task["id"], "action": next_task["action"],
                "status": "done", "result": str(real_result)[:300],
            })
        except Exception as e:
            node.result = str(e)
            node.status = "failed"
            trace.append({
                "step": step, "task_id": next_task["id"], "action": next_task["action"],
                "status": "failed", "error": str(e),
            })

        completed_order.append(next_task["id"])

    return {
        "dag": dag,
        "completed_order": completed_order,
        "trace": trace,
        "llm_calls": llm_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "latency_seconds": total_latency,
    }


if __name__ == "__main__":
    # Same request type as decomposition.py's demo, so the two are
    # directly comparable in planning_eval/run_eval.py.
    request = (
        "Flight BH202 is disrupted due to a mechanical issue. Find every affected "
        "passenger, check whether reserve crew is needed, and draft the passenger notice."
    )

    outcome = run_dynamic_decomposition(request)

    print("=== Dynamic decomposition run ===")
    print(f"LLM calls: {outcome['llm_calls']}, tokens: in={outcome['input_tokens']} "
          f"out={outcome['output_tokens']}, latency={outcome['latency_seconds']:.2f}s\n")

    for entry in outcome["trace"]:
        print(f"  {entry}")

    print("\nFinal DAG order actually taken:", outcome["completed_order"])