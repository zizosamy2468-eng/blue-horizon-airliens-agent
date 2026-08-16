# planning/dag.py
#
# DAG CONSTRUCTION + ACYCLICITY concern.
#
# Both decomposition methods (decomposition.py = decomposition-first,
# dynamic_decomposition.py = dynamic/interleaved) build their sub-task
# graph through THIS file's TaskDAG class, so cycle-checking only lives
# in one place instead of being re-implemented per method.
#
# A node is one sub-task: "find affected passengers", "rank by loyalty
# tier", "propose reserve crew", etc. An edge A -> B means "B depends on
# A's result" (B cannot run until A has produced output). Acyclicity is
# enforced AT INSERTION TIME, not checked afterward as an audit step --
# add_edge() raises immediately if the new edge would create a cycle, so
# a broken plan can never even be constructed, let alone executed and
# deadlock.

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Status = Literal["pending", "running", "done", "failed"]


class CycleError(Exception):
    """Raised the instant an edge would create a cycle. This is the
    'enforce acyclicity at construction time' requirement -- a plan that
    can deadlock is rejected before it ever exists, not caught later."""
    pass


@dataclass
class TaskNode:
    task_id: str
    description: str
    # Which domain action / planning method resolves this node -- filled
    # in by router.py, not by this file (dag.py only owns structure).
    assigned_action: str | None = None
    status: Status = "pending"
    result: Any = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TaskDAG:
    """
    A minimal, dependency-tracked DAG of sub-tasks for one top-level
    request (e.g. "resolve disruption for BH202").

    Used by BOTH decomposition methods:
      - decomposition.py builds the whole DAG in one shot, then this
        class's topological_order() drives execution.
      - dynamic_decomposition.py adds ONE node at a time (usually with
        edges only from already-completed nodes), so the DAG is built
        incrementally as the request unfolds.
    """

    def __init__(self, request_description: str):
        self.request_description = request_description
        self.nodes: dict[str, TaskNode] = {}
        self.edges: dict[str, set[str]] = {}   # node_id -> set of node_ids it depends on
        self.reverse_edges: dict[str, set[str]] = {}  # node_id -> set of dependents

    # -----------------------------------------------------------
    # Construction
    # -----------------------------------------------------------
    def add_node(self, task_id: str, description: str, assigned_action: str | None = None) -> TaskNode:
        if task_id in self.nodes:
            raise ValueError(f"Task id already exists in this DAG: {task_id}")
        node = TaskNode(task_id=task_id, description=description, assigned_action=assigned_action)
        self.nodes[task_id] = node
        self.edges[task_id] = set()
        self.reverse_edges[task_id] = set()
        return node

    def add_edge(self, from_id: str, to_id: str) -> None:
        """from_id -> to_id means to_id depends on from_id (to_id runs after)."""
        if from_id not in self.nodes or to_id not in self.nodes:
            raise KeyError(f"Both nodes must exist before adding an edge: {from_id} -> {to_id}")

        # Provisionally add, then check reachability from to_id back to
        # from_id -- if to_id can already reach from_id, this edge would
        # close a cycle. Roll back immediately if so.
        self.edges[to_id].add(from_id)
        self.reverse_edges[from_id].add(to_id)

        if self._creates_cycle():
            self.edges[to_id].discard(from_id)
            self.reverse_edges[from_id].discard(to_id)
            raise CycleError(
                f"Adding edge {from_id} -> {to_id} would create a cycle in the DAG "
                f"for request: {self.request_description!r}. Rejected at construction time."
            )

    def _creates_cycle(self) -> bool:
        """Standard DFS three-color cycle check over the whole graph.
        Run after every edge insertion -- the graph is small (a handful
        of sub-tasks per request), so re-checking the whole thing each
        time is cheap and simplest to reason about correctly."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {node_id: WHITE for node_id in self.nodes}

        def visit(node_id: str) -> bool:
            color[node_id] = GRAY
            for dep in self.edges[node_id]:
                if color[dep] == GRAY:
                    return True
                if color[dep] == WHITE and visit(dep):
                    return True
            color[node_id] = BLACK
            return False

        for node_id in self.nodes:
            if color[node_id] == WHITE:
                if visit(node_id):
                    return True
        return False

    # -----------------------------------------------------------
    # Execution ordering
    # -----------------------------------------------------------
    def topological_order(self) -> list[str]:
        """Kahn's algorithm. Used by decomposition-first, which needs the
        FULL order up front before executing anything."""
        in_degree = {node_id: len(deps) for node_id, deps in self.edges.items()}
        queue = [node_id for node_id, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            queue.sort()  # deterministic tie-break, easier to reproduce in eval runs
            current = queue.pop(0)
            order.append(current)
            for dependent in self.reverse_edges[current]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(self.nodes):
            # Should be unreachable given add_edge's cycle guard, but kept
            # as a hard assertion since a silent partial order would be
            # far worse than a loud crash here.
            raise CycleError("Topological sort could not order all nodes -- cycle slipped through.")

        return order

    def ready_nodes(self) -> list[str]:
        """Nodes whose dependencies are all 'done' and are themselves
        still 'pending'. Used by dynamic_decomposition.py, which only
        ever needs to know 'what could run right now', not the whole order."""
        ready = []
        for node_id, node in self.nodes.items():
            if node.status != "pending":
                continue
            deps = self.edges[node_id]
            if all(self.nodes[d].status == "done" for d in deps):
                ready.append(node_id)
        return sorted(ready)

    def is_complete(self) -> bool:
        return all(n.status in ("done", "failed") for n in self.nodes.values())

    # -----------------------------------------------------------
    # Inspection / evidence
    # -----------------------------------------------------------
    def snapshot(self) -> dict:
        """Used for the artifacts/ trace -- a JSON-serializable view of
        the DAG's current shape and state."""
        return {
            "request": self.request_description,
            "nodes": {
                node_id: {
                    "description": n.description,
                    "assigned_action": n.assigned_action,
                    "status": n.status,
                    "depends_on": sorted(self.edges[node_id]),
                }
                for node_id, n in self.nodes.items()
            },
        }


