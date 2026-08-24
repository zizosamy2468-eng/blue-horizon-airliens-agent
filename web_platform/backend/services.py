"""
Shared services for the Blue Horizon platform backend.
Bridges HTTP routes to state-graph agents, HITL, and tickets.
"""

from __future__ import annotations

from typing import Any


def list_active_runs() -> list[dict[str, Any]]:
    """Return recent workflow runs from the checkpoint store when available."""
    try:
        from state_graph.checkpoint_store import list_runs  # type: ignore

        return list_runs(limit=50)
    except Exception:
        return []


def get_run(run_id: str) -> dict[str, Any] | None:
    try:
        from state_graph.checkpoint_store import load_run_state  # type: ignore

        state = load_run_state(run_id)
        return _state_to_dict(state)
    except Exception:
        return None


def start_agent(agent_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Start a new agent workflow by type."""
    if agent_type in {"safety_incident", "safety"}:
        from state_graph.safety.graph import start_safety_incident

        state = start_safety_incident(
            flight_number=payload.get("flight_number", "BH000"),
            incident_type=payload.get("incident_type", "unspecified"),
            severity=payload.get("severity", "medium"),
            description=payload.get("description", ""),
            requested_by=payload.get("requested_by", "platform"),
            has_passenger_impact=bool(payload.get("has_passenger_impact", False)),
            crew_facts=payload.get("crew_facts"),
        )
        return _state_to_dict(state)

    if agent_type in {"maintenance_release", "maintenance"}:
        from state_graph.maintenance.graph import start_maintenance_release

        state = start_maintenance_release(
            flight_number=payload.get("flight_number", "BH000"),
            requested_by=payload.get("requested_by", "platform"),
        )
        return _state_to_dict(state)

    if agent_type in {"compensation_appeal", "compensation"}:
        from state_graph.compensation.graph import start_compensation_appeal

        state = start_compensation_appeal(
            flight_number=payload.get("flight_number", "BH000"),
            passenger_email=payload.get("passenger_email", "passenger@example.com"),
            appeal_reason=payload.get("appeal_reason", ""),
            requested_amount=float(payload.get("requested_amount", 0)),
            currency=payload.get("currency", "USD"),
            requested_by=payload.get("requested_by", "platform"),
            loyalty_tier=payload.get("loyalty_tier", "unknown"),
        )
        return _state_to_dict(state)

    raise ValueError(f"Unknown agent_type: {agent_type}")


def resume_agent(run_id: str, data_updates: dict[str, Any], transition_name: str = "platform_resume") -> dict[str, Any]:
    """Resume any workflow by inspecting its type from stored state."""
    try:
        from state_graph.checkpoint_store import load_run_state

        state = load_run_state(run_id)
        wtype = state.workflow_type
    except Exception as exc:
        raise ValueError(f"Cannot load run {run_id}: {exc}") from exc

    if wtype == "safety_incident":
        from state_graph.safety.graph import resume_safety_incident

        new_state = resume_safety_incident(run_id, data_updates, transition_name)
    elif wtype == "maintenance_release":
        from state_graph.maintenance.graph import resume_maintenance_release

        new_state = resume_maintenance_release(run_id, data_updates, transition_name)
    elif wtype == "compensation_appeal":
        from state_graph.compensation.graph import resume_compensation_appeal

        new_state = resume_compensation_appeal(run_id, data_updates, transition_name)
    else:
        from state_graph.runner import StateGraphRunner
        from state_graph.checkpoint_store import load_run_state as _load

        # Generic resume via empty runner (handlers must already be registered externally)
        runner = StateGraphRunner(handlers={})
        new_state = runner.resume(run_id, data_updates, transition_name)

    return _state_to_dict(new_state)


def list_admin_tasks(status: str | None = None) -> list[dict[str, Any]]:
    try:
        from state_graph.hitl import list_admin_tasks as _list

        return _list(status=status)
    except Exception:
        return []


def resolve_admin_task(
    task_id: str,
    decision: str,
    decided_by: str,
    comment: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Apply an admin HITL decision and resume the linked workflow.
    """
    try:
        from state_graph.hitl import resolve_admin_task as _resolve

        task = _resolve(
            task_id=task_id,
            decision=decision,
            decided_by=decided_by,
            comment=comment,
            payload=payload,
        )
    except Exception:
        task = {
            "task_id": task_id,
            "decision": decision,
            "decided_by": decided_by,
            "comment": comment,
            "run_id": (payload or {}).get("run_id"),
        }

    run_id = task.get("run_id") if isinstance(task, dict) else None
    if not run_id and payload:
        run_id = payload.get("run_id")

    if run_id:
        data_updates: dict[str, Any] = {
            "admin_decision": decision,
            "admin_comment": comment or "",
            "admin_decided_by": decided_by,
        }
        if payload:
            data_updates.update(payload)
        try:
            resume_agent(run_id, data_updates, transition_name=f"admin_{decision}")
        except Exception as exc:
            task = dict(task) if isinstance(task, dict) else {"task_id": task_id}
            task["resume_error"] = str(exc)

    return task if isinstance(task, dict) else {"task_id": task_id, "decision": decision}


def list_tickets(status: str | None = None) -> list[dict[str, Any]]:
    try:
        from state_graph.tickets import list_failure_tickets

        return list_failure_tickets(status=status)
    except Exception:
        return []


def resolve_ticket(
    ticket_id: str,
    resolved_by: str,
    resolution_notes: str,
    resume: bool = True,
    data_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from state_graph.tickets import resolve_failure_ticket

        ticket = resolve_failure_ticket(
            ticket_id=ticket_id,
            resolved_by=resolved_by,
            resolution_notes=resolution_notes,
        )
    except Exception:
        ticket = {
            "ticket_id": ticket_id,
            "resolved_by": resolved_by,
            "resolution_notes": resolution_notes,
            "status": "resolved",
        }

    run_id = ticket.get("run_id") if isinstance(ticket, dict) else None
    if resume and run_id:
        updates = data_updates or {"ticket_resolved": True, "resolution_notes": resolution_notes}
        try:
            resume_agent(run_id, updates, transition_name="ticket_recovered")
        except Exception as exc:
            ticket = dict(ticket) if isinstance(ticket, dict) else {"ticket_id": ticket_id}
            ticket["resume_error"] = str(exc)

    return ticket if isinstance(ticket, dict) else {"ticket_id": ticket_id}


def _state_to_dict(state: Any) -> dict[str, Any]:
    if hasattr(state, "to_dict"):
        return state.to_dict()
    return {
        "run_id": getattr(state, "run_id", None),
        "workflow_type": getattr(state, "workflow_type", None),
        "current_node": getattr(state, "current_node", None),
        "status": getattr(getattr(state, "status", None), "value", str(getattr(state, "status", None))),
        "flight_number": getattr(state, "flight_number", None),
        "data": getattr(state, "data", {}),
        "waiting_for": getattr(state, "waiting_for", None),
        "admin_task_id": getattr(state, "admin_task_id", None),
        "last_error": getattr(state, "last_error", None),
        "checkpoint_number": getattr(state, "checkpoint_number", 0),
    }
