"""Command-line interface for the PushPress members API.

Every command prints JSON to stdout (safe for agents and scripts). Credentials
are read from the environment or a local ``.env`` file (``PUSHPRESS_EMAIL`` /
``PUSHPRESS_PASSWORD``); the JWT is cached in SQLite so repeated invocations
don't re-login.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

import typer

from . import __version__
from .client import PushPressClient
from .config import settings

app = typer.Typer(
    name="pushpress",
    help="Unofficial PushPress members API client.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"pushpress {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit", callback=_version_callback
    ),
) -> None:
    """PushPress members API — schedule, workouts, and bookings."""


def _require_config() -> None:
    if not settings.configured:
        typer.echo(
            "PUSHPRESS_EMAIL / PUSHPRESS_PASSWORD not set.\n"
            "Copy .env.example to .env and fill them in, or export them.",
            err=True,
        )
        raise typer.Exit(code=1)


def _print_json(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str, ensure_ascii=False))


def _run(fn: Callable[[PushPressClient], Awaitable[Any]]) -> None:
    _require_config()
    client = PushPressClient(
        email=settings.pushpress_email,
        password=settings.pushpress_password,
        tz=settings.tz,
        db_path=settings.db_path,
    )

    async def _impl() -> Any:
        try:
            return await fn(client)
        finally:
            await client.aclose()

    _print_json(asyncio.run(_impl()))


# --------------------------------------------------------------------------
# auth

_auth_app = typer.Typer(help="Inspect login state.", no_args_is_help=True)
app.add_typer(_auth_app, name="auth")


@_auth_app.command("status")
def auth_status() -> None:
    """Report the current login/token state."""
    _require_config()
    client = PushPressClient(
        email=settings.pushpress_email,
        password=settings.pushpress_password,
        tz=settings.tz,
        db_path=settings.db_path,
    )

    async def _impl() -> dict:
        try:
            await client.ensure_token()
        except Exception as e:
            return {"logged_in": False, "error": str(e)}
        finally:
            await client.aclose()
        return {
            "logged_in": True,
            "user_uuid": client.token_user_uuid(),
            "client_uuid": client.token_client_uuid(),
            "token_expires_at": client.token_expiry().isoformat()
            if client.token_expiry()
            else None,
        }

    _print_json(asyncio.run(_impl()))


# --------------------------------------------------------------------------
# reads


@app.command()
def locations() -> None:
    """List active gym locations."""
    _run(lambda c: c.list_locations())


@app.command()
def class_types(
    date_str: str = typer.Option(None, "--date", help="ISO date (YYYY-MM-DD)"),
) -> None:
    """List available class types (trainings)."""
    _run(lambda c: c.list_class_types(date_str))


@app.command()
def schedule(
    start: str = typer.Option(None, "--start", help="ISO date (default today)"),
    end: str = typer.Option(None, "--end", help="ISO date (default start+7d)"),
    days: int = typer.Option(None, "--days", help="Number of days from start"),
) -> None:
    """List classes (trainings) in a date range, with spots and booked status."""
    today = datetime.now().date()
    start_d = date.fromisoformat(start) if start else today
    if end:
        end_d = date.fromisoformat(end)
    elif days is not None:
        end_d = start_d + timedelta(days=max(days, 0))
    else:
        end_d = start_d + timedelta(days=7)
    if end_d < start_d:
        raise typer.BadParameter("end must be on or after start")

    def _impl(c: PushPressClient):
        return c.list_schedule(start_d, end_d)

    _run(_impl)


@app.command()
def class_details(uuid: str = typer.Argument(..., help="Class (calendar item) UUID")) -> None:
    """Get details for a single class."""
    _run(lambda c: c.get_class_details(uuid))


@app.command()
def reservations() -> None:
    """List upcoming bookings."""
    _run(lambda c: c.list_reservations())


@app.command()
def credits() -> None:
    """Show session-credit balance for the current billing period."""
    _run(lambda c: c.list_subscription_usage())


@app.command()
def me() -> None:
    """Show the logged-in member profile."""
    _run(lambda c: c.get_member_info())


@app.command()
def workout_of_day(
    date_str: str = typer.Option(..., "--date", help="ISO date (YYYY-MM-DD)"),
    class_type: str = typer.Option(None, "--class-type", help="Class type UID (see `class-types`)"),
) -> None:
    """Get the workout of the day for a class type."""
    _run(lambda c: c.get_workout_of_day(date_str, class_type))


@app.command()
def workout_scores(
    workout_part_uid: str = typer.Option(..., "--workout-part-uid"),
    workout_uid: str = typer.Option(..., "--workout-uid"),
) -> None:
    """Get member scores for a workout part (often empty)."""
    _run(lambda c: c.get_workout_scores(workout_part_uid, workout_uid))


# --------------------------------------------------------------------------
# writes


@app.command()
def book(calendar_item_uuid: str = typer.Argument(..., help="Class UUID from `schedule`")) -> None:
    """Book yourself into a class."""
    _run(lambda c: c.book(calendar_item_uuid))


@app.command()
def cancel(
    reservation_uuid: str = typer.Argument(..., help="Reservation UUID from `reservations`"),
) -> None:
    """Cancel a booking."""

    async def _impl(c: PushPressClient) -> dict:
        return {"ok": await c.cancel(reservation_uuid)}

    _run(_impl)


@app.command()
def waitlist(
    calendar_item_uuid: str = typer.Argument(..., help="Class UUID from `schedule`"),
    expiration_mins: int = typer.Option(
        None, "--expiration-mins", help="How long the waitlist spot is held"
    ),
) -> None:
    """Join the waitlist for a full class."""
    _run(lambda c: c.join_waitlist(calendar_item_uuid, expiration_mins))


@app.command()
def leave_waitlist(
    reservation_uuid: str = typer.Argument(..., help="Waitlist entry UUID from `reservations`"),
) -> None:
    """Remove yourself from a waitlist."""

    async def _impl(c: PushPressClient) -> dict:
        return {"ok": await c.leave_waitlist(reservation_uuid)}

    _run(_impl)


@app.command()
def roster(
    calendar_item_uuid: str = typer.Argument(..., help="Class UUID from `schedule`"),
) -> None:
    """Show a class roster with waitlist positions (who's on the waitlist, in order)."""
    _run(lambda c: c.get_class_roster(calendar_item_uuid))


# --------------------------------------------------------------------------
# appointments


@app.command()
def appointment_types() -> None:
    """List appointment-based service types (PT, physio, etc.)."""
    _run(lambda c: c.list_appointment_types())


@app.command()
def appointment_packages() -> None:
    """List appointment packages (e.g. PT session bundles)."""
    _run(lambda c: c.list_appointment_packages())


@app.command()
def appointment_credits() -> None:
    """Appointment-credit counts per appointment type."""
    _run(lambda c: c.get_appointment_credits())


@app.command()
def appointment_history() -> None:
    """My upcoming/past appointments."""
    _run(lambda c: c.get_appointment_history())


@app.command()
def appointment_schedule(
    appointment_type_uuid: str = typer.Argument(..., help="Appointment type UUID"),
    date_str: str = typer.Option(..., "--date", help="ISO date YYYY-MM-DD"),
    staff_uuid: str = typer.Option(None, "--staff", help="Optional staff UUID"),
) -> None:
    """Available appointment slots for a type on a date."""
    _run(lambda c: c.get_appointment_schedule(appointment_type_uuid, date_str, staff_uuid))


@app.command()
def book_appointment(
    appointment_type_uuid: str = typer.Argument(..., help="Appointment type UUID"),
    start_time: str = typer.Option(..., "--start", help="ISO start datetime"),
    end_time: str = typer.Option(..., "--end", help="ISO end datetime"),
    staff_uuid: str = typer.Option(None, "--staff", help="Optional staff UUID"),
) -> None:
    """Book an appointment (PT session, etc.)."""
    _run(lambda c: c.book_appointment(appointment_type_uuid, start_time, end_time, staff_uuid))


@app.command()
def cancel_appointment(
    appointment_uuid: str = typer.Argument(..., help="Appointment UUID"),
) -> None:
    """Cancel an appointment."""

    async def _impl(c: PushPressClient) -> dict:
        return {"ok": await c.cancel_appointment(appointment_uuid)}

    _run(_impl)


@app.command()
def reschedule_appointment(
    appointment_uuid: str = typer.Argument(..., help="Appointment UUID"),
    start_time: str = typer.Option(..., "--start", help="ISO start datetime"),
    end_time: str = typer.Option(..., "--end", help="ISO end datetime"),
    staff_id: str = typer.Option(None, "--staff-id", help="Optional staff UUID"),
) -> None:
    """Reschedule an appointment."""

    async def _impl(c: PushPressClient) -> dict:
        return {
            "ok": await c.reschedule_appointment(appointment_uuid, start_time, end_time, staff_id)
        }

    _run(_impl)


@app.command()
def check_in_appointment(
    appointment_uuid: str = typer.Argument(..., help="Appointment UUID"),
) -> None:
    """Check in to an appointment."""

    async def _impl(c: PushPressClient) -> dict:
        return {"ok": await c.check_in_appointment(appointment_uuid)}

    _run(_impl)


# --------------------------------------------------------------------------
# check-in


@app.command()
def check_in(
    reservation_id: str = typer.Argument(..., help="Reservation UUID from `reservations`"),
) -> None:
    """Self check-in to a booked class."""

    async def _impl(c: PushPressClient) -> dict:
        return {"ok": await c.check_in(reservation_id)}

    _run(_impl)


@app.command()
def attendance(
    start_date: str = typer.Option(..., "--start", help="ISO date YYYY-MM-DD"),
    end_date: str = typer.Option(..., "--end", help="ISO date YYYY-MM-DD"),
) -> None:
    """My class attendance history between two dates."""
    _run(lambda c: c.get_attendance(start_date, end_date))


@app.command()
def attendance_stats() -> None:
    """Class attendance stats (week/month/year/allTime)."""
    _run(lambda c: c.get_attendance_stats())


# --------------------------------------------------------------------------
# workout logging


@app.command()
def log_score(
    workout_uid: str = typer.Option(..., "--workout-uid"),
    workout_part_uid: str = typer.Option(..., "--workout-part-uid"),
    sets: str = typer.Option("[]", "--sets", help='JSON list e.g. \'[{"weight":50,"reps":10}]\''),
    comment: str = typer.Option(None, "--comment"),
    division: str = typer.Option(None, "--division"),
    date_str: str = typer.Option(None, "--date", help="ISO date YYYY-MM-DD"),
) -> None:
    """Log a workout score for a WOD part."""
    import json as _json

    sets_list = _json.loads(sets)
    _run(
        lambda c: c.log_workout_score(
            workout_uid,
            workout_part_uid,
            sets=sets_list,
            athlete_comment=comment,
            division=division,
            date_str=date_str,
        )
    )


@app.command()
def score_history(keyword: str = typer.Option(None, "--keyword")) -> None:
    """My workout score history."""
    _run(lambda c: c.get_score_history(keyword))
