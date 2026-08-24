# State Graphs

All graphs use `WorkflowState`, `StateGraphRunner`, and MySQL checkpoints. A node returns a `NodeResult`; the runner saves the resulting state before executing any following node.

## Maintenance Release Coordinator

```text
inspect_flight
  -> retrieve_maintenance_policy [RAG]
  -> build_release_plan [task decomposition]
  -> awaiting_maintenance_report
  -> validate_maintenance_report
      invalid -> failure ticket / failed
      valid -> requires_operations_approval [HITL]
  -> mark_flight_ready
  -> completed
```

This workflow waits for a real maintenance report and requires an Operations Manager before the release write. An invalid report is an unplanned failure, not an approval pause.

## Compensation Appeal Agent

```text
load_original_compensation
  -> retrieve_compensation_policy [RAG]
  -> compare_appeal_strategies [Tree of Thoughts]
  -> await_customer_documents
  -> validate_documents
      invalid -> failure ticket / failed
      valid -> prepare_action -> constrained_action
  -> awaiting_admin_approval when amount > USD 500 [HITL]
  -> submit_payment [constrained action]
  -> await_payment_result
      paid -> completed
      rejected -> compare_appeal_strategies (revision loop, maximum 3 rounds)
```

The graph’s loop is a substantive business cycle: a rejected payment or rejected approval eliminates the prior strategy and records a new appeal revision. It is not a blind retry.

## Safety Incident Agent

```text
collect_flight_and_crew_facts
  -> retrieve_safety_policy [RAG]
  -> explore_reporting_paths [LATS]
  -> awaiting_ground_or_crew_report
  -> validate_evidence
      missing/conflicting -> failure ticket / failed
      sufficient -> draft_regulatory_report
  -> safety_manager_review [HITL]
      changes requested -> revise_report -> safety_manager_review
      approved -> submit_report
  -> awaiting_authority_acknowledgement
      acknowledged -> completed
      rejected -> failure ticket / failed
```

The review/revision path is a genuine cycle. The report cannot be submitted until the Safety Manager supplies an approval, and the workflow remains durable while it awaits evidence or an authority response.

## Status meanings

| Status | Meaning |
| --- | --- |
| `running` | The runner may execute the current node. |
| `waiting_external` | The graph is paused for data outside the model’s control. |
| `waiting_admin` | The graph is paused for a human decision that it is not allowed to make. |
| `failed` | An unexpected error has produced a failure ticket. |
| `completed` | The terminal success path has finished. |
