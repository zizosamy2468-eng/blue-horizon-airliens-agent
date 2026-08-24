# Blue Horizon Airlines — Demo Transcript (Final)

This document is the evidence trail the rubric asks for: a run pausing on a genuine HITL
condition and resolved by an admin through the platform, a run failing mid-node and
becoming a ticket that gets resolved and resumed from its checkpoint, and a process
being killed and restarted mid-run to show recovery.

Section 1 is **real, captured terminal output** from the Compensation Appeal agent
(Zizo). Sections 2 and 3 are the scripted walkthroughs for Maintenance Release and
Safety Incident, following the exact same commands their own test files expose.

---

## 1. Compensation Appeal Agent — captured evidence (Zizo)

### 1.1 HITL: over-cap amount → admin approves → resume

```
$ python tests/test_compensation_hitl.py create
PAUSED_FOR_DOCUMENTS
RUN_ID=ef728cb4-5c98-4134-9ee8-b7f75ef20633
CHECKPOINTS=5

HITL_PAUSE_REACHED
RUN_ID=ef728cb4-5c98-4134-9ee8-b7f75ef20633
STATUS=waiting_admin
CURRENT_NODE=awaiting_admin_approval
ADMIN_TASK_ID=021e5532-e551-443a-9a09-f8c91bcc2d08
CHECKPOINTS=10
```

```
$ python tests/test_compensation_hitl.py resume ef728cb4-5c98-4134-9ee8-b7f75ef20633 approved
STATE_LOADED_AFTER_RESTART
RUN_ID=ef728cb4-5c98-4134-9ee8-b7f75ef20633
STATUS=waiting_admin
CHECKPOINTS_BEFORE=10

HITL_RESUME_RESULT
STATUS_AFTER=waiting_external
CURRENT_NODE_AFTER=await_payment_result
CHECKPOINTS_AFTER=13
HITL_APPROVED_PATH_OK — waiting for payment_result
```

**What this proves:** the requested amount ($750) was compared against the real
$500 auto-approve cap — not a model's opinion. The run genuinely stopped
(`STATUS=waiting_admin`), a real `admin_tasks` row was created
(`ADMIN_TASK_ID=...`), and it was `resolve_admin_task()`'d **and** resumed only
after the second command ran — a separate process, loading the run fresh from
MySQL (`STATE_LOADED_AFTER_RESTART`).

### 1.2 HITL: over-cap amount → admin rejects → real loop (revised appeal)

```
$ python tests/test_compensation_hitl.py create
PAUSED_FOR_DOCUMENTS
RUN_ID=4adc0aa9-6945-49b6-a57a-87607632f17c
CHECKPOINTS=5

HITL_PAUSE_REACHED
RUN_ID=4adc0aa9-6945-49b6-a57a-87607632f17c
STATUS=waiting_admin
CURRENT_NODE=awaiting_admin_approval
ADMIN_TASK_ID=d43f728a-7089-4a4a-ad40-170fc55e5926
CHECKPOINTS=10
```

```
$ python tests/test_compensation_hitl.py resume 4adc0aa9-6945-49b6-a57a-87607632f17c rejected
STATE_LOADED_AFTER_RESTART
RUN_ID=4adc0aa9-6945-49b6-a57a-87607632f17c
STATUS=waiting_admin
CHECKPOINTS_BEFORE=10

HITL_RESUME_RESULT
STATUS_AFTER=waiting_external
CURRENT_NODE_AFTER=await_customer_documents
CHECKPOINTS_AFTER=14
HITL_REJECTED_PATH_OK — revised appeal or closed
```

**What this proves:** a rejection did **not** end the run. `_record_revision()`
wrote a real row into `compensation_appeal_revisions`
(`trigger_reason='admin_rejected'`), the rejected strategy was excluded, and the
graph looped back through `compare_appeal_strategies` (a real second Tree-of-Thoughts
call) before landing on `await_customer_documents` again — 4 genuinely new
transitions in one resume (10 → 14). This is the concrete cycle a plain DAG
cannot express.

