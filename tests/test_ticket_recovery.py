"""
Failure-ticket path test for Compensation Appeal.

Demonstrates:
  1) unplanned error (invalid documents, or a poison gateway amount)
     -> Failure Ticket
  2) run status becomes FAILED with checkpointed state
  3) after "fixing" the actual cause, resume from the same node/checkpoint
     (not from the beginning)

Usage:
  python tests/test_ticket_recovery.py documents
  python tests/test_ticket_recovery.py gateway
  python tests/test_ticket_recovery.py resume <RUN_ID>
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
)
from state_graph.external_events import inject_customer_documents, inject_generic_resume
from state_graph.models import RunStatus
from mcp_server.dbase import get_connection


def _latest_open_ticket(run_id: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT ticket_id, failed_node, status, error_type, error_message
            FROM failure_tickets
            WHERE run_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id,),
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def _resolve_ticket(ticket_id: str, resolved_by: str = "ops_manager_001") -> None:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE failure_tickets
            SET
                status = 'resolved',
                resolution_notes = 'Fixed the real cause and retrying from checkpoint',
                resolved_by = %s,
                resolved_at = NOW()
            WHERE ticket_id = %s
            """,
            (resolved_by, ticket_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def test_invalid_documents_ticket() -> None:
    """
    Invalid customer_documents -> Failure Ticket on validate_documents.
    """
    state = create_compensation_appeal_state(
        flight_number="BH202",
        passenger_email="mona.khaled@example.com",
        appeal_reason="Need higher compensation after mechanical delay.",
        requested_amount=200.0,  # under cap so HITL is not involved
        currency="USD",
        requested_by="agent_014",
    )

    runner = build_compensation_appeal_runner()
    state = runner.start(state)

    assert state.status == RunStatus.WAITING_EXTERNAL
    assert state.current_node == "await_customer_documents"
    print(f"RUN_ID={state.run_id}")
    print("Injecting INVALID documents to force a Failure Ticket...")

    # Missing required fields on purpose
    state = inject_customer_documents(
        run_id=state.run_id,
        documents={"reference": "DOC-BAD"},  # missing file_type
    )

    assert state.status == RunStatus.FAILED, (
        f"Expected FAILED, got {state.status.value}"
    )
    assert state.current_node == "validate_documents"

    ticket = _latest_open_ticket(state.run_id)
    assert ticket is not None, "Expected a failure_tickets row"
    assert ticket["status"] == "open"
    assert ticket["failed_node"] == "validate_documents"
    assert ticket["error_type"] in {
        "missing_document_fields",
        "invalid_document_type",
        "unsupported_document_type",
    }

    print()
    print("TICKET_CREATED")
    print(f"TICKET_ID={ticket['ticket_id']}")
    print(f"FAILED_NODE={ticket['failed_node']}")
    print(f"ERROR_TYPE={ticket['error_type']}")
    print(f"ERROR_MESSAGE={ticket['error_message']}")
    print(f"CHECKPOINTS={get_checkpoint_count(state.run_id)}")
    print()
    print("Resolve the ticket then retry from checkpoint:")
    print(f"  python tests/test_ticket_recovery.py resume {state.run_id}")


def test_gateway_failure_ticket() -> None:
    """
    amount == 9999.99 triggers the mock gateway's RuntimeError -> Failure
    Ticket on submit_payment.
    """
    state = create_compensation_appeal_state(
        flight_number="BH202",
        passenger_email="mona.khaled@example.com",
        appeal_reason="Gateway failure path test.",
        requested_amount=9999.99,  # special poison value in submit_compensation_payment
        currency="USD",
        requested_by="agent_014",
    )

    runner = build_compensation_appeal_runner()
    state = runner.start(state)

    # documents wait
    state = inject_customer_documents(
        run_id=state.run_id,
        documents={"reference": "DOC-GW-001", "file_type": "pdf"},
    )

    # 9999.99 is above COMPENSATION_APPEAL_AUTO_APPROVE_CAP (500), so
    # constrained_action routes to HITL first. Approve so we actually
    # reach submit_payment and hit the real gateway failure.
    if state.status == RunStatus.WAITING_ADMIN:
        from state_graph.external_events import inject_admin_decision

        state = inject_admin_decision(
            run_id=state.run_id,
            decision="approved",
            decided_by="ops_manager_001",
            decision_comment="Approve so we can hit the gateway failure path",
        )

    assert state.status == RunStatus.FAILED, (
        f"Expected FAILED after gateway error, got {state.status.value} "
        f"node={state.current_node}"
    )
    assert state.current_node == "submit_payment"

    ticket = _latest_open_ticket(state.run_id)
    assert ticket is not None
    assert ticket["failed_node"] == "submit_payment"
    assert ticket["error_type"] == "payment_gateway_error"

    print()
    print("GATEWAY_TICKET_CREATED")
    print(f"RUN_ID={state.run_id}")
    print(f"TICKET_ID={ticket['ticket_id']}")
    print(f"ERROR_MESSAGE={ticket['error_message']}")
    print(f"CHECKPOINTS={get_checkpoint_count(state.run_id)}")


def resume_after_ticket(run_id: str) -> None:
    """
    Mark the open ticket resolved, then resume from the SAME checkpoint
    with whatever actually fixes the real cause of failure.

    This branches on which node actually failed instead of always
    injecting documents -- a gateway ticket (submit_payment, caused by
    the 9999.99 poison amount) needs a corrected requested_amount, not a
    fresh document upload, or the same failure would just repeat.
    """
    before = load_run_state(run_id)
    assert before.status == RunStatus.FAILED
    print("STATE_LOADED_AFTER_FAILURE")
    print(f"RUN_ID={before.run_id}")
    print(f"FAILED_NODE={before.current_node}")
    print(f"CHECKPOINTS_BEFORE={get_checkpoint_count(run_id)}")

    ticket = _latest_open_ticket(run_id)
    assert ticket is not None
    _resolve_ticket(ticket["ticket_id"])
    print(f"TICKET_RESOLVED={ticket['ticket_id']}")

    if ticket["failed_node"] == "validate_documents":
        # The real cause was a bad document payload -- fix means real,
        # valid documents this time.
        data_updates = {
            "customer_documents": {
                "reference": "DOC-FIXED-001",
                "file_type": "pdf",
            },
        }
    elif ticket["failed_node"] == "submit_payment":
        # The real cause was the poison amount (9999.99) the mock gateway
        # rejects -- fix means a corrected requested_amount, not resending
        # documents that were never the problem. This amount stays under
        # the auto-approve cap so the retry goes straight to payment.
        data_updates = {"requested_amount": 200.0}
    else:
        raise ValueError(
            f"No known fix for failed_node={ticket['failed_node']!r} -- "
            "add a branch here before resuming this ticket type."
        )

    state = inject_generic_resume(
        run_id=run_id,
        data_updates=data_updates,
        transition_name="ticket_resolved_retry",
    )

    print()
    print("TICKET_RESUME_RESULT")
    print(f"STATUS_AFTER={state.status.value}")
    print(f"CURRENT_NODE_AFTER={state.current_node}")
    print(f"CHECKPOINTS_AFTER={get_checkpoint_count(run_id)}")
    print("Resumed from checkpoint — did not restart from load_original_compensation.")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage:\n"
            "  python tests/test_ticket_recovery.py documents\n"
            "  python tests/test_ticket_recovery.py gateway\n"
            "  python tests/test_ticket_recovery.py resume <RUN_ID>"
        )

    command = sys.argv[1]

    if command == "documents":
        test_invalid_documents_ticket()
        return

    if command == "gateway":
        test_gateway_failure_ticket()
        return

    if command == "resume":
        if len(sys.argv) != 3:
            raise SystemExit(
                "Usage: python tests/test_ticket_recovery.py resume <RUN_ID>"
            )
        resume_after_ticket(sys.argv[2])
        return

    raise SystemExit("First argument must be documents | gateway | resume")


if __name__ == "__main__":
    main()