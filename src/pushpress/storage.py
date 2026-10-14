"""Persistent JWT cache backed by SQLite (stdlib ``sqlite3``).

A single row (id=1) stores the active PushPress access token and its expiry.
The client loads it on startup and rewrites it whenever it logs in again, so
the token survives process restarts and container redeploys (when the DB file
lives on a mounted volume).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_UPSERT = """
INSERT INTO token_cache (id, access_token, expires_at, updated_at)
VALUES (1, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    access_token = excluded.access_token,
    expires_at = excluded.expires_at,
    updated_at = excluded.updated_at
"""


class TokenStorage:
    """Tiny SQLite-backed store for a single OAuth access token."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    def load(self) -> tuple[str | None, datetime | None]:
        """Return ``(token, expiry)`` or ``(None, None)`` if absent/unparseable."""
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT access_token, expires_at FROM token_cache WHERE id = 1"
            ).fetchone()
        if not row:
            return None, None
        token, expires_at = row
        try:
            expiry = datetime.fromisoformat(expires_at)
        except (TypeError, ValueError):
            return None, None
        return token, expiry

    def save(self, token: str, expiry: datetime) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(_UPSERT, (token, expiry.isoformat(), datetime.now().isoformat()))
            conn.commit()
