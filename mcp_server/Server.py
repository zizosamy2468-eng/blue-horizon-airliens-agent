# server.py
# Final version of the MCP server for the Blue Horizon Airlines project.

from __future__ import annotations

import sys
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from tools_read import get_flight_status, get_passenger_booking
from tools_write import assign_reserve_crew, issue_compensation, rebook_passenger
from notifications_logic import check_supervisor_credentials, session_state
from sampling_logic import generate_disruption_notice
from progress_logic import rebook_all_passengers_on_flight
from tools_search import search_knowledge_base
from memory_tools import (
    recall_flight_history,
    run_memory_consolidation,
    search_policy_manual,
)


# =========================================================
# PLANNING AGENT IMPORT
# =========================================================
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_PLANNING_DIR = _PROJECT_ROOT / "planning"
if str(_PLANNING_DIR) not in sys.path:
    sys.path.insert(0, str(_PLANNING_DIR))

from planning_agent_tools import resolve_disruption  # noqa: E402


# =========================================================
# FINAL PROJECT: MAINTENANCE STATE GRAPH IMPORT
# =========================================================
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from state_graph.maintenance.graph import (  # noqa: E402
    resume_maintenance_release,
    start_maintenance_release,
)


def _workflow_summary(state) -> str:
    """Return a readable workflow result for MCP clients and the platform."""
    return (
        f"Workflow run: {state.run_id}\n"
        f"Workflow type: {state.workflow_type}\n"
        f"Current node: {state.current_node}\n"
        f"Status: {state.status.value}\n"
        f"Waiting for: {state.waiting_for or 'nothing'}\n"
        f"Checkpoint number: {state.checkpoint_number}"
    )


def start_maintenance_release_workflow(
    flight_number: str,
    requested_by: str,
) -> str:
    """
    Start the Maintenance Release Coordinator for one disrupted flight.

    The workflow persists after every transition and pauses when waiting
    for a maintenance report or an operations-manager decision.
    """
    state = start_maintenance_release(
        flight_number=flight_number,
        requested_by=requested_by,
    )

    return _workflow_summary(state)


def resume_maintenance_release_workflow(
    run_id: str,
    maintenance_report: dict | None = None,
    operations_decision: str | None = None,
    operations_manager_id: str | None = None,
) -> str:
    """
    Resume a saved Maintenance Release workflow.

    maintenance_report example:
    {
        "reference": "MX-2026-100",
        "clearance": "cleared",
        "summary": "Inspection completed successfully."
    }

    operations_decision must be either 'approved' or 'rejected'.
    operations_manager_id example: 'ops_manager_001'.
    """
    data_updates = {}

    if maintenance_report is not None:
        data_updates["maintenance_report"] = maintenance_report

    if operations_decision is not None:
        data_updates["operations_decision"] = operations_decision

    if operations_manager_id is not None:
        data_updates["operations_manager_id"] = operations_manager_id

    if not data_updates:
        raise ValueError(
            "Provide a maintenance_report or an operations decision "
            "to resume the workflow."
        )

    transition_name = (
        "maintenance_report_received"
        if maintenance_report is not None
        else "operations_decision_received"
    )

    state = resume_maintenance_release(
        run_id=run_id,
        data_updates=data_updates,
        transition_name=transition_name,
    )

    return _workflow_summary(state)


# =========================================================
# MCP SERVER
# =========================================================
mcp = FastMCP("Blue Horizon IROPS Assistant")


# =========================================================
# TOOLS AVAILABLE TO EVERY CONNECTED CLIENT FROM THE START
# =========================================================
mcp.tool()(get_flight_status)
mcp.tool()(get_passenger_booking)
mcp.tool()(rebook_passenger)
mcp.tool()(rebook_all_passengers_on_flight)
mcp.tool()(generate_disruption_notice)
mcp.tool()(search_knowledge_base)

# Memory and RAG tools.
mcp.tool()(recall_flight_history)
mcp.tool()(search_policy_manual)

# Planning Agent tool.
mcp.tool()(resolve_disruption)

# Final Project: persistent State Graph Agent tools.
mcp.tool()(start_maintenance_release_workflow)
mcp.tool()(resume_maintenance_release_workflow)


# =========================================================
# TOOL: authenticate_supervisor
# =========================================================
@mcp.tool()
async def authenticate_supervisor(
    supervisor_id: str,
    pin: str,
    ctx: Context[ServerSession, None],
) -> str:
    """
    Authenticates a supervisor. On success, unlocks the supervisor-only
    tools and notifies the connected client that the tool list changed.
    """
    if not check_supervisor_credentials(supervisor_id, pin):
        return f"Rejected: invalid supervisor credentials for '{supervisor_id}'."

    if session_state["supervisor_authenticated"]:
        return f"Supervisor {supervisor_id} is already authenticated. No change made."

    mcp.add_tool(assign_reserve_crew)
    mcp.add_tool(issue_compensation)
    mcp.add_tool(run_memory_consolidation)

    session_state["supervisor_authenticated"] = True
    session_state["supervisor_id"] = supervisor_id

    await ctx.session.send_tool_list_changed()

    return (
        f"Supervisor {supervisor_id} authenticated. "
        "assign_reserve_crew, issue_compensation, and run_memory_consolidation "
        "are now available."
    )


# =========================================================
# RESOURCE: duty_time_policy
# =========================================================
@mcp.resource("policy://duty-time-limits")
def duty_time_policy() -> str:
    """
    Simplified duty-time policy resource.
    """
    return (
        "Crew duty-time limit policy (simplified for this project):\n"
        "- Max flying hours per day: 8 hours\n"
        "- Max hours on duty per day: 14 hours\n"
        "- If an assignment would make a pilot exceed either limit, "
        "explicit supervisor approval is required before assigning them."
    )


# =========================================================
# PROMPT: draft_disruption_message
# =========================================================
@mcp.prompt()
def draft_disruption_message(
    flight_number: str,
    disruption_reason: str,
) -> str:
    """
    Template for drafting a passenger disruption message.
    """
    return (
        f"Write a polite, brief message to passengers on flight {flight_number}, "
        f"explaining the flight was affected due to: {disruption_reason}, "
        "and outline the next steps (rebooking or compensation) "
        "without going into unnecessary technical detail."
    )


# =========================================================
# TRANSPORT
# =========================================================
if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")
