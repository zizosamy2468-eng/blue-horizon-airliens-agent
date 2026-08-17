# planning/lats.py
#
# LATS (Language Agent Tree Search) concern.
#
# Adapted from the reference toolkit's algorithms/lats.py: the standard
# four-phase MCTS loop -- SELECT (walk down via UCB1), EXPAND & SIMULATE
# (generate candidate next-actions via the LLM), EVALUATE (score each
# candidate -- HERE, using environment.py's real grounded checks instead
# of the toolkit's randomized default), BACKPROPAGATE (push the real
# score back up the tree so future SELECT calls favor better subtrees).
# A failed branch also gets a verbal reflection fed into the NEXT
# expansion's prompt, steering the search away from repeating the same
# mistake -- not just pruning it silently.
#
# router.py sends "retrieval"-shaped sub-tasks to LATS, but the sub-task
# this file demonstrates on is deliberately the SAME crew-assignment
# decision tree_of_thoughts.py handles with ungrounded self-evaluation.
# This is the lab's required "deliberately swapped an ungrounded
# self-critique for a grounded one" case: same decision, same candidate-
# generation shape, but EVALUATE now calls
# environment.check_crew_assignment_feasibility() (a real duty_time_logs
# query) instead of asking the model to score its own idea. The __main__
# demo below shows the concrete failure case this catches.

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from environment import EnvironmentFeedback, check_crew_assignment_feasibility  # noqa: E402
from llm_client import call_llm, call_llm_json                                   # noqa: E402

EXPAND_SYSTEM_PROMPT = """You are proposing ONE candidate reserve-crew member to assign \
to a disrupted Blue Horizon Airlines flight. You may be given verbal reflections from \
PREVIOUSLY REJECTED candidates this search already tried -- do not repeat the same mistake.

Respond ONLY with JSON:
{"crew_id": <int>, "full_name": "...", "reasoning": "..."}
"""

REFLECT_SYSTEM_PROMPT = """A candidate reserve-crew assignment was evaluated against real \
duty-hour data and scored poorly. Write ONE short, concrete verbal reflection (1-2 \
sentences) explaining what was wrong with this choice, phrased so a future search step \
can avoid repeating it. Do not restate the raw numbers, explain the LESSON.

Respond ONLY with JSON: {"reflection": "..."}
"""


@dataclass
class LATSNode:
    node_id: str
    candidate: dict | None            # the proposed crew assignment, None for the root
    visits: int = 0
    total_value: float = 0.0          # sum of grounded scores backpropagated through this node
    children: list["LATSNode"] = field(default_factory=list)
    feedback: EnvironmentFeedback | None = None
    reflection: str | None = None     # verbal reflection if this node's evaluation failed

    @property
    def mean_value(self) -> float:
        return self.total_value / self.visits if self.visits > 0 else 0.0

    def ucb1(self, parent_visits: int, c: float = 1.4) -> float:
        if self.visits == 0:
            return float("inf")  # unvisited nodes are always worth trying first
        exploitation = self.mean_value
        exploration = c * math.sqrt(math.log(parent_visits) / self.visits)
        return exploitation + exploration


def select(node: LATSNode) -> LATSNode:
    """SELECT: walk down the tree picking the child with the highest
    UCB1 score at each level, until reaching a node with no children yet."""
    current = node
    while current.children:
        current = max(current.children, key=lambda child: child.ucb1(current.visits))
    return current


def expand_and_simulate(
    flight_number: str,
    eligible_crew: list[dict],
    prior_reflections: list[str],
) -> tuple[dict, dict]:
    """EXPAND & SIMULATE: ask the LLM for ONE new candidate, informed by
    verbal reflections from earlier rejected branches in THIS search
    (not from a different session -- this is search-local, unlike
    Reflexion's cross-trial buffer in reflexion.py)."""
    reflections_text = (
        "\n".join(f"- {r}" for r in prior_reflections) if prior_reflections
        else "(none yet -- this is the first candidate)"
    )
    user_prompt = (
        f"Disrupted flight: {flight_number}\nEligible crew:\n{eligible_crew}\n\n"
        f"Reflections from previously rejected candidates in this search:\n{reflections_text}"
    )
    result = call_llm_json(system_prompt=EXPAND_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.9)
    if result["parsed"] is None:
        raise ValueError(f"LATS expand step returned invalid JSON: {result['parse_error']}")

    stats = {"llm_calls": 1, "input_tokens": result["input_tokens"],
              "output_tokens": result["output_tokens"], "latency_seconds": result["latency_seconds"]}
    return result["parsed"], stats


def evaluate(flight_number: str, candidate: dict) -> EnvironmentFeedback:
    """EVALUATE: the grounded step. Calls environment.py's REAL
    duty_time_logs check -- this is the actual replacement for the
    toolkit's randomized default, wired in at exactly the point where
    the toolkit's fake evaluator used to sit."""
    return check_crew_assignment_feasibility(candidate["crew_id"], flight_number)


