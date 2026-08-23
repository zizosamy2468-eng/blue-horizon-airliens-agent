"""
Runtime tool-permission registry.

The admin platform changes permissions in MySQL.
Agents read these permissions at runtime before using a tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mcp_server.dbase import get_connection


@dataclass(frozen=True)
class ToolPermission:
    """One tool permission belonging to one agent."""

    agent_name: str
    tool_name: str
    is_enabled: bool


def _utc_now() -> str:
    """Return a MySQL-friendly UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def list_agent_tools(agent_name: str) -> list[ToolPermission]:
    """
    Return every configured tool for one agent.

    The platform can use this to show the admin which tools are enabled.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT agent_name, tool_name, is_enabled
            FROM agent_tool_permissions
            WHERE agent_name = %s
            ORDER BY tool_name
            """,
            (agent_name,),
        )

        rows = cursor.fetchall()

        return [
            ToolPermission(
                agent_name=row["agent_name"],
                tool_name=row["tool_name"],
                is_enabled=bool(row["is_enabled"]),
            )
            for row in rows
        ]

    finally:
        cursor.close()
        conn.close()


def list_enabled_tools(agent_name: str) -> set[str]:
    """
    Return only currently enabled tools for one agent.

    This reads MySQL every time, so an admin change takes effect
    without redeploying or restarting the server.
    """
    permissions = list_agent_tools(agent_name)

    return {
        permission.tool_name
        for permission in permissions
        if permission.is_enabled
    }


def is_tool_enabled(agent_name: str, tool_name: str) -> bool:
    """
    Return True only if this specific tool is enabled for this agent.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT is_enabled
            FROM agent_tool_permissions
            WHERE agent_name = %s AND tool_name = %s
            """,
            (agent_name, tool_name),
        )

        row = cursor.fetchone()

        return row is not None and bool(row["is_enabled"])

    finally:
        cursor.close()
        conn.close()


def set_tool_permission(
    agent_name: str,
    tool_name: str,
    is_enabled: bool,
    updated_by: str,
) -> None:
    """
    Enable or disable one tool for one agent.

    Adel's admin platform will call this function later.
    """
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO agent_tool_permissions (
                agent_name,
                tool_name,
                is_enabled,
                updated_by,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                is_enabled = VALUES(is_enabled),
                updated_by = VALUES(updated_by),
                updated_at = VALUES(updated_at)
            """,
            (
                agent_name,
                tool_name,
                int(is_enabled),
                updated_by,
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


def require_enabled_tool(agent_name: str, tool_name: str) -> None:
    """
    Raise an error if an agent tries to use a disabled tool.

    We will call this before any sensitive MCP tool execution.
    """
    if not is_tool_enabled(agent_name, tool_name):
        raise PermissionError(
            f"Tool '{tool_name}' is disabled for agent '{agent_name}'."
        )