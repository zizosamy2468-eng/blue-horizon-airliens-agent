"""
Agent-facing API routes.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import services

agents_bp = Blueprint("agents", __name__, url_prefix="/api/agents")


@agents_bp.get("")
def list_agents():
    """List supported agent types and recent runs."""
    return jsonify(
        {
            "agents": [
                {
                    "type": "safety_incident",
                    "name": "Safety Incident Agent",
                    "owner": "Adel",
                },
                {
                    "type": "maintenance_release",
                    "name": "Maintenance Release Coordinator",
                    "owner": "Mostafa",
                },
                {
                    "type": "compensation_appeal",
                    "name": "Compensation Appeal Agent",
                    "owner": "Zizo",
                },
            ],
            "recent_runs": services.list_active_runs(),
        }
    )


@agents_bp.post("/start")
def start_agent():
    body = request.get_json(force=True, silent=True) or {}
    agent_type = body.get("agent_type") or body.get("type")
    if not agent_type:
        return jsonify({"error": "agent_type is required"}), 400
    try:
        result = services.start_agent(agent_type, body)
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@agents_bp.get("/runs/<run_id>")
def get_run(run_id: str):
    result = services.get_run(run_id)
    if not result:
        return jsonify({"error": "run not found"}), 404
    return jsonify(result)


@agents_bp.post("/runs/<run_id>/resume")
def resume_run(run_id: str):
    body = request.get_json(force=True, silent=True) or {}
    data_updates = body.get("data_updates") or body
    transition = body.get("transition_name", "platform_resume")
    try:
        result = services.resume_agent(run_id, data_updates, transition)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
