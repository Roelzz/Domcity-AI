"""Async PushPress members API client.

Wraps the GraphQL backend at ``api.pushpress.com/v2/graph/graphql``. Auth is a
bearer JWT (HS256, ~60-day lifetime) minted by POSTing email+password to
``/v2/auth/login`` and cached in SQLite via :class:`~pushpress.storage.TokenStorage`.

The client refreshes the token automatically: it re-logs in whenever the cached
token is expired or near expiry, and retries once on a 401/403 (or a GraphQL
"token" error) when the cached token is actually close to expiry.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time as time_mod
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from loguru import logger

from . import graphql as g
from .models import BookingResult, ClassSlot, Reservation, SubscriptionUsage, TenantContext
from .storage import TokenStorage

_RECENT_LOGIN_WINDOW_SEC = 30  # skip force_refresh if a fresh login just happened
_LOGIN_COOLDOWN_SEC = 60  # don't hammer /auth/login more than once a minute

# In-memory cache TTLs (seconds).
_SCHEDULE_TTL_SEC = 60
_RESERVATIONS_TTL_SEC = 15
_USAGE_TTL_SEC = 30


class PushPressError(RuntimeError):
    """Raised when the PushPress API returns an error or auth fails."""


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Not a JWT (expected 3 segments)")
    pad = "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(parts[1] + pad))


def _now() -> float:
    return time_mod.monotonic()


class PushPressClient:
    """Stateful async client for one member account."""

    def __init__(
        self,
        email: str,
        password: str,
        tz: str = "Europe/Amsterdam",
        db_path: str | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self.tz_name = tz
        self.storage = TokenStorage(db_path or "./data/pushpress.db")

        self._http: httpx.AsyncClient | None = None
        self._token = ""
        self._expiry: datetime | None = None
        self._token_lock = asyncio.Lock()
        self._tenant: TenantContext | None = None
        self._last_login_success_at: datetime | None = None
        self._last_login_attempt_at: datetime | None = None

        self._schedule_cache: dict[str, tuple[float, list[ClassSlot]]] = {}
        self._schedule_locks: dict[str, asyncio.Lock] = {}
        self._reservations_cache: tuple[float, list[Reservation]] | None = None
        self._reservations_lock = asyncio.Lock()
        self._usage_cache: tuple[float, list[SubscriptionUsage]] | None = None
        self._usage_lock = asyncio.Lock()

    # ------------------------------------------------------------------ infra

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=20,
                http2=False,
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    def active_token(self) -> str:
        return self._token

    def token_expiry(self) -> datetime | None:
        return self._expiry

    def token_user_uuid(self) -> str:
        if not self._token:
            return ""
        return decode_jwt_payload(self._token).get("sub", "")

    def token_client_uuid(self) -> str:
        if not self._token:
            return ""
        return decode_jwt_payload(self._token).get("clientUuid", "")

    def _set_active_token(self, token: str) -> None:
        self._token = token
        try:
            self._expiry = datetime.fromtimestamp(decode_jwt_payload(token)["exp"], tz=UTC)
        except Exception:
            self._expiry = None
        self._tenant = None  # force tenant re-lookup under the new token

    # ------------------------------------------------------------------- auth

    async def login(self) -> tuple[str, datetime]:
        """Exchange email+password for a fresh token. Returns ``(token, expiry)``."""
        if not self.email or not self.password:
            raise PushPressError("PUSHPRESS_EMAIL / PUSHPRESS_PASSWORD not set — see .env.example")
        r = await self._get_http().post(
            g.LOGIN_URL,
            headers=g.DEFAULT_HEADERS,
            json={"username": self.email, "password": self.password},
        )
        if r.status_code != 200:
            raise PushPressError(f"PushPress login failed: HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        token = data.get("accessToken")
        if not token:
            raise PushPressError(f"Login response missing accessToken: keys={list(data.keys())}")
        expiry = datetime.fromtimestamp(decode_jwt_payload(token)["exp"], tz=UTC)
        logger.info("PushPress login OK, token expires {}", expiry.isoformat())
        return token, expiry

    async def ensure_token(self) -> str:
        """Return a valid token, loading from cache or logging in as needed."""
        if self._token and self._expiry and self._expiry > datetime.now(UTC):
            return self._token
        async with self._token_lock:
            if self._token and self._expiry and self._expiry > datetime.now(UTC):
                return self._token
            await self._refresh_locked("ensure_token")
            return self._token

    async def force_refresh(self) -> str:
        """Drop the cached token and log in fresh (coalesced across callers)."""
        async with self._token_lock:
            if (
                self._last_login_success_at
                and (datetime.now(UTC) - self._last_login_success_at).total_seconds()
                < _RECENT_LOGIN_WINDOW_SEC
                and self._token
            ):
                return self._token
            await self._refresh_locked("force_refresh")
            return self._token

    async def _refresh_locked(self, reason: str) -> None:
        # Try the persisted cache first (a sibling process may have refreshed).
        cached, cached_expiry = self.storage.load()
        if (
            cached
            and cached != self._token
            and cached_expiry
            and cached_expiry > datetime.now(UTC) + timedelta(minutes=5)
        ):
            self._set_active_token(cached)
            logger.info(
                "Token loaded from cache ({}), expires {}", reason, cached_expiry.isoformat()
            )
            return

        if (
            self._last_login_attempt_at
            and (datetime.now(UTC) - self._last_login_attempt_at).total_seconds()
            < _LOGIN_COOLDOWN_SEC
        ):
            if self._token:
                return
            raise PushPressError("login cooldown active, no cached token to fall back on")

        self._last_login_attempt_at = datetime.now(UTC)
        token, expiry = await self.login()
        self._set_active_token(token)
        self._last_login_success_at = datetime.now(UTC)
        self.storage.save(token, expiry)

    def _token_near_expiry(self) -> bool:
        if not self._expiry:
            return True
        return self._expiry <= datetime.now(UTC) + timedelta(minutes=5)

    # ------------------------------------------------------------------ tenant

    async def get_tenant(self) -> TenantContext:
        if self._tenant is not None:
            return self._tenant
        if not self.email or not self.password:
            raise PushPressError("PUSHPRESS_EMAIL / PUSHPRESS_PASSWORD not set")
        await self.ensure_token()
        client_uuid = self.token_client_uuid()
        user_uuid = self.token_user_uuid()
        profile = await self._gql(
            g.QUERY_PROFILE, {"clientUuid": client_uuid, "userUuid": user_uuid}
        )
        p = profile["profile"]
        sub = self._pick_active_subscription(p.get("subscriptions") or [])
        self._tenant = TenantContext(
            client_uuid=client_uuid,
            user_uuid=user_uuid,
            client_user_uuid=p["clientUserUuid"],
            subscription_uuid=sub["subscriptionUuid"] if sub else "",
        )
        logger.info(
            "PushPress tenant loaded: client={}, user={}, sub={}",
            self._tenant.client_uuid,
            self._tenant.user_uuid,
            self._tenant.subscription_uuid or "(none)",
        )
        return self._tenant

    @staticmethod
    def _pick_active_subscription(subs: list[dict]) -> dict | None:
        for s in subs:
            if s.get("active") and s.get("status") == "active":
                return s
        return subs[0] if subs else None

    # ------------------------------------------------------------------ reads

    async def list_locations(self) -> list[dict]:
        data = await self._gql(g.QUERY_LOCATIONS, {})
        return [
            {"uuid": loc["uuid"], "name": loc["name"], "is_active": loc.get("isActive")}
            for loc in (data.get("locations") or [])
        ]

    async def list_class_types(self, date_str: str | None = None) -> list[dict]:
        if not date_str:
            date_str = datetime.now(self.tz).strftime("%Y-%m-%d")
        data = await self._gql(g.QUERY_CLASS_TYPES, {"date": date_str})
        return [{"id": ct["id"], "name": ct["name"]} for ct in (data.get("classTypes") or [])]

    async def list_schedule(self, start: date, end: date) -> list[ClassSlot]:
        """Fetch classes in ``[start, end]`` (inclusive). Days fetched in parallel."""
        days: list[date] = []
        d = start
        while d <= end:
            days.append(d)
            d += timedelta(days=1)
        chunks = await asyncio.gather(*[self._get_day(day) for day in days])
        out: list[ClassSlot] = []
        for chunk in chunks:
            out.extend(chunk)
        return out

    async def _get_day(self, day: date) -> list[ClassSlot]:
        key = day.isoformat()
        cached = self._schedule_cache.get(key)
        if cached and _now() - cached[0] < _SCHEDULE_TTL_SEC:
            return cached[1]
        lock = self._schedule_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._schedule_cache.get(key)
            if cached and _now() - cached[0] < _SCHEDULE_TTL_SEC:
                return cached[1]
            data = await self._gql(g.QUERY_CLASSES, {"classDate": key})
            slots: list[ClassSlot] = []
            for c in data.get("classes") or []:
                try:
                    slots.append(self._to_slot(c))
                except Exception as e:
                    logger.warning("Skip malformed class {}: {}", c.get("uuid"), e)
            self._schedule_cache[key] = (_now(), slots)
            return slots

    async def get_class_details(self, uuid: str) -> dict:
        data = await self._gql(g.QUERY_CLASS, {"uuid": uuid})
        ci = data.get("calendarItem") or {}
        coach = ci.get("mainCoach") or {}
        coach_name = (
            " ".join(x for x in [coach.get("firstName"), coach.get("lastName")] if x) or None
        )
        location = (ci.get("location") or {}).get("name") or ""
        return {
            "uuid": ci.get("uuid"),
            "title": ci.get("title"),
            "start_datetime": ci.get("startDatetime"),
            "end_datetime": ci.get("endDatetime"),
            "attendance_cap": ci.get("attendanceCap"),
            "spots_available": ci.get("spotsAvailable"),
            "location": location,
            "instructor": coach_name,
            "description": ci.get("description") or "",
        }

    async def get_member_info(self) -> dict:
        await self.ensure_token()
        data = await self._gql(
            g.QUERY_MEMBER_INFO,
            {"clientUuid": self.token_client_uuid(), "userUuid": self.token_user_uuid()},
        )
        profile = data.get("profile") or {}
        subs = []
        for s in profile.get("subscriptions") or []:
            usage = s.get("currentPeriodUsage") or {}
            subs.append(
                {
                    "subscription_uuid": s.get("subscriptionUuid"),
                    "plan": s.get("plan"),
                    "status": s.get("status"),
                    "active": s.get("active"),
                    "limit": usage.get("limit"),
                    "reservations": usage.get("reservations"),
                    "checkins": usage.get("checkins"),
                    "period": usage.get("period"),
                    "period_start": usage.get("periodStart"),
                    "period_end": usage.get("periodEnd"),
                }
            )
        return {
            "first_name": profile.get("firstName"),
            "last_name": profile.get("lastName"),
            "email": profile.get("email"),
            "phone": profile.get("phone"),
            "address1": profile.get("address1"),
            "address2": profile.get("address2"),
            "emergency_name": profile.get("emergencyName"),
            "emergency_phone": profile.get("emergencyPhone"),
            "subscriptions": subs,
        }

    async def list_reservations(self) -> list[Reservation]:
        if (
            self._reservations_cache
            and _now() - self._reservations_cache[0] < _RESERVATIONS_TTL_SEC
        ):
            return self._reservations_cache[1]
        async with self._reservations_lock:
            if (
                self._reservations_cache
                and _now() - self._reservations_cache[0] < _RESERVATIONS_TTL_SEC
            ):
                return self._reservations_cache[1]
            data = await self._gql(g.QUERY_RESERVATIONS, {})
            out: list[Reservation] = []
            for r in data.get("reservations") or []:
                if r.get("isCancelled") or not r.get("isActive"):
                    continue
                try:
                    out.append(self._to_reservation(r))
                except Exception as e:
                    logger.warning("Skip malformed reservation {}: {}", r.get("uuid"), e)
            self._reservations_cache = (_now(), out)
            return out

    async def list_subscription_usage(self) -> list[SubscriptionUsage]:
        if self._usage_cache and _now() - self._usage_cache[0] < _USAGE_TTL_SEC:
            return self._usage_cache[1]
        async with self._usage_lock:
            if self._usage_cache and _now() - self._usage_cache[0] < _USAGE_TTL_SEC:
                return self._usage_cache[1]
            await self.ensure_token()
            data = await self._gql(
                g.QUERY_SUBSCRIPTION_USAGE,
                {"clientUuid": self.token_client_uuid(), "userUuid": self.token_user_uuid()},
            )
            profile = data.get("profile") or {}
            out: list[SubscriptionUsage] = []
            for s in profile.get("subscriptions") or []:
                try:
                    out.append(self._to_subscription_usage(s))
                except Exception as e:
                    logger.warning(
                        "Skip malformed subscription {}: {}", s.get("subscriptionUuid"), e
                    )
            self._usage_cache = (_now(), out)
            return out

    async def get_workout_of_day(
        self, date_str: str, class_type_uid: str | None = None
    ) -> list[dict]:
        """Return the workout of the day for a class type on a date, including
        the structured ``parts`` list (Warm-Up, A, B, METCON, …)."""
        data = await self._gql(
            g.QUERY_WORKOUT_OF_DAY, {"classTypeUid": class_type_uid, "date": date_str}
        )
        out: list[dict] = []
        for w in data.get("getWorkoutOfDay") or []:
            parts = []
            for p in w.get("parts") or []:
                parts.append(
                    {
                        "workout_part_uid": p.get("workoutPartUid"),
                        "title": p.get("title"),
                        "description": p.get("description"),
                        "score_type": p.get("scoreType"),
                        "athletes_notes": p.get("athletesNotes"),
                        "coaches_notes": p.get("coachesNotes"),
                        "score_count": p.get("scoreCount"),
                        "sets": p.get("sets"),
                        "default_reps": p.get("defaultReps"),
                    }
                )
            out.append(
                {
                    "uid": w.get("uid"),
                    "workout_uid": w.get("workoutUid"),
                    "workout_state": w.get("workoutState"),
                    "workout_program_group_id": w.get("workoutProgramGroupId"),
                    "workout_program_template_id": w.get("workoutProgramTemplateId"),
                    "image_url": w.get("imageUrl"),
                    "video_url_id": w.get("videoUrlId"),
                    "day": w.get("day"),
                    "parts": parts,
                }
            )
        return out

    async def get_workout_scores(self, workout_part_uid: str, workout_uid: str) -> dict:
        data = await self._gql(
            g.QUERY_WORKOUT_SCORES,
            {"workoutPartUid": workout_part_uid, "workoutUid": workout_uid},
        )
        result = data.get("workoutGetScores") or {}
        return {"scores": result.get("scores") or [], "topScore": result.get("topScore")}

    # ----------------------------------------------------------------- writes

    async def book(self, calendar_item_uuid: str) -> BookingResult:
        tenant = await self.get_tenant()
        if not tenant.subscription_uuid:
            return BookingResult(ok=False, message="No active subscription found on this account")
        try:
            existing = await self.list_reservations()
        except Exception as e:
            logger.warning("book: pre-check list_reservations failed, proceeding: {}", e)
        else:
            if any(r.class_id == calendar_item_uuid for r in existing):
                return BookingResult(ok=False, message="already reserved")
        variables = {
            "clientUserUuid": tenant.client_user_uuid,
            "calendarItemUuid": calendar_item_uuid,
            "subscriptionUuid": tenant.subscription_uuid,
            "source": "domcity_ai",
        }
        try:
            data = await self._gql(g.MUTATION_BOOK, variables)
            uuid = (data.get("createReservation") or {}).get("uuid")
            if uuid:
                self._invalidate_reservations_cache()
                self._invalidate_schedule_cache()
                return BookingResult(ok=True, reservation_id=uuid, message="booked")
            return BookingResult(ok=False, message="createReservation returned no uuid")
        except PushPressError as e:
            return BookingResult(ok=False, message=str(e))

    async def cancel(self, reservation_uuid: str) -> bool:
        try:
            data = await self._gql(g.MUTATION_CANCEL, {"reservationId": reservation_uuid})
            ok = bool((data.get("cancelReservation") or {}).get("uuid"))
            if ok:
                self._invalidate_reservations_cache()
                self._invalidate_schedule_cache()
            return ok
        except PushPressError as e:
            logger.warning("cancel failed: {}", e)
            return False

    def _invalidate_schedule_cache(self) -> None:
        self._schedule_cache.clear()

    def _invalidate_reservations_cache(self) -> None:
        self._reservations_cache = None

    # ------------------------------------------------------------------ parsing

    def _parse_dt(self, v: Any) -> datetime:
        """Parse a PushPress datetime. The ``+00:00``/``Z`` suffix is misleading —
        the digits are the gym's local wall-clock, not UTC. Re-attach the
        configured local zone."""
        if isinstance(v, datetime):
            dt = v
        elif isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        else:
            raise ValueError(f"Cannot parse datetime: {v!r}")
        return dt.replace(tzinfo=self.tz)

    def _to_slot(self, c: dict) -> ClassSlot:
        coach = c.get("mainCoach") or {}
        coach_name = (
            " ".join(x for x in [coach.get("firstName"), coach.get("lastName")] if x) or None
        )
        location = (c.get("location") or {}).get("name") or ""
        title = c.get("title") or "Class"
        code, category = parse_title(title)
        return ClassSlot(
            id=c["uuid"],
            name=title,
            location=location,
            location_code=code,
            category=category,
            start=self._parse_dt(c.get("startTime") or c.get("startDatetime")),
            end=self._parse_dt(c.get("endTime") or c.get("endDatetime")),
            instructor=coach_name,
            spots_available=c.get("spotsAvailable"),
            spots_total=c.get("attendanceCap"),
            booked=False,
            registration_start_offset_min=c.get("registrationStartOffset"),
        )

    def _to_reservation(self, r: dict) -> Reservation:
        ci = r.get("calendarItem") or {}
        coach = ci.get("mainCoach") or {}
        coach_name = (
            " ".join(x for x in [coach.get("firstName"), coach.get("lastName")] if x) or None
        )
        location = (ci.get("location") or {}).get("name") or ""
        status = (r.get("rawStatus") or "").lower()
        cancellable = (
            bool(r.get("isActive"))
            and not r.get("isCancelled")
            and status not in {"attended", "no_show"}
        )
        return Reservation(
            id=r["uuid"],
            class_id=r.get("calendarItemUuid") or "",
            class_name=r.get("reservationTitle") or "Class",
            location=location,
            start=self._parse_dt(r.get("rawStartTime") or r.get("reservationStart")),
            end=self._parse_dt(r.get("rawEndTime") or r.get("reservationEnd")),
            instructor=coach_name,
            cancellable=cancellable,
        )

    @staticmethod
    def _to_subscription_usage(s: dict) -> SubscriptionUsage:
        usage = s.get("currentPeriodUsage") or {}
        raw_limit = usage.get("limit")
        limit = int(raw_limit) if isinstance(raw_limit, (int, float)) else None
        reservations = int(usage.get("reservations") or 0)
        checkins = int(usage.get("checkins") or 0)
        used = reservations + checkins
        remaining = max(limit - used, 0) if limit is not None else None
        return SubscriptionUsage(
            subscription_uuid=s.get("subscriptionUuid") or "",
            plan=s.get("plan") or "",
            status=s.get("status") or "",
            limit=limit,
            reservations=reservations,
            checkins=checkins,
            used=used,
            remaining=remaining,
            period=str(usage.get("period") or ""),
            period_start=str(usage.get("periodStart") or ""),
            period_end=str(usage.get("periodEnd") or ""),
        )

    # -------------------------------------------------------------------- gql

    async def _gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        token = await self.ensure_token()
        body = {"operationName": None, "variables": variables, "query": query}
        client = self._get_http()
        r = await client.post(
            g.GRAPHQL_URL,
            headers={**g.DEFAULT_HEADERS, "authorization": f"Bearer {token}"},
            json=body,
        )
        if r.status_code in (401, 403) and self._token_near_expiry():
            token = await self.force_refresh()
            r = await client.post(
                g.GRAPHQL_URL,
                headers={**g.DEFAULT_HEADERS, "authorization": f"Bearer {token}"},
                json=body,
            )
        if r.status_code != 200:
            raise PushPressError(f"HTTP {r.status_code}: {r.text[:300]}")
        payload = r.json()
        if payload.get("errors"):
            msg = "; ".join(e.get("message", "") for e in payload["errors"])
            token_words = (
                "token expired",
                "invalid token",
                "token is required",
                "token not provided",
            )
            if any(w in msg.lower() for w in token_words) and self._token_near_expiry():
                logger.info("GraphQL token error + near expiry — refreshing and retrying once")
                token = await self.force_refresh()
                r = await client.post(
                    g.GRAPHQL_URL,
                    headers={**g.DEFAULT_HEADERS, "authorization": f"Bearer {token}"},
                    json=body,
                )
                payload = r.json()
                if payload.get("errors"):
                    raise PushPressError("; ".join(e.get("message", "") for e in payload["errors"]))
            else:
                raise PushPressError(msg)
        return payload.get("data") or {}


# --------------------------------------------------------------------------
# Title parsing heuristic (gym-specific: "OV | Classic CrossFit" → ("OV", ...)).

_KNOWN_CATEGORIES = (
    "Classic CrossFit",
    "Functional CrossFit",
    "Hyrox Open",
    "Open Gym",
    "Trial Class",
    "Trial class",
)


def parse_title(title: str) -> tuple[str, str]:
    """Return ``(location_code, category)`` parsed from a PushPress class title.

    Examples:
      "OV | Classic CrossFit"            -> ("OV", "Classic CrossFit")
      "HW | Open Gym Back Hall 6"        -> ("HW", "Open Gym")
      "Trial Class | Kanaalweg 29c"      -> ("",   "Trial Class")
    """
    parts = [p.strip() for p in title.split("|")]
    if not parts:
        return "", ""
    head = parts[0]
    rest = " ".join(parts[1:]) if len(parts) > 1 else ""

    if head.lower().startswith("trial"):
        return "", "Trial Class"

    if len(head) <= 4 and head.isupper():
        code = head
        candidate = rest
    else:
        code = ""
        candidate = title

    cat = ""
    for known in _KNOWN_CATEGORIES:
        if candidate.lower().startswith(known.lower()):
            cat = known.title() if known.islower() else known
            break
    if not cat:
        cat = " ".join(candidate.split()[:2]) or candidate
    return code, cat
