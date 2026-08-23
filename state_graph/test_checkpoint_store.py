"""
Manual smoke test for workflow persistence.

Run from the project root:
python -m state_graph.test_checkpoint_store
"""

from state_graph.checkpoint_store import (
    create_run,
    get_checkpoint_count,
    load_run_state,
    save_checkpoint,
)
from state_graph.models import RunStatus, WorkflowState


def main() -> None:
    print("1. Creating a new workflow state...")

    state = WorkflowState.create(
        workflow_type="maintenance_release",
        initial_node="inspect_flight",
        flight_number="BH202",
        data={
            "requested_by": "agent_014",
            "maintenance_report_received": False,
        },
        context={
            "purpose": "Verify durable checkpoint persistence.",
        },
    )

    create_run(state)

    print(f"   Run created: {state.run_id}")
    print(f"   Checkpoints after creation: {get_checkpoint_count(state.run_id)}")

    assert get_checkpoint_count(state.run_id) == 1

    print("2. Moving the workflow to a waiting state...")

    state.data["flight_status_checked"] = True
    state.waiting_for = "maintenance_report"
    state.move_to(
        next_node="awaiting_maintenance_report",
        transition_name="flight_inspected",
        status=RunStatus.WAITING_EXTERNAL,
    )

    # move_to clears waiting_for because the workflow changed node.
    # We set it again to describe exactly what external event is awaited.
    state.waiting_for = "maintenance_report"

    save_checkpoint(state, "flight_inspected")

    print(f"   Checkpoints after transition: {get_checkpoint_count(state.run_id)}")

    assert get_checkpoint_count(state.run_id) == 2

    print("3. Loading the latest state from MySQL...")

    restored_state = load_run_state(state.run_id)

    assert restored_state.run_id == state.run_id
    assert restored_state.current_node == "awaiting_maintenance_report"
    assert restored_state.status == RunStatus.WAITING_EXTERNAL
    assert restored_state.waiting_for == "maintenance_report"
    assert restored_state.data["flight_status_checked"] is True
    assert restored_state.checkpoint_number == 2

    print("4. Persistence test passed.")
    print(f"   Restored node: {restored_state.current_node}")
    print(f"   Restored status: {restored_state.status.value}")
    print(f"   Waiting for: {restored_state.waiting_for}")
    print(f"   Final checkpoint number: {restored_state.checkpoint_number}")


if __name__ == "__main__":
    main()