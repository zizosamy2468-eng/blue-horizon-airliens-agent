# Blue Horizon Airlines — System Architecture

**Final Project · State Graph Agents + Platform**  
مصطفى فرج · عبدالعزيز سامي · مصطفى عادل

This document describes how the full system fits together: shared core, three state-graph agents, HITL/tickets, checkpointing, MCP server reuse, and the live platform.

---

## 1. High-level view

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PLATFORM (web)                                   │
│  Admin: tools · RAG docs · HITL tasks · failure tickets                  │
│  User:  agent switcher · start/resume runs · live run dashboard          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTP (Flask)
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STATE GRAPH LAYER                                     │
│  runner.py  ·  models.py  ·  checkpoint_store.py  ·  tool_registry.py   │
│  hitl.py    ·  tickets.py ·  external_events.py                          │
│                                                                          │
│  ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐        │
│  │ Maintenance      │ │ Compensation     │ │ Safety Incident  │        │
│  │ (مصطفى فرج)        │ │ Appeal (عبدالعزيز سامي)    │ │ (مصطفى عادل)           │        │
│  │ RAG + Decomp.    │ │ RAG + ToT        │ │ RAG + LATS       │        │
│  └────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘        │
└───────────┼─────────────────────┼─────────────────────┼──────────────────┘
            │                     │                     │
            ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP SERVER (existing)                            │
│  tools_read / tools_write · memory_tools · search_policy_manual          │
│  mark_flight_ready · submit_compensation_payment · agent_tool_permissions│
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    MySQL  ·  blue_horizon_db                             │
│  Domain tables (flights, passengers, compensation, …)                    │
│  workflow_runs · workflow_checkpoints                                    │
│  admin_tasks · failure_tickets · agent_tool_permissions                  │
│  maintenance_cases · compensation_appeals · safety_incidents · revisions │
└─────────────────────────────────────────────────────────────────────────┘
```

**Design rule:** one shared persistence / HITL / ticket / runner stack. Agents only add domain nodes and LLM calls — they do not re-implement the spine.

---

## 2. Repository layout (logical)

```
blu-hor/
├── mcp_server/                 # Existing MCP runtime (extended, not replaced)
│   ├── server.py
│   ├── dbase.py
│   ├── tools_read.py
│   ├── tools_write.py          # mark_flight_ready, submit_compensation_payment
│   ├── memory_tools.py         # search_policy_manual (RAG entry)
│   └── …
├── memory/ · rag/ · planning/  # Prior labs — reused by graph nodes
├── state_graph/                # NEW — shared durable workflow core
│   ├── models.py
│   ├── checkpoint_store.py
│   ├── runner.py
│   ├── tool_registry.py
│   ├── hitl.py
│   ├── tickets.py
│   ├── external_events.py
│   ├── maintenance/            # مصطفى فرج
│   │   ├── graph.py
│   │   └── nodes.py
│   ├── compensation/           # عبدالعزيز سامي
│   │   ├── graph.py
│   │   ├── nodes.py
│   │   └── appeal_strategies.py
│   └── safety/                 # مصطفى عادل
│       ├── graph.py
│       ├── nodes.py
│       └── search_strategy.py
├── database/
│   ├── 002_state_graph_core.sql
│   ├── 003_compensation_appeals.sql
│   ├── 004_safety_incidents.sql
│   └── …
├── web_platform/               # مصطفى عادل — live UI against MCP + MySQL
│   ├── backend/                # Flask routes + services
│   └── frontend/               # dashboard, HITL, tickets, agent switch
└── tests/
    ├── test_checkpoint_resume.py
    ├── test_compensation_hitl.py
    ├── test_ticket_recovery.py
    └── test_end_to_end_demo.py
```

---

## 3. Shared core (`state_graph/`)

### 3.1 `models.py`

| Type | Role |
|------|------|
| `WorkflowState` | Full in-memory + serializable run state (`run_id`, `workflow_type`, `current_node`, `status`, `data`, `context`, `waiting_for`, `admin_task_id`, history) |
| `RunStatus` | `running` · `waiting_external` · `waiting_admin` · `failed` · `completed` |
| `NodeResult` | What a node returns: `next_node`, `transition_name`, `status`, optional `waiting_for` |

State is JSON-serialized into MySQL on every meaningful transition.

### 3.2 `checkpoint_store.py`

| Function | Role |
|----------|------|
| `create_run(state)` | Insert `workflow_runs` + first checkpoint |
| `save_checkpoint(state)` | Append `workflow_checkpoints` row; update `workflow_runs` latest snapshot |
| `load_run_state(run_id)` | Rebuild `WorkflowState` from DB (fresh process) |
| `get_checkpoint_count(run_id)` | Evidence for crash-and-resume demos |

**Invariant:** `checkpoint_number` only increases for a given `run_id`. Kill + restart never resets it.

### 3.3 `runner.py` — `StateGraphRunner`

```
start(state):
  create_run → loop: handlers[current_node](state) → apply NodeResult → save_checkpoint
  stop when status ∈ {WAITING_EXTERNAL, WAITING_ADMIN, FAILED, COMPLETED}

