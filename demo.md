# Final Demo Transcript

This is a presenter script for the live system. Replace IDs with those produced by the running local database; do not invent results before the demo.

## 1. Opening (0:00-0:45)

“Blue Horizon has three airline workflows that cannot be safely run as one-pass scripts. Each records a durable checkpoint after every transition, can wait for external data, and distinguishes an expected human decision from an unexpected failure.”

Show the user agent selector and the administrator dashboard.

## 2. Compensation HITL (0:45-3:15)

1. Start a Compensation Appeal for `BH202` with a requested amount of `750.00 USD`.
2. Show the run pause for customer documents. Submit a valid document reference through the available resume/event flow.
3. Show the run at `awaiting_admin_approval` with status `waiting_admin` and its persisted checkpoint number.
4. Open the linked pending administrator task. Explain that USD 750 exceeds the USD 500 auto-approval cap.
5. Approve it as the administrator. Show that the decision is persisted and that the run resumes at the saved node, then waits for the payment result instead of restarting.

Say: “This pause is expected and is stored as an admin task, not as a failure ticket.”

## 3. Ticket and recovery (3:15-5:15)

1. Start another compensation appeal and send a document payload missing `file_type`.
2. Show the resulting `failed` run, the open failure ticket, its failed node (`validate_documents`), and its error message.
3. Resolve the ticket only after submitting valid document data.
4. Resume the run and show that it continues from `validate_documents`; it does not return to `load_original_compensation`.

Say: “This is not a human approval. It is an unplanned validation failure that has a separate ticket lifecycle.”

## 4. Crash and resume (5:15-6:45)

1. Start a Maintenance Release run and let it reach `awaiting_maintenance_report`.
2. Record the run ID, current node, and checkpoint count. Stop the process.
3. Restart the service and load the same run ID.
4. Submit a valid maintenance report. Show that the run continues to operations approval without calling the initial inspection or planning nodes again.

## 5. Safety workflow and platform (6:45-8:30)

1. Start a Safety Incident run and show policy retrieval plus reporting-path exploration.
2. Submit the ground/crew evidence, then open the pending Safety Manager review task.
3. Request a revision once; show the review/revision cycle. Approve the revised report and show the wait for authority acknowledgement.
4. Show the API health endpoint and recent runs list to confirm the platform is reading the same durable workflow records.

## 6. Close (8:30-9:30)

“The important property is recoverability: every meaningful transition is durable, external data and human decisions are injected only into waiting runs, and recovery picks up at the last saved node. The architecture and API contracts are documented in the repository.”
