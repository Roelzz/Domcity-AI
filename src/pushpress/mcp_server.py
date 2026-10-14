"""MCP server exposing the PushPress members API to agents (Claude, pi, …).

Runs over stdio (default, for local MCP clients) or streamable HTTP (for
hosting in Coolify and pointing remote agents at it). When ``MCP_TOKEN`` is
set, the HTTP endpoint is protected by a static bearer token; stdio is trusted
local-only and never gated.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import get_client
from .client import PushPressClient
from .config import settings

_client: PushPressClient | None = None


async def _get_client() -> PushPressClient:
    global _client
    if _client is None:
        _client = get_client()
    return _client


# --------------------------------------------------------------------------
# tools


def register_tools(mcp: FastMCP) -> None:
    @mcp.tool
    async def list_locations() -> list[dict]:
        """List all active gym locations (name + uuid)."""
        c = await _get_client()
        return await c.list_locations()

    @mcp.tool
    async def list_class_types(date_str: str | None = None) -> list[dict]:
        """List available class types (trainings) for a date (ISO YYYY-MM-DD,
        default today). Returns ``{id, name}`` — pass ``id`` to
        ``get_workout_of_day`` as the class type UID."""
        c = await _get_client()
        return await c.list_class_types(date_str)

    @mcp.tool
    async def get_schedule(
        start_date: str | None = None, end_date: str | None = None
    ) -> list[dict]:
        """List classes between two dates (inclusive, ISO YYYY-MM-DD).

        Defaults to today through 7 days ahead. Each class includes ``id`` (the
        calendar_item_uuid for ``book_class``), name, location, category,
        start/end times, instructor, spots available/total, and whether you have
        already booked it.
        """
        today = datetime.now().date()
        try:
            start = date.fromisoformat(start_date) if start_date else today
            end = date.fromisoformat(end_date) if end_date else today + timedelta(days=7)
        except ValueError as e:
            raise ToolError(f"bad date, expected YYYY-MM-DD: {e}") from e
        if end < start:
            raise ToolError("end_date must be on or after start_date")
        c = await _get_client()
        slots = await c.list_schedule(start, end)
        return [s.model_dump(mode="json") for s in slots]

    @mcp.tool
    async def get_class_details(uuid: str) -> dict:
        """Get details for a single class by its calendar item UUID."""
        c = await _get_client()
        return await c.get_class_details(uuid)

    @mcp.tool
    async def get_member_info() -> dict:
        """Return the logged-in member's profile: name, contact info, emergency
        contact, and subscriptions with session-credit usage."""
        c = await _get_client()
        return await c.get_member_info()

    @mcp.tool
    async def list_reservations() -> list[dict]:
        """List your upcoming bookings. Each has ``id`` (for
        ``cancel_reservation``), class name, location, times, instructor, and
        whether it is cancellable."""
        c = await _get_client()
        res = await c.list_reservations()
        return [r.model_dump(mode="json") for r in res]

    @mcp.tool
    async def get_session_credits() -> list[dict]:
        """Show session/credit balance per subscription for the current billing
        period (limit, used, remaining, period window)."""
        c = await _get_client()
        usage = await c.list_subscription_usage()
        return [u.model_dump(mode="json") for u in usage]

    @mcp.tool
    async def get_tenant_info() -> dict:
        """Return the account context: client/user/subscription identifiers."""
        c = await _get_client()
        return (await c.get_tenant()).model_dump(mode="json")

    @mcp.tool
    async def get_workout_of_day(date_str: str, class_type_uid: str | None = None) -> list[dict]:
        """Get the workout of the day for a class type on a date (ISO YYYY-MM-DD).

        Returns workout metadata plus a structured ``parts`` list (Warm-Up, A, B,
        METCON, …) with ``title``, ``description`` (movements/reps/time caps),
        ``score_type``, and notes. Use ``list_class_types`` to find the class
        type UID.
        """
        c = await _get_client()
        return await c.get_workout_of_day(date_str, class_type_uid)

    @mcp.tool
    async def get_workout_scores(workout_part_uid: str, workout_uid: str) -> dict:
        """Get member scores for a workout part. Often empty if the gym doesn't
        log scores in PushPress."""
        c = await _get_client()
        return await c.get_workout_scores(workout_part_uid, workout_uid)

    @mcp.tool
    async def book_class(calendar_item_uuid: str) -> dict:
        """Book yourself into a class by its calendar_item_uuid (the ``id`` from
        ``get_schedule``). Idempotent. Returns ``ok``, ``reservation_id``, and a
        ``message``."""
        c = await _get_client()
        result = await c.book(calendar_item_uuid)
        return result.model_dump(mode="json")

    @mcp.tool
    async def cancel_reservation(reservation_uuid: str) -> dict:
        """Cancel a booking by its reservation ``id`` (from ``list_reservations``).
        Returns ``{"ok": bool}``."""
        c = await _get_client()
        return {"ok": await c.cancel(reservation_uuid)}

    @mcp.tool
    async def join_waitlist(
        calendar_item_uuid: str, waitlist_expiration_mins: int | None = None
    ) -> dict:
        """Join the waitlist for a class (use when the class is full). Returns
        ``ok``, ``reservation_id`` (now a waitlist entry id), and ``message``."""
        c = await _get_client()
        result = await c.join_waitlist(calendar_item_uuid, waitlist_expiration_mins)
        return result.model_dump(mode="json")

    @mcp.tool
    async def leave_waitlist(reservation_uuid: str) -> dict:
        """Remove yourself from a waitlist by the reservation/waitlist entry ``id``
        (from ``list_reservations``). Returns ``{"ok": bool}``."""
        c = await _get_client()
        return {"ok": await c.leave_waitlist(reservation_uuid)}

    @mcp.tool
    async def get_class_roster(calendar_item_uuid: str) -> list[dict]:
        """Return a class roster with waitlist positions. Each entry has ``name``,
        ``status``, and ``waitlist_priority`` (0 = first on the waitlist, ``null``
        = confirmed/not waitlisted). Use this to see which number you are on the
        waitlist."""
        c = await _get_client()
        return await c.get_class_roster(calendar_item_uuid)

    # --- appointments ---

    @mcp.tool
    async def list_appointment_types() -> list[dict]:
        """List appointment-based service types (personal training, physio, etc.)."""
        c = await _get_client()
        return await c.list_appointment_types()

    @mcp.tool
    async def list_appointment_packages() -> list[dict]:
        """List appointment packages (e.g. PT session bundles)."""
        c = await _get_client()
        return await c.list_appointment_packages()

    @mcp.tool
    async def get_appointment_credits() -> list[dict]:
        """Appointment-credit counts per appointment type."""
        c = await _get_client()
        return await c.get_appointment_credits()

    @mcp.tool
    async def get_appointment_history() -> list[dict]:
        """My upcoming/past appointments."""
        c = await _get_client()
        return await c.get_appointment_history()

    @mcp.tool
    async def get_appointment_schedule(
        appointment_type_uuid: str, date_str: str, staff_uuid: str | None = None
    ) -> list[dict]:
        """Available appointment slots for a type on a date (ISO YYYY-MM-DD)."""
        c = await _get_client()
        return await c.get_appointment_schedule(appointment_type_uuid, date_str, staff_uuid)

    @mcp.tool
    async def book_appointment(
        appointment_type_uuid: str,
        start_time: str,
        end_time: str,
        staff_uuid: str | None = None,
    ) -> dict:
        """Book an appointment (PT, etc.). ``start_time``/``end_time`` are ISO
        datetimes. Returns ``ok``, ``reservation_id``, and ``message``."""
        c = await _get_client()
        result = await c.book_appointment(appointment_type_uuid, start_time, end_time, staff_uuid)
        return result.model_dump(mode="json")

    @mcp.tool
    async def cancel_appointment(appointment_uuid: str) -> dict:
        """Cancel an appointment by its uuid. Returns ``{"ok": bool}``."""
        c = await _get_client()
        return {"ok": await c.cancel_appointment(appointment_uuid)}

    @mcp.tool
    async def reschedule_appointment(
        appointment_uuid: str, start_time: str, end_time: str, staff_id: str | None = None
    ) -> dict:
        """Reschedule an appointment. Returns ``{"ok": bool}``."""
        c = await _get_client()
        return {
            "ok": await c.reschedule_appointment(appointment_uuid, start_time, end_time, staff_id)
        }

    @mcp.tool
    async def check_in_appointment(appointment_uuid: str) -> dict:
        """Check in to an appointment. Returns ``{"ok": bool}``."""
        c = await _get_client()
        return {"ok": await c.check_in_appointment(appointment_uuid)}

    # --- check-in ---

    @mcp.tool
    async def check_in(reservation_id: str, source: str | None = None) -> dict:
        """Self check-in to a booked class by its reservation ``id``. Returns
        ``{"ok": bool}``."""
        c = await _get_client()
        return {"ok": await c.check_in(reservation_id, source)}

    @mcp.tool
    async def get_attendance(start_date: str, end_date: str) -> list[dict]:
        """My class attendance history between two dates (ISO YYYY-MM-DD)."""
        c = await _get_client()
        return await c.get_attendance(start_date, end_date)

    @mcp.tool
    async def get_attendance_stats() -> dict:
        """Class attendance stats (week/month/year/allTime counts)."""
        c = await _get_client()
        return await c.get_attendance_stats()

    # --- workout logging ---

    @mcp.tool
    async def log_workout_score(
        workout_uid: str,
        workout_part_uid: str,
        sets: list[dict] | None = None,
        athlete_comment: str | None = None,
        division: str | None = None,
        class_type_id: float | None = None,
        date_str: str | None = None,
    ) -> dict:
        """Log a workout score for a WOD part. Returns ``ok`` + ``reservation_id``."""
        c = await _get_client()
        result = await c.log_workout_score(
            workout_uid,
            workout_part_uid,
            sets,
            athlete_comment,
            division,
            class_type_id,
            date_str,
        )
        return result.model_dump(mode="json")

    @mcp.tool
    async def get_score_history(keyword: str | None = None) -> list[dict]:
        """My workout score history."""
        c = await _get_client()
        return await c.get_score_history(keyword)


def build_mcp(token: str | None) -> FastMCP:
    auth = None
    if token:
        auth = StaticTokenVerifier({token: {"client_id": "pushpress", "scopes": []}})
    mcp = FastMCP(
        "PushPress", auth=auth, instructions="PushPress members API: schedule, workouts, bookings."
    )
    register_tools(mcp)

    @mcp.custom_route("/healthz", methods=["GET"])
    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pushpress-mcp",
        description="PushPress members API MCP server.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http"],
        default="stdio",
        help="Transport to serve (default: stdio)",
    )
    parser.add_argument("--host", default=settings.mcp_host, help="Bind host for HTTP")
    parser.add_argument("--port", type=int, default=settings.mcp_port, help="Port for HTTP")
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path")
    parser.add_argument("--token", default=settings.mcp_token, help="Bearer token for HTTP")
    args = parser.parse_args(argv)

    token = args.token or settings.mcp_token
    mcp = build_mcp(token)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport=args.transport,
            host=args.host,
            port=args.port,
            path=args.path,
        )


if __name__ == "__main__":
    main()