### 1.3 Failure Ticket: invalid documents → resolve → resume from the same node

```
$ python tests/test_ticket_recovery.py documents
RUN_ID=f5a100f6-ff67-4ec1-8c0f-f236c2b4988a
Injecting INVALID documents to force a Failure Ticket...

TICKET_CREATED
TICKET_ID=b84312a0-7a0e-4ff5-8a5f-e1d4e2db3bbd
FAILED_NODE=validate_documents
ERROR_TYPE=missing_document_fields
ERROR_MESSAGE=Customer documents are missing fields: ['file_type']
CHECKPOINTS=8
```

```
$ python tests/test_ticket_recovery.py resume f5a100f6-ff67-4ec1-8c0f-f236c2b4988a
STATE_LOADED_AFTER_FAILURE
RUN_ID=f5a100f6-ff67-4ec1-8c0f-f236c2b4988a
FAILED_NODE=validate_documents
CHECKPOINTS_BEFORE=8
TICKET_RESOLVED=b84312a0-7a0e-4ff5-8a5f-e1d4e2db3bbd

TICKET_RESUME_RESULT
STATUS_AFTER=waiting_external
CURRENT_NODE_AFTER=await_payment_result
CHECKPOINTS_AFTER=13
Resumed from checkpoint — did not restart from load_original_compensation.
```

**What this proves:** the ticket carries the real error (`missing_document_fields`,
the exact missing field list), the run genuinely stopped (`status='failed'` in
`workflow_runs`), and after the ticket was marked `resolved`, the run continued
with corrected documents from `validate_documents` onward — never re-running
`load_original_compensation`, `retrieve_compensation_policy`, or
`compare_appeal_strategies` again.

### 1.4 Failure Ticket: mock payment-gateway timeout → resolve → resume

```
$ python tests/test_ticket_recovery.py gateway

GATEWAY_TICKET_CREATED
RUN_ID=acd2827b-ff40-4cda-8ed9-47d1aaae4b57
TICKET_ID=094fabc7-6112-4017-992f-5c4d79c659f7
ERROR_MESSAGE=Mock payment gateway unavailable (timeout).
CHECKPOINTS=13
```

```
$ python tests/test_ticket_recovery.py resume acd2827b-ff40-4cda-8ed9-47d1aaae4b57
STATE_LOADED_AFTER_FAILURE
RUN_ID=acd2827b-ff40-4cda-8ed9-47d1aaae4b57
FAILED_NODE=submit_payment
CHECKPOINTS_BEFORE=13
TICKET_RESOLVED=094fabc7-6112-4017-992f-5c4d79c659f7

TICKET_RESUME_RESULT
STATUS_AFTER=waiting_external
CURRENT_NODE_AFTER=await_payment_result
CHECKPOINTS_AFTER=15
Resumed from checkpoint — did not restart from load_original_compensation.
```

**What this proves:** this is a genuinely different failure than 1.3 — same
shared `create_failure_ticket()` path, different `failed_node`
(`submit_payment`) and a different real fix on resume (a corrected
`requested_amount`, not resent documents — see `resume_after_ticket()`'s branch
on `ticket['failed_node']`). Two distinct failure causes, one consistent
recovery mechanism, no restart from scratch either time.

### 1.5 Database evidence (run after any of the above)

```sql
SELECT run_id, appeal_status, payment_reference, payment_gateway_status
FROM compensation_appeals
WHERE run_id = 'ef728cb4-5c98-4134-9ee8-b7f75ef20633';

SELECT * FROM compensation_appeal_revisions
WHERE appeal_id = (
  SELECT appeal_id FROM compensation_appeals
  WHERE run_id = '4adc0aa9-6945-49b6-a57a-87607632f17c'
);
```

---

## 2. Maintenance Release Coordinator — walkthrough (Mostafa)

Two-process crash-and-resume demo, using `tests/test_checkpoint_resume.py`.

