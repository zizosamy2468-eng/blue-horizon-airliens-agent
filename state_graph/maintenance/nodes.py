"""
Nodes for the Maintenance Release Coordinator workflow.
"""

from __future__ import annotations

import sys
from pathlib import Path

from state_graph.models import RunStatus, WorkflowState
from state_graph.runner import NodeResult


PROJECT_ROOT = Path(__file__).resolve().parents[2]

MCP_SERVER_DIR = PROJECT_ROOT / "mcp_server"
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

PLANNING_DIR = PROJECT_ROOT / "planning"
if str(PLANNING_DIR) not in sys.path:
    sys.path.insert(0, str(PLANNING_DIR))


ALLOWED_RELEASE_STEPS = {
    "verify_flight_state",
    "review_maintenance_policy",
    "await_maintenance_report",
    "validate_maintenance_report",
    "request_operations_approval",
    "release_aircraft",
}


def _get_flight_status_record_from_existing_mcp_tool(
    flight_number: str,
) -> dict:
    """
    Reuse the structured MCP/database read helper.
    """
    from tools_read import get_flight_status_record

    flight_record = get_flight_status_record(flight_number)

    if flight_record is None:
        raise LookupError(
            f"No flight found with number '{flight_number}'."
        )

    return flight_record


def inspect_flight(state: WorkflowState) -> NodeResult:
    """
    Read the real flight status and decide whether maintenance release is needed.
    """
    if not state.flight_number:
        raise ValueError(
            "Maintenance Release workflow requires a flight_number."
        )

    flight_record = _get_flight_status_record_from_existing_mcp_tool(
        state.flight_number
    )

    flight_status = flight_record["status"]

    state.data["flight_record"] = flight_record
    state.data["flight_status"] = flight_status
    state.data["disruption_reason"] = (
        flight_record["disruption_reason"] or "unknown"
    )

    maintenance_needed_statuses = {"disrupted", "delayed", "cancelled"}

    if flight_status not in maintenance_needed_statuses:
        state.data["maintenance_required"] = False
        state.data["completion_reason"] = (
            f"Flight status is '{flight_status}', so no maintenance release "
            "workflow is required."
        )

        return NodeResult(
            next_node="completed",
            transition_name="maintenance_not_required",
            status=RunStatus.COMPLETED,
        )

    state.data["maintenance_required"] = True

    return NodeResult(
        next_node="retrieve_maintenance_policy",
        transition_name="flight_inspected",
        status=RunStatus.RUNNING,
    )


def _search_policy_from_existing_mcp_tool(query: str) -> str:
    """
    Reuse the existing Memory/RAG MCP tool.
    """
    from memory_tools import search_policy_manual

    return search_policy_manual(query=query, category=None, top_k=3)


def retrieve_maintenance_policy(state: WorkflowState) -> NodeResult:
    """
    Retrieve policy evidence before creating the maintenance-release plan.
    """
    from state_graph.tool_registry import require_enabled_tool

    require_enabled_tool(
        agent_name="maintenance_release",
        tool_name="search_policy_manual",
    )

    query = (
        "Aircraft maintenance clearance requirements and operations release "
        "rules after a disrupted, delayed, or cancelled flight."
    )

    policy_result = _search_policy_from_existing_mcp_tool(query)

    state.data["maintenance_policy_query"] = query
    state.data["maintenance_policy_result"] = policy_result

    return NodeResult(
        next_node="build_release_plan",
        transition_name="maintenance_policy_retrieved",
        status=RunStatus.RUNNING,
    )


def build_release_plan(state: WorkflowState) -> NodeResult:
    """
    Use one constrained LLM call to decompose a maintenance-release case.
    """
    from llm_client import call_llm_json

    request_description = (
        f"Create a minimal maintenance-release plan for flight "
        f"{state.flight_number}. Current flight status is "
        f"'{state.data.get('flight_status', 'unknown')}'. "
        "The workflow must wait for a maintenance report and must require "
        "an operations-manager approval before aircraft release."
    )

    system_prompt = """
You are a flight-operations planning assistant.

Return JSON only, using this exact format:
{
  "steps": [
    "one_allowed_step_name"
  ],
  "reasoning": "short explanation"
}

Allowed steps:
- verify_flight_state
- review_maintenance_policy
- await_maintenance_report
- validate_maintenance_report
- request_operations_approval
- release_aircraft

Rules:
- Use only the allowed step names exactly as written.
- The plan must include await_maintenance_report.
- The plan must include validate_maintenance_report.
- The plan must include request_operations_approval.
- The plan must include release_aircraft.
- Never place release_aircraft before request_operations_approval.
"""

    result = call_llm_json(
        system_prompt=system_prompt,
        user_prompt=request_description,
    )

    parsed = result.get("parsed")

    if not isinstance(parsed, dict):
        raise ValueError(
            "Task decomposition returned invalid JSON. "
            f"Details: {result.get('parse_error')}"
        )

    steps = parsed.get("steps")

    if not isinstance(steps, list) or not steps:
        raise ValueError(
            "Task decomposition did not return a valid non-empty steps list."
        )

    unknown_steps = set(steps) - ALLOWED_RELEASE_STEPS

    if unknown_steps:
        raise ValueError(
            f"Task decomposition proposed forbidden steps: {sorted(unknown_steps)}"
        )

    required_steps = {
        "await_maintenance_report",
        "validate_maintenance_report",
        "request_operations_approval",
        "release_aircraft",
    }

    missing_steps = required_steps - set(steps)

    if missing_steps:
        raise ValueError(
            f"Task decomposition skipped required safety steps: "
            f"{sorted(missing_steps)}"
        )

    if steps.index("release_aircraft") < steps.index(
        "request_operations_approval"
    ):
        raise ValueError(
            "Invalid release plan: release_aircraft appears before "
            "request_operations_approval."
        )

    state.data["release_plan"] = {
        "steps": steps,
        "reasoning": parsed.get("reasoning", ""),
    }

    state.data["release_plan_llm_stats"] = {
        "llm_calls": 1,
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "latency_seconds": result.get("latency_seconds"),
    }

    return NodeResult(
        next_node="awaiting_maintenance_report",
        transition_name="release_plan_built",
        status=RunStatus.RUNNING,
    )


