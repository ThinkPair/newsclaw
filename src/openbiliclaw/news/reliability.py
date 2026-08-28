"""Crash-safe source execution with leases, retries, and health projection."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from openbiliclaw.news.dedupe import SemanticDeduplicator
from openbiliclaw.news.errors import (
    NewsSourceError,
    SourceLeaseBusy,
    SourcePermissionDenied,
    SourceRateLimited,
    SourceSchemaError,
    SourceTimedOut,
    SourceTransientError,
    classify_provider_exception,
    safe_error_message,
)
from openbiliclaw.news.identity import stable_event_id, stable_request_id
from openbiliclaw.news.models import Article, normalize_utc

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from openbiliclaw.news.protocol import FetchWindow, NewsSourceAdapter, SourceProbe

TaskStatus = Literal[
    "running",
    "succeeded",
    "empty",
    "permission_denied",
    "rate_limited",
    "timed_out",
    "failed",
]
HealthStatus = Literal["healthy", "degraded", "unavailable", "unknown"]
_TERMINAL_STATUSES = frozenset(
    {"succeeded", "empty", "permission_denied", "rate_limited", "timed_out", "failed"}
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded retry and pacing policy for one source run."""

    max_attempts: int = 3
    timeout_seconds: float = 15.0
    base_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 8.0
    minimum_interval_seconds: float = 0.0
    lease_seconds: float = 90.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 0.01 <= self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 0.01 and 300")
        if not 0 <= self.base_backoff_seconds <= self.max_backoff_seconds <= 3600:
            raise ValueError("backoff bounds are invalid")
        if not 0 <= self.minimum_interval_seconds <= 3600:
            raise ValueError("minimum_interval_seconds is invalid")
        if not 1 <= self.lease_seconds <= 86_400:
            raise ValueError("lease_seconds must be between 1 and 86400")


@dataclass(frozen=True, slots=True)
class SourceTaskRecord:
    """Persisted state of one idempotent source request."""

    source_id: str
    request_id: str
    event_id: str
    status: TaskStatus
    attempt: int
    lease_owner: str
    lease_expires_at: float
    article_count: int
    error_code: str
    error_message: str
    created_at: str
    completed_at: str
    articles: tuple[Article, ...] = ()

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """Latest durable source-health projection for the settings UI."""

    source_id: str
    status: HealthStatus
    available: bool | None
    last_checked_at: str
    last_success_at: str
    consecutive_failures: int
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class SourceRunResult:
    """One source run, including idempotent replay and dedupe evidence."""

    request_id: str
    event_id: str
    status: TaskStatus
    articles: tuple[Article, ...]
    attempt_count: int
    duplicate_count: int = 0
    replayed: bool = False