resume(run_id, data_updates, transition_name):
  load_run_state → merge data_updates → continue loop from current_node
```

Nodes never sleep. Real waits set `WAITING_*` and exit; external systems (or the platform) call `resume` later.

### 3.4 `tool_registry.py`

- Reads `agent_tool_permissions` (agent_name, tool_name, enabled).
- `require_enabled_tool(agent_name, tool_name)` raises if disabled.
- Admin panel toggles permissions → next tool call on the live MCP path respects the change (no redeploy).

### 3.5 `hitl.py` vs `tickets.py`

| | HITL (`admin_tasks`) | Failure ticket (`failure_tickets`) |
|--|----------------------|-------------------------------------|
| Meaning | Expected decision the agent must not make alone | Unplanned mid-node error |
| Run status | `waiting_admin` | `failed` |
| Resume | Only after platform action (`resolve_admin_task` + resume) | After cause is fixed + ticket resolved + resume from **same** node |
| Examples | Amount > $500; aircraft release; Safety Manager sign-off | Missing `file_type`; gateway timeout; conflicting evidence |

These are **two different tables and two different code paths** — not the same pause renamed.

### 3.6 `external_events.py`

Typed injectors used by tests and platform:

- `inject_customer_documents`
- `inject_admin_decision`
- `inject_payment_result`
- `inject_generic_resume` (ticket retry)

Each asserts the run is actually waiting for that event before merging data and calling `runner.resume`.

---

## 4. Agent graphs

### 4.1 Maintenance Release (مصطفى فرج)

```
inspect_flight
  → retrieve_maintenance_policy      [RAG · search_policy_manual]
  → build_release_plan               [constrained task decomposition]
  → awaiting_maintenance_report      [WAITING_EXTERNAL]
  → validate_maintenance_report      [invalid → Failure Ticket]
  → requires_operations_approval     [WAITING_ADMIN · HITL]
  → mark_flight_ready                [tools_write · require_enabled_tool]
  → completed
```

**State held (examples):** `flight_record`, `flight_status`, `maintenance_policy`, `release_plan`, `maintenance_report`, `operations_decision`, `operations_manager_id`.

**LLM pair:** RAG before planning; decomposition only allowed to emit constrained step names.

### 4.2 Compensation Appeal (عبدالعزيز سامي)

```
load_original_compensation
  → retrieve_compensation_policy     [RAG]
  → compare_appeal_strategies        [Tree of Thoughts]
  → await_customer_documents         [WAITING_EXTERNAL]
  → validate_documents               [invalid → Failure Ticket]
  → constrained_action               [amount > $500 → HITL]
  → submit_payment                   [gateway error → Failure Ticket]
  → await_payment_result             [WAITING_EXTERNAL]
        ├─ paid → completed
        └─ rejected → record revision → back to compare_appeal_strategies
                      (bounded by MAX_REVISION_ROUNDS)
```

**State held (examples):** `passenger_email`, `requested_amount`, `selected_strategy`, `customer_documents`, `admin_decision`, `payment_result`, `revision_count`, `rejected_strategy_names`.

**LLM pair:** RAG grounds ToT; ToT generates/scores strategies; HITL is a **numeric** comparison to the policy cap, not a model guess.

### 4.3 Safety Incident (مصطفى عادل)

```
collect_flight_and_crew_facts
  → retrieve_safety_policy           [RAG · IROPS-SAFE-*]
  → explore_reporting_paths          [LATS-style]
  → awaiting_ground_or_crew_report   [WAITING_EXTERNAL]
  → validate_evidence                [missing/conflict → Failure Ticket]
  → draft_regulatory_report
  → safety_manager_review            [WAITING_ADMIN · HITL]
        ├─ approved → submit_report
        └─ changes_requested → revise_report → safety_manager_review
  → awaiting_authority_acknowledgement [WAITING_EXTERNAL]
  → completed
