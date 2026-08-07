# Blue Horizon IROPS Assistant — Memory & RAG Extension

This extends the existing Blue Horizon MCP Server Lab project (`mcp_server/`, `db/`)
with a real long-term memory system and a real retrieval layer. Nothing in the
original server, database, or tool set was rebuilt — this is growth on top of it.

## 1. The problem we found

Blue Horizon's IROPS (Irregular Operations) ops agents handle flight disruptions
through the existing MCP server: checking flight status, assigning reserve crew,
issuing compensation, rebooking passengers. Two real gaps showed up once we looked
at how this would actually run day to day:

**Gap 1 — no memory across sessions.** Every IROPS session starts from zero. If a
supervisor approves a duty-hour override for a crew member at 9am, and a different
ops agent picks up the same flight's disruption at 2pm, the system has no way to
tell them that override already happened, or that the disruption cause was later
reclassified from "mechanical" to "weather" by maintenance. Front-desk agents would
re-ask and re-approve things that already have an answer. This has a real cost:
duplicate compensation, or a crew member's duty-hour override getting requested
twice for the same day when the first one already covers the situation.

**Gap 2 — no grounding in policy.** The original `search_knowledge_base` tool was
four hardcoded sentences. Real IROPS decisions depend on an actual policy manual —
compensation eligibility, duty-time override rules, rebooking priority, loyalty-tier
multipliers — with real cross-references between sections (a compensation rule that
only applies if a duty-time override also happened, IROPS-COMP-5 vs IROPS-DUTY-3).
Nobody wants to turn 18 policy sections into 18 more MCP tools, and an agent
guessing at policy instead of retrieving it is exactly the kind of hallucination
that costs real money or breaks a real safety rule.

Both gaps needed the full architecture the lab asks for — a partial version
(memory with no consolidation, or RAG with no verification) wouldn't have actually
fixed either problem.

## 2. Extending the existing system

`mcp_server/tools_read.py` and `mcp_server/tools_write.py` keep their original SQL
and business logic untouched. The only addition is a `record_turn()` /
`update_scratchpad()` call before each return, wired through the new
`mcp_server/memory_tools.py`. `server.py` registers three new tools
(`recall_flight_history`, `search_policy_manual`, `run_memory_consolidation`)
using the exact same `mcp.tool()` / `mcp.add_tool()` pattern already used for every
other tool in the file. The old `search_knowledge_base` tool is left in place for
comparison but is superseded by `search_policy_manual`.

## 3. Repository structure

```
memory/
  short_term.py       rolling buffer + scratchpad
  router.py            promote-or-drop routing (forget / episodic only)
  episodic.py           episodic store (session-scoped, persisted to JSON)
  semantic.py            semantic store (versioned, conflict-aware)
  consolidation.py         the only writer to semantic.py, runs periodically

context_eval/
  llm_client.py        real Gemini calls for summarization + answering
  test_suite.py         40-turn long-context test transcripts, 10 variations
  strategies.py           all four context management strategies
  run_eval.py                comparison table + strategy selection

rag/
  policy_corpus.py     the 18-section IROPS policy manual (the RAG corpus)
  embeddings.py          Gemini embedding pipeline
  vector_store.py          Chroma vector DB (HNSW + metadata filtering)
  naive_rag.py                architecture 1/3
  hybrid_rag.py                 architecture 2/3 (vector + BM25)
  agentic_rag.py                  architecture 3/3 (multi-hop function calling)
  self_rag.py                       relevance + support verification, both RAG and memory recall

retrieval_eval/
  test_questions.py    12 domain questions across general/citation/multi_part
  run_eval.py             comparison table + architecture selection

mcp_server/
  memory_tools.py       the only new file wiring memory/ and rag/ into the live server
  tools_read.py, tools_write.py, server.py    unchanged logic, memory hooks added
client/
  client_stdio.py, client_http.py    updated to exercise the new tools
```

## 4. Memory architecture

**Short-term memory + scratchpad** (`memory/short_term.py`): a token-bounded
rolling buffer of turns, and a separate flat scratchpad (`current_flight`,
`current_goal`, `sub_goal`, `working_facts`, `pending_decisions`) that is never
touched by buffer pruning. Verified directly — after the buffer was fully drained
to 0 turns, the scratchpad still held the full working state:

```
Final snapshot:
{'session_id': 'BH202-2026-08-02', 'buffer_turns': 0, 'buffer_tokens': 0,
 'scratchpad': {'current_flight': 'BH202', 'current_goal': 'resolve disruption for BH202',
 'sub_goal': 'check crew duty hours before assigning reserve crew',
 'working_facts': {'disruption_reason': 'mechanical'},
 'pending_decisions': ['awaiting supervisor approval for crew_id=1 duty override']}}
```

**Promote-or-drop routing** (`memory/router.py`): fires when the buffer overflows.
A transparent, rule-based scorer (financial / operational / authorization /
root-cause signal keywords) decides forget vs. episodic per turn, with the full
reasoning logged. It never writes to semantic memory. In one drain of 5 turns, 2
were promoted (a disruption status with a root-cause fact, and a supervisor
override) and 3 were dropped as routine noise — and the scratchpad was untouched
by any of it.

