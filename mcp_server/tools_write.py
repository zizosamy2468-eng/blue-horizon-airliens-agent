# tools_write.py
# All WRITE tools live here -- the ones that actually change real state
# (crew assignments, money, bookings). These are the risky ones, so each
# function here has three things on purpose:
#   1) Server-side validation that does NOT depend on the input schema
#      (the schema only checks shape/type, not business rules).
#   2) An authorization check done INSIDE the handler (requested_by / issued_by
#      must be a valid-looking ops agent ID).
#   3) A safety check tied to real data (duty hours, existing compensation,
#      existing bookings) before any INSERT/UPDATE happens.
#
# ELICITATION: assign_reserve_crew and issue_compensation are now async and
# take a `ctx: Context` parameter. FastMCP injects this automatically because
# the parameter is named/typed as Context. When these tools hit a risky
# situation (pilot near/over duty-hour limit, compensation over the
# auto-approve cap), they no longer just reject -- they call
# request_supervisor_approval(ctx, ...), which pauses the tool call and asks
# a human on the client side to approve or decline. See elicitation_logic.py.
#
# MEMORY INTEGRATION (added for the Memory & RAG lab): every return path --
# rejections included, not just approvals -- now calls record_turn() so the
# promote-or-drop router sees the full picture of what was attempted during
# a session, not just what succeeded. A rejected duplicate-compensation
# attempt is exactly the kind of thing worth remembering.
#
# One small necessary schema addition: rebook_passenger's SELECT now also
# pulls f.flight_number (it wasn't selected before), because a session_id
# has to be derived from the flight being handled and the original query
# didn't expose it. Nothing else about that query changed.
#
# The actual @mcp.tool() registration happens in server.py.

from typing import Literal
from mcp.server.fastmcp import Context
from dbase import get_connection
from elicitation_logic import request_supervisor_approval
from memory_tools import get_session_id, record_turn, resolve_pending_decision, update_scratchpad

MAX_FLYING_HOURS_PER_DAY = 8.00
MAX_DUTY_HOURS_PER_DAY = 14.00
MAX_COMPENSATION_WITHOUT_APPROVAL = 500.00  # USD, simplified for this project