```

**State held (examples):** `incident_type`, `severity`, `crew_facts`, `ground_report`, `draft_report`, `admin_decision`, `authority_ack`.

**LLM pair:** RAG for safety policy; LATS explores reporting paths before irreversible external filing.

---

## 5. Database (core additions)

```
workflow_runs              — latest state_json, status, current_node per run
workflow_checkpoints       — ordered history (run_id, checkpoint_number, state_json)
admin_tasks                — HITL queue (pending / resolved)
failure_tickets            — unplanned failures (open / resolved)
agent_tool_permissions     — live enable/disable per agent × tool

maintenance_cases          — domain row for Maintenance runs
compensation_appeals       — domain row for Appeal runs
compensation_appeal_revisions — real revision loop evidence
safety_incidents           — domain row for Safety runs
```

Plus existing Blue Horizon domain tables: `flights`, `passengers`, `bookings`, `compensation`, `crew`, …

---

## 6. MCP server integration

Graphs **call into** the existing MCP server; they do not fork a second tool stack.

| Concern | Mechanism |
|---------|-----------|
| Reads | `get_flight_status_record`, booking/passenger lookups |
| Policy | `search_policy_manual` (hybrid RAG + Self-RAG) |
| Writes | `mark_flight_ready`, `submit_compensation_payment` |
| Permission | `require_enabled_tool` before sensitive tools |
| Prior agents | Memory/RAG and Planning agents remain registered and reachable from the platform switcher |

---

## 7. Platform architecture

```
Browser (frontend)
    │
    ▼
Flask backend (routes_admin · routes_agents · services)
    │
    ├── agent_tool_permissions CRUD  → live tool_registry
    ├── policy document CRUD         → next RAG call sees change
    ├── list/resolve admin_tasks     → hitl.resolve_admin_task + runner.resume
    ├── list/resolve failure_tickets → tickets + inject_generic_resume
    └── start/resume workflow        → graph start_* / resume_*
```

**Admin surface:** tools, RAG docs, HITL, tickets.  
**User surface:** switch agent, start run, see status / current_node / waiting_for.  
Both talk to the **same** MySQL and the **same** MCP-backed tool path — not a mock UI.

---

## 8. Runtime sequence (example: Compensation HITL)

```
1. User / test starts appeal (requested_amount = 750)
2. Runner: load → RAG → ToT → WAITING_EXTERNAL (documents)
3. inject_customer_documents({reference, file_type})
4. validate_documents OK → constrained_action
5. 750 > 500 → create_admin_task → WAITING_ADMIN
6. Process may die here; checkpoints already on disk
7. Admin approves on platform → resolve_admin_task + resume
8. submit_payment → WAITING_EXTERNAL (payment_result)
9. inject_payment_result("paid") → COMPLETED
```

Ticket path differs only at the failure: status `failed`, row in `failure_tickets`, resume after fix from the **failed node**, not from `load_original_compensation`.

---

## 9. Ownership map

| Owner | Owns |
|-------|------|
| **مصطفى فرج** | Shared foundation (`models`, `checkpoint_store`, `runner`, `tool_registry`) + Maintenance graph + checkpoint resume tests |
| **عبدالعزيز سامي** | Compensation graph + ToT strategies + HITL/tickets helpers + external_events + HITL/ticket tests |
| **مصطفى عادل** | Safety graph + LATS paths + full web platform (admin + user) |

---

## 10. What “done” means architecturally

1. **Three multi-turn graphs** with external waits and branches the model cannot control.  
2. **Checkpoints first-class** — process kill + resume from same `run_id` / node.  
3. **HITL ≠ tickets** — separate tables, statuses, and resume rules.  
4. **Two justified LLM additions per graph** on a **shared** LLM client.  
5. **Platform** reaches live MCP + MySQL (tools, RAG, HITL, tickets, agent switch).  
6. **Prior labs extended**, not rewritten — same `mcp_server/`, `db/`, memory/RAG, planning client.

---

## 11. Key test entry points

| Script | Proves |
|--------|--------|
| `tests/test_checkpoint_resume.py` | Kill/restart at maintenance wait |
| `tests/test_compensation_hitl.py` | Real `waiting_admin` → resume approved/rejected |
| `tests/test_ticket_recovery.py` | Documents + gateway tickets; resume same node |
| `tests/test_end_to_end_demo.py` | Full path start→waits→HITL→complete for all three agents |

---

*Blue Horizon Airlines · Final Project Architecture*
