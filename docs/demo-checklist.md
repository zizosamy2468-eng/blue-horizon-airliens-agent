# Demo Checklist

## Before presenting

- [ ] Configure a local MySQL instance and apply the SQL schemas in `database/`.
- [ ] Create a local `mcp_server/.env`; confirm it is not staged or included in the delivery ZIP.
- [ ] Start the Flask API and frontend; verify `GET /api/health` returns `status: ok`.
- [ ] Run `python -m unittest tests/test_end_to_end_demo.py -v`.
- [ ] Prepare one flight number and separate data for the HITL, ticket, and restart demonstrations.

## Required evidence

- [ ] Start a Compensation Appeal above USD 500 and show `waiting_admin` plus its pending `admin_tasks` row.
- [ ] Approve or reject the task through the administrator flow; show the same run ID resume from its checkpoint.
- [ ] Trigger a real invalid-document or gateway failure; show `failed` and an open `failure_tickets` row.
- [ ] Resolve the ticket only after fixing its cause; show the run resumes from its failed node.
- [ ] Pause a Maintenance Release at `awaiting_maintenance_report`, stop the process, restart it, and load/resume the same run ID.
- [ ] Run the Safety Incident workflow through evidence collection, Safety Manager review, and authority acknowledgement.
- [ ] Show the user-facing agent list and the administrator task/ticket lists.
- [ ] Show enabled-tool permissions from `agent_tool_permissions` and verify a permission change is read at runtime by the relevant graph.

## Do not claim without showing

- Do not describe a ticket as an approval.
- Do not claim a restart demonstration if the process was never stopped.
- Do not claim that a state resumed if the run ID changed or initial nodes were executed again.
- Do not expose `.env`, database passwords, or API keys in the recording or presentation.
