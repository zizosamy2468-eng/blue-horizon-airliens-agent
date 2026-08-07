# SHORT-TERM MEMORY + SCRATCHPAD concern.
#
# The problem this solves: during a disruption event (say flight BH202 goes
# "disrupted" for a mechanical reason), the IROPS agent makes many tool calls
# in one session -- get_flight_status, get_passenger_booking, duty-hour checks,
# elicitation for supervisor approval, issue_compensation, generate_disruption_notice.
# That's a long, tool-heavy transcript. If we just keep appending every turn
# forever, the context window fills up. But if we prune blindly (e.g. drop
# the oldest turns), we can lose the agent's *current plan* -- e.g. "I'm in
# the middle of handling BH202, I still need to check crew duty hours before
# I can assign reserve crew" -- along with old chit-chat.
#
# So we keep two separate things:
#   1) The rolling buffer: the actual turn-by-turn transcript (tool calls,
#      tool results, agent replies). This is what gets pruned/summarized.
#   2) The scratchpad: a small structured dict holding what the agent is
#      *currently* doing (which flight, current goal, sub-goal, working
#      facts it has already learned this session). Pruning the buffer must
#      NEVER touch the scratchpad.
#
# This is the foundation the promote-or-drop router (memory/router.py) builds
# on: when the buffer overflows, the router looks at aging turns and decides
# forget vs. promote-to-episodic. The scratchpad is never a candidate for
# that -- it's not "aging", it's live working state.

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Role = Literal["user", "agent", "tool_call", "tool_result"]


@dataclass
class Turn:
    """One entry in the rolling buffer."""
    role: Role
    content: str
    timestamp: str
    # Rough token estimate so overflow checks don't need a real tokenizer
    # for this project. ~4 chars/token is the usual rule-of-thumb estimate.
    token_estimate: int = field(init=False)

    def __post_init__(self):
        self.token_estimate = max(1, len(self.content) // 4)


class ShortTermMemory:
    """
    Rolling buffer (bounded by token budget) + a separate scratchpad.

    session_id: ties this memory to one IROPS handling session, e.g. an
    ops agent working "BH202-2026-08-02". Episodic memory later stores
    promoted turns under this same session_id so they can be traced back.
    """

    def __init__(self, session_id: str, max_tokens: int = 2000):
        self.session_id = session_id
        self.max_tokens = max_tokens
        self.buffer: deque[Turn] = deque()

        # The scratchpad is intentionally a flat, small dict -- not a list
        # that grows. It represents current state, not history.
        self.scratchpad: dict = {
            "current_flight": None,        # e.g. "BH202"
            "current_goal": None,          # e.g. "resolve disruption for BH202"
            "sub_goal": None,              # e.g. "check crew duty hours before assigning reserve"
            "working_facts": {},           # facts learned this session, e.g. {"disruption_reason": "mechanical"}
            "pending_decisions": [],       # e.g. ["awaiting supervisor approval for crew_id=1"]
        }

    # -----------------------------------------------------------
    # Buffer operations
    # -----------------------------------------------------------
    def add_turn(self, role: Role, content: str) -> Turn:
        turn = Turn(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.buffer.append(turn)
        return turn

    def total_tokens(self) -> int:
        return sum(t.token_estimate for t in self.buffer)

    def is_overflowing(self) -> bool:
        return self.total_tokens() > self.max_tokens

    def pop_oldest(self) -> Turn | None:
        """
        Removes and returns the single oldest turn in the buffer, if any.
        This is the hand-off point to the promote-or-drop router: the
        router calls this repeatedly while is_overflowing() is True, and
        decides forget-vs-promote for each turn it pops. This function
        never touches self.scratchpad.
        """
        if not self.buffer:
            return None
        return self.buffer.popleft()

    def get_recent(self, n: int | None = None) -> list[Turn]:
        if n is None:
            return list(self.buffer)
        return list(self.buffer)[-n:]

    # -----------------------------------------------------------
    # Scratchpad operations
    # -----------------------------------------------------------
    def update_scratchpad(self, **kwargs) -> None:
        """
        Update one or more scratchpad fields. working_facts and
        pending_decisions are merged/appended rather than overwritten,
        everything else (current_flight, current_goal, sub_goal) is
        replaced outright since there's only ever one "current" value.
        """
        for key, value in kwargs.items():
            if key not in self.scratchpad:
                raise KeyError(f"Unknown scratchpad field: {key}")

            if key == "working_facts" and isinstance(value, dict):
                self.scratchpad["working_facts"].update(value)
            elif key == "pending_decisions" and isinstance(value, str):
                self.scratchpad["pending_decisions"].append(value)
            else:
                self.scratchpad[key] = value

    def resolve_pending_decision(self, decision_text: str) -> None:
        """Called once an elicitation/approval actually comes back."""
        if decision_text in self.scratchpad["pending_decisions"]:
            self.scratchpad["pending_decisions"].remove(decision_text)

    def get_scratchpad(self) -> dict:
        return self.scratchpad

    # -----------------------------------------------------------
    # Debug / demo helper
    # -----------------------------------------------------------
    def snapshot(self) -> dict:
        """Used by the demo transcript to show buffer + scratchpad side by side."""
        return {
            "session_id": self.session_id,
            "buffer_turns": len(self.buffer),
            "buffer_tokens": self.total_tokens(),
            "scratchpad": self.scratchpad,
        }


if __name__ == "__main__":
    # Small smoke test matching the client_stdio.py demo flow: BH202,
    # mechanical disruption, supervisor approval needed for crew_id=1.
    stm = ShortTermMemory(session_id="BH202-2026-08-02", max_tokens=120)

    stm.update_scratchpad(
        current_flight="BH202",
        current_goal="resolve disruption for BH202",
        sub_goal="check crew duty hours before assigning reserve crew",
    )
    stm.update_scratchpad(working_facts={"disruption_reason": "mechanical"})
    stm.update_scratchpad(pending_decisions="awaiting supervisor approval for crew_id=1 duty override")

    stm.add_turn("tool_call", "get_flight_status(flight_number='BH202')")
    stm.add_turn("tool_result", "Flight BH202: CAI to LHR - Status: disrupted - Reason: mechanical")
    stm.add_turn("tool_call", "get_passenger_booking(passenger_email='mona.khaled@example.com')")
    stm.add_turn("tool_result", "- Flight BH202 | Seat 3C | business | Booking status: confirmed | Flight status: disrupted")

    print("Overflowing before big tool dump?", stm.is_overflowing())

    # Simulate a large tool_result flooding the buffer (like a long duty log dump)
    stm.add_turn("tool_result", "duty_time_logs " * 60)

    print("Overflowing after big tool dump?", stm.is_overflowing())
    print("Scratchpad survives regardless of buffer state:")
    print(stm.get_scratchpad())

    print("\nDraining oldest turns until buffer fits (router will do this for real later):")
    while stm.is_overflowing():
        oldest = stm.pop_oldest()
        print(f"  popped -> [{oldest.role}] {oldest.content[:50]}...")

    print("\nFinal snapshot:")
    print(stm.snapshot())