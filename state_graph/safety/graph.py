"""
Safety Incident Agent state graph.

Wires all safety nodes into the shared StateGraphRunner.
Owned by: Adel
"""

from __future__ import annotations

from typing import Any

from state_graph.models import WorkflowState
from state_graph.runner import StateGraphRunner
from state_graph.safety.nodes import (
    awaiting_authority_acknowledgement,
    awaiting_ground_or_crew_report,
    collect_flight_and_crew_facts,
    draft_regulatory_report,
    explore_reporting_paths,
    retrieve_safety_policy,
    revise_report,
    safety_manager_review,
    submit_report,
    validate_evidence,
)

WORKFLOW_TYPE = "safety_incident"


def build_safety_incident_runner() -> StateGraphRunner:
    """Return a runner configured with every Safety Incident node."""
    return StateGraphRunner(
        handlers={
            "collect_flight_and_crew_facts": collect_flight_and_crew_facts,
            "retrieve_safety_policy": retrieve_safety_policy,
            "explore_reporting_paths": explore_reporting_paths,
            "awaiting_ground_or_crew_report": awaiting_ground_or_crew_report,
            "validate_evidence": validate_evidence,
            "draft_regulatory_report": draft_regulatory_report,
            "safety_manager_review": safety_manager_review,
            "revise_report": revise_report,
            "submit_report": submit_report,
            "awaiting_authority_acknowledgement": awaiting_authority_acknowledgement,
            # Terminal alias — runner stops on COMPLETED status.
            "completed": lambda state: __import__(
                "state_graph.models", fromlist=["NodeResult", "RunStatus"]
            ).NodeResult(
                next_node="completed",
                transition_name="already_completed",
                status=__import__(
                    "state_graph.models", fromlist=["RunStatus"]
                ).RunStatus.COMPLETED,
            ),
        }
    )


def create_safety_incident_state(
    flight_number: str,
    incident_type: str,
    severity: str = "medium",
    description: str = "",
    requested_by: str = "agent_safety",
    has_passenger_impact: bool = False,
    crew_facts: dict[str, Any] | None = None,
) -> WorkflowState:
    """Create the first state for a new safety-incident workflow."""
    return WorkflowState.create(
        workflow_type=WORKFLOW_TYPE,
        initial_node="collect_flight_and_crew_facts",
        flight_number=flight_number,
        data={
            "incident_type": incident_type,
            "severity": severity,
            "description": description,
            "requested_by": requested_by,
            "has_passenger_impact": has_passenger_impact,
            "crew_facts": crew_facts or {},
            "report_revision": 0,
        },
        context={
            "agent_name": WORKFLOW_TYPE,
            "purpose": (
                "Investigate safety incidents, retrieve policy via RAG, "
                "explore reporting paths with LATS, draft regulatory reports, "
                "and pause for Safety Manager HITL approval."
            ),
        },
    )


def start_safety_incident(
    flight_number: str,
    incident_type: str,
    severity: str = "medium",
    description: str = "",
    requested_by: str = "agent_safety",
    has_passenger_impact: bool = False,
    crew_facts: dict[str, Any] | None = None,
) -> WorkflowState:
    """
    Create, persist, and run a new safety-incident workflow.

    Stops automatically at:
      - WAITING_EXTERNAL (ground/crew report or authority ack)
      - WAITING_ADMIN   (Safety Manager HITL)
      - FAILED          (insufficient evidence / authority rejection)
      - COMPLETED       (authority acknowledged)
    """
    runner = build_safety_incident_runner()
    state = create_safety_incident_state(
        flight_number=flight_number,
        incident_type=incident_type,
        severity=severity,
        description=description,
        requested_by=requested_by,
        has_passenger_impact=has_passenger_impact,
        crew_facts=crew_facts,
    )
    return runner.start(state)


def resume_safety_incident(
    run_id: str,
    data_updates: dict[str, Any],
    transition_name: str = "workflow_resumed",
) -> WorkflowState:
    """
    Resume an existing safety incident after an external event or admin decision.

    Typical data_updates:
      - {"ground_report": {...}, "crew_report": {...}, "crew_report_received": True}
      - {"admin_decision": "approved"}  or  {"admin_decision": "changes_requested", "admin_comment": "..."}
      - {"authority_ack": {"status": "acknowledged", "ref": "NAA-2026-..."}}
    """
    runner = build_safety_incident_runner()
    return runner.resume(
        run_id=run_id,
        data_updates=data_updates,
        transition_name=transition_name,
    )
