"""
Human-in-the-Loop persistence helpers.

This module creates real admin tasks in MySQL.
It does not auto-approve anything.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from mcp_server.dbase import get_connection
from state_graph.models import RunStatus, WorkflowState


def _utc_now() -> str:
    """Return a MySQL-friendly UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def create_admin_task(
    state: WorkflowState,
    task_type: str,
    requested_by: str,
    request_message: str,
    request_payload: dict[str, Any] | None = None,
) -> str:
    """
    Create a pending admin task linked to one workflow run.

    The graph will pause after this function, and it can only continue
    after an admin resolves the task through the platform.
    """
    task_id = str(uuid4())

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO admin_tasks (
                task_id,
                run_id,
                task_type,
                status,
                requested_by,
                request_message,
                request_payload,
                created_at
            )
            VALUES (%s, %s, %s, 'pending', %s, %s, %s, %s)
            """,
            (
                task_id,
                state.run_id,
                task_type,
                requested_by,
                request_message,
                json.dumps(request_payload or {}, ensure_ascii=False),
                _utc_now(),
            ),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()

    state.admin_task_id = task_id
    state.status = RunStatus.WAITING_ADMIN
    state.waiting_for = "admin_decision"

    return task_id


def get_admin_task(task_id: str) -> dict[str, Any]:
    """Return one admin task so the platform can display it."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT *
            FROM admin_tasks
            WHERE task_id = %s
            """,
            (task_id,),
        )

        task = cursor.fetchone()

        if task is None:
            raise LookupError(f"Admin task '{task_id}' was not found.")

        return task

    finally:
        cursor.close()
        conn.close()


def resolve_admin_task(
    task_id: str,
    decision: str,
    decided_by: str,
    decision_comment: str = "",
    decision_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Resolve a pending HITL task.

    decision must be either 'approved' or 'rejected'.
    The platform will call this function after the admin takes an action.
    """
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be either 'approved' or 'rejected'.")

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT task_id, run_id, status
            FROM admin_tasks
            WHERE task_id = %s
            """,
            (task_id,),
        )

        task = cursor.fetchone()

        if task is None:
            raise LookupError(f"Admin task '{task_id}' was not found.")

        if task["status"] != "pending":
            raise ValueError(
                f"Admin task '{task_id}' is already '{task['status']}' "
                "and cannot be resolved again."
            )

        cursor.execute(
            """
            UPDATE admin_tasks
            SET
                status = %s,
                decision_by = %s,
                decision_comment = %s,
                decision_payload = %s,
                resolved_at = %s
            WHERE task_id = %s
            """,
            (
                decision,
                decided_by,
                decision_comment,
                json.dumps(decision_payload or {}, ensure_ascii=False),
                _utc_now(),
                task_id,
            ),
        )

        conn.commit()

        return {
            "task_id": task_id,
            "run_id": task["run_id"],
            "decision": decision,
            "decided_by": decided_by,
            "decision_comment": decision_comment,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()