"""
Failure-ticket helpers for unplanned mid-node errors.

HITL = expected pause for an admin decision.
Failure ticket = unplanned error (bad payload, tool failure, etc.).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from mcp_server.dbase import get_connection
from state_graph.models import RunStatus, WorkflowState


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def create_failure_ticket(
    state: WorkflowState,
    failed_node: str,
    error_type: str,
    error_message: str,
) -> str:
    """
    Persist an open failure ticket and mark the workflow as failed.

    The run can later be resumed from its last checkpoint after an admin
    resolves the ticket through the platform.
    """
    ticket_id = str(uuid4())

    state.status = RunStatus.FAILED
    state.last_error = error_message
    state.waiting_for = None

    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO failure_tickets (
                ticket_id,
                run_id,
                failed_node,
                status,
                error_type,
                error_message,
                state_json,
                created_at
            )
            VALUES (%s, %s, %s, 'open', %s, %s, %s, %s)
            """,
            (
                ticket_id,
                state.run_id,
                failed_node,
                error_type,
                error_message,
                json.dumps(state.to_dict(), ensure_ascii=False),
                _utc_now(),
            ),
        )

        cursor.execute(
            """
            UPDATE workflow_runs
            SET
                status = 'failed',
                current_node = %s,
                state_json = %s,
                failure_count = failure_count + 1
            WHERE run_id = %s
            """,
            (
                state.current_node,
                json.dumps(state.to_dict(), ensure_ascii=False),
                state.run_id,
            ),
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()

    state.data["failure_ticket_id"] = ticket_id
    return ticket_id