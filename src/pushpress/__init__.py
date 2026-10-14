"""Unofficial PushPress members API client.

Provides a Python library (:class:`PushPressClient`), a CLI (``pushpress``),
and an MCP server (``pushpress-mcp``) for agents.
"""

from __future__ import annotations

from .client import PushPressClient, PushPressError, decode_jwt_payload, parse_title
from .config import Settings, settings
from .models import (
    BookingResult,
    ClassSlot,
    Reservation,
    SubscriptionUsage,
    TenantContext,
)

__all__ = [
    "PushPressClient",
    "PushPressError",
    "BookingResult",
    "ClassSlot",
    "Reservation",
    "SubscriptionUsage",
    "TenantContext",
    "Settings",
    "settings",
    "decode_jwt_payload",
    "parse_title",
]

__version__ = "0.1.0"


def get_client() -> PushPressClient:
    """Build a client from the process settings (``.env`` / environment)."""
    return PushPressClient(
        email=settings.pushpress_email,
        password=settings.pushpress_password,
        tz=settings.tz,
        db_path=settings.db_path,
    )