**Episodic memory** (`memory/episodic.py`): promoted turns persisted to disk with
extracted flight numbers, amounts, and supervisor IDs, so a session tomorrow can
pull up today's history without re-asking:

```
--- Simulating tomorrow: a NEW session pulls up BH202's history ---
Found 1 past episodes about BH202 without re-asking anything:
  - Flight BH202: CAI to LHR - Status: disrupted - Reason: mechanical
```

**Semantic memory consolidation** (`memory/consolidation.py`, the only writer to
`memory/semantic.py`): a separate periodic pass over episodic memory — never
triggered inline by the router. It extracts (subject, predicate, value) facts,
scores authority by source (supervisor-involved > confirmed > unconfirmed), and
resolves conflicts explicitly rather than silently overwriting. Real conflict
resolved by a live consolidation run — an initial unconfirmed "mechanical" report
for BH202 vs. a later maintenance-confirmed "weather" report:

```
=== Consolidation run summary ===
 - NEW BH202.disruption_reason = mechanical
 - CONFLICT BH202.disruption_reason: 'mechanical' (auth=1) vs 'weather' (auth=2) -> kept 'weather'
 - NEW policy:compensation_cap.auto_approve_cap_usd = 750

new_facts=2 updates=0 conflicts_resolved=1
```

The losing version (`mechanical`, authority 1) is not deleted — it's retained with
`status=superseded_due_to_conflict` and a `conflict_notes` field explaining exactly
what it lost to. A separate, non-conflicting case (the compensation cap rising from
500 to 750 USD) is handled as a routine update instead, since it isn't two
sources disagreeing about the same fact, just a policy value changing over time.

## 5. Context window management — all four strategies

