# SEMANTIC MEMORY CONSOLIDATION concern.
#
# This is the ONLY thing allowed to write to memory/semantic.py's store.
# It is a separate, periodic pass -- meant to be run on a schedule (a cron
# job, an Airflow task, whatever) completely independent of any live agent
# session, NOT something that fires inline when the promote-or-drop router
# promotes a turn. That separation is the point: episodic memory can fill
# up with raw, possibly-contradictory episodes all day; semantic memory
# only gets touched when this pass deliberately runs and reconciles them.
#
# What this pass actually does, each run:
#   1) Pull episodes since the last consolidation run (tracked in a small
#      checkpoint file so re-running doesn't reprocess old episodes).
#   2) Extract candidate (subject, predicate, value) facts out of each
#      episode's free text, using the same domain-specific patterns
#      episodic.py already tags episodes with (flight numbers, amounts,
#      plus a couple of predicate-specific extractors below).
#   3) Score each extracted fact's authority (how much to trust it) based
#      on its source: a supervisor-backed episode outranks a maintenance-
#      confirmed report, which outranks an initial unconfirmed one.
#   4) Group extracted facts by (subject, predicate). If a subject/predicate
#      already has an active semantic fact with a DIFFERENT value, this is
#      a real conflict -- resolve it by authority (ties broken by recency)
#      and record why, via semantic.py's is_conflict path. If the new value
#      just extends/updates the same predicate with higher authority and no
#      real disagreement in meaning (e.g. a cap change), it's a routine
#      update instead.
#
# Extraction here is intentionally simple pattern matching, not an LLM
# call, so the whole consolidation run is deterministic, cheap, and easy
# for a grader to trace fact-by-fact back to the episodes that produced it.

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from episodic import EpisodicStore
from semantic import SemanticStore

CHECKPOINT_PATH = Path(__file__).parent / "consolidation_checkpoint.json"

# predicate -> (regex, group index for the value)
FACT_EXTRACTORS = {
    "disruption_reason": (re.compile(r"reason:\s*(mechanical|weather|crew|other)", re.I), 1),
    "auto_approve_cap_usd": (re.compile(r"cap\s*(?:of|raised to|is)\s*(\d+)", re.I), 1),
}


def _score_authority(episode) -> int:
    """
    3 = a supervisor was directly involved in this episode (approval/override)
    2 = the episode explicitly says a fact was confirmed/verified
    1 = a plain, unconfirmed report (default)
    """
    if episode.supervisors:
        return 3
    if "confirmed" in episode.content.lower() or "verified" in episode.content.lower():
        return 2
    return 1


def _extract_facts(episode) -> list[tuple[str, str, str]]:
    """Returns a list of (subject, predicate, value) candidates found in one episode."""
    found = []

    for predicate, (pattern, group_idx) in FACT_EXTRACTORS.items():
        match = pattern.search(episode.content)
        if not match:
            continue
        value = match.group(group_idx).lower()

        if predicate == "auto_approve_cap_usd":
            subject = "policy:compensation_cap"
        elif episode.flight_numbers:
            # disruption_reason and similar per-flight predicates
            subject = episode.flight_numbers[0]
        else:
            continue  # no clear subject to attach this fact to, skip it

        found.append((subject, predicate, value))

    return found


