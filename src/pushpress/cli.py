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
