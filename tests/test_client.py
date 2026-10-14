"""Unit tests for the PushPress client's pure parsing/storage logic (no network)."""

from __future__ import annotations

import base64
import json
from datetime import datetime

from pushpress.client import (
    PushPressClient,
    decode_jwt_payload,
    parse_title,
)
from pushpress.storage import TokenStorage


def _make_jwt(payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.sig"


def test_decode_jwt_payload():
    token = _make_jwt({"sub": "user-1", "clientUuid": "client-1", "exp": 1_800_000_000})
    claims = decode_jwt_payload(token)
    assert claims["sub"] == "user-1"
    assert claims["clientUuid"] == "client-1"


def test_parse_title_location_and_category():
    assert parse_title("OV | Classic CrossFit") == ("OV", "Classic CrossFit")
    assert parse_title("HW | Open Gym Back Hall 6") == ("HW", "Open Gym")


def test_parse_title_trial():
    assert parse_title("Trial Class | Kanaalweg 29c") == ("", "Trial Class")


def test_parse_title_fallback():
    code, cat = parse_title("Some Long Title Without Pipe")
    assert code == ""
    assert cat == "Some Long"


def test_token_storage_roundtrip(tmp_path):
    store = TokenStorage(tmp_path / "tokens.db")
    assert store.load() == (None, None)
    expiry = datetime(2026, 12, 31, 12, 0, 0)
    store.save("abc123", expiry)
    token, loaded_expiry = store.load()
    assert token == "abc123"
    assert loaded_expiry == expiry


def _client(tmp_path) -> PushPressClient:
    return PushPressClient(
        email="me@example.com",
        password="secret",
        tz="Europe/Amsterdam",
        db_path=str(tmp_path / "t.db"),
    )


def test_to_slot_parses_timezone(tmp_path):
    c = _client(tmp_path)
    slot = c._to_slot(
        {
            "uuid": "cal-1",
            "title": "OV | Classic CrossFit",
            "attendanceCap": 20,
            "spotsAvailable": 5,
            "startTime": "2026-05-18T16:30:00+00:00",
            "endTime": "2026-05-18T17:30:00+00:00",
            "location": {"name": "Oude Gracht"},
            "mainCoach": {"firstName": "Jan", "lastName": "Smit"},
        }
    )
    assert slot.id == "cal-1"
    assert slot.category == "Classic CrossFit"
    assert slot.location_code == "OV"
    assert slot.spots_available == 5
    # +00:00 suffix is treated as local wall-clock, re-attached to Amsterdam.
    assert slot.start.hour == 16
    assert slot.start.tzinfo is not None


def test_to_reservation(tmp_path):
    c = _client(tmp_path)
    res = c._to_reservation(
        {
            "uuid": "res-1",
            "reservationTitle": "Classic CrossFit",
            "calendarItemUuid": "cal-1",
            "isActive": True,
            "isCancelled": False,
            "reservationStart": "2026-05-18T16:30:00+00:00",
            "reservationEnd": "2026-05-18T17:30:00+00:00",
            "status": "booked",
            "calendarItem": {
                "location": {"name": "Oude Gracht"},
                "mainCoach": {"firstName": "Jan", "lastName": "Smit"},
            },
        }
    )
    assert res.id == "res-1"
    assert res.class_id == "cal-1"
    assert res.cancellable is True


def test_subscription_usage_remaining(tmp_path):
    c = _client(tmp_path)
    usage = c._to_subscription_usage(
        {
            "subscriptionUuid": "sub-1",
            "plan": "9 sessions",
            "status": "active",
            "currentPeriodUsage": {
                "limit": 9,
                "reservations": 2,
                "checkins": 3,
            },
        }
    )
    assert usage.remaining == 4
    assert usage.used == 5


def test_subscription_usage_unlimited(tmp_path):
    c = _client(tmp_path)
    usage = c._to_subscription_usage({"subscriptionUuid": "sub-2", "currentPeriodUsage": {}})
    assert usage.remaining is None
