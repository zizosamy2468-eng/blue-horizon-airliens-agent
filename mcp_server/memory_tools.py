# This is the ONLY new file needed to wire the memory system (memory/) and
# the RAG system (rag/) into the live MCP server -- it imports and reuses
# both, it does not duplicate a single line of their logic. Register these
# functions as tools in server.py exactly like every other tool there (see
# the three lines to add at the bottom of this file's docstring).
#
# What this adds, session by session:
#   - Every read/write tool call in a session should also be recorded into
#     that session's ShortTermMemory via record_turn() (see the note on
#     wiring this into server.py below -- the cleanest hook is a small
#     wrapper around each existing tool call, not rewriting tools_read.py/
#     tools_write.py themselves).
#   - When a session's buffer overflows, the promote-or-drop router drains
#     it automatically into episodic memory.
#   - recall_flight_history: lets the agent pull up what happened on a
#     flight in EARLIER sessions (not just this one) -- this is what fixes
#     the original problem (front-desk agents re-explaining/re-approving
#     things every session).
#   - search_policy_manual: replaces the old toy search_knowledge_base
#     (4 hardcoded sentences) with real hybrid RAG over the full policy
#     manual, Self-RAG-verified before anything is returned to the agent.
#   - run_memory_consolidation: a supervisor-only tool that triggers the
#     periodic consolidation pass on demand (a real deployment would run
#     this on a schedule instead/as well -- see README for the cron note).
#
# HOW TO WIRE THIS INTO server.py (3 additions, same pattern as every
# other tool already registered there):
#
#   from memory_tools import recall_flight_history, search_policy_manual, run_memory_consolidation
#
#   mcp.tool()(recall_flight_history)
#   mcp.tool()(search_policy_manual)
#   # run_memory_consolidation is supervisor-only, register it the same way
#   # assign_reserve_crew/issue_compensation get registered inside
#   # authenticate_supervisor() in server.py -- add this line right next to
#   # mcp.add_tool(assign_reserve_crew) there:
#   mcp.add_tool(run_memory_consolidation)
#
# Also add ONE line inside each existing tool in tools_read.py/tools_write.py
# right before it returns, so every call actually gets recorded:
#   record_turn(session_id="<derive from context or a passed session_id>", role="tool_result", content=<the return string>)
# For this class project, the simplest correct approach is deriving
# session_id from the flight_number being handled (e.g. f"{flight_number}-
# {date.today()}"), since that's the natural unit of a Blue Horizon IROPS
# session -- see get_session_id() below.
#
# The scratchpad is a SEPARATE addition from record_turn() -- call
# update_scratchpad() at the specific moments a tool actually changes what
# the agent is working on (a new flight becomes the current focus, a
# sub-goal like "waiting on a duty-hour override" starts or resolves), not
# on every tool call. See tools_read.py/tools_write.py for exactly where
# each call was added.

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "memory"))
sys.path.insert(0, str(Path(__file__).parent.parent / "rag"))

from episodic import EpisodicStore  # noqa: E402
from router import PromoteOrDropRouter  # noqa: E402
from semantic import SemanticStore  # noqa: E402
from short_term import ShortTermMemory  # noqa: E402

from hybrid_rag import BM25PolicyIndex, hybrid_search  # noqa: E402
from self_rag import verify_memory_recall, verify_rag_answer  # noqa: E402
from vector_store import PolicyVectorStore  # noqa: E402


# -----------------------------------------------------------
# Session-scoped state. One ShortTermMemory per active IROPS session,
# shared stores for episodic/semantic since those persist across sessions
# on disk anyway (memory/episodic_store.json, memory/semantic_store.json).
# -----------------------------------------------------------
_active_sessions: dict[str, ShortTermMemory] = {}
_router = PromoteOrDropRouter()
_episodic = EpisodicStore()
_semantic = SemanticStore()

# Built once per server process -- rebuilding the vector index or the BM25
# index on every tool call would be wasteful; these only change when the
# policy manual itself changes.
_vector_store = PolicyVectorStore()
_bm25_index = BM25PolicyIndex()


def get_session_id(flight_number: str) -> str:
    """The natural session unit for this project: one IROPS handling
    session per flight per day."""
    return f"{flight_number}-{date.today().isoformat()}"


def record_turn(session_id: str, role: str, content: str) -> None:
    """
    Called from inside the existing read/write tools (see the wiring note
    above) after every real tool call. Adds the turn to that session's
    short-term buffer and, if the buffer overflows, drains it through the
    promote-or-drop router into episodic memory automatically.
    """
    if session_id not in _active_sessions:
        _active_sessions[session_id] = ShortTermMemory(session_id=session_id)

    stm = _active_sessions[session_id]
    stm.add_turn(role=role, content=content)

    if stm.is_overflowing():
        _router.process_overflow(stm, episodic_store=_episodic)


def update_scratchpad(session_id: str, **kwargs) -> None:
    """
    The other half of the short-term memory concern that record_turn()
    above does NOT cover: the scratchpad (current_flight, current_goal,
    sub_goal, working_facts, pending_decisions). Tools call this
    separately from record_turn() at the specific moments that actually
    represent a change in what the agent is working on -- not on every
    single tool call, since most tool calls don't change the plan, they
    just add another observation to the buffer.
    """
    if session_id not in _active_sessions:
        _active_sessions[session_id] = ShortTermMemory(session_id=session_id)

    _active_sessions[session_id].update_scratchpad(**kwargs)


def resolve_pending_decision(session_id: str, decision_text: str) -> None:
    """Called once an elicitation/approval actually comes back, so the
    scratchpad's pending_decisions list doesn't grow stale entries."""
    if session_id in _active_sessions:
        _active_sessions[session_id].resolve_pending_decision(decision_text)


