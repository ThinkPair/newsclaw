"""SQLite persistence for bounded research runs and evidence snapshots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from typing import TYPE_CHECKING

from openbiliclaw.research.models import ResearchEvidence, ResearchResult

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


class ResearchStore:
    """Persist successful cache entries and auditable non-cache terminal runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('search', 'extract')),
                    query TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT '',
                    time_range TEXT NOT NULL DEFAULT '',
                    parameters_json TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_request_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('success', 'empty', 'failed')),
                    requested_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    fresh_until TEXT,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    credits_used REAL,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_research_runs_cache
                    ON research_runs(request_key, status, fresh_until, completed_at DESC);

                CREATE TABLE IF NOT EXISTS research_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    canonical_url TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_domain TEXT NOT NULL,
                    published_at TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE(canonical_url, content_hash)
                );

                CREATE TABLE IF NOT EXISTS research_run_evidence (
                    run_id TEXT NOT NULL REFERENCES research_runs(run_id) ON DELETE CASCADE,
                    evidence_id TEXT NOT NULL REFERENCES research_evidence(evidence_id),
                    rank INTEGER NOT NULL,
                    provider_score REAL,
                    PRIMARY KEY(run_id, evidence_id)
                );

                CREATE INDEX IF NOT EXISTS idx_research_run_evidence_rank
                    ON research_run_evidence(run_id, rank);
                """
            )

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def load_fresh(self, *, request_key: str, now: datetime) -> ResearchResult | None:
        """Return the newest non-empty success while its TTL remains valid."""

        now_text = now.isoformat()
        with self._lock, self._connect() as connection:
            run = connection.execute(
                """
                SELECT *
                FROM research_runs
                WHERE request_key = ?
                  AND status = 'success'
                  AND result_count > 0
                  AND fresh_until IS NOT NULL
                  AND fresh_until > ?
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (request_key, now_text),
            ).fetchone()
            if run is None:
                return None
            rows = connection.execute(
                """
                SELECT evidence.*, link.provider_score AS run_provider_score
                FROM research_run_evidence AS link
                JOIN research_evidence AS evidence
                  ON evidence.evidence_id = link.evidence_id
                WHERE link.run_id = ?
                ORDER BY link.rank ASC
                """,
                (str(run["run_id"]),),
            ).fetchall()

        evidence = tuple(
            ResearchEvidence(
                url=str(row["canonical_url"]),
                title=str(row["title"]),
                content=str(row["content"]),
                source_domain=str(row["source_domain"]),
                published_at=str(row["published_at"]),
                score=float(row["run_provider_score"])
                if row["run_provider_score"] is not None
                else None,
            )
            for row in rows
        )
        return ResearchResult(
            run_id=str(run["run_id"]),
            operation=str(run["operation"]),  # type: ignore[arg-type]
            query=str(run["query"]),
            provider=str(run["provider"]),
            provider_request_id=str(run["provider_request_id"]),
            cache_hit=True,
            fresh_until=str(run["fresh_until"] or ""),
            topic=str(run["topic"]),
            time_range=str(run["time_range"]),
            evidence=evidence,
            credits_used=float(run["credits_used"]) if run["credits_used"] is not None else None,
        )

    def record_success(
        self,
        *,
        result: ResearchResult,
        request_key: str,
        parameters: dict[str, object],
        requested_at: str,
        completed_at: str,
    ) -> None:
        """Atomically persist a non-empty run and its bounded evidence."""

        if not result.evidence or not result.fresh_until:
            raise ValueError("successful research cache entries must be non-empty and fresh")
        parameters_json = json.dumps(
            parameters,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    run_id, request_key, operation, query, topic, time_range,
                    parameters_json, provider, provider_request_id, status,
                    requested_at, completed_at, fresh_until, result_count, credits_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    request_key,
                    result.operation,
                    result.query,
                    result.topic,
                    result.time_range,
                    parameters_json,
                    result.provider,
                    result.provider_request_id,
                    requested_at,
                    completed_at,
                    result.fresh_until,
                    len(result.evidence),
                    result.credits_used,
                ),
            )
            for rank, item in enumerate(result.evidence, start=1):
                content_hash = self._digest(item.content)
                evidence_identity = "\0".join((item.url, content_hash))
                evidence_id = f"rev_{self._digest(evidence_identity)[:32]}"
                connection.execute(
                    """
                    INSERT INTO research_evidence (
                        evidence_id, canonical_url, url_hash, title, content,
                        source_domain, published_at, provider, retrieved_at, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canonical_url, content_hash) DO NOTHING
                    """,
                    (
                        evidence_id,
                        item.url,
                        self._digest(item.url),
                        item.title,
                        item.content,
                        item.source_domain,
                        item.published_at,
                        result.provider,
                        completed_at,
                        content_hash,
                    ),
                )
                persisted = connection.execute(
                    """
                    SELECT evidence_id
                    FROM research_evidence
                    WHERE canonical_url = ? AND content_hash = ?
                    """,
                    (item.url, content_hash),
                ).fetchone()
                if persisted is None:  # pragma: no cover - SQLite invariant
                    raise RuntimeError("research evidence persistence failed")
                connection.execute(
                    """
                    INSERT INTO research_run_evidence (
                        run_id, evidence_id, rank, provider_score
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (result.run_id, str(persisted["evidence_id"]), rank, item.score),
                )

    def record_terminal(
        self,
        *,
        run_id: str,
        request_key: str,
        operation: str,
        query: str,
        topic: str,
        time_range: str,
        parameters: dict[str, object],
        requested_at: str,
        completed_at: str,
        status: str,
        provider_request_id: str = "",
        credits_used: float | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        """Record empty/failed observability without creating a cache entry."""

        if status not in {"empty", "failed"}:
            raise ValueError("terminal research status must be empty or failed")
        parameters_json = json.dumps(
            parameters,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO research_runs (
                    run_id, request_key, operation, query, topic, time_range,
                    parameters_json, provider, provider_request_id, status,
                    requested_at, completed_at, fresh_until, result_count,
                    credits_used, error_code, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'tavily', ?, ?, ?, ?, NULL, 0, ?, ?, ?)
                """,
                (
                    run_id,
                    request_key,
                    operation,
                    query,
                    topic,
                    time_range,
                    parameters_json,
                    provider_request_id[:200],
                    status,
                    requested_at,
                    completed_at,
                    credits_used,
                    error_code[:80],
                    error_message[:500],
                ),
            )

    def status_counts(self) -> dict[str, int]:
        """Return small diagnostics used by tests and local status surfaces."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM research_runs GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}
