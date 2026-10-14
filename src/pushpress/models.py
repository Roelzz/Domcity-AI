"""Typed data models returned by the PushPress client."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ClassSlot(BaseModel):
    id: str  # calendarItemUuid
    name: str
    location: str = ""
    location_code: str = ""
    category: str = ""
    start: datetime
    end: datetime
    instructor: str | None = None
    spots_available: int | None = None
    spots_total: int | None = None
    booked: bool = False
    registration_start_offset_min: int | None = None


class Reservation(BaseModel):
    id: str  # reservation uuid
    class_id: str
    class_name: str
    location: str = ""
    start: datetime
    end: datetime
    instructor: str | None = None
    cancellable: bool = True
    waitlisted: bool = False
    waitlist_until: str | None = None


class BookingResult(BaseModel):
    ok: bool
    reservation_id: str | None = None
    message: str = ""


class SubscriptionUsage(BaseModel):
    """Session/credit balance for one subscription in the current billing period."""

    subscription_uuid: str
    plan: str = ""
    status: str = ""
    limit: int | None = None
    reservations: int = 0
    checkins: int = 0
    used: int = 0
    remaining: int | None = None
    period: str = ""
    period_start: str = ""
    period_end: str = ""


class TenantContext(BaseModel):
    client_uuid: str
    user_uuid: str
    client_user_uuid: str
    subscription_uuid: str
