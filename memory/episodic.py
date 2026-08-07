# EPISODIC MEMORY concern.
#
# This is where the promote-or-drop router (memory/router.py) sends turns
# it decided are worth keeping. An "episode" here is one promoted turn from
# one session, e.g. "during the BH202-2026-08-02 session, flight BH202 was
# found disrupted for a mechanical reason" or "supervisor sup_001 approved a
# duty-hour override for crew_id=1 on BH202".
#
# Episodic memory is intentionally close to raw fact-plus-context: it still
# knows WHICH session and WHEN something happened. It is NOT the same as
# semantic memory. Semantic memory (memory/semantic.py) holds durable,
# de-duplicated, versioned facts like "BH202's disruption cause is
# mechanical" with no session baggage attached -- and it is only ever built
# by the separate consolidation pass (memory/consolidation.py), which reads
# FROM this episodic store on a periodic schedule. Nothing writes to
# semantic memory directly, including this file.
#
# Why persist to disk instead of keeping this in a Python list in memory:
# the whole point of episodic memory is that it survives past the current
# process. An ops agent reconnecting tomorrow to handle a new BH202-related
# call should be able to pull up what happened last time. A plain in-memory
# list would defeat that, so this uses a simple JSON-file store -- good
# enough for a class project, and it's the kind of thing that would be a
# real table (or a real vector/document DB) in production.

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STORE_PATH = Path(__file__).parent / "episodic_store.json"

# Cheap entity extraction so later retrieval (and consolidation) can filter
# episodes by flight number or dollar amount without re-reading free text
# every time. Not NLP -- just regex over patterns this domain actually uses.
FLIGHT_PATTERN = re.compile(r"\bBH\d{3}\b")
AMOUNT_PATTERN = re.compile(r"\b(\d+(?:\.\d{2})?)\s?(USD|EUR|GBP|EGP)\b")
SUPERVISOR_PATTERN = re.compile(r"\bsup_\d+\b")


@dataclass
class Episode:
    episode_id: str
    session_id: str
    role: str
    content: str
    reasoning: str  # why the router promoted this turn, kept for traceability
    created_at: str
    flight_numbers: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    supervisors: list[str] = field(default_factory=list)


class EpisodicStore:
    def __init__(self, store_path: Path = STORE_PATH):
        self.store_path = store_path
        self.episodes: list[Episode] = self._load()
        self._next_id = len(self.episodes) + 1

    # -----------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------
    def _load(self) -> list[Episode]:
        if not self.store_path.exists():
            return []
        raw = json.loads(self.store_path.read_text())
        return [Episode(**row) for row in raw]

    def _save(self) -> None:
        self.store_path.write_text(
            json.dumps([asdict(e) for e in self.episodes], indent=2)
        )

    # -----------------------------------------------------------
    # Writing (called by the router, never by anything trying to
    # write semantic facts directly -- this store only ever holds
    # session-scoped episodes)
    # -----------------------------------------------------------
    def add_episode(self, session_id: str, turn, reasoning: str) -> Episode:
        episode = Episode(
            episode_id=f"ep_{self._next_id:05d}",
            session_id=session_id,
            role=turn.role,
            content=turn.content,
            reasoning=reasoning,
            created_at=datetime.now(timezone.utc).isoformat(),
            flight_numbers=FLIGHT_PATTERN.findall(turn.content),
            amounts=[f"{m[0]} {m[1]}" for m in AMOUNT_PATTERN.findall(turn.content)],
            supervisors=SUPERVISOR_PATTERN.findall(turn.content),
        )
        self._next_id += 1
        self.episodes.append(episode)
        self._save()
        return episode

    # -----------------------------------------------------------
    # Reading (used by: the agent recalling past sessions, and later
    # by the consolidation pass, which pulls episodes in bulk to
    # build/update semantic facts)
    # -----------------------------------------------------------
    def get_by_session(self, session_id: str) -> list[Episode]:
        return [e for e in self.episodes if e.session_id == session_id]

    def get_by_flight(self, flight_number: str) -> list[Episode]:
        return [e for e in self.episodes if flight_number in e.flight_numbers]

    def get_all(self) -> list[Episode]:
        return list(self.episodes)

    def search(self, keyword: str) -> list[Episode]:
        keyword = keyword.lower()
        return [e for e in self.episodes if keyword in e.content.lower()]


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from short_term import ShortTermMemory
    from router import PromoteOrDropRouter

    # Wire all three pieces built so far into one real flow: buffer fills up
    # during a BH202 session -> router drains overflow -> episodic store
    # persists whatever gets promoted.
    store_path = Path(__file__).parent / "episodic_store_demo.json"
    store_path.unlink(missing_ok=True)  # clean slate for the demo run
    episodic = EpisodicStore(store_path=store_path)

    stm = ShortTermMemory(session_id="BH202-2026-08-02", max_tokens=40)
    router = PromoteOrDropRouter()

    stm.add_turn("user", "can you check on BH202 for me")
    stm.add_turn("tool_result", "Flight BH202: CAI to LHR - Status: disrupted - Reason: mechanical")
    stm.add_turn("tool_result", "SUPERVISOR APPROVAL NEEDED: Karim Mostafa already at 13.0 duty hours today. Approved by sup_001.")
    stm.add_turn("tool_result", "Approved: 150.00 USD compensation issued to Mona Khaled for flight BH202. Issued by agent_007.")

    print(f"Buffer tokens after adding all turns: {stm.total_tokens()} (max: {stm.max_tokens})\n")
    router.process_overflow(stm, episodic_store=episodic)

    print(f"Episodes persisted to episodic memory: {len(episodic.get_all())}\n")
    for ep in episodic.get_all():
        print(f"- [{ep.episode_id}] session={ep.session_id} flights={ep.flight_numbers} "
              f"amounts={ep.amounts} supervisors={ep.supervisors}")
        print(f"    content: {ep.content[:70]}...")

    print("\n--- Simulating tomorrow: a NEW session pulls up BH202's history ---")
    new_process = EpisodicStore(store_path=store_path)  # fresh instance, reads from disk
    history = new_process.get_by_flight("BH202")
    print(f"Found {len(history)} past episodes about BH202 without re-asking anything:")
    for ep in history:
        print(f"  - {ep.content[:70]}")

    store_path.unlink(missing_ok=True)  # tidy up after the demo