def get_scratchpad(session_id: str) -> dict | None:
    """Lets a tool (or a demo script) inspect the current plan for a session."""
    if session_id not in _active_sessions:
        return None
    return _active_sessions[session_id].get_scratchpad()


# =========================================================
# TOOL: recall_flight_history
# =========================================================
def recall_flight_history(flight_number: str, current_question: str) -> str:
    """
    Recalls what happened on a given flight across ALL past sessions
    (not just the current one), from episodic memory, filtered through a
    Self-RAG-style relevance check before being handed back -- so a stale
    or irrelevant old episode doesn't get surfaced just because it
    mentions the same flight number.

    flight_number: the flight number to recall history for, e.g. BH202
    current_question: what the ops agent is actually trying to find out
        right now (used for the relevance check, not just a flight-number filter)
    """
    episodes = _episodic.get_by_flight(flight_number)
    if not episodes:
        return f"No past episodic history found for flight {flight_number}."

    verification = verify_memory_recall(current_question, episodes)
    if not verification["verified"]:
        return (
            f"Found {len(episodes)} past episode(s) for {flight_number}, but none were "
            "judged relevant to this specific question after review."
        )

    lines = [f"- [{e.created_at}] {e.content}" for e in verification["relevant_items"]]
    return f"Relevant history for {flight_number}:\n" + "\n".join(lines)


# =========================================================
# TOOL: search_policy_manual
# =========================================================
def search_policy_manual(query: str, category: str | None = None, top_k: int = 3) -> str:
    """
    Searches the full IROPS policy manual using hybrid search (vector +
    BM25), Self-RAG-verified for relevance before anything is returned.
    Replaces the old search_knowledge_base tool, which only had 4
    hardcoded sentences and no real retrieval.

    query: what to search the policy manual for
    category: optional filter -- one of compensation, duty_time, rebooking,
        weather, mechanical, crew, communication
    top_k: how many policy sections to retrieve, default 3
    """
    retrieved = hybrid_search(query, _vector_store, _bm25_index, k=top_k)
    if not retrieved:
        return "No policy sections found for this query."

    # We're not generating prose inside the tool here (the connected
    # client's own model does that, same as every other tool in this
    # server) -- but we DO run the relevance half of Self-RAG so an
    # irrelevant chunk that merely scored well numerically doesn't get
    # handed to the agent as if it were a match.
    verified_chunks = []
    for r in retrieved:
        from self_rag import check_relevance
        verdict = check_relevance(query, r.section.text)
        if verdict["relevant"]:
            verified_chunks.append(r)

    if not verified_chunks:
        return "Retrieved candidates did not pass relevance verification for this query."

    return "\n\n".join(
        f"[{r.section.code}] {r.section.title}\n{r.section.text}" for r in verified_chunks
    )


# =========================================================
# TOOL: run_memory_consolidation (supervisor-only, see wiring note above)
# =========================================================
def run_memory_consolidation() -> str:
    """
    Runs one consolidation pass: reads new episodes since the last run,
    extracts/updates semantic facts, and resolves any conflicts explicitly.
    Supervisor-only because this changes what the whole system treats as
    "current truth" going forward -- not something a front-desk session
    should trigger implicitly.
    """
    from consolidation import ConsolidationPass

    consolidation = ConsolidationPass(_episodic, _semantic)
    result = consolidation.run()

    if not result["details"]:
        return "Consolidation ran: no new episodes to process since the last run."

    lines = "\n".join(f"- {d}" for d in result["details"])
    return (
        f"Consolidation complete at {datetime.now(timezone.utc).isoformat()}.\n"
        f"new_facts={result['new_facts']} updates={result['updates']} "
        f"conflicts_resolved={result['conflicts_resolved']}\n\n{lines}"
    )

# =========================================================
# COMPENSATION APPEAL — memory side-effect helper
# =========================================================
def record_appeal_event(
    flight_number: str,
    content: str,
    role: str = "tool_result",
) -> None:
    """
    Record one compensation-appeal event into the flight's short-term
    memory buffer (and promote-or-drop if overflowing). Used by the
    Compensation Appeal graph so appeal decisions survive across sessions
    the same way IROPS tool results do.
    """
    session_id = get_session_id(flight_number)
    record_turn(session_id=session_id, role=role, content=content)


if __name__ == "__main__":
    # Smoke test wiring memory + RAG together the way the live server would.
    session_id = get_session_id("BH202")
    update_scratchpad(
        session_id,
        current_flight="BH202",
        current_goal="resolve disruption for BH202",
    )
    record_turn(session_id, "tool_result", "Flight BH202: Status: disrupted - Reason: mechanical")
    update_scratchpad(session_id, working_facts={"disruption_reason": "mechanical"})

    update_scratchpad(session_id, sub_goal="check duty hours before assigning reserve crew",
                       pending_decisions="awaiting supervisor approval for crew_id=1 duty override")
    record_turn(session_id, "tool_result",
                "SUPERVISOR APPROVAL NEEDED: Karim Mostafa (crew_id=1) already at 13.0 duty hours "
                "today. Approved by sup_001 as an override for flight BH202.")
    resolve_pending_decision(session_id, "awaiting supervisor approval for crew_id=1 duty override")

    print("=== Scratchpad after the session (this is what stays intact across buffer pruning) ===")
    print(get_scratchpad(session_id))

    print("\n=== search_policy_manual ===")
    print(search_policy_manual("what is the compensation policy for mechanical disruptions"))

    print("\n=== recall_flight_history (before any overflow -- likely empty) ===")
    print(recall_flight_history("BH202", "any duty-hour overrides on record?"))
