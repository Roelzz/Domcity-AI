# Domcity-AI

Unofficial client for the **[PushPress](https://www.pushpress.com)** members API
(`api.pushpress.com`), packaged as three things in one repo:

1. **A Python library** — `from pushpress import PushPressClient`
2. **A CLI** — `pushpress schedule`, `pushpress book …`, …
3. **An MCP server** — `pushpress-mcp`, so agents (Claude, pi, …) can read your
   schedule, see the workout of the day, and manage your bookings.

It wraps the same GraphQL backend that the `members.pushpress.com` web/mobile app
uses, with **persistent auth**: you provide your PushPress email + password once,
it logs in and caches the JWT in SQLite, and auto-refreshes it whenever it nears
expiry. No manual token juggling.

> Extracted from [`Roelzz/domcity_wrapper`](https://github.com/Roelzz/domcity_wrapper)
> — the web UI, booking-automation scheduler, Telegram reminders and Claude-OAuth
> were deliberately left out to keep this a lean, general-purpose client.

## Capabilities

| Area | Operations |
| --- | --- |
| Locations | `list_locations` |
| Trainings / class types | `list_class_types` |
| Schedule | `get_schedule` (classes, spots, booked status), `get_class_details` |
| Workout of the day | `get_workout_of_day` (WOD with structured `parts`), `get_workout_scores` |
| Member | `get_member_info`, `get_session_credits`, `get_tenant_info` |
| Bookings | `list_reservations`, `book_class`, `cancel_reservation`, `join_waitlist`, `leave_waitlist`, `get_class_roster` |
| Appointments | `list_appointment_types`, `list_appointment_packages`, `get_appointment_credits`, `get_appointment_history`, `get_appointment_schedule`, `book_appointment`, `cancel_appointment`, `reschedule_appointment`, `check_in_appointment` |
| Check-in | `check_in`, `get_attendance`, `get_attendance_stats` |
| Workout logging | `log_workout_score`, `get_score_history` |

### Waitlist support

The PushPress member API **does** expose waitlists (recovered by reverse-engineering
`members.pushpress.com/main.dart.js`):

- `join_waitlist(calendar_item_uuid)` — join the waitlist for a full class.
- `leave_waitlist(reservation_uuid)` — remove yourself from a waitlist.
- `get_class_roster(calendar_item_uuid)` — the class roster with `waitlist_priority`
  (0 = first in line) so you can see your position.
- `list_reservations` now includes `waitlisted` and `waitlist_until`.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Roelzz/Domcity-AI.git
cd Domcity-AI
uv sync
cp .env.example .env
```

Fill in `.env` (see [Configuration](#configuration)), then:

```bash
uv run pushpress --help
```

Or install globally:

```bash
uv tool install .
```

## Configuration

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PUSHPRESS_EMAIL` | ✅ | — | Your `members.pushpress.com` email |
| `PUSHPRESS_PASSWORD` | ✅ | — | Your members portal password |
| `TZ` | — | `Europe/Amsterdam` | Gym's local timezone |
| `DB_PATH` | — | `./data/pushpress.db` | Where the JWT is cached (mount as a volume in Coolify) |
| `MCP_TOKEN` | — | *(empty)* | Static bearer token gating the HTTP MCP endpoint |
| `MCP_HOST` | — | `0.0.0.0` | MCP HTTP bind address |
| `MCP_PORT` | — | `8000` | MCP HTTP port |
| `LOG_LEVEL` | — | `INFO` | Log verbosity |

> **Timezone gotcha**: PushPress serialises class times with a `+00:00` suffix,
> but the digits are the gym's **local wall-clock**, not UTC. Set `TZ` to the
> gym's timezone so times display and compare correctly.

## CLI

Every command prints **JSON** to stdout (safe for scripts and agents).

```bash
pushpress auth status                       # login/token state
pushpress locations                         # active gym locations
pushpress class-types --date 2026-09-30     # available trainings
pushpress schedule --start 2026-09-30 --end 2026-10-06
pushpress schedule --start 2026-09-30 --days 14
pushpress class-details <class-uuid>
pushpress workout-of-day --date 2026-09-30 --class-type <uid>
pushpress reservations
pushpress credits
pushpress me
pushpress book <class-uuid>                 # sign up for a class
pushpress cancel <reservation-uuid>         # remove yourself from a class
pushpress waitlist <class-uuid>             # join the waitlist for a full class
pushpress leave-waitlist <reservation-uuid> # leave a waitlist
pushpress roster <class-uuid>               # who's on the waitlist, in order
```

## MCP server

Two transports, one command:

```bash
# stdio (local MCP clients — Claude Desktop, pi, etc.)
pushpress-mcp

# streamable HTTP (host in Coolify, point remote agents at it)
pushpress-mcp --transport http --host 0.0.0.0 --port 8000
```

**Tools** (29): `list_locations`, `list_class_types`, `get_schedule`,
`get_class_details`, `get_member_info`, `list_reservations`,
`get_session_credits`, `get_tenant_info`, `get_workout_of_day`,
`get_workout_scores`, `book_class`, `cancel_reservation`, `join_waitlist`,
`leave_waitlist`, `get_class_roster`, `list_appointment_types`,
`list_appointment_packages`, `get_appointment_credits`,
`get_appointment_history`, `get_appointment_schedule`, `book_appointment`,
`cancel_appointment`, `reschedule_appointment`, `check_in_appointment`,
`check_in`, `get_attendance`, `get_attendance_stats`, `log_workout_score`,
`get_score_history`.

### Auth

- **stdio** — trusted local transport, no auth.
- **HTTP** — set `MCP_TOKEN` to protect the endpoint with a static bearer token
  (clients send `Authorization: Bearer <token>`). A `/healthz` route is always
  open for load-balancer health checks.

Connect an agent to the HTTP endpoint with the MCP URL `https://<your-domain>/mcp`
and the bearer token from `MCP_TOKEN`.

## Python library

```python
import asyncio
from datetime import date, timedelta

from pushpress import PushPressClient


async def main():
    client = PushPressClient(
        email="you@example.com",
        password="hunter2",  # or read from env — see config
        tz="Europe/Amsterdam",
        db_path="./data/pushpress.db",
    )
    classes = await client.list_schedule(date.today(), date.today() + timedelta(days=7))
    for c in classes:
        print(c.id, c.name, c.spots_available)
    await client.aclose()


asyncio.run(main())
```

All read methods are async and return `list[dict]` / pydantic models. The token is
loaded/refreshed transparently on first call and persisted to `db_path`.

## Deploy to Coolify

The repo includes a `Dockerfile`, `Procfile`, and `nixpacks.toml`, so both build
packs work.

1. Create a new Coolify app → source = this repo.
2. **Environment variables**: set `PUSHPRESS_EMAIL`, `PUSHPRESS_PASSWORD`, `TZ`,
   and `MCP_TOKEN` (a long random string).
3. **Persistent storage**: add a volume mount at **`/app/data`** so the cached
   JWT survives redeploys.
4. Expose port `8000` and point your domain/proxy at it.

The container runs `pushpress-mcp --transport http` on `0.0.0.0:8000` by default.

## Development

```bash
uv sync                 # install deps + editable package
uv run ruff check .     # lint
uv run ruff format .    # format
uv run pytest           # tests
```

## License

MIT
