"""
Generic runner for persistent state-graph workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from state_graph.checkpoint_store import (
    create_run,
    load_run_state,
    save_checkpoint,
)
from state_graph.models import RunStatus, WorkflowState


@dataclass
class NodeResult:
    """
    The result returned by one graph node.

    next_node:
        The node that should run next.

    transition_name:
        A readable name saved in the checkpoint history.

    status:
        The workflow status after the transition.

    waiting_for:
        Used only when the workflow pauses for an external event or admin.
    """

    next_node: str
    transition_name: str
    status: RunStatus = RunStatus.RUNNING
    waiting_for: str | None = None


NodeHandler = Callable[[WorkflowState], NodeResult]


class StateGraphRunner:
    """
    Runs a workflow until it completes, fails, or reaches a waiting state.
    """

    def __init__(self, handlers: dict[str, NodeHandler]) -> None:
        self.handlers = handlers

    def start(self, state: WorkflowState) -> WorkflowState:
        """
        Save a new run first, then execute it until it pauses or finishes.
        """
        create_run(state)
        return self.run_until_pause(state)

    def resume(
        self,
        run_id: str,
        data_updates: dict | None = None,
        transition_name: str = "workflow_resumed",
    ) -> WorkflowState:
        """
        Reload the latest durable state, add new external/admin data,
        then continue execution from the saved current node.
        """
        state = load_run_state(run_id)

        if state.status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
            raise ValueError(
                f"Workflow '{run_id}' cannot resume because its status is "
                f"'{state.status.value}'."
            )

        if data_updates:
            state.data.update(data_updates)

        state.status = RunStatus.RUNNING
        state.waiting_for = None

        save_checkpoint(state, transition_name)

        return self.run_until_pause(state)

    def run_until_pause(self, state: WorkflowState) -> WorkflowState:
        """
        Execute nodes while the workflow is running.

        The loop stops immediately when a node changes the status to:
        waiting_external, waiting_admin, failed, completed, or cancelled.
        """
        while state.status == RunStatus.RUNNING:
            handler = self.handlers.get(state.current_node)

            if handler is None:
                raise KeyError(
                    f"No handler was registered for node '{state.current_node}'."
                )

            result = handler(state)

            state.move_to(
                next_node=result.next_node,
                transition_name=result.transition_name,
                status=result.status,
            )

            state.waiting_for = result.waiting_for

            save_checkpoint(state, result.transition_name)

        return state