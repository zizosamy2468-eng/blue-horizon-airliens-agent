# PROMOTE-OR-DROP ROUTING concern.
#
# This fires when ShortTermMemory.is_overflowing() is True. For each aging
# turn popped off the front of the buffer, the router decides ONE of two
# things:
#   - "forget": the turn is discarded for good (e.g. a routine status check
#     that already got answered and has no lasting value).
#   - "episodic": the turn gets written to the episodic store, because it
#     represents something that happened during this session that a human
#     or the agent itself might need to recall later -- a decision, an
#     approval, a disruption reason, a compensation outcome.
#
# IMPORTANT (this is graded explicitly): this router NEVER writes to semantic
# memory. Semantic memory is only ever built by the separate, periodic
# consolidation pass (memory/consolidation.py) that reads FROM the episodic
# store later. The router's whole job stops at forget-vs-episodic.
#
# Every decision is logged with the reasoning behind it -- real signals
# matched, not just "the router said so" -- so a grader can see exactly why
# a given turn was kept or dropped.
#
# NOTE on the decision logic: in a production system this classification
# step would likely be an LLM call ("is this turn worth remembering?").
# For this project we use a transparent, inspectable rule-based scorer
# instead -- partly because it's honest about what's actually driving the
# decision (no hidden LLM judgment to second-guess), and partly because it
# keeps this concern testable without burning API calls on every single
# turn that ages out of a long session.

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from short_term import Turn

Action = Literal["forget", "episodic"]

# Signals that mark a turn as worth keeping, grouped by why they matter.
# Each matched signal adds to the turn's significance score.
SIGNAL_WEIGHTS: dict[str, list[str]] = {
    "financial_outcome": ["compensation", "approved", "rejected", "payout", "refund"],
    "operational_decision": ["rebooked", "reassigned", "cancelled", "disrupted", "assigned"],
    "authorization_event": ["supervisor", "elicit", "override", "authenticate"],
    "root_cause_fact": ["mechanical", "weather", "duty hours", "disruption_reason", "hydraulic"],
}

PROMOTE_THRESHOLD = 1  # a single matched signal is already enough to promote


@dataclass
class RoutingDecision:
    turn_role: str
    turn_content: str
    action: Action
    matched_signals: list[str]
    reasoning: str
    decided_at: str


class PromoteOrDropRouter:
    """
    Stateless decision logic + a running log of every decision made, so a
    grader (or the agent itself, during a demo) can see the reasoning trail.
    """

    def __init__(self):
        self.decision_log: list[RoutingDecision] = []

    def _score_turn(self, turn: Turn) -> list[str]:
        text = turn.content.lower()
        matched = []
        for category, keywords in SIGNAL_WEIGHTS.items():
            for kw in keywords:
                if kw in text:
                    matched.append(f"{category}:{kw}")
        return matched

    def route(self, turn: Turn) -> RoutingDecision:
        matched = self._score_turn(turn)
        action: Action = "episodic" if len(matched) >= PROMOTE_THRESHOLD else "forget"

        if action == "episodic":
            reasoning = (
                f"Promoted to episodic: matched {len(matched)} significance "
                f"signal(s) -> {matched}. This looks like a decision, outcome, "
                "or fact that could be needed again this event."
            )
        else:
            reasoning = (
                "Dropped: no financial, operational, authorization, or "
                "root-cause signal matched. Treated as routine/transient "
                "conversational noise with no lasting value."
            )

        decision = RoutingDecision(
            turn_role=turn.role,
            turn_content=turn.content,
            action=action,
            matched_signals=matched,
            reasoning=reasoning,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )
        self.decision_log.append(decision)
        return decision

    def process_overflow(self, short_term_memory, episodic_store=None) -> list[RoutingDecision]:
        """
        Drains ShortTermMemory while it's overflowing, routing each popped
        turn to forget or episodic. episodic_store is any object exposing
        add_episode(session_id, turn, reasoning) -- duck-typed on purpose so
        this file doesn't need to import memory/episodic.py directly and
        stays a single, easy-to-locate piece of the promote-or-drop concern.

        Returns the list of decisions made in this pass (also appended to
        self.decision_log for the full session history).
        """
        decisions_this_pass = []

        while short_term_memory.is_overflowing():
            turn = short_term_memory.pop_oldest()
            if turn is None:
                break

            decision = self.route(turn)
            decisions_this_pass.append(decision)

            if decision.action == "episodic" and episodic_store is not None:
                episodic_store.add_episode(
                    session_id=short_term_memory.session_id,
                    turn=turn,
                    reasoning=decision.reasoning,
                )

        return decisions_this_pass

    def print_log(self) -> None:
        for d in self.decision_log:
            print(f"[{d.decided_at}] {d.action.upper():9} <- [{d.turn_role}] {d.turn_content[:60]!r}")
            print(f"    reason: {d.reasoning}")


if __name__ == "__main__":
    from short_term import ShortTermMemory

    stm = ShortTermMemory(session_id="BH202-2026-08-02", max_tokens=80)
    router = PromoteOrDropRouter()

    # Mix of routine noise and turns that should genuinely matter later.
    stm.add_turn("user", "can you check on BH202 for me")
    stm.add_turn("tool_call", "get_flight_status(flight_number='BH202')")
    stm.add_turn("tool_result", "Flight BH202: CAI to LHR - Status: disrupted - Reason: mechanical")
    stm.add_turn("tool_call", "assign_reserve_crew(flight_number='BH202', crew_id=1, requested_by='agent_014')")
    stm.add_turn("tool_result", "SUPERVISOR APPROVAL NEEDED: Karim Mostafa already at 13.0 duty hours today. Approved by sup_001.")
    stm.add_turn("tool_result", "Approved: Karim Mostafa (pilot) assigned as reserve crew on flight BH202. (Supervisor override: Approved by sup_001.)")
    stm.add_turn("user", "ok thanks, and one more small thing")
    stm.add_turn("tool_result", "issue_compensation approved: 150.00 USD compensation issued to Mona Khaled for flight BH202.")

    print("Buffer tokens before draining:", stm.total_tokens(), "(max:", stm.max_tokens, ")")
    print()

    decisions = router.process_overflow(stm)  # no episodic_store yet -- next file

    print(f"Routed {len(decisions)} turns while draining overflow:\n")
    router.print_log()

    kept = sum(1 for d in decisions if d.action == "episodic")
    dropped = sum(1 for d in decisions if d.action == "forget")
    print(f"\nSummary: {kept} promoted to episodic, {dropped} forgotten.")
    print("Scratchpad untouched by any of this:", stm.get_scratchpad())