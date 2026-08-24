"""
Admin / HITL / Ticket API routes.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import services

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin_bp.get("/tasks")
def list_tasks():
    status = request.args.get("status")
    return jsonify({"tasks": services.list_admin_tasks(status=status)})


@admin_bp.post("/tasks/<task_id>/decide")
def decide_task(task_id: str):
    body = request.get_json(force=True, silent=True) or {}
    decision = (body.get("decision") or "").lower().strip()
    if decision not in {"approved", "changes_requested", "rejected", "revise"}:
        return jsonify(
            {"error": "decision must be approved | changes_requested | rejected"}
        ), 400
    decided_by = body.get("decided_by") or body.get("admin") or "admin"
    comment = body.get("comment") or body.get("admin_comment")
    payload = body.get("payload") or {}
    if body.get("run_id"):
        payload["run_id"] = body["run_id"]
    if body.get("revised_report"):
        payload["revised_report"] = body["revised_report"]

    try:
        result = services.resolve_admin_task(
            task_id=task_id,
            decision=decision,
            decided_by=decided_by,
            comment=comment,
            payload=payload,
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@admin_bp.get("/tickets")
def list_tickets():
    status = request.args.get("status")
    return jsonify({"tickets": services.list_tickets(status=status)})


@admin_bp.post("/tickets/<ticket_id>/resolve")
def resolve_ticket(ticket_id: str):
    body = request.get_json(force=True, silent=True) or {}
    resolved_by = body.get("resolved_by") or body.get("admin") or "admin"
    notes = body.get("resolution_notes") or body.get("notes") or "Resolved from admin dashboard"
    resume = body.get("resume", True)
    data_updates = body.get("data_updates")
    try:
        result = services.resolve_ticket(
            ticket_id=ticket_id,
            resolved_by=resolved_by,
            resolution_notes=notes,
            resume=resume,
            data_updates=data_updates,
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@admin_bp.get("/health")
def health():
    return jsonify({"status": "ok", "component": "admin_api"})
