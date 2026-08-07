# SEMANTIC MEMORY concern (storage side).
#
# Semantic facts are durable, de-duplicated, subject-predicate-value facts
# with no session baggage -- e.g. "BH202's disruption cause is weather" or
# "the compensation auto-approve cap is 750 USD". This is different from
# episodic memory (memory/episodic.py), which still remembers WHICH session
# and WHEN something was said. Semantic memory is what's left after that
# noise is consolidated away.
#
# CRITICAL RULE (graded explicitly): nothing writes to this store directly
# except the consolidation pass (memory/consolidation.py), and that pass
# only runs periodically, never at write-time. The promote-or-drop router
# never touches this file. If you're tempted to call add_fact() from
# anywhere else, that's the wrong place to call it from.
#
# Every fact is versioned. Overwriting a fact silently is not allowed --
# old versions get an explicit valid_until, a status, and (when relevant) a
# conflict_notes explanation of why they were superseded. Nothing is ever
# deleted from this file's history.

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

STORE_PATH = Path(__file__).parent / "semantic_store.json"

Status = Literal["active", "expired", "superseded_due_to_conflict"]


@dataclass
class SemanticFact:
    fact_id: str
    subject: str            # e.g. "BH202" or "policy:compensation_cap"
    predicate: str          # e.g. "disruption_reason" or "auto_approve_cap_usd"
    value: str
    version: int
    status: Status
    authority: int          # 1 = unconfirmed report, 2 = confirmed, 3 = supervisor-backed
    source_episode_ids: list[str] = field(default_factory=list)
    valid_from: str = ""
    valid_until: str | None = None       # None while still active
    superseded_by: str | None = None     # fact_id of the version that replaced this one
    conflict_notes: str | None = None    # filled in only for real conflicts, not routine updates


class SemanticStore:
    def __init__(self, store_path: Path = STORE_PATH):
        self.store_path = store_path
        self.facts: list[SemanticFact] = self._load()
        self._next_id = len(self.facts) + 1

    # -----------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------
    def _load(self) -> list[SemanticFact]:
        if not self.store_path.exists():
            return []
        raw = json.loads(self.store_path.read_text())
        return [SemanticFact(**row) for row in raw]

    def _save(self) -> None:
        self.store_path.write_text(
            json.dumps([asdict(f) for f in self.facts], indent=2)
        )

    # -----------------------------------------------------------
    # Reading
    # -----------------------------------------------------------
    def get_active(self, subject: str, predicate: str) -> SemanticFact | None:
        matches = [
            f for f in self.facts
            if f.subject == subject and f.predicate == predicate and f.status == "active"
        ]
        # There should only ever be one active fact per (subject, predicate) --
        # add_fact() below enforces that by retiring the old one first.
        return matches[-1] if matches else None

    def get_history(self, subject: str, predicate: str) -> list[SemanticFact]:
        return [
            f for f in self.facts
            if f.subject == subject and f.predicate == predicate
        ]

    # -----------------------------------------------------------
    # Writing -- called ONLY by consolidation.py
    # -----------------------------------------------------------
    def add_fact(
        self,
        subject: str,
        predicate: str,
        value: str,
        authority: int,
        source_episode_ids: list[str],
        is_conflict: bool = False,
        conflict_notes: str | None = None,
    ) -> SemanticFact:
        """
        Adds a new version of a (subject, predicate) fact.

        If an active version already exists with a DIFFERENT value:
          - Routine update (is_conflict=False): the old fact is retired
            ("expired"), the new one becomes active. Used for values that
            legitimately change over time, like a policy cap.
          - Real conflict (is_conflict=True): authority decides the winner.
            If the new fact's authority >= the existing fact's authority,
            the new fact wins and becomes active (existing one marked
            "superseded_due_to_conflict"). If the new fact's authority is
            LOWER, the existing fact stays active and the new fact is
            stored already-superseded -- it never silently becomes the
            answer just because it arrived more recently.

        Either way, nothing is ever deleted -- every version stays in
        self.facts with a status, a valid_until, and (for conflicts) an
        explicit note of what it was resolved against.
        """
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_active(subject, predicate)

        new_fact = SemanticFact(
            fact_id=f"fact_{self._next_id:05d}",
            subject=subject,
            predicate=predicate,
            value=value,
            version=(existing.version + 1) if existing else 1,
            status="active",
            authority=authority,
            source_episode_ids=source_episode_ids,
            valid_from=now,
        )
        self._next_id += 1

        if existing is not None and existing.value != value:
            new_wins = (not is_conflict) or (authority >= existing.authority)

            if new_wins:
                existing.status = "superseded_due_to_conflict" if is_conflict else "expired"
                existing.valid_until = now
                existing.superseded_by = new_fact.fact_id
                if is_conflict:
                    new_fact.conflict_notes = (
                        conflict_notes
                        or f"Superseded fact_id={existing.fact_id} (authority={existing.authority}) "
                           f"with value={existing.value!r}."
                    )
            else:
                # The new candidate loses on authority: keep the existing
                # fact active, store the new one already marked as the
                # loser so the disagreement is still on record.
                new_fact.status = "superseded_due_to_conflict"
                new_fact.valid_until = now
                new_fact.superseded_by = existing.fact_id
                new_fact.conflict_notes = (
                    conflict_notes
                    or f"Lower authority ({authority}) than active fact_id={existing.fact_id} "
                       f"(authority={existing.authority}, value={existing.value!r}); kept existing."
                )

        self.facts.append(new_fact)
        self._save()
        return new_fact


if __name__ == "__main__":
    store_path = Path(__file__).parent / "semantic_store_demo.json"
    store_path.unlink(missing_ok=True)
    store = SemanticStore(store_path=store_path)

    # --- Case 1: routine update, not a conflict (policy cap raised) ---
    store.add_fact(
        subject="policy:compensation_cap",
        predicate="auto_approve_cap_usd",
        value="500",
        authority=3,
        source_episode_ids=["ep_00010"],
    )
    updated = store.add_fact(
        subject="policy:compensation_cap",
        predicate="auto_approve_cap_usd",
        value="750",
        authority=3,
        source_episode_ids=["ep_00031"],
    )
    print("Cap fact history:")
    for f in store.get_history("policy:compensation_cap", "auto_approve_cap_usd"):
        print(f"  v{f.version} status={f.status} value={f.value} until={f.valid_until}")

    # --- Case 2: a real conflict (disruption reason disagreement on BH202) ---
    store.add_fact(
        subject="BH202",
        predicate="disruption_reason",
        value="mechanical",
        authority=1,  # initial, unconfirmed report from a front-desk agent
        source_episode_ids=["ep_00002"],
    )
    resolved = store.add_fact(
        subject="BH202",
        predicate="disruption_reason",
        value="weather",
        authority=2,  # maintenance-confirmed report, outranks the initial guess
        source_episode_ids=["ep_00015"],
        is_conflict=True,
    )
    print("\nBH202 disruption_reason history:")
    for f in store.get_history("BH202", "disruption_reason"):
        print(f"  v{f.version} status={f.status} value={f.value} conflict_notes={f.conflict_notes}")

    print("\nCurrently active fact for BH202 disruption_reason:", store.get_active("BH202", "disruption_reason"))
    store_path.unlink(missing_ok=True)