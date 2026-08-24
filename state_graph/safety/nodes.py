"""
Node handlers for the Safety Incident Agent state graph.

Workflow (Adel):
  incident_created
    → collect_flight_and_crew_facts
    → retrieve_safety_policy [RAG]
    → explore_reporting_paths [LATS]
    → awaiting_ground_or_crew_report
    → validate_evidence
         ├── missing / conflicting → Failure Ticket
         └── sufficient → draft_regulatory_report
    → HITL: safety manager review
         ├── changes requested → revise report
         └── approved → submit report
    → awaiting_authority_acknowledgement
    → completed
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from state_graph.models import RunStatus, WorkflowState
from state_graph.runner import NodeResult
from state_graph.safety.search_strategy import explore_reporting_paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _search_policy(query: str) -> dict[str, Any]:
    """Reuse the existing Memory/RAG MCP tool when available."""
    try:
        from memory_tools import search_policy_manual  # type: ignore

        return search_policy_manual(query=query, category="safety", top_k=3)
    except Exception:
        try:
            from rag.vector_store import PolicyVectorStore  # type: ignore

            store = PolicyVectorStore()
            results = store.search(query, top_k=3)
            return {
                "source": "vector_store",
                "hits": [
                    {
                        "code": r.chunk_id,
                        "score": r.score,
                        "text": getattr(r.section, "text", str(r.section)),
                    }
                    for r in results
                ],
            }
        except Exception as exc:
            return {
                "source": "fallback",
                "hits": [],
                "note": f"Policy search unavailable: {exc}",
            }


def _create_failure_ticket(state: WorkflowState, error_type: str, message: str) -> str:
    """Create a failure ticket via the shared tickets module."""
    try:
        from state_graph.tickets import create_failure_ticket

        ticket = create_failure_ticket(
            run_id=state.run_id,
            failed_node=state.current_node,
            error_type=error_type,
            error_message=message,
            state_json=state.to_dict() if hasattr(state, "to_dict") else {
                "run_id": state.run_id,
                "current_node": state.current_node,
                "data": state.data,
            },
        )
        ticket_id = ticket.get("ticket_id") if isinstance(ticket, dict) else str(ticket)
    except Exception:
        ticket_id = str(uuid4())
        state.data.setdefault("failure_tickets", []).append(
            {
                "ticket_id": ticket_id,
                "error_type": error_type,
                "error_message": message,
            }
        )
    return ticket_id


def _create_hitl_task(state: WorkflowState, report_text: str) -> str:
    """Register an admin HITL task for safety-manager review."""
    try:
        from state_graph.hitl import create_admin_task

        task = create_admin_task(
            run_id=state.run_id,
            task_type="safety_report_review",
            title=f"Safety report review — {state.flight_number}",
            description="Review and approve or request changes to the draft regulatory safety report.",
            payload={
                "draft_report": report_text,
                "incident_type": state.data.get("incident_type"),
                "severity": state.data.get("severity"),
                "flight_number": state.flight_number,
            },
        )
        task_id = task.get("task_id") if isinstance(task, dict) else str(task)
    except Exception:
        task_id = str(uuid4())
        state.data["pending_hitl_task"] = {
            "task_id": task_id,
            "draft_report": report_text,
        }
    state.admin_task_id = task_id
    return task_id


# ---------------------------------------------------------------------------
# Node handlers
# ---------------------------------------------------------------------------

def collect_flight_and_crew_facts(state: WorkflowState) -> NodeResult:
    """
    Collect flight status and any crew facts already present in state.data.
    """
    flight_status = state.data.get("flight_status")
    if not flight_status:
        try:
            from state_graph.tool_registry import require_enabled_tool

            require_enabled_tool(
                agent_name="safety_incident",
                tool_name="get_flight_status",
            )
        except Exception:
            pass

        try:
            from tools_read import get_flight_status  # type: ignore

            flight_status = get_flight_status(state.flight_number or "")
        except Exception:
            flight_status = {
                "flight_number": state.flight_number,
                "status": "unknown",
                "note": "flight status tool unavailable; using placeholder",
            }

    state.data["flight_status"] = flight_status
    state.data.setdefault("crew_facts", state.data.get("crew_facts") or {})
    state.data.setdefault(
        "incident_id",
        state.data.get("incident_id") or str(uuid4()),
    )

    return NodeResult(
        next_node="retrieve_safety_policy",
        transition_name="flight_and_crew_facts_collected",
        status=RunStatus.RUNNING,
    )


def retrieve_safety_policy(state: WorkflowState) -> NodeResult:
    """Retrieve applicable safety policies via RAG."""
    try:
        from state_graph.tool_registry import require_enabled_tool

        require_enabled_tool(
            agent_name="safety_incident",
            tool_name="search_policy_manual",
        )
    except Exception:
        pass

    incident_type = state.data.get("incident_type", "general safety incident")
    severity = state.data.get("severity", "medium")
    query = (
        f"Aviation safety reporting requirements for {severity} severity "
        f"{incident_type} incidents, crew and ground evidence standards, "
        f"and regulatory submission rules."
    )

    policy_result = _search_policy(query)
    state.data["safety_policy_query"] = query
    state.data["safety_policy_result"] = policy_result

    return NodeResult(
        next_node="explore_reporting_paths",
        transition_name="safety_policy_retrieved",
        status=RunStatus.RUNNING,
    )


def explore_reporting_paths_node(state: WorkflowState) -> NodeResult:
    """Explore possible reporting paths using LATS / deterministic ranking."""
    severity = state.data.get("severity", "medium")
    incident_type = state.data.get("incident_type", "unspecified")
    has_passenger_impact = bool(state.data.get("has_passenger_impact", False))

    exploration = explore_reporting_paths(
        severity=severity,
        incident_type=incident_type,
        has_passenger_impact=has_passenger_impact,
    )
    state.data["reporting_path_exploration"] = exploration
    state.data["recommended_path_id"] = exploration.get("recommended_path_id")

    return NodeResult(
        next_node="awaiting_ground_or_crew_report",
        transition_name="reporting_paths_explored",
        status=RunStatus.RUNNING,
    )


def awaiting_ground_or_crew_report(state: WorkflowState) -> NodeResult:
    """
    Pause until ground or crew report is supplied externally.
    On resume, state.data should contain ground_report and/or crew_report.
    """
    ground = state.data.get("ground_report")
    crew = state.data.get("crew_report") or state.data.get("crew_facts")

    if ground or (crew and state.data.get("crew_report_received")):
        return NodeResult(
            next_node="validate_evidence",
            transition_name="ground_or_crew_report_received",
            status=RunStatus.RUNNING,
        )

    return NodeResult(
        next_node="awaiting_ground_or_crew_report",
        transition_name="waiting_for_ground_or_crew_report",
        status=RunStatus.WAITING_EXTERNAL,
        waiting_for="ground_or_crew_report",
    )


def validate_evidence(state: WorkflowState) -> NodeResult:
    """
    Validate collected evidence. Missing or conflicting data → Failure Ticket.
    """
    ground = state.data.get("ground_report") or {}
    crew = state.data.get("crew_report") or state.data.get("crew_facts") or {}
    flight_status = state.data.get("flight_status") or {}

    missing: list[str] = []
    conflicts: list[str] = []

    if not ground and not crew:
        missing.append("No ground or crew report provided")
    if not state.data.get("incident_type"):
        missing.append("incident_type")
    if not state.data.get("severity"):
        missing.append("severity")

    # Simple conflict detection: timestamps / location mismatch
    ground_loc = (ground.get("location") or "").strip().lower()
    crew_loc = (crew.get("location") or "").strip().lower()
    if ground_loc and crew_loc and ground_loc != crew_loc:
        conflicts.append(
            f"Location mismatch: ground='{ground_loc}' vs crew='{crew_loc}'"
        )

    ground_ts = ground.get("timestamp")
    crew_ts = crew.get("timestamp")
    if ground_ts and crew_ts and ground_ts != crew_ts:
        # Soft conflict — note but do not auto-fail unless forced
        state.data.setdefault("evidence_notes", []).append(
            f"Timestamp difference: ground={ground_ts}, crew={crew_ts}"
        )

    if missing or conflicts:
        message = "; ".join(missing + conflicts)
        ticket_id = _create_failure_ticket(
            state,
            error_type="insufficient_or_conflicting_evidence",
            message=message,
        )
        state.data["failure_ticket_id"] = ticket_id
        state.last_error = message
        return NodeResult(
            next_node="validate_evidence",
            transition_name="evidence_validation_failed",
            status=RunStatus.FAILED,
        )

    evidence_summary = {
        "has_ground_report": bool(ground),
        "has_crew_report": bool(crew),
        "flight_status": flight_status.get("status")
        if isinstance(flight_status, dict)
        else str(flight_status),
        "recommended_path_id": state.data.get("recommended_path_id"),
    }
    state.data["evidence_summary"] = evidence_summary

    return NodeResult(
        next_node="draft_regulatory_report",
        transition_name="evidence_validated",
        status=RunStatus.RUNNING,
    )


def draft_regulatory_report(state: WorkflowState) -> NodeResult:
    """Generate a draft regulatory safety report."""
    flight = state.flight_number or "UNKNOWN"
    incident_type = state.data.get("incident_type", "unspecified")
    severity = state.data.get("severity", "medium")
    path_id = state.data.get("recommended_path_id", "ops_and_safety_manager")
    evidence = state.data.get("evidence_summary", {})
    policy = state.data.get("safety_policy_result", {})

    policy_codes = []
    if isinstance(policy, dict):
        for hit in policy.get("hits") or []:
            if isinstance(hit, dict) and hit.get("code"):
                policy_codes.append(hit["code"])

    report_lines = [
        f"# Safety Incident Report — Flight {flight}",
        f"**Incident ID:** {state.data.get('incident_id')}",
        f"**Run ID:** {state.run_id}",
        f"**Type:** {incident_type}",
        f"**Severity:** {severity}",
        f"**Recommended reporting path:** {path_id}",
        "",
        "## Evidence Summary",
        f"- Ground report present: {evidence.get('has_ground_report')}",
        f"- Crew report present: {evidence.get('has_crew_report')}",
        f"- Flight status: {evidence.get('flight_status')}",
        "",
        "## Applicable Policy References",
        ", ".join(policy_codes) if policy_codes else "See RAG retrieval results.",
        "",
        "## Narrative",
        state.data.get("description")
        or "Automated draft generated from collected facts. Safety Manager review required.",
        "",
        "## Recommendation",
        "Submit to designated authority after Safety Manager approval."
        if path_id in {"national_authority", "authority_and_icao"}
        else "Retain as internal safety record after Safety Manager approval.",
    ]

    # Prefer constrained LLM polish when available.
    try:
        from llm_client import call_llm  # type: ignore

        polished = call_llm(
            system_prompt=(
                "You are a civil-aviation safety officer. "
                "Polish the following draft report into clear formal English. "
                "Do not invent facts. Keep all identifiers and severity unchanged."
            ),
            user_prompt="\n".join(report_lines),
        )
        if isinstance(polished, str) and len(polished) > 50:
            report_text = polished
        else:
            report_text = "\n".join(report_lines)
    except Exception:
        report_text = "\n".join(report_lines)

    state.data["draft_report"] = report_text
    state.data["report_revision"] = int(state.data.get("report_revision") or 0)

    return NodeResult(
        next_node="safety_manager_review",
        transition_name="draft_report_created",
        status=RunStatus.RUNNING,
    )


def safety_manager_review(state: WorkflowState) -> NodeResult:
    """
    HITL pause for Safety Manager review.
    On resume, state.data['admin_decision'] should be 'approved' or 'changes_requested'.
    Optional: state.data['admin_comment'] and state.data['revised_report'].
    """
    decision = (state.data.get("admin_decision") or "").lower().strip()

    if decision == "approved":
        state.data["final_report"] = state.data.get("revised_report") or state.data.get(
            "draft_report"
        )
        return NodeResult(
            next_node="submit_report",
            transition_name="safety_manager_approved",
            status=RunStatus.RUNNING,
        )

    if decision in {"changes_requested", "revise", "rejected"}:
        # Clear decision so the next cycle waits again after revision.
        state.data.pop("admin_decision", None)
        comment = state.data.get("admin_comment") or "Changes requested by Safety Manager."
        state.data.setdefault("revision_notes", []).append(comment)
        return NodeResult(
            next_node="revise_report",
            transition_name="safety_manager_requested_changes",
            status=RunStatus.RUNNING,
        )

    # First entry or still waiting — create HITL task and pause.
    if not state.admin_task_id:
        _create_hitl_task(state, state.data.get("draft_report") or "")

    return NodeResult(
        next_node="safety_manager_review",
        transition_name="waiting_for_safety_manager",
        status=RunStatus.WAITING_ADMIN,
        waiting_for="safety_manager_decision",
    )


def revise_report(state: WorkflowState) -> NodeResult:
    """Apply Safety Manager feedback and return to HITL review."""
    notes = state.data.get("revision_notes") or []
    last_note = notes[-1] if notes else "General revision requested."
    current = state.data.get("revised_report") or state.data.get("draft_report") or ""

    revised = (
        f"{current}\n\n---\n**Revision note:** {last_note}\n"
        f"(Revision #{int(state.data.get('report_revision') or 0) + 1})"
    )
    state.data["revised_report"] = revised
    state.data["draft_report"] = revised
    state.data["report_revision"] = int(state.data.get("report_revision") or 0) + 1
    state.admin_task_id = None  # force new HITL task on next review

    return NodeResult(
        next_node="safety_manager_review",
        transition_name="report_revised",
        status=RunStatus.RUNNING,
    )


def submit_report(state: WorkflowState) -> NodeResult:
    """Submit the approved report (optionally via tool) and wait for authority ack."""
    final_report = state.data.get("final_report") or state.data.get("draft_report") or ""
    path_id = state.data.get("recommended_path_id", "")

    submission_id = str(uuid4())
    state.data["submission_id"] = submission_id
    state.data["submitted_report"] = final_report
    state.data["submission_status"] = "pending"

    try:
        from state_graph.tool_registry import require_enabled_tool

        require_enabled_tool(
            agent_name="safety_incident",
            tool_name="submit_regulatory_report",
        )
    except Exception:
        pass

    # Simulated authority submission record.
    state.data["authority_submission"] = {
        "submission_id": submission_id,
        "path_id": path_id,
        "status": "pending",
        "authority": (
            "National Aviation Authority"
            if path_id in {"national_authority", "authority_and_icao"}
            else "Internal Safety Board"
        ),
    }

    return NodeResult(
        next_node="awaiting_authority_acknowledgement",
        transition_name="report_submitted",
        status=RunStatus.RUNNING,
    )


def awaiting_authority_acknowledgement(state: WorkflowState) -> NodeResult:
    """
    Wait for external authority acknowledgement.
    On resume: state.data['authority_ack'] = {'status': 'acknowledged'|'rejected', 'ref': '...'}
    """
    ack = state.data.get("authority_ack") or {}
    status = (ack.get("status") or "").lower()

    if status == "acknowledged":
        state.data["submission_status"] = "acknowledged"
        state.data["authority_reference"] = ack.get("ref") or ack.get("reference")
        return NodeResult(
            next_node="completed",
            transition_name="authority_acknowledged",
            status=RunStatus.COMPLETED,
        )

    if status == "rejected":
        ticket_id = _create_failure_ticket(
            state,
            error_type="authority_rejection",
            message=ack.get("reason") or "Authority rejected the submitted report",
        )
        state.data["failure_ticket_id"] = ticket_id
        state.last_error = "Authority rejected report"
        return NodeResult(
            next_node="awaiting_authority_acknowledgement",
            transition_name="authority_rejected",
            status=RunStatus.FAILED,
        )

    return NodeResult(
        next_node="awaiting_authority_acknowledgement",
        transition_name="waiting_for_authority_ack",
        status=RunStatus.WAITING_EXTERNAL,
        waiting_for="authority_acknowledgement",
    )


# Alias used by the graph builder (matches task-division node names).
explore_reporting_paths = explore_reporting_paths_node
