# state_graph/external_events.py
#
# External-event injection for Compensation Appeal (and any other graph
# that pauses on WAITING_EXTERNAL or WAITING_ADMIN).
#
# Real waits in this project are not sleep() calls and not auto-approvals.
# Something outside the model has to land real data in state.data, then
# the runner resumes from the saved checkpoint. This file is that bridge:
#   - customer documents  -> await_customer_documents
#   - admin decision      -> awaiting_admin_approval
#   - payment result      -> await_payment_result
#
# The platform / MCP resume tools call these helpers. They never invent
# events, never skip validation, and never change status without going
# through runner.resume() so a checkpoint is always written.

from __future__ import annotations

from typing import Any, Literal

from state_graph.compensation.graph import (
    build_compensation_appeal_runner,
    resume_compensation_appeal,
)
from state_graph.models import RunStatus, WorkflowState
from state_graph.checkpoint_store import load_run_state


AdminDecision = Literal["approved", "rejected"]
PaymentResult = Literal["paid", "rejected"]


def _assert_waiting(state: WorkflowState, expected_waiting_for: str) -> None:
    """
    Refuse to inject an event into a run that is not actually waiting
    for it. Silent injection into the wrong node would corrupt the graph.
    """
    if state.status not in {RunStatus.WAITING_EXTERNAL, RunStatus.WAITING_ADMIN}:
        raise ValueError(
            f"Run '{state.run_id}' has status '{state.status.value}' "
            f"(node={state.current_node}). Only waiting runs accept external events."
        )

    if state.waiting_for != expected_waiting_for:
        raise ValueError(
            f"Run '{state.run_id}' is waiting for '{state.waiting_for}', "
            f"not '{expected_waiting_for}'."
        )


def inject_customer_documents(
    run_id: str,
    documents: dict[str, Any],
) -> WorkflowState:
    """
    Deliver customer supporting documents to a run paused on
    await_customer_documents.

    documents must include at least:
      {
        "reference": "DOC-2026-001",
        "file_type": "pdf" | "image" | "receipt"
      }

    Validation of the payload shape happens inside validate_documents
    (the next node). This helper only checks that *something* was provided
    and that the run is actually waiting for documents.
    """
    if not isinstance(documents, dict) or not documents:
        raise ValueError(
            "documents must be a non-empty dict with at least "
            "'reference' and 'file_type'."
        )

    state = load_run_state(run_id)
    _assert_waiting(state, expected_waiting_for="customer_documents")

    return resume_compensation_appeal(
        run_id=run_id,
        data_updates={"customer_documents": documents},
        transition_name="customer_documents_received",
    )


def inject_admin_decision(
    run_id: str,
    decision: AdminDecision,
    decided_by: str,
    decision_comment: str = "",
) -> WorkflowState:
    """
    Deliver the admin's HITL decision to a run paused on
    awaiting_admin_approval.

    Also resolves the pending admin_tasks row so the platform and the
    graph stay in sync. The graph only continues after this call.
    """
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be 'approved' or 'rejected'.")

    if not decided_by or not decided_by.strip():
        raise ValueError("decided_by is required (admin identity).")

    state = load_run_state(run_id)
    _assert_waiting(state, expected_waiting_for="admin_compensation_approval")

    # Resolve the HITL task row if one is linked to this run.
    task_id = state.admin_task_id or state.data.get("compensation_approval_task_id")
    if task_id:
        from state_graph.hitl import resolve_admin_task

        resolve_admin_task(
            task_id=task_id,
            decision=decision,
            decided_by=decided_by,
            decision_comment=decision_comment,
            decision_payload={"decision": decision},
        )

    return resume_compensation_appeal(
        run_id=run_id,
        data_updates={
            "admin_decision": decision,
            "admin_decided_by": decided_by,
            "admin_decision_comment": decision_comment,
        },
        transition_name="admin_decision_received",
    )


def inject_payment_result(
    run_id: str,
    result: PaymentResult,
) -> WorkflowState:
    """
    Deliver the mock payment gateway's asynchronous result to a run
    paused on await_payment_result.

    'paid'     -> graph completes
    'rejected' -> graph records a revision and loops back to
                  compare_appeal_strategies (bounded by MAX_REVISION_ROUNDS)

    A gateway *infrastructure* failure is NOT delivered here -- that path
    raises inside submit_payment and becomes a Failure Ticket before the
    run ever reaches this wait.
    """
    if result not in {"paid", "rejected"}:
        raise ValueError("result must be 'paid' or 'rejected'.")

    state = load_run_state(run_id)
    _assert_waiting(state, expected_waiting_for="payment_result")

    return resume_compensation_appeal(
        run_id=run_id,
        data_updates={"payment_result": result},
        transition_name="payment_result_received",
    )


def inject_generic_resume(
    run_id: str,
    data_updates: dict[str, Any],
    transition_name: str,
) -> WorkflowState:
    """
    Escape hatch for the platform when the event type is already encoded
    in data_updates (e.g. after resolving a Failure Ticket and retrying
    the same node). Prefer the typed helpers above for normal paths.
    """
    if not data_updates:
        raise ValueError("data_updates must not be empty.")

    return resume_compensation_appeal(
        run_id=run_id,
        data_updates=data_updates,
        transition_name=transition_name,
    )