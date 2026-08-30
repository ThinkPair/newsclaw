"""Acceptance tests for NewsClaw's literal "today" recommendation contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from openbiliclaw.api.app import (
    _china_calendar_date,
    _china_today_window,
    _publication_is_china_today,
    create_app,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def test_china_today_helpers_observe_utc_boundary_and_reject_bad_times() -> None:
    before_midnight = datetime(2026, 8, 29, 15, 59, 59, tzinfo=UTC)
    midnight = datetime(2026, 8, 29, 16, 0, 0, tzinfo=UTC)

    assert _china_calendar_date(before_midnight) == "2026-08-29"
    assert _china_calendar_date(midnight) == "2026-08-30"
    assert _china_today_window(midnight) == (
        "2026-08-30",
        "2026-08-29T16:00:00Z",
        "2026-08-29T16:05:00Z",
    )
    assert _publication_is_china_today(midnight, now=midnight)
    assert not _publication_is_china_today(before_midnight, now=midnight)
    assert not _publication_is_china_today("", now=midnight)
    assert not _publication_is_china_today("not-a-date", now=midnight)
    assert not _publication_is_china_today(midnight + timedelta(minutes=6), now=midnight)


def _ready_delight(database: Database, bvid: str, *, published_at: str, score: float) -> None:
    database.cache_content(bvid, title=bvid, relevance_score=score)
    database.update_pool_copy(bvid, expression="推荐理由", topic_label="测试主题")
    database.update_delight_score(
        bvid,
        delight_score=score,
        delight_reason="推荐理由",
        delight_hook="测试主题",
    )
    database.conn.execute(
        "UPDATE content_cache SET published_at = ? WHERE bvid = ?",
        (published_at, bvid),
    )
    database.conn.commit()


def test_database_applies_publication_window_before_score_limit(tmp_path: Path) -> None:
    database = Database(tmp_path / "today.db")
    database.initialize()
    for index in range(3):
        _ready_delight(
            database,
            f"old-{index}",
            published_at="2026-08-28T09:00:00Z",
            score=0.99 - index * 0.01,
        )
    _ready_delight(
        database,
        "today-lower-score",
        published_at="2026-08-30T01:00:00Z",
        score=0.90,
    )

    rows = database.get_delight_candidates(
        min_delight_score=0.85,
        limit=1,
        include_liked=True,
        published_after="2026-08-29T16:00:00Z",
        published_before="2026-08-30T03:00:00Z",
    )

    assert [row["bvid"] for row in rows] == ["today-lower-score"]


def test_newsclaw_today_batch_rejects_old_rows_and_reports_processing(monkeypatch) -> None:
    class FakeDatabase:
        def __init__(self) -> None:
            self.bounds: tuple[str, str] | None = None

        def get_delight_candidates(
            self,
            *,
            min_delight_score: float,
            limit: int,
            include_liked: bool,
            published_after: str = "",
            published_before: str = "",
        ) -> list[dict[str, object]]:
            del min_delight_score, limit, include_liked
            self.bounds = (published_after, published_before)
            return [
                {
                    "bvid": "old-news",
                    "title": "旧闻",
                    "source_platform": "news",
                    "published_at": "2020-01-01T00:00:00Z",
                    "delight_score": 0.99,
                }
            ]

    class FakeRuntimeController:
        def get_runtime_status(self) -> dict[str, int]:
            return {"candidate_eval_pending": 7}

    database = FakeDatabase()
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    app = create_app(
        memory_manager=object(),
        database=database,
        soul_engine=object(),
        runtime_controller=FakeRuntimeController(),
    )

    response = TestClient(app).get("/api/delight/pending-batch")
    payload = response.json()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert payload["scope"] == "today"
    assert payload["items"] == []
    assert payload["state"] == "processing"
    assert payload["pending_count"] == 7
    assert database.bounds is not None
    assert database.bounds[0]
    assert database.bounds[1]
