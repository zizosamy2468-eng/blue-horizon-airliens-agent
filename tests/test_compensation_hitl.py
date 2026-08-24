"""
HITL path test for Compensation Appeal.

Demonstrates:
  1) amount above auto-approve cap -> real admin_task created
  2) graph pauses on WAITING_ADMIN
  3) admin decision injected through external_events
  4) graph resumes and continues from the checkpoint

Usage:
  python tests/test_compensation_hitl.py create
  python tests/test_compensation_hitl.py resume <RUN_ID> approved
  python tests/test_compensation_hitl.py resume <RUN_ID> rejected
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from state_graph.checkpoint_store import get_checkpoint_count, load_run_state
from state_graph.compensation.graph import (
    create_compensation_appeal_state,
    build_compensation_appeal_runner,
    resume_compensation_appeal,
)
from state_graph.external_events import inject_admin_decision, inject_customer_documents
from state_graph.models import RunStatus
from state_graph.hitl import get_admin_task


def create_hitl_waiting_run() -> None:
    """
    Drive the graph until it pauses on HITL (amount > 500).

    Uses a high requested_amount so constrained_action MUST open an admin task.
    We inject valid documents first so the run reaches constrained_action.
    """
    state = create_compensation_appeal_state(
        flight_number="BH202",
        passenger_email="mona.khaled@example.com",
        appeal_reason="Original compensation did not reflect the delay impact.",
        requested_amount=750.0,  # above COMPENSATION_APPEAL_AUTO_APPROVE_CAP (500)
        currency="USD",
        requested_by="agent_014",
        loyalty_tier="none",
    )

    runner = build_compensation_appeal_runner()
    state = runner.start(state)

    # Should stop first at await_customer_documents (WAITING_EXTERNAL)
    assert state.status == RunStatus.WAITING_EXTERNAL, (
        f"Expected WAITING_EXTERNAL for documents, got {state.status.value}"
    )
    assert state.current_node == "await_customer_documents"
    print("PAUSED_FOR_DOCUMENTS")
    print(f"RUN_ID={state.run_id}")
    print(f"CHECKPOINTS={get_checkpoint_count(state.run_id)}")

    # Inject valid documents so the graph continues to constrained_action
    state = inject_customer_documents(
        run_id=state.run_id,
        documents={"reference": "DOC-HITL-001", "file_type": "pdf"},
    )

    # Now it must pause on HITL
    assert state.status == RunStatus.WAITING_ADMIN, (
        f"Expected WAITING_ADMIN after over-cap amount, got {state.status.value}"
    )
    assert state.current_node == "awaiting_admin_approval"
    assert state.waiting_for == "admin_compensation_approval"
    assert state.admin_task_id or state.data.get("compensation_approval_task_id")

    task_id = state.admin_task_id or state.data["compensation_approval_task_id"]
    task = get_admin_task(task_id)
    assert task["status"] == "pending"
    assert task["task_type"] == "compensation_appeal_amount_approval"

    print()
    print("HITL_PAUSE_REACHED")
    print(f"RUN_ID={state.run_id}")
    print(f"STATUS={state.status.value}")
    print(f"CURRENT_NODE={state.current_node}")
    print(f"ADMIN_TASK_ID={task_id}")
    print(f"CHECKPOINTS={get_checkpoint_count(state.run_id)}")
    print()
    print("Now resolve HITL from a fresh process:")
    print(f"  python tests/test_compensation_hitl.py resume {state.run_id} approved")
    print(f"  python tests/test_compensation_hitl.py resume {state.run_id} rejected")


def resume_hitl(run_id: str, decision: str) -> None:
    """
    Simulate admin action from a new process (crash-and-resume style).
    """
    before = load_run_state(run_id)
    assert before.status == RunStatus.WAITING_ADMIN
    assert before.current_node == "awaiting_admin_approval"

    print("STATE_LOADED_AFTER_RESTART")
    print(f"RUN_ID={before.run_id}")
    print(f"STATUS={before.status.value}")
    print(f"CHECKPOINTS_BEFORE={get_checkpoint_count(run_id)}")

    state = inject_admin_decision(
        run_id=run_id,
        decision=decision,
        decided_by="ops_manager_001",
        decision_comment=f"HITL test decision: {decision}",
    )

    print()
    print("HITL_RESUME_RESULT")
    print(f"STATUS_AFTER={state.status.value}")
    print(f"CURRENT_NODE_AFTER={state.current_node}")
    print(f"CHECKPOINTS_AFTER={get_checkpoint_count(run_id)}")

    if decision == "approved":
        # After approval the graph submits payment and waits for payment_result
        assert state.status == RunStatus.WAITING_EXTERNAL
        assert state.current_node == "await_payment_result"
        assert state.waiting_for == "payment_result"
        print("HITL_APPROVED_PATH_OK — waiting for payment_result")
    else:
        # Rejection loops back (or completes if max revisions hit)
        assert state.status in {
            RunStatus.RUNNING,
            RunStatus.WAITING_EXTERNAL,
            RunStatus.COMPLETED,
            RunStatus.WAITING_ADMIN,
        }
        print("HITL_REJECTED_PATH_OK — revised appeal or closed")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage:\n"
            "  python tests/test_compensation_hitl.py create\n"
            "  python tests/test_compensation_hitl.py resume <RUN_ID> approved|rejected"
        )

    command = sys.argv[1]

    if command == "create":
        create_hitl_waiting_run()
        return

    if command == "resume":
        if len(sys.argv) != 4:
            raise SystemExit(
                "Usage: python tests/test_compensation_hitl.py resume <RUN_ID> approved|rejected"
            )
        run_id = sys.argv[2]
        decision = sys.argv[3]
        if decision not in {"approved", "rejected"}:
            raise SystemExit("decision must be approved or rejected")
        resume_hitl(run_id, decision)
        return

    raise SystemExit("First argument must be 'create' or 'resume'.")


if __name__ == "__main__":
    main()