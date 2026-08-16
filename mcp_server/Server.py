# server.py
# Final version of the MCP server for the Blue Horizon Airlines project.
#
# WHAT'S NEW IN THIS VERSION (Decomposition & Planning lab):
#   - A NEW, SEPARATE agent is wired in: the planning agent
#     (planning/planning_agent_tools.py). It owns disruption-response
#     PLANNING -- decomposing "resolve disruption for BH202" into a DAG,
#     routing sub-tasks to Plan-and-Solve / Tree of Thoughts / LATS, and
#     applying Self-Refine / Reflexion self-correction. This is a
#     DIFFERENT agent from the memory/RAG agent (memory_tools.py) added
#     in the previous lab -- the two additions below are the ONLY change
#     in this file for this lab. Nothing under memory_tools.py,
#     tools_read.py, or tools_write.py's memory-recording hooks was
#     touched, and the planning agent does not import from or share
#     state with any of them. It reuses the same MCP tools and database
#     underneath (via planning/domain_actions.py -> tools_read.py /
#     tools_write.py -> dbase.py), exactly as required.
#
# EVERYTHING BELOW THIS POINT THAT ISN'T THE TWO "--- Planning lab
# addition ---" BLOCKS IS UNCHANGED FROM THE MEMORY & RAG LAB VERSION.
#
#   - Sampling: generate_disruption_notice (sampling_logic.py) asks the
#     connected client's LLM to draft a passenger notice via
#     ctx.session.create_message(), instead of the server assuming a model.
#   - Progress tracking: rebook_all_passengers_on_flight (progress_logic.py)
#     is a genuinely long-running tool (loops over every affected passenger)
#     that reports real progress via ctx.report_progress() instead of
#     blocking silently until the whole batch finishes.
#   - Transport: this server ran on stdio for the entire development phase
#     (see earlier commits). Now that the project is close to submission,
#     it runs over Streamable HTTP by default, because a real airline would
#     run this as a shared network service for many ops agents at once, not
#     as a single local subprocess per agent. `python server.py stdio` is
#     kept as a fallback for quick local debugging with the MCP Inspector.
#   - MEMORY & RAG (added for the Memory & RAG lab): memory_tools.py wires
#     the memory system (memory/) and hybrid RAG with Self-RAG verification
#     (rag/) into this same server. recall_flight_history and
#     search_policy_manual are available to everyone from the start, same
#     as the other read-only tools -- they don't change state.
#     run_memory_consolidation is supervisor-only, registered the same way
#     assign_reserve_crew and issue_compensation are: it doesn't exist for
#     a session until authenticate_supervisor succeeds, because triggering
#     a consolidation pass changes what the whole system treats as current
#     truth going forward, not something a front-desk session should do
#     implicitly.

import sys

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from tools_read import get_flight_status, get_passenger_booking
from tools_write import assign_reserve_crew, issue_compensation, rebook_passenger
from notifications_logic import check_supervisor_credentials, session_state
from sampling_logic import generate_disruption_notice
from progress_logic import rebook_all_passengers_on_flight
from tools_search import search_knowledge_base
from memory_tools import recall_flight_history, search_policy_manual, run_memory_consolidation

# --- Planning lab addition: import the new, separate planning agent's
# top-level tool. planning/ sits next to mcp_server/ at the repo root, so
# this import assumes server.py is run with the repo root (or planning/)
# on sys.path -- see the "run instructions" note near the bottom of this
# file if you hit a ModuleNotFoundError here.
from pathlib import Path

_PLANNING_DIR = Path(__file__).resolve().parent.parent / "planning"
sys.path.insert(0, str(_PLANNING_DIR))
from planning_agent_tools import resolve_disruption  # noqa: E402
# --- end Planning lab addition ---

# =========================================================
# CAPABILITY NEGOTIATION
# =========================================================
# FastMCP handles the initialize/initialized exchange automatically, and tells
# the client this server supports: tools + resources + prompts + elicitation
# + sampling, because we register them below under this same "mcp" instance.
# It also declares tools.listChanged support, which is what makes the
# notification pushed from authenticate_supervisor meaningful.
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
# --- Memory & RAG lab additions ---
# Both read-only (recall_flight_history reads episodic memory,
# search_policy_manual reads the policy manual vector store), so they're
# available from the start like every other read-only tool above.
mcp.tool()(recall_flight_history)
mcp.tool()(search_policy_manual)