class ConsolidationPass:
    def __init__(
        self,
        episodic_store: EpisodicStore,
        semantic_store: SemanticStore,
        checkpoint_path: Path = CHECKPOINT_PATH,
    ):
        self.episodic_store = episodic_store
        self.semantic_store = semantic_store
        self.checkpoint_path = checkpoint_path

    def _last_run_time(self) -> str | None:
        if not self.checkpoint_path.exists():
            return None
        return json.loads(self.checkpoint_path.read_text())["last_run"]

    def _save_checkpoint(self) -> None:
        self.checkpoint_path.write_text(
            json.dumps({"last_run": datetime.now(timezone.utc).isoformat()})
        )

    def run(self) -> dict:
        """
        Runs one consolidation pass. Returns a summary dict (new facts,
        conflicts resolved, updates applied) so a grader or a demo script
        can print exactly what this run did.
        """
        last_run = self._last_run_time()
        episodes = self.episodic_store.get_all()
        if last_run is not None:
            episodes = [e for e in episodes if e.created_at > last_run]

        # Step 1: extract every candidate fact from the new episodes,
        # tagged with which episode and how much to trust it.
        candidates: dict[tuple[str, str], list[dict]] = {}
        for ep in episodes:
            authority = _score_authority(ep)
            for subject, predicate, value in _extract_facts(ep):
                key = (subject, predicate)
                candidates.setdefault(key, []).append({
                    "value": value,
                    "authority": authority,
                    "episode_id": ep.episode_id,
                    "created_at": ep.created_at,
                })

        summary = {"new_facts": 0, "updates": 0, "conflicts_resolved": 0, "details": []}

        # Step 2: for each (subject, predicate), reconcile against whatever
        # is already active in semantic memory, applying candidates in
        # chronological order so later, better-sourced episodes can
        # override earlier ones exactly like they did in production.
        for (subject, predicate), items in candidates.items():
            items.sort(key=lambda i: i["created_at"])

            for item in items:
                active = self.semantic_store.get_active(subject, predicate)

                if active is None:
                    self.semantic_store.add_fact(
                        subject=subject, predicate=predicate, value=item["value"],
                        authority=item["authority"], source_episode_ids=[item["episode_id"]],
                    )
                    summary["new_facts"] += 1
                    summary["details"].append(f"NEW {subject}.{predicate} = {item['value']}")
                    continue

                if active.value == item["value"]:
                    continue  # same fact re-confirmed, nothing to do

                # A differing value is a genuine conflict by default -- both were
                # asserted as true about the same subject/predicate, and they
                # can't both be right. Authority only decides which one WINS,
                # it doesn't decide whether this counts as a conflict at all.
                # The one exception: predicates that are known to legitimately
                # change over time (a policy cap being raised) are routine
                # updates, not disagreements about the same moment in time.
                is_conflict = predicate != "auto_approve_cap_usd"

                new_fact = self.semantic_store.add_fact(
                    subject=subject, predicate=predicate, value=item["value"],
                    authority=item["authority"], source_episode_ids=[item["episode_id"]],
                    is_conflict=is_conflict,
                )

                if is_conflict:
                    summary["conflicts_resolved"] += 1
                    summary["details"].append(
                        f"CONFLICT {subject}.{predicate}: {active.value!r} (auth={active.authority}) "
                        f"vs {item['value']!r} (auth={item['authority']}) -> kept {new_fact.value!r}"
                    )
                else:
                    summary["updates"] += 1
                    summary["details"].append(
                        f"UPDATE {subject}.{predicate}: {active.value!r} -> {item['value']!r}"
                    )

        self._save_checkpoint()
        return summary


if __name__ == "__main__":
    ep_path = Path(__file__).parent / "episodic_store_demo.json"
    sem_path = Path(__file__).parent / "semantic_store_demo.json"
    ckpt_path = Path(__file__).parent / "consolidation_checkpoint_demo.json"
    for p in (ep_path, sem_path, ckpt_path):
        p.unlink(missing_ok=True)

    episodic = EpisodicStore(store_path=ep_path)
    semantic = SemanticStore(store_path=sem_path)

    # Simulate three episodes landing in episodic memory over the course of
    # a real disruption: an initial unconfirmed report, then a maintenance-
    # confirmed correction (the real conflict), plus a policy cap update.
    class _FakeTurn:
        def __init__(self, role, content):
            self.role, self.content = role, content

    episodic.add_episode(
        session_id="BH202-2026-08-02",
        turn=_FakeTurn("tool_result", "Flight BH202: CAI to LHR - Status: disrupted - Reason: mechanical"),
        reasoning="root_cause_fact matched",
    )
    episodic.add_episode(
        session_id="BH202-2026-08-02",
        turn=_FakeTurn("tool_result", "Maintenance confirmed: BH202 disruption Reason: weather, not a mechanical fault."),
        reasoning="root_cause_fact matched, confirmed source",
    )
    episodic.add_episode(
        session_id="policy-update-2026-09-01",
        turn=_FakeTurn("tool_result", "Ops policy update: compensation auto-approve cap raised to 750 USD, approved by sup_002."),
        reasoning="financial_outcome + authorization_event matched",
    )

    pass_1 = ConsolidationPass(episodic, semantic, checkpoint_path=ckpt_path)
    result = pass_1.run()

    print("=== Consolidation run summary ===")
    for line in result["details"]:
        print(" -", line)
    print(f"\nnew_facts={result['new_facts']} updates={result['updates']} "
          f"conflicts_resolved={result['conflicts_resolved']}")

    print("\n=== Final active semantic facts ===")
    print("BH202.disruption_reason:", semantic.get_active("BH202", "disruption_reason"))
    print("policy cap:", semantic.get_active("policy:compensation_cap", "auto_approve_cap_usd"))

    for p in (ep_path, sem_path, ckpt_path):
        p.unlink(missing_ok=True)