if __name__ == "__main__":
    # Smoke test: build a small disruption-response DAG by hand and
    # confirm both the cycle guard and topological_order() behave.
    dag = TaskDAG("resolve disruption for BH202")

    dag.add_node("find_affected", "Find all confirmed bookings on BH202")
    dag.add_node("rank_priority", "Rank affected passengers by loyalty tier and fare class")
    dag.add_node("find_candidates", "Find candidate replacement flights CAI->LHR")
    dag.add_node("propose_rebooking", "Propose which passenger goes on which replacement flight")
    dag.add_node("check_crew", "Check whether reserve crew is needed and within duty hours")
    dag.add_node("draft_notice", "Draft the passenger disruption notice")

    dag.add_edge("find_affected", "rank_priority")
    dag.add_edge("rank_priority", "propose_rebooking")
    dag.add_edge("find_candidates", "propose_rebooking")
    dag.add_edge("propose_rebooking", "check_crew")
    dag.add_edge("find_affected", "draft_notice")

    print("Topological order:", dag.topological_order())

    print("\nAttempting to introduce a cycle (propose_rebooking -> find_affected)...")
    try:
        dag.add_edge("propose_rebooking", "find_affected")
        print("ERROR: cycle was not caught!")
    except CycleError as e:
        print(f"Correctly rejected: {e}")

    print("\nReady nodes before anything runs:", dag.ready_nodes())
    dag.nodes["find_affected"].status = "done"
    print("Ready nodes after find_affected completes:", dag.ready_nodes())

    print("\nDAG snapshot:")
    import json
    print(json.dumps(dag.snapshot(), indent=2))