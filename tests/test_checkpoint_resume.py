"""
Manual two-process test for checkpoint persistence.

Run 1:
python tests/test_checkpoint_resume.py create

Run 2, after stopping the first process:
python tests/test_checkpoint_resume.py resume <RUN_ID>
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from state_graph.checkpoint_store import get_checkpoint_count, load_run_state
from state_graph.maintenance.graph import (
    build_maintenance_release_runner,
    create_maintenance_release_state,
    resume_maintenance_release,
)
from state_graph.models import RunStatus


def create_waiting_workflow() -> None:
    """
    Create and persist a Maintenance workflow at its real external wait node.
    """
    state = create_maintenance_release_state(
        flight_number="BH202",
        requested_by="agent_014",
    )

    # For this persistence test, we begin at the external wait node.
    # The full end-to-end demo will reach this node through RAG and
    # task decomposition first.
    state.current_node = "awaiting_maintenance_report"
    state.data["flight_status"] = "disrupted"
    state.data["maintenance_required"] = True

    runner = build_maintenance_release_runner()
    state = runner.start(state)

    assert state.status == RunStatus.WAITING_EXTERNAL
    assert state.current_node == "awaiting_maintenance_report"
    assert state.waiting_for == "maintenance_report"

    print("WORKFLOW_CREATED")
    print(f"RUN_ID={state.run_id}")
    print(f"STATUS={state.status.value}")
    print(f"CURRENT_NODE={state.current_node}")
    print(f"CHECKPOINTS={get_checkpoint_count(state.run_id)}")
    print()
    print("Stop this process now. Then run this command in a new process:")
    print(f"python tests/test_checkpoint_resume.py resume {state.run_id}")


def resume_after_restart(run_id: str) -> None:
    """
    Simulate a fresh process reading the run from MySQL and resuming it.
    """
    before_resume = load_run_state(run_id)

    assert before_resume.status == RunStatus.WAITING_EXTERNAL
    assert before_resume.current_node == "awaiting_maintenance_report"

    print("STATE_LOADED_AFTER_RESTART")
    print(f"RUN_ID={before_resume.run_id}")
    print(f"STATUS={before_resume.status.value}")
    print(f"CURRENT_NODE={before_resume.current_node}")
    print(f"CHECKPOINTS_BEFORE={get_checkpoint_count(run_id)}")

    maintenance_report = {
        "reference": "MX-2026-100",
        "clearance": "cleared",
        "summary": "Inspection completed successfully.",
    }

    resumed_state = resume_maintenance_release(
        run_id=run_id,
        data_updates={
            "maintenance_report": maintenance_report,
        },
        transition_name="maintenance_report_received",
    )

    assert resumed_state.status == RunStatus.WAITING_ADMIN
    assert resumed_state.current_node == "requires_operations_approval"
    assert resumed_state.waiting_for == "operations_manager_approval"
    assert resumed_state.data["maintenance_report_validated"] is True

    print()
    print("CHECKPOINT_RESUME_PASSED")
    print(f"STATUS_AFTER_RESUME={resumed_state.status.value}")
    print(f"CURRENT_NODE_AFTER_RESUME={resumed_state.current_node}")
    print(f"CHECKPOINTS_AFTER={get_checkpoint_count(run_id)}")
    print("The workflow resumed from the saved waiting node.")
    print("It did not restart from inspect_flight.")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage:\n"
            "  python tests/test_checkpoint_resume.py create\n"
            "  python tests/test_checkpoint_resume.py resume <RUN_ID>"
        )

    command = sys.argv[1]

    if command == "create":
        create_waiting_workflow()
        return

    if command == "resume":
        if len(sys.argv) != 3:
            raise SystemExit(
                "Usage: python tests/test_checkpoint_resume.py resume <RUN_ID>"
            )

        resume_after_restart(sys.argv[2])
        return

    raise SystemExit(
        "First argument must be either 'create' or 'resume'."
    )


if __name__ == "__main__":
    main()