async def assign_reserve_crew(
    flight_number: str,
    crew_id: int,
    requested_by: str,
    ctx: Context,
) -> str:
    """
    Assigns a crew member (usually reserve crew) to a disrupted flight.
    If the crew member would exceed daily duty-time limits, this pauses and
    asks a supervisor for explicit approval before proceeding.

    flight_number: the flight number that needs a crew member assigned, e.g. BH202
    crew_id: the numeric ID of the crew member being assigned (must be a positive integer)
    requested_by: the ops agent ID making this request, e.g. agent_014 (required for authorization)
    """
    session_id = get_session_id(flight_number)

    if crew_id <= 0:
        msg = "Rejected: crew_id must be a positive integer."
        record_turn(session_id, "tool_result", msg)
        return msg

    if not requested_by or not requested_by.strip():
        msg = "Rejected: requested_by is required so this action is attributable to an ops agent."
        record_turn(session_id, "tool_result", msg)
        return msg

    if not requested_by.startswith("agent_"):
        msg = (
            f"Rejected: '{requested_by}' is not recognized as a valid ops agent ID. "
            "Crew assignment requires an authenticated ops agent."
        )
        record_turn(session_id, "tool_result", msg)
        return msg

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT flight_id, status FROM flights WHERE flight_number = %s",
            (flight_number,),
        )
        flight = cursor.fetchone()
        if flight is None:
            msg = f"Rejected: no flight found with number {flight_number}."
            record_turn(session_id, "tool_result", msg)
            return msg

        if flight["status"] not in ("disrupted", "delayed", "cancelled"):
            msg = (
                f"Rejected: flight {flight_number} has status '{flight['status']}'. "
                "Reserve crew assignment is only allowed for disrupted, delayed, or cancelled flights."
            )
            record_turn(session_id, "tool_result", msg)
            return msg

        cursor.execute(
            "SELECT crew_id, full_name, role FROM crew WHERE crew_id = %s",
            (crew_id,),
        )
        crew_member = cursor.fetchone()
        if crew_member is None:
            msg = f"Rejected: no crew member found with ID {crew_id}."
            record_turn(session_id, "tool_result", msg)
            return msg

        cursor.execute(
            """
            SELECT COALESCE(SUM(hours_flown), 0) AS total_flown,
                   COALESCE(SUM(hours_on_duty), 0) AS total_duty
            FROM duty_time_logs
            WHERE crew_id = %s AND log_date = CURDATE()
            """,
            (crew_id,),
        )
        duty_totals = cursor.fetchone()
        total_flown = float(duty_totals["total_flown"])
        total_duty = float(duty_totals["total_duty"])

        override_note = ""
        if total_duty >= MAX_DUTY_HOURS_PER_DAY or total_flown >= MAX_FLYING_HOURS_PER_DAY:
            # Scratchpad update: this is the exact moment a real sub-goal
            # starts -- the agent can't complete the assignment until this
            # specific decision comes back. pending_decisions survives even
            # if the buffer gets pruned while waiting on the supervisor.
            decision_text = f"awaiting duty-hour override decision for crew_id={crew_id} on {flight_number}"
            update_scratchpad(
                session_id=session_id,
                sub_goal=f"assign crew_id={crew_id} to {flight_number} pending duty-hour override",
                pending_decisions=decision_text,
            )

            # This is the real trigger for elicitation: the tool cannot safely decide this
            # on its own, so it pauses and asks a supervisor instead of guessing or refusing.
            approved, info = await request_supervisor_approval(
                ctx,
                message=(
                    f"{crew_member['full_name']} has already logged {total_duty} duty hours "
                    f"and {total_flown} flying hours today (limits: {MAX_DUTY_HOURS_PER_DAY}h duty / "
                    f"{MAX_FLYING_HOURS_PER_DAY}h flying). Assigning them to flight {flight_number} "
                    "would exceed the legal limit. Do you approve this assignment anyway?"
                ),
            )
            resolve_pending_decision(session_id, decision_text)

            if not approved:
                msg = f"Rejected: {info}"
                record_turn(session_id, "tool_result", msg)
                return msg
            override_note = f" (Supervisor override: {info})"
            # This is exactly the kind of turn the promote-or-drop router
            # should keep: an authorization_event + operational_decision
            # signal together (see memory/router.py's SIGNAL_WEIGHTS).
            record_turn(
                session_id,
                "tool_result",
                f"SUPERVISOR APPROVAL NEEDED: {crew_member['full_name']} (crew_id={crew_id}) "
                f"already at {total_duty} duty hours today.{override_note}",
            )

        cursor.execute(
            """
            INSERT INTO crew_assignments (crew_id, flight_id, duty_start, duty_end, assignment_type)
            VALUES (%s, %s, NOW(), DATE_ADD(NOW(), INTERVAL 8 HOUR), 'reserve')
            """,
            (crew_id, flight["flight_id"]),
        )
        conn.commit()

        result = (
            f"Approved: {crew_member['full_name']} ({crew_member['role']}) assigned as reserve "
            f"crew on flight {flight_number}. Requested by {requested_by}.{override_note}"
        )
        record_turn(session_id, "tool_result", result)
        return result

    finally:
        cursor.close()
        conn.close()


