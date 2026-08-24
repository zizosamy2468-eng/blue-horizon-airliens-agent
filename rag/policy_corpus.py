# THE RAG CORPUS.
#
# This is the "ungoverned knowledge" the lab asks for: a real body of
# operational policy that ops agents need during a disruption but that
# nobody wants to turn into a dozen more MCP tools. It's too large, too
# cross-referenced, and changes too often for that.
#
# Relationship to the existing duty_time_policy resource in server.py:
# that resource stays exactly as it is -- it's a tiny, fixed lookup
# ("8h flying / 14h duty") that's cheap to just hand over whole, so it
# stays a plain MCP resource. This manual is different: it's long, has
# real cross-references between sections (a compensation rule that only
# applies if a duty-time override also happened), and ops agents ask
# about small slices of it, not the whole thing -- that's exactly the
# shape of problem RAG is for, and moving the whole thing into a resource
# would just dump 20 sections of text into every prompt whether it's
# relevant or not.
#
# Each section gets a stable policy code (IROPS-XXX-N), a category, and a
# last_reviewed date -- these become the metadata payload in the vector
# store, and the metadata index (rag/vector_store.py) lets retrieval
# filter by category before doing similarity search, e.g. "only search
# the compensation sections" instead of searching all 18.

from dataclasses import dataclass


@dataclass
class PolicySection:
    code: str            # e.g. "IROPS-COMP-4.2b" -- exact-identifier lookups are what hybrid search is for
    category: str        # compensation | duty_time | rebooking | weather | mechanical | crew | communication
    title: str
    text: str
    last_reviewed: str   # ISO date


