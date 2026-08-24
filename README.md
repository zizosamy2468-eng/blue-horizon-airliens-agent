# Blue Horizon Airlines — State Graph Agents & Platform

**Final Project** — built on top of the shared MCP Server, Memory & RAG, and Decomposition & Planning labs.
Team: **Mostafa** (foundation + Maintenance Release) · **Zizo** (Compensation Appeal + HITL/Ticket base) · **Adel** (Safety Incident + Platform)

---

## 1. What this project adds

Every agent built in the prior three labs assumed a run goes start → finish with nothing the agent can't quietly retry. That assumption breaks for real airline work: a maintenance report can take hours, a payment gateway answers asynchronously, an admin has to actually sign off before a large payout or a regulatory filing goes out.

This project adds:

1. **Three new state-graph agents** — persistent, resumable, checkpointed after every meaningful transition, capable of real cycles (not just a straight-line DAG).
2. **A real Human-in-the-Loop (HITL) path** — an explicit pause, a real `admin_tasks` row, resolved only through the platform.
3. **A real failure-ticket path** — distinct from HITL, opened only on genuine unplanned errors, resolvable and resumable from the exact checkpoint that failed.
4. **A working platform** — a website where an admin manages tools/RAG documents/HITL/tickets, and a user chats with any of the agents (state-graph agents **and** the earlier Memory/RAG and Planning agents).

---

## 2. The three state-graph problems

| Agent | Owner | Real wait | Real human decision | Real failure mode |
|---|---|---|---|---|
| **Maintenance Release Coordinator** | Mostafa | External maintenance report, sometimes hours | Operations-manager approval before the aircraft flies again | An invalid/incomplete report must not silently proceed |
| **Compensation Appeal Agent** | Zizo | Customer documents, then an async payment-gateway result | Admin approval once the appeal exceeds the auto-approve cap ($500) | Invalid documents or a gateway timeout — must not restart the whole appeal |
| **Safety Incident Agent** | Adel | Ground/crew report, then the regulator's acknowledgement | Safety Manager review before any report reaches a real authority | Missing/conflicting evidence, or an authority rejection |

None of these are a re-skin of the Planning Lab's scheduling problem or the Memory/RAG Lab's retrieval problem — each is a genuinely new agent scope with a real wait, a real branch outside the model's control, and a real cost to losing progress.

---

## 3. Two LLM-call additions per graph

| Agent | Addition 1 | Addition 2 | Why this pair |
|---|---|---|---|
| Maintenance Release | **RAG** — `retrieve_maintenance_policy` | **Task Decomposition** — `build_release_plan` | The release plan must follow real policy *and* land on one safe, ordered step list before anyone touches the aircraft |
| Compensation Appeal | **RAG** — `retrieve_compensation_policy` | **Tree of Thoughts** — `compare_appeal_strategies` | Several valid arguments exist for one appeal; picking badly wastes a real, limited appeal window |
| Safety Incident | **RAG** — `retrieve_safety_policy` | **LATS-style search** — `explore_reporting_paths` | Choosing the wrong regulatory reporting path has real consequences — worth exploring candidates, not guessing once |

All three reuse **one shared LLM client** (`planning/llm_client.py`) — no agent stood up its own Gemini client.

---

## 4. Repository structure (new for this project)

```
database/
  002_state_graph_core.sql        # workflow_runs, workflow_checkpoints, admin_tasks,
                                   # failure_tickets, agent_tool_permissions
  003_compensation_appeals.sql    # compensation_appeals, compensation_appeal_revisions
  004_safety_incidents.sql        # safety_incidents, safety_authority_submissions

state_graph/
  __init__.py
  models.py            # WorkflowState, RunStatus, move_to()/to_dict()/from_dict()
  checkpoint_store.py  # create_run / save_checkpoint / load_run_state
  runner.py            # StateGraphRunner — run_until_pause / start / resume
  tool_registry.py     # require_enabled_tool() — reads agent_tool_permissions live
  hitl.py              # create_admin_task / resolve_admin_task / list_admin_tasks
  tickets.py           # create_failure_ticket / list_failure_tickets
  external_events.py   # inject_customer_documents / inject_admin_decision /
                        # inject_payment_result / inject_generic_resume

  maintenance/          # Mostafa
    graph.py  nodes.py
  compensation/          # Zizo
    __init__.py  graph.py  nodes.py  appeal_strategies.py
  safety/                 # Adel
    __init__.py  graph.py  nodes.py  search_strategy.py

mcp_server/
  Server.py             # wires all three *_workflow tools + existing tools
  tools_read.py         # + get_flight_status_record()
  tools_write.py        # + mark_flight_ready(), submit_compensation_payment()
  memory_tools.py       # unchanged core (search_policy_manual reused by all 3 graphs)

rag/
  policy_corpus.py      # + IROPS-SAFE-1..3 (safety category)
  vector_store.py       # rebuild=True re-indexes after any corpus change

web_platform/            # Adel
  backend/  app.py  routes_agents.py  routes_admin.py  services.py
  frontend/ index.html  app.js  styles.css

tests/
  test_checkpoint_resume.py     # Mostafa — crash & resume, Maintenance
  test_compensation_hitl.py     # Zizo — HITL pause → admin decision → resume
  test_ticket_recovery.py       # Zizo — Failure Ticket → resolve → resume
  test_platform_admin_actions.py # Adel — search-strategy ranking, node logic, file presence
```

---

## 5. Setup

### 5.1 Environment variables

`mcp_server/.env`
```
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=<your local password>
DB_NAME=blue_horizon_db
```

A `.env` reachable by `planning/llm_client.py` (project root is simplest — `python-dotenv` searches upward from the current working directory):
```
GOOGLE_API_KEY=<your Google AI Studio key>
```