# --- Planning lab addition ---
# resolve_disruption is the planning agent's single top-level tool. It
# is read/write internally (it can call rebook_passenger, issue_compensation,
# assign_reserve_crew as part of resolving a disruption end to end), but
# it applies the SAME authorization rules those underlying tools already
# enforce (requested_by must be a valid ops agent id) -- so, like the
# read-only tools above, it's safe to expose to every connected client
# from the start rather than gating it behind authenticate_supervisor.
# It is NOT gated behind supervisor auth because the individual write
# actions it may call already gate themselves (e.g. a >$500 compensation
# proposal still triggers the real elicitation flow inside
# issue_compensation, unchanged).
mcp.tool()(resolve_disruption)
# --- end Planning lab addition ---


# =========================================================
# TOOL: authenticate_supervisor
# =========================================================
# The trigger for the runtime tool-set change (notifications concern). A
# plain front-desk session cannot assign crew or issue compensation -- those
# tools do not exist for it yet. Only after this call succeeds do they appear.
@mcp.tool()
async def authenticate_supervisor(
    supervisor_id: str,
    pin: str,
    ctx: Context[ServerSession, None],
) -> str:
    """
    Authenticates a supervisor. On success, unlocks the assign_reserve_crew
    and issue_compensation tools for the rest of this session and notifies
    the client that the tool list has changed.

    supervisor_id: the supervisor's ID, e.g. sup_001
    pin: the supervisor's PIN
    """
    if not check_supervisor_credentials(supervisor_id, pin):
        return f"Rejected: invalid supervisor credentials for '{supervisor_id}'."

    if session_state["supervisor_authenticated"]:
        return f"Supervisor {supervisor_id} is already authenticated. No change made."

    # Register the supervisor-only tools now that we know who this is.
    mcp.add_tool(assign_reserve_crew)
    mcp.add_tool(issue_compensation)
    # Memory & RAG lab addition: consolidation is supervisor-gated for the
    # same reason the two tools above are -- it changes shared state
    # (semantic memory) that every future session relies on.
    mcp.add_tool(run_memory_consolidation)

    session_state["supervisor_authenticated"] = True
    session_state["supervisor_id"] = supervisor_id

    # The actual notifications/tools/list_changed push. Without this call,
    # the client would have no way to know new tools just became available
    # short of disconnecting and reconnecting.
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
    Crew duty-time limit policy (simplified version for this project,
    not the full official regulation). For the full policy manual --
    compensation rules, overrides, rebooking priority, exceptions -- see
    the search_policy_manual tool instead, which retrieves from the real
    IROPS Policy Manual (rag/policy_corpus.py) rather than this fixed text.
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
# A static template the HOST can surface and fill in with whatever model
# the host itself is using. This is distinct from generate_disruption_notice
# above, which is the server actively requesting sampling from the client
# mid-tool-call rather than just handing over a fill-in-the-blanks string.
# Also distinct from the planning agent's refine_disruption_notice tool
# (planning/planning_agent_tools.py), which drafts AND self-corrects a
# notice against grounded + rubric critique server-side, rather than
# handing a template to the client to fill in itself.
@mcp.prompt()
def draft_disruption_message(flight_number: str, disruption_reason: str) -> str:
    """
    Template for drafting an apology/explanation message to passengers
    about a disrupted flight.
    """
    return (
        f"Write a polite, brief message to passengers on flight {flight_number}, "
        f"explaining the flight was affected due to: {disruption_reason}, "
        f"and outline the next steps (rebooking or compensation) "
        f"without going into unnecessary technical detail."
    )


# =========================================================
# TRANSPORT
# =========================================================
# Development phase (see earlier commit history): stdio only, one agent
# talking to one local server process -- simplest thing that could work
# while we were still building and debugging tools.
#
# This version: Streamable HTTP by default, because Blue Horizon is a
# multi-location airline and this server needs to be reachable by many ops
# agents' clients at once over the network, not spawned as a subprocess per
# agent. `python server.py stdio` is kept only as a fallback for quick local
# testing with the MCP Inspector (`mcp dev server.py`).
if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")