def reflect_on_failure(candidate: dict, feedback: EnvironmentFeedback) -> tuple[str, dict]:
    """REFLECT: turn a failed branch's grounded feedback into a short
    verbal lesson, fed into the next expand_and_simulate call so the
    search doesn't just prune silently -- it actively steers away."""
    user_prompt = f"Rejected candidate: {candidate}\nGrounded evaluation: {feedback.detail}"
    result = call_llm_json(system_prompt=REFLECT_SYSTEM_PROMPT, user_prompt=user_prompt, temperature=0.3)
    if result["parsed"] is None:
        return f"Candidate crew_id={candidate['crew_id']} was rejected: {feedback.detail}", {
            "llm_calls": 1, "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"], "latency_seconds": result["latency_seconds"],
        }
    stats = {"llm_calls": 1, "input_tokens": result["input_tokens"],
              "output_tokens": result["output_tokens"], "latency_seconds": result["latency_seconds"]}
    return result["parsed"]["reflection"], stats


def backpropagate(node: LATSNode, score: float) -> None:
    """BACKPROPAGATE: push the real grounded score up through every
    ancestor so future SELECT calls at the root favor this subtree
    (or avoid it) based on real evidence, not just the leaf's own score."""
    node.visits += 1
    node.total_value += score


def run_lats(
    flight_number: str,
    eligible_crew: list[dict],
    max_iterations: int = 4,
    pass_threshold: float = 5.0,
) -> dict:
    """
    Full LATS loop for the reserve-crew decision, grounded end to end.
    Stops early once a candidate's REAL environment check passes with a
    score above threshold, or after max_iterations (safety bound).
    """
    root = LATSNode(node_id="root", candidate=None)
    reflections: list[str] = []
    trace = []
    total_llm_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_latency = 0.0
    best_passing: LATSNode | None = None

    for i in range(max_iterations):
        leaf = select(root)  # with only one level deep here, this is always root until children exist

        candidate, gen_stats = expand_and_simulate(flight_number, eligible_crew, reflections)
        total_llm_calls += gen_stats["llm_calls"]
        total_input_tokens += gen_stats["input_tokens"]
        total_output_tokens += gen_stats["output_tokens"]
        total_latency += gen_stats["latency_seconds"]

        child = LATSNode(node_id=f"node_{i}", candidate=candidate)
        leaf.children.append(child)

        feedback = evaluate(flight_number, candidate)   # GROUNDED, not model self-opinion
        child.feedback = feedback

        backpropagate(child, feedback.score)
        backpropagate(root, feedback.score)  # root's visit count also drives its own UCB1 baseline

        entry = {
            "iteration": i, "candidate": candidate, "passed": feedback.passed,
            "score": feedback.score, "detail": feedback.detail, "source": feedback.source,
        }

        if feedback.passed and feedback.score >= pass_threshold:
            trace.append(entry)
            best_passing = child
            break

        reflection, refl_stats = reflect_on_failure(candidate, feedback)
        total_llm_calls += refl_stats["llm_calls"]
        total_input_tokens += refl_stats["input_tokens"]
        total_output_tokens += refl_stats["output_tokens"]
        total_latency += refl_stats["latency_seconds"]
        child.reflection = reflection
        reflections.append(reflection)
        entry["reflection"] = reflection
        trace.append(entry)

    final = best_passing or max(root.children, key=lambda c: c.mean_value, default=None)

    return {
        "selected_candidate": final.candidate if final else None,
        "selected_feedback": final.feedback if final else None,
        "trace": trace,
        "llm_calls": total_llm_calls,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "latency_seconds": total_latency,
    }


if __name__ == "__main__":
    # Same eligible-crew shape as tree_of_thoughts.py's demo, so the two
    # are directly comparable for the "grounded vs ungrounded" contrast
    # planning_eval/run_eval.py measures. Run this on 2026-08-02 (the
    # seed data's date) to see crew_id=1 (13.0h duty, near the 14h limit)
    # get grounded-rejected even if the LLM's own free-text reasoning
    # about it sounded confident -- that's the exact failure case an
    # ungrounded self-score (ToT) can miss but a real DB query cannot.
    eligible_crew = [
        {"crew_id": 1, "full_name": "Capt. Karim Mostafa", "role": "pilot", "base_airport": "CAI"},
        {"crew_id": 2, "full_name": "Capt. Laila Hassan", "role": "co_pilot", "base_airport": "CAI"},
        {"crew_id": 3, "full_name": "Nourhan Fathy", "role": "flight_attendant", "base_airport": "CAI"},
    ]

    outcome = run_lats("BH202", eligible_crew, max_iterations=4)

    print("=== LATS: grounded reserve-crew search for BH202 ===")
    print(f"LLM calls: {outcome['llm_calls']}, tokens: in={outcome['input_tokens']} "
          f"out={outcome['output_tokens']}, latency={outcome['latency_seconds']:.2f}s\n")

    for entry in outcome["trace"]:
        print(f"  iter={entry['iteration']} candidate={entry['candidate']} "
              f"passed={entry['passed']} score={entry['score']}")
        print(f"    grounded detail: {entry['detail']}")
        if "reflection" in entry:
            print(f"    reflection carried forward: {entry['reflection']}")

    print(f"\nFinal selected candidate: {outcome['selected_candidate']}")
    if outcome["selected_feedback"]:
        print(f"Grounded feedback: {outcome['selected_feedback'].detail}")