Test suite: 10 seeded 40-turn transcripts, each burying one critical fact (a
supervisor's duty-hour override for a specific crew member) under ~36 turns of
realistic tool-call noise, including near-duplicate but non-critical override
mentions for other crew members. Each strategy's pruned output is fed to a real
Gemini call, and a run only counts as correct if the model's actual answer
contains the required markers.

| Strategy | Accuracy | Avg input tokens | Avg output tokens | Avg latency |
|---|---|---|---|---|
| Sliding window (last 10 turns) | 0/10 | 534 | 24 | 1.21s |
| Observation masking | 10/10 | 1246 | 53 | 0.79s |
| Recursive summarization | 8/10 | 770 | 216 | 11.97s |
| Zone-based pruning | 10/10 | 1554 | 55 | 0.90s |

**Chosen: Observation masking.** Sliding window failed outright — the critical
fact sits early in the transcript and falls straight out of a fixed 10-turn
window, which is exactly the real failure mode for a tool-heavy IROPS session.
Recursive summarization preserved the fact in 8/10 runs but at nearly 15× the
latency of observation masking, because it makes real per-chunk LLM calls to
compress older turns. Zone-based pruning tied observation masking at 10/10, but
cost more input tokens (1554 vs. 1246) and higher latency (0.90s vs. 0.79s) for
no accuracy benefit given this transcript shape — Blue Horizon's IROPS sessions
are bloated by tool-call JSON, not dialogue, which observation masking targets
directly by keeping any significance-flagged tool output regardless of age.

## 6. Vector database architecture

`rag/vector_store.py` uses Chroma with an HNSW index built automatically per
collection, chunk metadata (category, title, last_reviewed) stored alongside each
vector, and a `where=` metadata filter applied *before* the similarity search, not
after. Verified directly — filtering to `category='duty_time'` on a query that
literally mentions "compensation" still returns only duty-time sections:

```
=== Filtered search (category='duty_time'): 'compensation cap amount' ===
(query mentions compensation, but the where= filter forces duty_time-only candidates)
  0.511  [IROPS-DUTY-3] Supervisor override for duty-time limits during IROPS
  0.502  [IROPS-DUTY-1] Standard duty-time limits
  0.490  [IROPS-DUTY-5] Duty-time override does not apply retroactively
```

## 7. Retrieval architectures — three required

Test suite: 12 questions across three categories (general, citation-heavy,
multi-part/decomposition), 4 per category, evaluated through the **verified**
entry points in `rag/self_rag.py` so the table reflects what the live agent would
actually hand back — including any answer replaced or refused by the Self-RAG
check.

| Architecture | Accuracy | Avg tokens/query | Avg latency/query | By category |
|---|---|---|---|---|
| Naive RAG | 11/12 | 474.5 | 1.69s | general=4/4, citation=4/4, multi_part=3/4 |
| Hybrid search | 11/12 | 476.2 | 1.89s | general=4/4, citation=4/4, multi_part=3/4 |
| Agentic RAG | 12/12 | 1120.8 | 3.06s | general=4/4, citation=4/4, multi_part=4/4 |

**Justification (based on the actual numbers above):**
- Naive RAG and hybrid search both scored 11/12 overall, with identical
  per-category results (general=4/4, citation=4/4, multi_part=3/4).
- Agentic RAG was the only architecture to get every multi-part question right
  (4/4) and finished at 12/12 overall, but at roughly 3.06s and 1121 tokens per
  query — about 1.6× the latency and 2.3× the tokens of hybrid search.
- In this particular test run, the citation-heavy questions happened to be
  retrievable by pure vector search as well, so hybrid did not show a clear
  accuracy edge over naive here. Hybrid is still preferred over naive as the
  **default**, because it is structurally robust to exact policy-code lookups
  (the failure mode hybrid search was specifically built for — an embedding
  model doesn't treat "4.2b" as a meaningful token, BM25 does) at almost no
  extra token or latency cost, and a larger or differently-worded citation set
  could easily expose the gap this run didn't happen to surface.
- Blue Horizon's live IROPS call volume is dominated by quick general and
  citation questions during active disruptions, where an ops agent on the
  phone cannot wait several seconds for a multi-hop reasoning loop. **Hybrid
  search ships as the default retrieval path**, and multi-part /
  decomposition-shaped questions (detectable from question length and the
  presence of multiple distinct entities/conditions) are routed to the
  **agentic path** instead.

A concrete example of the agentic path earning its cost — a genuinely
decomposition-shaped question resolved in 2 hops, pulling from 5 different
policy sections across compensation, mechanical-cause, and duty-time categories
that a single-shot retrieval on the whole question would not have surfaced
together:

```
Q: A gold-tier passenger is on flight BH202, which is disrupted for a mechanical
reason, and the crew member we'd assign as reserve already needs a duty-time
override. What compensation applies to the passenger, and what has to happen
before we can assign that crew member?

Hops used: 2
Retrieved across all hops: ['IROPS-COMP-5', 'IROPS-MECH-1', 'IROPS-COMP-1',
'IROPS-CREW-1', 'IROPS-DUTY-4', 'IROPS-DUTY-5', 'IROPS-DUTY-1', 'IROPS-DUTY-3']
tokens=2773 latency=15.977s
```

## 8. Self-RAG-style verification

`rag/self_rag.py` runs two explicit LLM-judged checks before any answer reaches
the user — relevance (is each retrieved passage actually relevant?) and support
(is the generated answer actually backed by what passed relevance?) — and applies
identically to RAG answers and to episodic/semantic memory recall
(`recall_flight_history` uses this same check).

A visible consequence when relevance fails, from a mixed-relevance memory recall
check against the question "what caused BH202's disruption":

```json
{
  "verified": true,
  "checks": [
    {
      "item": "disruption_reason: weather",
      "relevant": true,
      "reason": "The passage explicitly identifies weather as the cause of the disruption for BH202."
    },
    {
      "item": "auto_approve_cap_usd: 750",
      "relevant": false,
      "reason": "The passage provides a financial limit and contains no information regarding the disruption of BH202."
    }
  ]
}
```

The compensation-cap fact was correctly filtered out — it matched nothing about
the question asked, even though it lives in the same store. Nothing irrelevant
was allowed through just because it was recalled successfully.

## 9. Live integration

With the server running, a front-desk session sees `recall_flight_history` and
`search_policy_manual` available from the start (read-only, like every other
read tool). `run_memory_consolidation` only appears after `authenticate_supervisor`
succeeds — registered inside that same handler, right next to
`assign_reserve_crew` and `issue_compensation`, and announced via the same
`notifications/tools/list_changed` push the notifications concern already used:

```
=== Authenticating as supervisor sup_001 ===

>>> NOTIFICATION RECEIVED: notifications/tools/list_changed <<<
The server just told us its tool list changed.

Supervisor sup_001 authenticated. assign_reserve_crew, issue_compensation, and
run_memory_consolidation are now available.
```

See `demo_transcript.md` for the full end-to-end run.

## 10. Setup

```bash
pip install google-genai python-dotenv chromadb rank_bm25 mysql-connector-python mcp
```

Add to `.env` (never commit this file — confirm it's in `.gitignore`):
```
GOOGLE_API_KEY=...
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=...
DB_NAME=blue_horizon_db
```

Build the vector index once (subsequent runs reuse the persisted Chroma
collection):
```bash
python rag/vector_store.py
```

Run the server (Streamable HTTP by default, `stdio` for local debugging):
```bash
python mcp_server/server.py
python mcp_server/server.py stdio
```

Run the evaluations that produced the tables above:
```bash
python context_eval/run_eval.py
python retrieval_eval/run_eval.py
```

## 11. Team contributions

- **Memory system + context management evaluation** (`memory/`, `context_eval/`)
- **RAG system** (`rag/`)
- **Retrieval evaluation + live integration** (`retrieval_eval/`,
  `mcp_server/memory_tools.py`, wiring into `tools_read.py` / `tools_write.py`
  / `server.py` / clients, README + demo) — starts once `rag/` is merged, since
  `retrieval_eval/` evaluates the architectures built there before the
  integration work begins

See linked pull requests on each GitHub Issue for the detailed commit history
per contributor.