def awaiting_maintenance_report(state: WorkflowState) -> NodeResult:
    """
    Pause until an external maintenance report arrives.
    """
    report = state.data.get("maintenance_report")

    if report is None:
        return NodeResult(
            next_node="awaiting_maintenance_report",
            transition_name="maintenance_report_not_received",
            status=RunStatus.WAITING_EXTERNAL,
            waiting_for="maintenance_report",
        )

    return NodeResult(
        next_node="validate_maintenance_report",
        transition_name="maintenance_report_received",
        status=RunStatus.RUNNING,
    )

def validate_maintenance_report(state: WorkflowState) -> NodeResult:
    """
    Validate the external maintenance report before requesting approval.

    Invalid reports open a Failure Ticket (unplanned error path).
    Valid cleared reports open a real HITL admin task.
    """
    report = state.data.get("maintenance_report")

    def _fail(error_type: str, error_message: str) -> NodeResult:
        from state_graph.tickets import create_failure_ticket

        create_failure_ticket(
            state=state,
            failed_node="validate_maintenance_report",
            error_type=error_type,
            error_message=error_message,
        )
        return NodeResult(
            next_node="validate_maintenance_report",
            transition_name="maintenance_report_invalid",
            status=RunStatus.FAILED,
        )

    if not isinstance(report, dict):
        return _fail(
            error_type="invalid_report_type",
            error_message=(
                "Maintenance report must be a dictionary with reference, "
                "clearance, and summary."
            ),
        )

    required_fields = {"reference", "clearance", "summary"}
    missing_fields = required_fields - set(report.keys())

    if missing_fields:
        return _fail(
            error_type="missing_report_fields",
            error_message=(
                f"Maintenance report is missing fields: {sorted(missing_fields)}"
            ),
        )

    clearance = report["clearance"]

    if clearance not in {"cleared", "not_cleared"}:
        return _fail(
            error_type="invalid_clearance_value",
            error_message=(
                "Maintenance report clearance must be either "
                "'cleared' or 'not_cleared'."
            ),
        )

    state.data["maintenance_report_validated"] = True
    state.data["maintenance_clearance"] = clearance

    if clearance == "not_cleared":
        state.data["completion_reason"] = (
            "Maintenance did not clear the aircraft for release."
        )
        return NodeResult(
            next_node="completed",
            transition_name="aircraft_not_cleared",
            status=RunStatus.COMPLETED,
        )

    # HITL: open a real admin task before waiting for operations approval
    from state_graph.hitl import create_admin_task

    task_id = create_admin_task(
        state=state,
        task_type="operations_aircraft_release_approval",
        requested_by=state.data.get("requested_by", "maintenance_release_agent"),
        request_message=(
            f"Approve aircraft release for flight {state.flight_number}? "
            f"Maintenance clearance: {clearance}. "
            f"Report reference: {report.get('reference', 'N/A')}."
        ),
        request_payload={
            "flight_number": state.flight_number,
            "maintenance_clearance": clearance,
            "maintenance_report": report,
            "waiting_for": "operations_manager_approval",
        },
    )

    state.data["operations_approval_task_id"] = task_id

    return NodeResult(
        next_node="requires_operations_approval",
        transition_name="maintenance_report_validated",
        status=RunStatus.WAITING_ADMIN,
        waiting_for="operations_manager_approval",
    )

  


def requires_operations_approval(state: WorkflowState) -> NodeResult:
    """
    Wait for the operations manager's final decision.
    """
    decision = state.data.get("operations_decision")

    if decision is None:
        return NodeResult(
            next_node="requires_operations_approval",
            transition_name="operations_approval_pending",
            status=RunStatus.WAITING_ADMIN,
            waiting_for="operations_manager_approval",
        )

    if decision not in {"approved", "rejected"}:
        raise ValueError(
            "operations_decision must be either 'approved' or 'rejected'."
        )

    state.data["operations_decision_processed"] = True

    if decision == "rejected":
        state.data["completion_reason"] = (
            "Operations manager rejected aircraft release."
        )

        return NodeResult(
            next_node="completed",
            transition_name="aircraft_release_rejected",
            status=RunStatus.COMPLETED,
        )

    return NodeResult(
        next_node="mark_flight_ready",
        transition_name="aircraft_release_approved",
        status=RunStatus.RUNNING,
    )


def mark_flight_ready(state: WorkflowState) -> NodeResult:
    """
    Run the final database write after clearance and approval.
    """
    from state_graph.tool_registry import require_enabled_tool

    require_enabled_tool(
        agent_name="maintenance_release",
        tool_name="mark_flight_ready",
    )

    from tools_write import mark_flight_ready as mark_flight_ready_tool

    released_by = state.data.get("operations_manager_id")

    if not released_by:
        raise ValueError(
            "operations_manager_id is required before marking a flight ready."
        )

    tool_result = mark_flight_ready_tool(
        flight_number=state.flight_number,
        run_id=state.run_id,
        released_by=released_by,
    )

    state.data["flight_release_result"] = tool_result
    state.data["completion_reason"] = (
        "Aircraft maintenance clearance and operations approval completed."
    )

    return NodeResult(
        next_node="completed",
        transition_name="flight_marked_ready",
        status=RunStatus.COMPLETED,
    )