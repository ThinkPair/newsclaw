"""Stable request/event identifiers and a persistent idempotency ledger."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

_EXTERNAL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def _stable_identifier(prefix: str, namespace: str, parts: tuple[object, ...]) -> str:
    normalized_namespace = str(namespace or "").strip().casefold()
    if not normalized_namespace:
        raise ValueError("namespace is required")
    payload = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{normalized_namespace}\x1f{payload}".encode()).hexdigest()
    return f"{prefix}_{digest[:40]}"


def stable_request_id(namespace: str, *parts: object, supplied: str = "") -> str:
    """Preserve a valid caller ID or derive a deterministic request ID."""

    normalized = supplied.strip()
    if normalized:
        if not _EXTERNAL_ID_RE.fullmatch(normalized):
            raise ValueError("request_id has an unsupported format")
        return normalized
    return _stable_identifier("nrq", namespace, parts)


def stable_event_id(namespace: str, *parts: object, supplied: str = "") -> str:
    """Preserve a valid caller ID or derive a deterministic event ID."""

    normalized = supplied.strip()
    if normalized:
        if not _EXTERNAL_ID_RE.fullmatch(normalized):
            raise ValueError("event_id has an unsupported format")
        return normalized
    return _stable_identifier("nev", namespace, parts)


def _payload_fingerprint(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class IdempotencyConflict(RuntimeError):  # noqa: N818 - public domain contract
    """One event ID was reused for a different semantic payload."""


class SQLiteIdempotencyLedger:
    """Crash-safe exactly-once claim helper for feedback and source events."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS news_idempotency (
                    namespace TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (namespace, event_id)
                )
                """
            )

    def claim(self, namespace: str, event_id: str, payload: Any) -> bool:
        """Return true only for the first identical event write.

        A retry with the same payload returns ``False``.  Reusing an event ID
        for a different payload is rejected instead of silently learning twice.
        """

        normalized_namespace = str(namespace or "").strip().casefold()
        normalized_event_id = stable_event_id(
            normalized_namespace,
            supplied=event_id,
        )
        fingerprint = _payload_fingerprint(payload)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_hash
                FROM news_idempotency
                WHERE namespace = ? AND event_id = ?
                """,
                (normalized_namespace, normalized_event_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO news_idempotency (namespace, event_id, payload_hash)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_namespace, normalized_event_id, fingerprint),
                )
                connection.commit()
                return True
            connection.commit()
            if str(row["payload_hash"]) != fingerprint:
                raise IdempotencyConflict("event_id was reused with a different payload")
            return False
