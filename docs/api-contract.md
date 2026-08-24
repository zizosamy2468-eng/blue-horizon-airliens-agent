# API Contract

Base URL: `http://127.0.0.1:5050`. All request and response bodies are JSON. Error responses use `{ "error": "message" }`.

## Health and agent runs

| Method and path | Request | Success response |
| --- | --- | --- |
| `GET /api/health` | none | `{ "status": "ok", "service": "blue-horizon-platform" }` |
| `GET /api/agents` | none | Supported agent types and recent durable runs |
| `POST /api/agents/start` | `agent_type` plus graph input | A newly created run (201) |
| `GET /api/agents/runs/{run_id}` | none | Full persisted workflow state |
| `POST /api/agents/runs/{run_id}/resume` | `data_updates` and optional `transition_name` | The state after the runner continues to its next pause or terminal status |

Accepted `agent_type` values are `maintenance_release`, `compensation_appeal`, and `safety_incident`; each also has the short alias used by the route service.

### Start examples

```json
{ "agent_type": "maintenance_release", "flight_number": "BH202", "requested_by": "platform" }
```

```json
{
  "agent_type": "compensation_appeal",
  "flight_number": "BH202",
  "passenger_email": "passenger@example.com",
  "appeal_reason": "Delay impact was not fully compensated",
  "requested_amount": 750.0,
  "currency": "USD"
}
```

```json
{ "agent_type": "safety_incident", "flight_number": "BH202", "incident_type": "turbulence", "severity": "high" }
```

## Administrator tasks and tickets

| Method and path | Request | Purpose |
| --- | --- | --- |
| `GET /api/admin/tasks?status=pending` | none | List HITL tasks, optionally filtered by status. |
| `POST /api/admin/tasks/{task_id}/decide` | `decision`, `decided_by`, optional `comment`, `payload` | Record an administrator decision and request a graph resume. |
| `GET /api/admin/tickets?status=open` | none | List failure tickets, optionally filtered by status. |
| `POST /api/admin/tickets/{ticket_id}/resolve` | `resolved_by`, optional `resolution_notes`, `resume`, `data_updates` | Resolve a ticket and, by default, request resumption from the failed checkpoint. |
| `GET /api/admin/health` | none | Confirm the administrator API blueprint is registered. |

For a compensation decision, `decision` must be `approved` or `rejected`. Safety review flows may use `approved`, `changes_requested`, `rejected`, or `revise` at the route boundary. The client must supply only data relevant to the node that is waiting; injecting an event into a non-waiting run is invalid.

## Runtime tool permissions

`state_graph.tool_registry` provides the internal runtime contract:

```python
set_tool_permission(agent_name, tool_name, is_enabled, updated_by)
require_enabled_tool(agent_name, tool_name)
```

Permissions are stored in `agent_tool_permissions` and read from MySQL at execution time. The current HTTP routes above are the public API surface present in this archive; any additional administrator UI control must call this runtime registry rather than merely changing a front-end toggle.
