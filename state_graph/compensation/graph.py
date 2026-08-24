# state_graph/compensation/graph.py
#
# Compensation Appeal state graph.
#
# Wires every node from nodes.py into the shared StateGraphRunner
# (the same runner Mostafa built for Maintenance). This file owns:
#   - the handler map
#   - create / start / resume entry points
# It does NOT own node logic, HITL, tickets, or the payment tool --
# those live in nodes.py, hitl.py, tickets.py, and tools_write.py.

from __future__ import annotations

from typing import Any

from state_graph.compensation.nodes import (
    await_customer_documents,
    await_payment_result,
    awaiting_admin_approval,
    compare_appeal_strategies,
    constrained_action,
    load_original_compensation,
    prepare_action,
    retrieve_compensation_policy,
    submit_payment,
    validate_documents,
)
from state_graph.models import WorkflowState
from state_graph.runner import StateGraphRunner


WORKFLOW_TYPE = "compensation_appeal"


def build_compensation_appeal_runner() -> StateGraphRunner:
    """
    Return a runner configured with every Compensation Appeal node.

    Node names here must match exactly what nodes.py returns in
    NodeResult.next_node -- a typo on either side deadlocks the run.
    """
    return StateGraphRunner(
        handlers={
            "load_original_compensation": load_original_compensation,
            "retrieve_compensation_policy": retrieve_compensation_policy,
            "compare_appeal_strategies": compare_appeal_strategies,
            "await_customer_documents": await_customer_documents,
            "validate_documents": validate_documents,
            "prepare_action": prepare_action,
            "constrained_action": constrained_action,
            "awaiting_admin_approval": awaiting_admin_approval,
            "submit_payment": submit_payment,
            "await_payment_result": await_payment_result,
        }
    )


def create_compensation_appeal_state(
    flight_number: str,
    passenger_email: str,
    appeal_reason: str,
    requested_amount: float,
    currency: str = "USD",
    requested_by: str = "agent_014",
    loyalty_tier: str = "unknown",
) -> WorkflowState:
    """
    Create the first durable state for a new compensation-appeal workflow.

    The actual compensation_appeals DB row is inserted later inside
    load_original_compensation (first node), once the original
    compensation record has been looked up -- same pattern as
    Maintenance creating its domain row after the first real read.
    """
    return WorkflowState.create(
        workflow_type=WORKFLOW_TYPE,
        initial_node="load_original_compensation",
        flight_number=flight_number,
        data={
            "passenger_email": passenger_email,
            "appeal_reason": appeal_reason,
            "requested_amount": requested_amount,
            "currency": currency,
            "requested_by": requested_by,
            "loyalty_tier": loyalty_tier,
            "revision_count": 0,
            "rejected_strategy_names": [],
        },
        context={
            "agent_name": WORKFLOW_TYPE,
            "purpose": (
                "Handle compensation appeals with Tree-of-Thoughts strategy "
                "selection, HITL for over-cap amounts, and failure tickets "
                "for unplanned gateway/document errors."
            ),
        },
    )


def start_compensation_appeal(
    flight_number: str,
    passenger_email: str,
    appeal_reason: str,
    requested_amount: float,
    currency: str = "USD",
    requested_by: str = "agent_014",
    loyalty_tier: str = "unknown",
) -> WorkflowState:
    """
    Create, persist, and run a new compensation-appeal workflow.

    Stops automatically at:
      - WAITING_EXTERNAL (customer documents or payment result)
      - WAITING_ADMIN   (over-cap HITL approval)
      - FAILED          (invalid documents / gateway error ticket)
      - COMPLETED       (paid, or closed after max revisions)
    """
    runner = build_compensation_appeal_runner()

    state = create_compensation_appeal_state(
        flight_number=flight_number,
        passenger_email=passenger_email,
        appeal_reason=appeal_reason,
        requested_amount=requested_amount,
        currency=currency,
        requested_by=requested_by,
        loyalty_tier=loyalty_tier,
    )

    return runner.start(state)


def resume_compensation_appeal(
    run_id: str,
    data_updates: dict[str, Any],
    transition_name: str,
) -> WorkflowState:
    """
    Resume an existing appeal after an external event or admin decision.

    Typical data_updates:
      - {"customer_documents": {"reference": "...", "file_type": "pdf"}}
      - {"admin_decision": "approved"}  or  {"admin_decision": "rejected"}
      - {"payment_result": "paid"}      or  {"payment_result": "rejected"}
    """
    runner = build_compensation_appeal_runner()

    return runner.resume(
        run_id=run_id,
        data_updates=data_updates,
        transition_name=transition_name,
    )