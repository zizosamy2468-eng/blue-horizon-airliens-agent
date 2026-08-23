"""
Maintenance Release Coordinator state graph.

This file wires all maintenance nodes into the shared StateGraphRunner.
"""

from __future__ import annotations

from typing import Any

from state_graph.maintenance.nodes import (
    awaiting_maintenance_report,
    build_release_plan,
    inspect_flight,
    mark_flight_ready,
    requires_operations_approval,
    retrieve_maintenance_policy,
    validate_maintenance_report,
)
from state_graph.models import WorkflowState
from state_graph.runner import StateGraphRunner


WORKFLOW_TYPE = "maintenance_release"


def build_maintenance_release_runner() -> StateGraphRunner:
    """
    Return a runner configured with every Maintenance Release node.
    """
    return StateGraphRunner(
        handlers={
            "inspect_flight": inspect_flight,
            "retrieve_maintenance_policy": retrieve_maintenance_policy,
            "build_release_plan": build_release_plan,
            "awaiting_maintenance_report": awaiting_maintenance_report,
            "validate_maintenance_report": validate_maintenance_report,
            "requires_operations_approval": requires_operations_approval,
            "mark_flight_ready": mark_flight_ready,
        }
    )


def create_maintenance_release_state(
    flight_number: str,
    requested_by: str,
) -> WorkflowState:
    """
    Create the first state for a new maintenance-release workflow.
    """
    return WorkflowState.create(
        workflow_type=WORKFLOW_TYPE,
        initial_node="inspect_flight",
        flight_number=flight_number,
        data={
            "requested_by": requested_by,
        },
        context={
            "agent_name": WORKFLOW_TYPE,
            "purpose": "Coordinate maintenance clearance and operations release.",
        },
    )


def start_maintenance_release(
    flight_number: str,
    requested_by: str,
) -> WorkflowState:
    """
    Create, persist, and run a new maintenance-release workflow.

    It will stop automatically at an external wait, an admin decision,
    or completion.
    """
    runner = build_maintenance_release_runner()

    state = create_maintenance_release_state(
        flight_number=flight_number,
        requested_by=requested_by,
    )

    return runner.start(state)


def resume_maintenance_release(
    run_id: str,
    data_updates: dict[str, Any],
    transition_name: str,
) -> WorkflowState:
    """
    Resume an existing workflow after an external report or admin decision.
    """
    runner = build_maintenance_release_runner()

    return runner.resume(
        run_id=run_id,
        data_updates=data_updates,
        transition_name=transition_name,
    )