async def issue_compensation(
    passenger_email: str,
    flight_number: str,
    amount: float,
    currency: Literal["USD", "EUR", "GBP", "EGP"],
    reason: str,
    issued_by: str,
    ctx: Context,
) -> str:
    """
    Issues compensation to a passenger for a disrupted flight. If the amount
    exceeds the auto-approve cap, this pauses and asks a supervisor for
    explicit approval before the payout is recorded.

    passenger_email: the passenger's registered email address
    flight_number: the flight the compensation relates to, e.g. BH202
    amount: the compensation amount as a positive number, must not exceed 500 without supervisor approval
    currency: one of USD, EUR, GBP, EGP
    reason: a short explanation for the compensation, e.g. "flight cancelled due to weather"
    issued_by: the ops agent ID issuing this compensation (required for authorization)
    """
    session_id = get_session_id(flight_number)

    if amount <= 0:
        msg = "Rejected: compensation amount must be a positive number."
        record_turn(session_id, "tool_result", msg)
        return msg

    if not reason or not reason.strip():
        msg = "Rejected: a reason is required for every compensation payout."
        record_turn(session_id, "tool_result", msg)
        return msg

    if not issued_by or not issued_by.strip() or not issued_by.startswith("agent_"):
        msg = (
            "Rejected: compensation must be issued by an authenticated ops agent "
            "(expected an ID like agent_007)."
        )
        record_turn(session_id, "tool_result", msg)
        return msg

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT passenger_id, full_name FROM passengers WHERE email = %s",
            (passenger_email,),
        )
        passenger = cursor.fetchone()
        if passenger is None:
            msg = f"Rejected: no passenger found with email {passenger_email}."
            record_turn(session_id, "tool_result", msg)
            return msg

        cursor.execute(
            "SELECT flight_id, status FROM flights WHERE flight_number = %s",
            (flight_number,),
        )
        flight = cursor.fetchone()
        if flight is None:
            msg = f"Rejected: no flight found with number {flight_number}."
            record_turn(session_id, "tool_result", msg)
            return msg

        if flight["status"] not in ("disrupted", "delayed", "cancelled"):
            msg = (
                f"Rejected: flight {flight_number} has status '{flight['status']}'. "
                "Compensation can only be issued for disrupted, delayed, or cancelled flights."
            )
            record_turn(session_id, "tool_result", msg)
            return msg

        cursor.execute(
            """
            SELECT compensation_id, amount, status
            FROM compensation
            WHERE passenger_id = %s AND flight_id = %s AND status IN ('pending', 'approved')
            """,
            (passenger["passenger_id"], flight["flight_id"]),
        )
        existing = cursor.fetchone()
        if existing is not None:
            msg = (
                f"Rejected: passenger {passenger['full_name']} already has a "
                f"{existing['status']} compensation of {existing['amount']} for this flight. "
                "Duplicate compensation is not allowed."
            )
            record_turn(session_id, "tool_result", msg)
            return msg

        override_note = ""
        if amount > MAX_COMPENSATION_WITHOUT_APPROVAL:
            decision_text = f"awaiting compensation approval ({amount} {currency}) for {passenger['full_name']} on {flight_number}"
            update_scratchpad(
                session_id=session_id,
                sub_goal=f"issue {amount} {currency} compensation pending supervisor approval",
                pending_decisions=decision_text,
            )

            # This is the real trigger for elicitation: the tool cannot decide on its own
            # whether a large payout is justified, so it pauses and asks a supervisor.
            approved, info = await request_supervisor_approval(
                ctx,
                message=(
                    f"Requested compensation of {amount} {currency} for {passenger['full_name']} "
                    f"(flight {flight_number}) exceeds the {MAX_COMPENSATION_WITHOUT_APPROVAL} "
                    f"auto-approve limit. Reason given: {reason}. Do you approve this payout?"
                ),
            )
            resolve_pending_decision(session_id, decision_text)

            if not approved:
                msg = f"Rejected: {info}"
                record_turn(session_id, "tool_result", msg)
                return msg
            override_note = f" (Supervisor override: {info})"

        cursor.execute(
            """
            INSERT INTO compensation (passenger_id, flight_id, amount, currency, reason, issued_by, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'approved')
            """,
            (passenger["passenger_id"], flight["flight_id"], amount, currency, reason, issued_by),
        )
        conn.commit()

        result = (
            f"Approved: {amount} {currency} compensation issued to {passenger['full_name']} "
            f"for flight {flight_number}. Reason: {reason}. Issued by {issued_by}.{override_note}"
        )
        record_turn(session_id, "tool_result", result)
        return result

    finally:
        cursor.close()
        conn.close()