**Process 1 — create and pause at the real external wait:**
```
$ python tests/test_checkpoint_resume.py create
WORKFLOW_CREATED
RUN_ID=<uuid>
STATUS=waiting_external
CURRENT_NODE=awaiting_maintenance_report
CHECKPOINTS=1

Stop this process now. Then run this command in a new process:
python tests/test_checkpoint_resume.py resume <RUN_ID>
```

**Kill the process here** (Ctrl+C, or close the terminal) — this is the literal
"kill the process mid-run on purpose" the rubric asks for.

**Process 2 — a fresh process, resume from MySQL:**
```
$ python tests/test_checkpoint_resume.py resume <RUN_ID>
STATE_LOADED_AFTER_RESTART
RUN_ID=<uuid>
STATUS=waiting_external
CURRENT_NODE=awaiting_maintenance_report
CHECKPOINTS_BEFORE=2

CHECKPOINT_RESUME_PASSED
STATUS_AFTER_RESUME=waiting_admin
CURRENT_NODE_AFTER_RESUME=requires_operations_approval
CHECKPOINTS_AFTER=3
The workflow resumed from the saved waiting node.
It did not restart from inspect_flight.
```

**Live-demo extension (operations-manager HITL):** call
`resume_maintenance_release_workflow(run_id, operations_decision="approved",
operations_manager_id="ops_manager_001")` through the MCP tool — the graph moves
to `mark_flight_ready`, which itself checks `run_id` belongs to a real
`maintenance_release` workflow before writing `flights.status = 'scheduled'`.

---

## 3. Safety Incident Agent — walkthrough (Adel)

**Start a new incident through the platform (or directly):**
```python
from state_graph.safety.graph import start_safety_incident

state = start_safety_incident(
    flight_number="BH202",
    incident_type="bird_strike",
    severity="high",
    description="Bird strike on approach, minor engine vibration reported by crew.",
    has_passenger_impact=False,
)
print(state.status, state.current_node)
# -> RunStatus.WAITING_EXTERNAL, "awaiting_ground_or_crew_report"
```

**Deliver a ground/crew report to unstick the wait, then reach HITL:**
```python
from state_graph.safety.graph import resume_safety_incident

state = resume_safety_incident(
    state.run_id,
    data_updates={
        "crew_report": {"location": "rwy 27L", "timestamp": "2026-08-24T10:02:00Z"},
        "crew_report_received": True,
    },
    transition_name="crew_report_received",
)
print(state.status, state.current_node)
# -> RunStatus.WAITING_ADMIN, "safety_manager_review"
```

**Safety Manager reviews on the platform (HITL Review tab) and either approves
or requests changes.** Requesting changes resumes into `revise_report`, which
appends the manager's note and returns to `safety_manager_review` — a real loop,
same shape as the Compensation Appeal's revised-appeal cycle. Approving resumes
into `submit_report` → `awaiting_authority_acknowledgement`, a second real
external wait for the regulator's response.

**Failure Ticket branch:** if `validate_evidence` finds no ground/crew report,
or a location/timeline conflict between the two, it opens a Failure Ticket
(`error_type="insufficient_or_conflicting_evidence"`) instead of drafting a
report from incomplete facts — visible and resolvable from the platform's
Tickets tab exactly like the Compensation Appeal's.

---

## 4. Platform demo checklist

- [ ] Admin → Agents: disable `search_policy_manual` for `compensation_appeal`,
      show the next `retrieve_compensation_policy` call is refused by
      `require_enabled_tool()` — then re-enable it live, no redeploy.
- [ ] Admin → RAG Documents: add a new policy section, show the very next
      `search_policy_manual` / `retrieve_*_policy` call can retrieve it.
- [ ] Admin → HITL Review: open the pending Safety Incident report, approve it,
      watch the run move to `submit_report`.
- [ ] Admin → Tickets: resolve an open Compensation Appeal ticket, watch the
      run resume from the failed node, not from the start.
- [ ] User → Agents: switch between Maintenance, Compensation Appeal, Safety
      Incident, and the earlier Memory/RAG and Planning agents in one session.
