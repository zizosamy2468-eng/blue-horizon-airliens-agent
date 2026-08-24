# Blue Horizon: Persistent Airline Operations Agents

Blue Horizon extends the team’s existing MCP, memory/RAG, and planning work with three durable state-graph workflows and a small web platform for users and administrators.

## What is included

| Workflow | Owner | Why it needs persistent state | Techniques used |
| --- | --- | --- | --- |
| Maintenance Release Coordinator | Mostafa | It waits for a maintenance report, requires an Operations Manager decision before release, and must survive a restart. | RAG policy retrieval; task decomposition for the release plan |
| Compensation Appeal Agent | Zizo | It waits for customer documents and payment results, may require approval above the cap, and can loop through revised appeal strategies. | RAG policy retrieval; Tree-of-Thoughts strategy comparison; constrained payment action |
| Safety Incident Agent | Adel | It waits for ground/crew evidence and authority acknowledgement, supports a review/revision cycle, and escalates incomplete or conflicting evidence. | RAG safety-policy retrieval; LATS reporting-path exploration |

The durable core is in `state_graph/`: `WorkflowState` holds the complete serializable run state, `checkpoint_store.py` writes the latest state and a historical checkpoint after every transition, and `StateGraphRunner.resume()` reloads that saved state rather than starting again.

## HITL, tickets, and recovery

- A HITL pause is expected: `state_graph/hitl.py` creates an `admin_tasks` row and the run stops with `waiting_admin` until an administrator decision is supplied.
- A failure ticket is unexpected: `state_graph/tickets.py` records an open `failure_tickets` row and the run is marked `failed`. It is deliberately separate from the HITL path.
- A resume reloads the latest `workflow_runs.state_json`, merges only the new external/admin data, checkpoints the resume event, and continues from `current_node`.

The compensation auto-approval cap is USD 500.00. A request above that value pauses for an administrator; it is never auto-approved by the graph.

## Project layout

```text
state_graph/       Durable runner, checkpoints, HITL, tickets, and three graphs
database/          MySQL schema for runs, checkpoints, tasks, tickets, and domain data
mcp_server/        Existing MCP server and read/write/memory tools reused by the graphs
rag/               Existing policy corpus and vector-store implementation
planning/          Existing planning/decomposition implementation used by maintenance
web_platform/      Flask API plus the user/admin frontend
docs/              Architecture, API, graph, and demo material
tests/             Workflow and presentation verification scripts
```

## Setup and run

Use Python 3.11+ and a MySQL database named `blue_horizon_db`.

1. Create a local `mcp_server/.env` with `DB_HOST`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME`. Keep it local; do not commit it.
2. Apply the SQL files in `database/` in numeric order, then apply the existing base schema required by the earlier labs.
3. Install the project dependencies used by your environment, including `mysql-connector-python`, `python-dotenv`, `flask`, and `flask-cors`.
4. From the project root, start the API with `python -m web_platform.backend.app`.
5. Serve `web_platform/frontend/` (for example, `python -m http.server 8080` from that directory) and open it in a browser.

Run the fast, database-free verification:

```bash
python -m unittest tests/test_end_to_end_demo.py -v
```

For the live presentation procedure, follow [docs/demo-checklist.md](docs/demo-checklist.md). The manual scripts in `tests/` demonstrate the MySQL-backed checkpoint, HITL, and ticket paths once the local database is configured.

## Prior-work integration

The final layer reuses the existing MCP tools, RAG corpus/vector store, and planning modules instead of creating parallel replacements. The supplied archive did not include earlier grading feedback, so this repository does not claim unverified prior-lab fixes. The demo checklist is the verification record for the reusable integration points and for the current final-project concerns.

## Safety

No credentials belong in source control. Use a local `.env` file only, and verify it is ignored before pushing. The delivery archive deliberately excludes local `.env` files.
