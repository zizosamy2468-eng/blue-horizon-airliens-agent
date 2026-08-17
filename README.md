# Blue Horizon IROPS — Decomposition & Planning Agent

This extends the same Blue Horizon Airlines repo, `mcp_server/`, and database
used in the MCP Server Lab and the Memory & RAG Lab. It adds a **new,
separate agent** — the **planning agent** — that owns a different real
problem than the memory/RAG agent: deciding, not just retrieving.

## The problem this agent owns

**Resolving a flight disruption** (`resolve_disruption(flight_number,
requested_by)`). When a flight goes `disrupted`, `delayed`, or `cancelled`,
an ops agent has to decide, in order and sometimes reactively:

- who among the affected passengers actually needs handling,
- whether reserve crew is needed, and if a candidate is near the duty-hour
  limit, whether an override is worth requesting or a different crew
  member should be picked instead,
- how to search for a replacement route when the direct one isn't
  available,
- how to propose compensation without violating duplicate-claim or
  loyalty-tier-multiplier rules, and
- what to actually tell the passengers, without overstating an unconfirmed
  cause as settled fact.

This is not a single-call problem. No individual MCP tool
(`get_flight_status`, `assign_reserve_crew`, `issue_compensation`,
`rebook_passenger`) can safely resolve it alone — the tools are
deliberately narrow (per the MCP Server Lab's own design), and the
*ordering, branching, and correction* across them is exactly what was
missing. A wrong plan has real cost: a duplicate compensation payout, an
avoidable duty-hour override, or a notice that asserts an unconfirmed
mechanical cause as fact (a real policy violation under `IROPS-MECH-1`).

This is a **different agent, different problem** from the memory/RAG lab
(which answered retrieval questions — "what happened", "what does policy
say"). This agent decides "what should happen next", which is why it needs
its own decomposition, planning-algorithm, and self-correction machinery
rather than reusing the memory/RAG agent's code path. `planning/` does not
import from `memory_tools.py`, and nothing in `memory/` or `rag/` was
touched for this lab.

## Which agent owns it

`planning/planning_agent_tools.py` exposes one top-level MCP tool,
`resolve_disruption`, registered in `mcp_server/server.py` alongside (not
instead of) every existing tool from the previous two labs. See
`mcp_server/server.py`'s `--- Planning lab addition ---` blocks for the
exact two-line wiring.

Three narrower tools are also exposed directly, so each planning concern
can be demonstrated in isolation without running the full pipeline:
`select_reserve_crew_grounded` (LATS), `refine_disruption_notice`
(Self-Refine), `propose_compensation_reflexion` (Reflexion).

## Where every concern lives

| Concern | File | What to look for |
|---|---|---|
| DAG construction + acyclicity | `planning/dag.py` | `TaskDAG.add_edge()` — rejects a cycle at insertion time, not after |
| Decomposition-first | `planning/decomposition.py` | `build_plan()` — one LLM call, whole DAG up front |
| Dynamic/interleaved decomposition | `planning/dynamic_decomposition.py` | `run_dynamic_decomposition()` — one sub-task per LLM call, real observation fed back before the next decision |
| Routing (PS vs ToT vs LATS) | `planning/router.py` | `route_sub_task()` — reads the action's `shape` from `domain_actions.ACTIONS`, no LLM call needed for the routing decision itself |
| Plan-and-Solve | `planning/plan_and_solve.py` | `plan()` (phase 1) / `solve()` (phase 2) — single pass, no branching |
| Tree of Thoughts | `planning/tree_of_thoughts.py` | `generate_candidates()` + `evaluate_candidate()` — **ungrounded**, self-scored |
| LATS | `planning/lats.py` | `run_lats()` — select/expand/evaluate/backpropagate loop, **grounded** via `environment.py` |
| Grounded environment | `planning/environment.py` | `check_crew_assignment_feasibility()`, `check_compensation_validity()` — real `duty_time_logs`/`compensation` queries, replacing the toolkit's randomized default |
| Self-Refine | `planning/self_refine.py` | `self_refine_notice()` — one draft, one grounded + one rubric critique, one revision |
| Reflexion | `planning/reflexion.py` | `run_reflexion()` — capped reflection buffer (`reflection_buffer_cap`) carried across trials |
| Orchestration + evidence trace | `planning/planning_agent_tools.py` | `resolve_disruption()` — wires all of the above, saves JSON to `artifacts/` |
| Test suite (fixed) | `planning_eval/test_suite.py` | 13 real cases across 5 categories |
| Comparison harness | `planning_eval/run_eval.py` | runs every method against every applicable case |

## Grounded vs. ungrounded — the deliberate swap

`tree_of_thoughts.py` and `lats.py` are run against the **same decision**:
which crew member to assign as reserve for BH202. This is intentional —
it's the lab's required "swap an ungrounded self-critique for a grounded
one on the same sub-task" case.

- **ToT (ungrounded):** the model scores its own candidates from what's
  typed into its prompt. In our real run, it gave `crew_id=1` (Capt. Karim
  Mostafa, 13.0h duty logged) a self-score of **10/10**, reasoning that 13
  hours was "within standard operational limits."
- **LATS (grounded):** the same candidate, evaluated by a real
  `duty_time_logs` query (`environment.check_crew_assignment_feasibility`),
  scored **5.5/10** — correctly reflecting that only 0.5h of headroom
  remained before the real 14h duty limit, a fact the ungrounded self-score
  had no access to and simply didn't weigh.

Both picked the same candidate in this run (there was no better-rested
option available), but the **score itself** shows the gap: ToT's self-belief
(10/10, "within standard limits") versus LATS's grounded number (5.5/10,
"0.5h headroom") are describing the same real crew member very
differently. In a scenario with a better-rested alternative crew member
available, this score gap is what would flip LATS's decision away from the
near-limit candidate while ToT's self-score would still confidently pick
them — that is the concrete failure mode grounding exists to catch.

## Comparison table (real runs, real Gemini calls, `planning_eval/comparison_results.json`)

### Decomposition-first vs. dynamic (6 applicable cases)

| Method | Accuracy | Avg LLM calls | Avg tokens | Avg latency |
|---|---|---|---|---|
| decomposition_first | 5/6 | 1 | 520 | 1.56s |
| dynamic | 5/6 | 3 | 1,635 | 4.59s |

**Divergence observed on `BH202`:** decomposition-first committed to a
3-node fixed plan (`check_flight_status`, `evaluate_crew_needs`,
`identify_passengers`) before seeing any real result. Dynamic decomposition
observed the real result of `get_candidate_replacement_flights` (an empty
list — no CAI→LHR replacement exists in the seed data) and **stopped
itself**, explicitly reasoning: *"no candidate replacement flights are
available to rebook the affected passenger... no further automated actions
can resolve the passenger's travel needs."* Decomposition-first has no
mechanism to react to that — it would have kept running its fixed 3 nodes
regardless of what `get_candidate_replacement_flights` returned, because it
never calls that action at all in its up-front plan. This is exactly the
lab's required "early surprise reshapes the rest of the plan" case, at
~3x the token/latency cost.

**Why decomposition-first still ships as the default** for the fully
mechanical sub-tasks (status checks, single-passenger notices — the
`decomposition_first_favored` test cases): the table shows dynamic paying
3x the LLM calls and tokens for cases where nothing actually changes
mid-plan. Dynamic decomposition is the one that gets routed to when
`resolve_disruption` is called for an active disruption with unresolved
rebooking/crew/compensation needs, where the reactivity has already been
shown to matter.

### Plan-and-Solve vs. Tree of Thoughts vs. LATS (reserve-crew selection)

| Method | Accuracy | Avg LLM calls | Avg tokens | Avg latency |
|---|---|---|---|---|
| plan_and_solve | 1/1 | 1 | 187 | 0.78s |
| tree_of_thoughts | 1/1 | 4 | 1,270 | 5.57s |
| lats_ungrounded (toolkit default, for contrast only) | 0/1 | 1 | 0 | 0.0s |
| lats_grounded (real, shipped) | 1/1 | 1 | 301 | 1.08s |

Plan-and-Solve is cheapest and got lucky on this single-candidate-obvious
case, but it never compares alternatives — it is routed to sub-tasks with
no real branching (rebooking assignment order), not crew selection.
Tree of Thoughts costs ~7x Plan-and-Solve's tokens to generate and
self-score 3 candidates. LATS-grounded matches ToT's correctness at a
fraction of the cost (1 LLM call vs. 4) because its grounded evaluate step
doesn't need an LLM call at all — it queries the database directly. The
`lats_ungrounded` row (the toolkit's original randomized-score default,
kept only for this contrast, never shipped) has no real signal by
construction and scores accordingly. **LATS-grounded ships for crew
selection**; ToT is kept for sub-tasks with real multi-way tradeoffs but no
existing real-time validator to check against (e.g. rebooking priority
ordering across several loyalty tiers at once).

### Self-Refine vs. Reflexion

| Method | Accuracy | Avg LLM calls | Avg tokens | Avg latency |
|---|---|---|---|---|
| self_refine | 2/2 | 3 | 507 | 3.06s |
| reflexion | 2/2 | 3 | 1,001.5 | 2.79s |

Both hit 100% on their respective fixed test cases, but they are **not**
interchangeable — they were run on different sub-task types on purpose.
Self-Refine's real run on BH202 caught a genuine grounded violation: the
draft asserted *"a confirmed mechanical fault"* as settled fact, and the
grounded critique (a live `flights` table read, not model opinion) flagged
it against `IROPS-MECH-1` and produced a corrected revision saying *"an
operational issue"* instead — one draft, one grounded critique, one
revision, exactly proportionate to a cheap-to-redraft output.
Reflexion's real run needed genuine cross-trial memory: trial 0 proposed
100 USD for Mona Khaled and failed the grounded `check_compensation_validity`
check (she already had an approved 150 USD claim on file); the reflection
*"perform a pre-check against the current compensation ledger"* was carried
into trial 1, which correctly proposed nothing further and marked her
`already_covered`. A single Self-Refine-style revision without a second
real evaluate pass could not have confirmed the fix actually worked — that
confirmation is Reflexion's job.

## Known limitation (documented honestly, not hidden)

`decomposition.py` and `dynamic_decomposition.py` currently execute a
sub-task's assigned domain action **directly** (via
`domain_actions.run_action`) rather than first checking `router.py`'s
routing decision and dispatching `reasoning`-shaped actions
(`assign_reserve_crew`) through `tree_of_thoughts.py`/`lats.py` before
execution. In real runs this surfaces as `assign_reserve_crew() missing 1
required positional argument: 'ctx'` whenever a DAG plan includes that
action directly — `assign_reserve_crew` is an async, context-bound MCP
tool (it needs a live `ctx` for elicitation), not a plain callable safe to
invoke outside a routed planning step. The standalone algorithm files
(`tree_of_thoughts.py`, `lats.py`) do not have this problem — they never
call `assign_reserve_crew` directly, they only read from
`environment.py`'s grounded checks. The fix (routing DAG nodes through
`router.py` before execution instead of calling `run_action` unconditionally
for every node) is scoped but not yet applied; `planning_eval/run_eval.py`'s
decomposition-comparison numbers above are unaffected because that harness
scores plan shape and stopping behavior, not this specific action's
execution success.

## Run instructions

```bash
# .env (repo root) needs both the existing DB_* vars and:
GOOGLE_API_KEY=...

cd planning
python dag.py                     # no LLM, pure structure + cycle check
python decomposition.py           # decomposition-first demo (BH202)
python dynamic_decomposition.py   # dynamic demo (BH202)
python router.py                  # routing decisions, no LLM
python plan_and_solve.py          # PS demo (rebooking)
python tree_of_thoughts.py        # ToT demo (crew selection, ungrounded)
python environment.py             # grounded checks, real DB queries
python lats.py                    # LATS demo (crew selection, grounded)
python self_refine.py             # Self-Refine demo (notice drafting)
python reflexion.py               # Reflexion demo (batch compensation)
python planning_agent_tools.py    # full orchestrator, both decomposition modes

cd ../planning_eval
python run_eval.py                # full comparison table -> comparison_results.json
```

To run the live server with the planning agent registered:
```bash
cd mcp_server
python server.py stdio            # or plain `python server.py` for Streamable HTTP
```
