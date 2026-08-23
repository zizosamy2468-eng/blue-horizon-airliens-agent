"""
MySQL persistence layer for durable workflow state.

Every meaningful workflow transition is saved here as a checkpoint.
"""

from __future__ import annotations

import json
from typing import Any

from mcp_server.dbase import get_connection
from state_graph.models import WorkflowState, utc_now


def _encode_json(value: dict[str, Any] | list[dict[str, Any]]) -> str:
    """Convert Python data to JSON text accepted by MySQL."""
    return json.dumps(value, ensure_ascii=False)


def _decode_json(value: Any) -> dict[str, Any]:
    """Handle JSON returned by MySQL as either text or a Python dictionary."""
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        return json.loads(value)

    raise TypeError(f"Expected JSON object from MySQL, received: {type(value).__name__}")


def create_run(state: WorkflowState) -> None:
    """
    Create a new workflow run and its first checkpoint.

    This must be called exactly once for every new workflow.
    """
    if state.checkpoint_number != 0:
        raise ValueError("A new workflow state must start with checkpoint_number = 0.")

    state.checkpoint_number = 1
    state.updated_at = utc_now()

    state.transition_history.append(
        {
            "from_node": None,
            "to_node": state.current_node,
            "transition_name": "run_created",
            "status": state.status.value,
            "at": state.updated_at,
        }
    )

    conn = get_connection()
    cursor = conn.cursor()

    try:
        state_json = _encode_json(state.to_dict())
        context_json = _encode_json(state.context)

        cursor.execute(
            """
            INSERT INTO workflow_runs (
                run_id,
                workflow_type,
                flight_number,
                status,
                current_node,
                state_json,
                context_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                state.run_id,
                state.workflow_type,
                state.flight_number,
                state.status.value,
                state.current_node,
                state_json,
                context_json,
            ),
        )

        cursor.execute(
            """
            INSERT INTO workflow_checkpoints (
                run_id,
                checkpoint_number,
                node_name,
                transition_name,
                state_json
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                state.run_id,
                state.checkpoint_number,
                state.current_node,
                "run_created",
                state_json,
            ),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


def save_checkpoint(state: WorkflowState, transition_name: str) -> None:
    """
    Save the current state after a meaningful transition.

    The workflow row always contains the latest state.
    The checkpoints table keeps the full history.
    """
    state.checkpoint_number += 1
    state.updated_at = utc_now()

    conn = get_connection()
    cursor = conn.cursor()

    try:
        state_json = _encode_json(state.to_dict())
        context_json = _encode_json(state.context)

        cursor.execute(
            """
            UPDATE workflow_runs
            SET
                status = %s,
                current_node = %s,
                state_json = %s,
                context_json = %s
            WHERE run_id = %s
            """,
            (
                state.status.value,
                state.current_node,
                state_json,
                context_json,
                state.run_id,
            ),
        )

        if cursor.rowcount != 1:
            raise LookupError(f"Workflow run '{state.run_id}' was not found.")

        cursor.execute(
            """
            INSERT INTO workflow_checkpoints (
                run_id,
                checkpoint_number,
                node_name,
                transition_name,
                state_json
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                state.run_id,
                state.checkpoint_number,
                state.current_node,
                transition_name,
                state_json,
            ),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


def load_run_state(run_id: str) -> WorkflowState:
    """
    Load the latest durable state for a workflow.

    This function is what makes crash-and-resume possible.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT state_json
            FROM workflow_runs
            WHERE run_id = %s
            """,
            (run_id,),
        )

        row = cursor.fetchone()

        if row is None:
            raise LookupError(f"Workflow run '{run_id}' was not found.")

        payload = _decode_json(row["state_json"])
        return WorkflowState.from_dict(payload)

    finally:
        cursor.close()
        conn.close()


def get_checkpoint_count(run_id: str) -> int:
    """Return how many durable checkpoints a workflow has."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM workflow_checkpoints
            WHERE run_id = %s
            """,
            (run_id,),
        )

        row = cursor.fetchone()
        return int(row[0])

    finally:
        cursor.close()
        conn.close()

def list_runs(limit: int = 50) -> list[dict]:
    """
    Return recent workflow runs for the platform dashboard.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                run_id,
                workflow_type,
                flight_number,
                status,
                current_node,
                created_at,
                updated_at
            FROM workflow_runs
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall() or []
        # Make timestamps JSON-friendly
        for row in rows:
            for key in ("created_at", "updated_at"):
                if row.get(key) is not None:
                    row[key] = str(row[key])
        return rows
    finally:
        cursor.close()
        conn.close()