class SourceTaskStore:
    """SQLite store for source leases, terminal results, and health."""

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS news_source_tasks (
                    source_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    lease_owner TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    article_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (source_id, request_id)
                );

                CREATE TABLE IF NOT EXISTS news_source_health (
                    source_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    available INTEGER,
                    last_checked_at TEXT NOT NULL,
                    last_success_at TEXT NOT NULL DEFAULT '',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                """
            )

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> SourceTaskRecord:
        try:
            raw_articles = json.loads(str(row["result_json"] or "[]"))
        except (TypeError, ValueError):
            raw_articles = []
        articles = tuple(Article.from_dict(item) for item in raw_articles if isinstance(item, dict))
        return SourceTaskRecord(
            source_id=str(row["source_id"]),
            request_id=str(row["request_id"]),
            event_id=str(row["event_id"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            attempt=int(row["attempt"]),
            lease_owner=str(row["lease_owner"]),
            lease_expires_at=float(row["lease_expires_at"]),
            article_count=int(row["article_count"]),
            error_code=str(row["error_code"]),
            error_message=str(row["error_message"]),
            created_at=str(row["created_at"]),
            completed_at=str(row["completed_at"]),
            articles=articles,
        )

    def acquire(
        self,
        *,
        source_id: str,
        request_id: str,
        event_id: str,
        owner: str,
        lease_seconds: float,
        now_epoch: float | None = None,
    ) -> tuple[SourceTaskRecord, bool]:
        """Acquire/reclaim a lease, or return an existing terminal/active row."""

        now = time.time() if now_epoch is None else now_epoch
        created_at = normalize_utc(datetime.fromtimestamp(now, UTC), allow_empty=False)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM news_source_tasks
                WHERE source_id = ? AND request_id = ?
                """,
                (source_id, request_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO news_source_tasks (
                        source_id, request_id, event_id, status, attempt,
                        lease_owner, lease_expires_at, created_at
                    ) VALUES (?, ?, ?, 'running', 1, ?, ?, ?)
                    """,
                    (source_id, request_id, event_id, owner, now + lease_seconds, created_at),
                )
                row = connection.execute(
                    """
                    SELECT * FROM news_source_tasks
                    WHERE source_id = ? AND request_id = ?
                    """,
                    (source_id, request_id),
                ).fetchone()
                connection.commit()
                if row is None:  # pragma: no cover - SQLite insert invariant
                    raise RuntimeError("source task insert was not visible")
                return self._task_from_row(row), True

            record = self._task_from_row(row)
            if record.terminal or record.lease_expires_at > now:
                connection.commit()
                return record, False
            connection.execute(
                """
                UPDATE news_source_tasks
                SET status = 'running', attempt = attempt + 1,
                    lease_owner = ?, lease_expires_at = ?,
                    error_code = '', error_message = ''
                WHERE source_id = ? AND request_id = ?
                """,
                (owner, now + lease_seconds, source_id, request_id),
            )
            row = connection.execute(
                """
                SELECT * FROM news_source_tasks
                WHERE source_id = ? AND request_id = ?
                """,
                (source_id, request_id),
            ).fetchone()
            connection.commit()
            if row is None:  # pragma: no cover - SQLite update invariant
                raise RuntimeError("source task reclaim was not visible")
            return self._task_from_row(row), True

    def set_attempt(
        self,
        *,
        source_id: str,
        request_id: str,
        owner: str,
        attempt: int,
        lease_seconds: float,
    ) -> None:
        """Renew the owned lease while a retry remains nonterminal."""

        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE news_source_tasks
                SET attempt = ?, lease_expires_at = ?
                WHERE source_id = ? AND request_id = ?
                  AND lease_owner = ? AND status = 'running'
                """,
                (attempt, time.time() + lease_seconds, source_id, request_id, owner),
            )
            if int(cursor.rowcount or 0) != 1:
                raise SourceLeaseBusy("Source task lease was lost during retry")

    def complete(
        self,
        *,
        source_id: str,
        request_id: str,
        owner: str,
        status: TaskStatus,
        attempt: int,
        articles: tuple[Article, ...] = (),
        error: NewsSourceError | None = None,
    ) -> SourceTaskRecord:
        """Persist one immutable terminal state and normalized result batch."""

        if status not in _TERMINAL_STATUSES:
            raise ValueError("source task completion requires a terminal status")
        completed_at = normalize_utc(datetime.now(UTC), allow_empty=False)
        result_json = json.dumps(
            [article.to_dict() for article in articles],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        error_code = error.code if error is not None else ""
        error_message = safe_error_message(error) if error is not None else ""
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE news_source_tasks
                SET status = ?, attempt = ?, article_count = ?,
                    error_code = ?, error_message = ?, result_json = ?,
                    completed_at = ?, lease_expires_at = 0
                WHERE source_id = ? AND request_id = ?
                  AND lease_owner = ? AND status = 'running'
                """,
                (
                    status,
                    attempt,
                    len(articles),
                    error_code,
                    error_message,
                    result_json,
                    completed_at,
                    source_id,
                    request_id,
                    owner,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise SourceLeaseBusy("Source task lease was lost before completion")
            row = connection.execute(
                """
                SELECT * FROM news_source_tasks
                WHERE source_id = ? AND request_id = ?
                """,
                (source_id, request_id),
            ).fetchone()
            if row is None:  # pragma: no cover - SQLite update invariant
                raise RuntimeError("completed source task was not visible")
            return self._task_from_row(row)

    def record_success(self, source_id: str, *, empty: bool = False) -> None:
        """Project a successful fetch into the durable source-health row."""

        now = normalize_utc(datetime.now(UTC), allow_empty=False)
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO news_source_health (
                    source_id, status, available, last_checked_at, last_success_at,
                    consecutive_failures, error_code, error_message
                ) VALUES (?, 'healthy', 1, ?, ?, 0, '', '')
                ON CONFLICT(source_id) DO UPDATE SET
                    status = 'healthy', available = 1, last_checked_at = excluded.last_checked_at,
                    last_success_at = CASE
                        WHEN ? = 1 THEN news_source_health.last_success_at
                        ELSE excluded.last_success_at
                    END,
                    consecutive_failures = 0, error_code = '', error_message = ''
                """,
                (source_id, now, now, 1 if empty else 0),
            )

    def record_failure(self, source_id: str, error: NewsSourceError) -> None:
        """Project a final failure without erasing the last successful time."""

        now = normalize_utc(datetime.now(UTC), allow_empty=False)
        unavailable = isinstance(error, SourcePermissionDenied)
        status = "unavailable" if unavailable else "degraded"
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO news_source_health (
                    source_id, status, available, last_checked_at,
                    consecutive_failures, error_code, error_message
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    status = excluded.status, available = excluded.available,
                    last_checked_at = excluded.last_checked_at,
                    consecutive_failures = news_source_health.consecutive_failures + 1,
                    error_code = excluded.error_code,
                    error_message = excluded.error_message
                """,
                (
                    source_id,
                    status,
                    0 if unavailable else None,
                    now,
                    error.code,
                    safe_error_message(error),
                ),
            )

    def record_probe(self, probe: SourceProbe) -> None:
        """Persist a permission probe even before the first scheduled fetch."""

        if probe.available:
            self.record_success(probe.source_id, empty=probe.status == "empty")
            return
        error_types: dict[str, type[NewsSourceError]] = {
            "permission_denied": SourcePermissionDenied,
            "rate_limited": SourceRateLimited,
            "schema_error": SourceSchemaError,
            "timed_out": SourceTimedOut,
            "transient_error": SourceTransientError,
        }
        error_type = error_types.get(probe.error_code, NewsSourceError)
        error = error_type(probe.detail or "Source probe failed")
        self.record_failure(probe.source_id, error)

    def get_task(self, source_id: str, request_id: str) -> SourceTaskRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM news_source_tasks
                WHERE source_id = ? AND request_id = ?
                """,
                (source_id, request_id),
            ).fetchone()
        return self._task_from_row(row) if row is not None else None

    def get_health(self, source_id: str) -> SourceHealth | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM news_source_health WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        raw_available = row["available"]
        available = None if raw_available is None else bool(raw_available)
        return SourceHealth(
            source_id=str(row["source_id"]),
            status=str(row["status"]),  # type: ignore[arg-type]
            available=available,
            last_checked_at=str(row["last_checked_at"]),
            last_success_at=str(row["last_success_at"]),
            consecutive_failures=int(row["consecutive_failures"]),
            error_code=str(row["error_code"]),
            error_message=str(row["error_message"]),
        )


class SourceRunner:
    """Execute adapters with idempotency, pacing, timeout, retry, and dedupe."""

    def __init__(
        self,
        store: SourceTaskStore,
        *,
        policy: RetryPolicy | None = None,
        deduplicator: SemanticDeduplicator | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._policy = policy or RetryPolicy()
        self._deduplicator = deduplicator or SemanticDeduplicator()
        self._sleep = sleep
        self._monotonic = monotonic
        self._rate_locks: dict[str, asyncio.Lock] = {}
        self._last_started: dict[str, float] = {}

    async def run(
        self,
        adapter: NewsSourceAdapter,
        window: FetchWindow,
        *,
        request_id: str = "",
        event_id: str = "",
    ) -> SourceRunResult:
        """Run or replay one durable source request."""

        normalized_request_id = stable_request_id(
            "source-fetch",
            adapter.source_id,
            window.start_at.isoformat(),
            window.end_at.isoformat(),
            window.limit,
            supplied=request_id,
        )
        normalized_event_id = stable_event_id(
            "source-fetch",
            adapter.source_id,
            normalized_request_id,
            supplied=event_id,
        )
        owner = secrets.token_hex(16)
        lease_seconds = max(
            self._policy.lease_seconds,
            (self._policy.timeout_seconds * self._policy.max_attempts)
            + (self._policy.max_backoff_seconds * max(0, self._policy.max_attempts - 1))
            + 5,
        )
        task, acquired = self._store.acquire(
            source_id=adapter.source_id,
            request_id=normalized_request_id,
            event_id=normalized_event_id,
            owner=owner,
            lease_seconds=lease_seconds,
        )
        if not acquired:
            if not task.terminal:
                raise SourceLeaseBusy("Source task is already running")
            return SourceRunResult(
                request_id=task.request_id,
                event_id=task.event_id,
                status=task.status,
                articles=task.articles,
                attempt_count=task.attempt,
                replayed=True,
            )

        attempt = task.attempt
        last_error: NewsSourceError | None = (
            SourceTimedOut("Source task lease expired after its retry budget")
            if task.attempt > self._policy.max_attempts
            else None
        )
        if task.attempt <= self._policy.max_attempts:
            for attempt in range(task.attempt, self._policy.max_attempts + 1):
                self._store.set_attempt(
                    source_id=adapter.source_id,
                    request_id=normalized_request_id,
                    owner=owner,
                    attempt=attempt,
                    lease_seconds=lease_seconds,
                )
                await self._pace(adapter.source_id)
                try:
                    raw_articles = await asyncio.wait_for(
                        adapter.fetch(window),
                        timeout=self._policy.timeout_seconds,
                    )
                    dedupe = self._deduplicator.deduplicate(raw_articles)
                except TimeoutError:
                    last_error = SourceTimedOut("News source task timed out")
                except Exception as error:
                    last_error = classify_provider_exception(error)
                else:
                    articles = dedupe.articles
                    status: TaskStatus = "succeeded" if articles else "empty"
                    self._store.complete(
                        source_id=adapter.source_id,
                        request_id=normalized_request_id,
                        owner=owner,
                        status=status,
                        attempt=attempt,
                        articles=articles,
                    )
                    try:
                        self._store.record_success(adapter.source_id, empty=not articles)
                    except sqlite3.Error:
                        logger.exception("Failed to project successful news source health")
                    return SourceRunResult(
                        request_id=normalized_request_id,
                        event_id=normalized_event_id,
                        status=status,
                        articles=articles,
                        attempt_count=attempt,
                        duplicate_count=max(0, len(raw_articles) - len(articles)),
                    )

                if not last_error.retryable or attempt >= self._policy.max_attempts:
                    break
                backoff = min(
                    self._policy.max_backoff_seconds,
                    self._policy.base_backoff_seconds * (2 ** max(0, attempt - 1)),
                )
                if last_error.retry_after_seconds is not None:
                    backoff = min(
                        self._policy.max_backoff_seconds,
                        max(backoff, last_error.retry_after_seconds),
                    )
                if backoff:
                    await self._sleep(backoff)

        if last_error is None:  # pragma: no cover - loop invariant
            last_error = NewsSourceError("News source task failed")
        if isinstance(last_error, SourcePermissionDenied):
            terminal: TaskStatus = "permission_denied"
        elif isinstance(last_error, SourceRateLimited):
            terminal = "rate_limited"
        elif isinstance(last_error, SourceTimedOut):
            terminal = "timed_out"
        else:
            terminal = "failed"
        self._store.complete(
            source_id=adapter.source_id,
            request_id=normalized_request_id,
            owner=owner,
            status=terminal,
            attempt=max(task.attempt, min(self._policy.max_attempts, attempt)),
            error=last_error,
        )
        try:
            self._store.record_failure(adapter.source_id, last_error)
        except sqlite3.Error:
            logger.exception("Failed to project failed news source health")
        return SourceRunResult(
            request_id=normalized_request_id,
            event_id=normalized_event_id,
            status=terminal,
            articles=(),
            attempt_count=max(task.attempt, min(self._policy.max_attempts, attempt)),
        )

    async def _pace(self, source_id: str) -> None:
        interval = self._policy.minimum_interval_seconds
        if interval <= 0:
            return
        lock = self._rate_locks.setdefault(source_id, asyncio.Lock())
        async with lock:
            now = self._monotonic()
            remaining = interval - (now - self._last_started.get(source_id, -interval))
            if remaining > 0:
                await self._sleep(remaining)
            self._last_started[source_id] = self._monotonic()
