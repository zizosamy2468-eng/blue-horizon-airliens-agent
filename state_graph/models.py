"""
Shared state models for every persistent Blue Horizon workflow.

A workflow state is stored in MySQL after every meaningful transition,
so a run can resume after a process restart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    """Return the current time in a JSON-safe UTC format."""
    return datetime.now(timezone.utc).isoformat()


class RunStatus(str, Enum):
    """Allowed values for workflow_runs.status in MySQL."""

    RUNNING = "running"
    WAITING_EXTERNAL = "waiting_external"
    WAITING_ADMIN = "waiting_admin"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class WorkflowState:
    """
    The complete durable state of one workflow run.

    data holds workflow-specific information.
    Example: maintenance report details, selected flight, or admin decision.
    """

    run_id: str
    workflow_type: str
    current_node: str
    status: RunStatus = RunStatus.RUNNING
    flight_number: str | None = None

    data: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    transition_history: list[dict[str, Any]] = field(default_factory=list)

    checkpoint_number: int = 0
    waiting_for: str | None = None
    admin_task_id: str | None = None
    last_error: str | None = None

    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        workflow_type: str,
        initial_node: str,
        flight_number: str | None = None,
        data: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> "WorkflowState":
        """Create a brand-new workflow with a unique run ID."""
        return cls(
            run_id=str(uuid4()),
            workflow_type=workflow_type,
            current_node=initial_node,
            flight_number=flight_number,
            data=data or {},
            context=context or {},
        )

    def move_to(
        self,
        next_node: str,
        transition_name: str,
        status: RunStatus | None = None,
    ) -> None:
        """
        Move the workflow to a new node and record why it moved.

        The runner will save a checkpoint immediately after this method.
        """
        previous_node = self.current_node

        self.current_node = next_node
        if status is not None:
            self.status = status

        self.waiting_for = None
        self.updated_at = utc_now()

        self.transition_history.append(
            {
                "from_node": previous_node,
                "to_node": next_node,
                "transition_name": transition_name,
                "status": self.status.value,
                "at": self.updated_at,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert the state to a JSON-safe dictionary for MySQL."""
        return {
            "run_id": self.run_id,
            "workflow_type": self.workflow_type,
            "current_node": self.current_node,
            "status": self.status.value,
            "flight_number": self.flight_number,
            "data": self.data,
            "context": self.context,
            "transition_history": self.transition_history,
            "checkpoint_number": self.checkpoint_number,
            "waiting_for": self.waiting_for,
            "admin_task_id": self.admin_task_id,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkflowState":
        """Rebuild a workflow state loaded from MySQL."""
        return cls(
            run_id=payload["run_id"],
            workflow_type=payload["workflow_type"],
            current_node=payload["current_node"],
            status=RunStatus(payload["status"]),
            flight_number=payload.get("flight_number"),
            data=payload.get("data", {}),
            context=payload.get("context", {}),
            transition_history=payload.get("transition_history", []),
            checkpoint_number=payload.get("checkpoint_number", 0),
            waiting_for=payload.get("waiting_for"),
            admin_task_id=payload.get("admin_task_id"),
            last_error=payload.get("last_error"),
            created_at=payload.get("created_at", utc_now()),
            updated_at=payload.get("updated_at", utc_now()),
        )