def rebook_passenger(
    booking_id: int,
    new_flight_number: str,
    requested_by: str,
) -> str:
    """
    Rebooks a passenger from their current (disrupted) flight onto a new flight.

    booking_id: the ID of the existing booking to rebook, must be a positive integer
    new_flight_number: the flight number the passenger is being moved to, e.g. BH101
    requested_by: the ops agent ID making this request (required for authorization)
    """
    if booking_id <= 0:
        return "Rejected: booking_id must be a positive integer."

    if not requested_by or not requested_by.strip() or not requested_by.startswith("agent_"):
        return "Rejected: rebooking requires an authenticated ops agent (expected an ID like agent_014)."

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # NOTE: f.flight_number added to this SELECT (it wasn't there
        # originally) -- needed to derive the session_id for the OLD
        # (disrupted) flight this rebooking belongs to. Nothing else about
        # this query changed.
        cursor.execute(
            """
            SELECT b.booking_id, b.passenger_id, b.flight_id, b.booking_status, b.fare_class,
                   f.status AS old_flight_status, f.flight_number AS old_flight_number, p.full_name
            FROM bookings b
            JOIN flights f ON b.flight_id = f.flight_id
            JOIN passengers p ON b.passenger_id = p.passenger_id
            WHERE b.booking_id = %s
            """,
            (booking_id,),
        )
        old_booking = cursor.fetchone()
        if old_booking is None:
            return f"Rejected: no booking found with ID {booking_id}."

        session_id = get_session_id(old_booking["old_flight_number"])

        if old_booking["old_flight_status"] not in ("disrupted", "delayed", "cancelled"):
            msg = (
                f"Rejected: booking {booking_id} is on a flight with status "
                f"'{old_booking['old_flight_status']}'. Rebooking is only allowed when the "
                "original flight is disrupted, delayed, or cancelled."
            )
            record_turn(session_id, "tool_result", msg)
            return msg

        if old_booking["booking_status"] == "rebooked":
            msg = f"Rejected: booking {booking_id} has already been rebooked."
            record_turn(session_id, "tool_result", msg)
            return msg

        cursor.execute(
            "SELECT flight_id, status FROM flights WHERE flight_number = %s",
            (new_flight_number,),
        )
        new_flight = cursor.fetchone()
        if new_flight is None:
            msg = f"Rejected: no flight found with number {new_flight_number}."
            record_turn(session_id, "tool_result", msg)
            return msg

        if new_flight["status"] not in ("scheduled", "delayed"):
            msg = (
                f"Rejected: cannot rebook onto flight {new_flight_number} because its status "
                f"is '{new_flight['status']}'."
            )
            record_turn(session_id, "tool_result", msg)
            return msg

        cursor.execute(
            """
            SELECT booking_id FROM bookings
            WHERE passenger_id = %s AND flight_id = %s AND booking_status = 'confirmed'
            """,
            (old_booking["passenger_id"], new_flight["flight_id"]),
        )
        if cursor.fetchone() is not None:
            msg = (
                f"Rejected: {old_booking['full_name']} is already booked on flight "
                f"{new_flight_number}. Double-booking is not allowed."
            )
            record_turn(session_id, "tool_result", msg)
            return msg

        cursor.execute(
            "UPDATE bookings SET booking_status = 'rebooked' WHERE booking_id = %s",
            (booking_id,),
        )
        cursor.execute(
            """
            INSERT INTO bookings (passenger_id, flight_id, seat_number, fare_class, booking_status)
            VALUES (%s, %s, 'TBD', %s, 'confirmed')
            """,
            (old_booking["passenger_id"], new_flight["flight_id"], old_booking["fare_class"]),
        )
        conn.commit()

        result = (
            f"Approved: {old_booking['full_name']} rebooked from booking {booking_id} "
            f"onto flight {new_flight_number}. Requested by {requested_by}."
        )
        record_turn(session_id, "tool_result", result)
        return result

    finally:
        cursor.close()
        conn.close()