POLICY_MANUAL: list[PolicySection] = [
    PolicySection(
        code="IROPS-COMP-1",
        category="compensation",
        title="General compensation eligibility",
        text=(
            "Passengers on a flight with status disrupted, delayed, or cancelled are eligible "
            "for compensation if the disruption reason is mechanical or crew-related. Compensation "
            "is issued in the passenger's original booking currency where possible. A passenger may "
            "only receive one active compensation claim per flight; duplicate claims must be rejected "
            "and the passenger referred to the existing claim."
        ),
        last_reviewed="2026-01-15",
    ),
    PolicySection(
        code="IROPS-COMP-2",
        category="compensation",
        title="Weather exception to compensation",
        text=(
            "When the disruption reason is weather, standard compensation does NOT apply, because "
            "weather is treated as outside airline control. Affected passengers are instead entitled "
            "to free rebooking and, for delays exceeding 6 hours, a meal voucher. Ops agents must "
            "confirm the disruption reason is genuinely weather-caused (not mislabeled) before "
            "declining a compensation request on this basis."
        ),
        last_reviewed="2026-01-15",
    ),
    PolicySection(
        code="IROPS-COMP-4.2b",
        category="compensation",
        title="Auto-approve cap and supervisor override threshold",
        text=(
            "Compensation amounts up to the auto-approve cap may be issued by any authenticated ops "
            "agent without further approval. Amounts above the cap require explicit supervisor "
            "approval via elicitation before the payout is recorded. The current cap value is a "
            "policy setting subject to periodic revision -- ops agents should treat the value "
            "returned by the compensation tool's own validation as authoritative over any cached "
            "number, since this cap has changed before (see policy update log)."
        ),
        last_reviewed="2026-06-01",
    ),
    PolicySection(
        code="IROPS-COMP-5",
        category="compensation",
        title="Loyalty tier compensation multiplier",
        text=(
            "Gold and platinum tier passengers receive a 25% compensation multiplier on top of the "
            "standard amount for mechanical or crew-related disruptions, reflecting their higher "
            "average fare class exposure. Silver and none-tier passengers receive the standard amount "
            "with no multiplier. This multiplier does not apply to weather-exception cases (see "
            "IROPS-COMP-2), since those are meal vouchers, not compensation payouts."
        ),
        last_reviewed="2026-02-10",
    ),
    PolicySection(
        code="IROPS-DUTY-1",
        category="duty_time",
        title="Standard duty-time limits",
        text=(
            "Crew may fly a maximum of 8 hours and remain on duty a maximum of 14 hours in a single "
            "day, tracked per crew member via daily duty logs. These are hard limits under normal "
            "operating conditions and are separate from the override process described in IROPS-DUTY-3."
        ),
        last_reviewed="2026-01-15",
    ),
    PolicySection(
        code="IROPS-DUTY-3",
        category="duty_time",
        title="Supervisor override for duty-time limits during IROPS",
        text=(
            "During an active IROPS event (any flight with status disrupted or cancelled), a "
            "supervisor may authorize a crew member to exceed standard duty-time limits by up to 2 "
            "additional hours, ONLY if no rested reserve crew member is available at the affected "
            "airport. Every such override must be logged with the approving supervisor's ID and the "
            "specific flight it was granted for. An override granted for one flight does not carry "
            "over to a different flight the same day -- a fresh override decision is required each time."
        ),
        last_reviewed="2026-03-20",
    ),
    PolicySection(
        code="IROPS-DUTY-4",
        category="duty_time",
        title="Reserve crew priority before requesting an override",
        text=(
            "Before requesting a supervisor override under IROPS-DUTY-3, ops agents must first check "
            "whether any reserve crew member based at the same airport is within standard duty-time "
            "limits. Overrides exist for genuine coverage gaps, not convenience, and unnecessary "
            "override requests should be avoided since they carry real fatigue-related safety risk."
        ),
        last_reviewed="2026-03-20",
    ),
    PolicySection(
        code="IROPS-REBOOK-1",
        category="rebooking",
        title="Rebooking priority order",
        text=(
            "When multiple passengers from a disrupted flight need rebooking onto the same limited-"
            "availability replacement flight, priority is given in this order: platinum tier, gold "
            "tier, silver tier, then none-tier, with ties broken by original booking time (earliest "
            "first). Business and premium fare-class passengers are rebooked into equivalent or higher "
            "fare classes where seats allow; economy passengers are rebooked into economy first."
        ),
        last_reviewed="2026-02-01",
    ),
    PolicySection(
        code="IROPS-REBOOK-2",
        category="rebooking",
        title="Rebooking eligibility windows",
        text=(
            "A booking can only be rebooked while its original flight has status disrupted, delayed, "
            "or cancelled, and a booking that has already been marked rebooked cannot be rebooked "
            "again through the standard tool -- a supervisor must manually intervene for a second "
            "rebooking, since double-rebooking usually indicates a data or process error worth "
            "investigating rather than just repeating the action."
        ),
        last_reviewed="2026-02-01",
    ),
    PolicySection(
        code="IROPS-WEATHER-1",
        category="weather",
        title="Weather disruption handling",
        text=(
            "Weather-caused disruptions should be logged with as much specificity as available (storm, "
            "fog, high winds) rather than a generic 'weather' tag, since specificity affects downstream "
            "reporting to aviation authorities. Weather disruptions do not trigger standard compensation "
            "(IROPS-COMP-2) but do trigger free rebooking with no fare-class downgrade."
        ),
        last_reviewed="2026-01-20",
    ),
    PolicySection(
        code="IROPS-MECH-1",
        category="mechanical",
        title="Mechanical disruption handling",
        text=(
            "A mechanical disruption reason must be confirmed by maintenance before compensation is "
            "issued under IROPS-COMP-1 -- an initial pilot or ops-agent report of 'mechanical' is "
            "provisional until maintenance confirms it, since misreported causes affect passenger "
            "compensation eligibility. If maintenance later reclassifies the cause (for example, from "
            "mechanical to weather), any compensation already issued under the mechanical reason must "
            "be reviewed, not silently left as-is."
        ),
        last_reviewed="2026-04-05",
    ),
    PolicySection(
        code="IROPS-CREW-1",
        category="crew",
        title="Reserve crew assignment eligibility",
        text=(
            "Reserve crew may only be assigned to flights with status disrupted, delayed, or cancelled. "
            "A reserve assignment must specify the requesting ops agent for accountability. If the "
            "reserve crew member is already at or near a duty-time limit, see IROPS-DUTY-3 for the "
            "override process before completing the assignment."
        ),
        last_reviewed="2026-03-20",
    ),
    PolicySection(
        code="IROPS-CREW-2",
        category="crew",
        title="Crew base airport preference",
        text=(
            "When more than one reserve crew member is eligible for assignment, prefer the one based "
            "at the affected flight's origin airport over one based elsewhere, to minimize additional "
            "positioning delays. This preference is secondary to duty-time eligibility -- never assign "
            "a base-matched crew member who would need an override over a non-base-matched one who "
            "would not."
        ),
        last_reviewed="2026-03-20",
    ),
    PolicySection(
        code="IROPS-COMM-1",
        category="communication",
        title="Passenger notice content requirements",
        text=(
            "Passenger disruption notices must state the flight number, the disruption status, a "
            "general (not overly technical) reason, and the next step (rebooking or compensation). "
            "Notices must not speculate about causes that have not been confirmed -- for a mechanical "
            "disruption still pending maintenance confirmation (see IROPS-MECH-1), the notice should "
            "say 'an operational issue' rather than asserting 'mechanical' as fact."
        ),
        last_reviewed="2026-02-15",
    ),
    PolicySection(
        code="IROPS-COMM-2",
        category="communication",
        title="Supervisor notification for large-impact disruptions",
        text=(
            "Any disruption affecting more than 20 confirmed bookings on a single flight must be "
            "escalated to a supervisor for awareness, even if every individual rebooking or "
            "compensation action stays within normal auto-approve limits. This is a notification "
            "requirement, not an approval requirement -- individual actions still proceed normally."
        ),
        last_reviewed="2026-02-15",
    ),
    PolicySection(
        code="IROPS-COMP-6",
        category="compensation",
        title="Compensation policy update log",
        text=(
            "2026-01-15: auto-approve cap set at 500 USD equivalent. 2026-06-01: auto-approve cap "
            "raised to 750 USD equivalent following a policy review, effective immediately for all "
            "compensation issued on or after that date. Ops agents should not assume a cap value from "
            "memory of a previous session -- always defer to the current live policy setting."
        ),
        last_reviewed="2026-06-01",
    ),
    PolicySection(
        code="IROPS-DUTY-5",
        category="duty_time",
        title="Duty-time override does not apply retroactively",
        text=(
            "A supervisor override granted under IROPS-DUTY-3 covers only the specific assignment it "
            "was requested for. It cannot be cited later the same day to justify a second assignment "
            "of the same crew member without a new override decision, even if the crew member's total "
            "duty hours have not changed since the first override."
        ),
        last_reviewed="2026-03-20",
    ),
    # ---- Safety incident policies (Adel / Safety Incident Agent) ----
    PolicySection(
        code="IROPS-SAFE-1",
        category="safety",
        title="Mandatory reporting thresholds",
        text=(
            "Any safety occurrence classified as high or critical severity must be reported to the "
            "National Aviation Authority within the regulatory time window. Medium-severity events "
            "require an internal Safety Manager review before a decision is made on external filing. "
            "Low-severity operational observations may remain internal operations-log entries only. "
            "Passenger injury, smoke/fire, runway incursion, and system failures that affect "
            "controllability always trigger at least a national-authority report path."
        ),
        last_reviewed="2026-07-01",
    ),
    PolicySection(
        code="IROPS-SAFE-2",
        category="safety",
        title="Evidence standards for safety reports",
        text=(
            "A draft regulatory safety report must be supported by either a ground report or a crew "
            "statement (or both). Conflicting location or timeline facts between ground and crew "
            "sources must be resolved or explicitly noted before submission. Missing evidence is "
            "grounds for opening a Failure Ticket and pausing the workflow until corrected. "
            "Safety Manager approval is required before any report is submitted to an external authority."
        ),
        last_reviewed="2026-07-01",
    ),
    PolicySection(
        code="IROPS-SAFE-3",
        category="safety",
        title="Human-in-the-loop for safety report approval",
        text=(
            "The Safety Manager reviews every draft regulatory report generated by the Safety Incident "
            "Agent. The manager may approve the report as-is, or request changes. Requested changes "
            "return the workflow to a revision step; the graph must resume from the HITL checkpoint "
            "rather than restarting the investigation. Authority acknowledgement is recorded after "
            "submission and closes the incident when received."
        ),
        last_reviewed="2026-07-15",
    ),
]


def get_manual() -> list[PolicySection]:
    return POLICY_MANUAL


if __name__ == "__main__":
    manual = get_manual()
    print(f"Loaded {len(manual)} policy sections across categories: "
          f"{sorted(set(s.category for s in manual))}\n")
    for s in manual:
        print(f"[{s.code}] ({s.category}) {s.title}")