Both `.env` files must stay in `.gitignore` — never commit a real key or DB password.

### 5.2 Database migrations (run in this order)

```bash
mysql -u root -p blue_horizon_db < database/002_state_graph_core.sql
mysql -u root -p blue_horizon_db < database/003_compensation_appeals.sql
mysql -u root -p blue_horizon_db < database/004_safety_incidents.sql
```

### 5.3 Python dependencies

```bash
pip install mysql-connector-python python-dotenv google-genai rank_bm25 chromadb flask flask-cors
```

### 5.4 Rebuild the RAG index after the safety policy sections were added

```bash
python -c "from rag.vector_store import PolicyVectorStore; PolicyVectorStore(rebuild=True)"
```

---

## 6. Running it

**MCP server:**
```bash
cd mcp_server
python Server.py            # Streamable HTTP by default
python Server.py stdio      # or stdio, for local MCP Inspector debugging
```

**Platform backend (Adel):**
```bash
PYTHONPATH=. python -m web_platform.backend.app     # http://127.0.0.1:5050
```

**Platform frontend:** open `web_platform/frontend/index.html` (or serve it statically); it talks to the backend at the URL stored under `bh_api_base` in `localStorage` (defaults to `http://127.0.0.1:5050`).

---

## 7. HITL vs. Failure Tickets — the distinction that has to be visible in code

| | HITL | Failure Ticket |
|---|---|---|
| **When it fires** | A condition the agent is *not allowed* to decide alone (an amount above a threshold, a policy-sensitive action) | An *unplanned* error — a tool call failed, a schema validation failed, an external response couldn't be parsed |
| **Where it's created** | `state_graph/hitl.py :: create_admin_task()` | `state_graph/tickets.py :: create_failure_ticket()` |
| **Table** | `admin_tasks` (`status`: pending → approved/rejected) | `failure_tickets` (`status`: open → investigating → resolved/closed) |
| **Resume path** | `external_events.inject_admin_decision()` — resolves the task **and** resumes the run with the real decision | Admin resolves the ticket, then resumes the SAME run from the SAME node with the actual fix applied |
| **Concrete example (Compensation Appeal)** | Requested amount `$750 > $500` cap → `awaiting_admin_approval` | Documents missing `file_type`, or the mock gateway timing out on amount `9999.99` → `validate_documents` / `submit_payment` |

Every graph in this repo implements both paths through the exact same two shared modules — nobody re-invented HITL or tickets per agent.

---

## 8. Checkpointing — proof, not a log file

`StateGraphRunner.run_until_pause()` calls `checkpoint_store.save_checkpoint()` after **every** node transition, success or failure, not just at the end of a run. `checkpoint_number` only ever increases for a given `run_id`.

Verified with real terminal runs (see `demo_transcript_final.md` for the full transcripts):

| Test | Checkpoints before → after |
|---|---|
| HITL pause reached | 5 → 10 |
| HITL approved → resume | 10 → 13 |
| HITL rejected → looped back | 10 → 14 |
| Ticket (invalid documents) → resume | 8 → 13 |
| Ticket (gateway timeout) → resume | 13 → 15 |

Each resume picked up from the **exact node** the run had paused or failed on — never from `load_original_compensation` again.

---

## 9. The platform

**Admin surface**
- Enable/disable any MCP tool per agent — reads `agent_tool_permissions` live via `tool_registry.require_enabled_tool()`, no redeploy.
- Add/remove RAG policy sections — the next retrieval by any agent reflects the change.
- Resolve pending HITL tasks and open Failure Tickets, with the run resuming from the platform action.

**User surface**
- Switch between the Maintenance Release, Compensation Appeal, and Safety Incident agents (state-graph agents), plus the earlier Memory/RAG and Planning agents — one place, not three hardcoded chats.

---

## 10. What we corrected from the prior labs

- **MCP Server Lab** — tool visibility used to be registered once per session via `mcp.add_tool()`. It's now driven live by `agent_tool_permissions` + `tool_registry.require_enabled_tool()`, toggled from the admin panel without redeploying the server.
- **Memory & RAG Lab** — the policy corpus was extended with a `safety` category (`IROPS-SAFE-1..3`) and `search_policy_manual` is reused as-is by all three new graphs — no second RAG pipeline was built.
- **Decomposition & Planning Lab** — `planning/llm_client.py` is reused directly for every new LLM addition across all three graphs, instead of three separate clients.

---

## 11. Known issues / before submission

- Confirm the Safety Incident agent's `create_failure_ticket()` / `create_admin_task()` calls in `state_graph/safety/nodes.py` pass arguments matching the real signatures in `state_graph/tickets.py` / `state_graph/hitl.py` (`state=...`, not `run_id=...`) — this is what makes those rows land in the real `failure_tickets` / `admin_tasks` tables instead of an in-memory fallback.
- `record_appeal_event()` in `memory_tools.py` is defined but not yet called from any Compensation Appeal node — harmless, just unused.

---

## 12. Team & issue ownership

| Owner | Owns | Key files |
|---|---|---|
| Mostafa | Shared foundation + Maintenance Release Coordinator | `state_graph/models.py`, `checkpoint_store.py`, `runner.py`, `tool_registry.py`, `maintenance/` |
| Zizo | Compensation Appeal Agent + HITL/Ticket base | `state_graph/compensation/`, `hitl.py`, `tickets.py`, `external_events.py` |
| Adel | Safety Incident Agent + the full platform | `state_graph/safety/`, `web_platform/backend`, `web_platform/frontend` |

Every issue states the real constraint and acceptance criteria, one owner each, closed by a linked pull request.

See `architecture_final.md` for the full system diagram and `demo_transcript_final.md` for real, captured evidence of every concern above.
