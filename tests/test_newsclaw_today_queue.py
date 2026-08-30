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
    late_today = datetime(2026, 8, 30, 15, 58, 0, tzinfo=UTC)
    assert _china_today_window(late_today) == (
        "2026-08-30",
        "2026-08-29T16:00:00Z",
        "2026-08-30T16:00:00Z",
    )
    assert _publication_is_china_today(midnight, now=midnight)
    assert not _publication_is_china_today(before_midnight, now=midnight)
    assert not _publication_is_china_today("", now=midnight)
    assert not _publication_is_china_today("not-a-date", now=midnight)
    assert not _publication_is_china_today(midnight + timedelta(minutes=6), now=midnight)


def _ready_delight(
    database: Database,
    bvid: str,
    *,
    published_at: str,
    score: float,
    source_platform: str = "bilibili",
) -> None:
    database.cache_content(
        bvid,
        title=bvid,
        relevance_score=score,
        source_platform=source_platform,
        content_url=f"https://example.com/{bvid}",
    )
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


def test_database_applies_source_scope_before_limit(tmp_path: Path) -> None:
    database = Database(tmp_path / "source-scope.db")
    database.initialize()
    for index in range(110):
        _ready_delight(
            database,
            f"video-{index}",
            published_at="2026-08-30T01:00:00Z",
            score=0.99,
        )
    _ready_delight(
        database,
        "today-news",
        published_at="2026-08-30T01:30:00Z",
        score=0.90,
        source_platform="news",
    )

    rows = database.get_delight_candidates(
        min_delight_score=0.85,
        limit=1,
        include_liked=True,
        source_platform="news",
        published_after="2026-08-29T16:00:00Z",
        published_before="2026-08-30T03:00:00Z",
    )

    assert [row["bvid"] for row in rows] == ["today-news"]


def test_database_processing_count_is_scoped_to_today_news_and_all_live_stages(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "processing.db")
    database.initialize()

    def add_cache_stage(
        bvid: str,
        *,
        published_at: str = "2026-08-30T06:00:00Z",
        expression: str = "推荐理由",
        label: str = "宏观",
        score: float = 0.0,
        status: str = "fresh",
    ) -> str:
        database.cache_content(
            bvid,
            title=bvid,
            source_platform="news",
            content_id=bvid,
            content_url=f"https://example.com/{bvid}",
            published_at=published_at,
            relevance_score=0.90,
            style_key="news",
            topic_group="macro",
        )
        database.conn.execute(
            """
            UPDATE content_cache
            SET pool_expression = ?,
                pool_topic_label = ?,
                delight_score = ?,
                delight_reason = ?,
                delight_hook = ?,
                pool_status = ?
            WHERE bvid = ?
            """,
            (
                expression,
                label,
                score,
                expression if score else "",
                label if score else "",
                status,
                bvid,
            ),
        )
        row = database.conn.execute(
            "SELECT item_key FROM content_cache WHERE bvid = ?",
            (bvid,),
        ).fetchone()
        assert row is not None
        return str(row[0])

    copy_key = add_cache_stage("copy-pending", expression="", label="")
    score_zero_key = add_cache_stage("score-zero")
    stale_score_key = add_cache_stage("stale-score", score=0.70)
    duplicate_key = add_cache_stage("cross-stage-duplicate")
    add_cache_stage("already-synced", score=0.90)
    add_cache_stage("suppressed-missing-copy", expression="", label="", status="suppressed")
    add_cache_stage("old-score", published_at="2026-08-28T03:00:00Z")

    rows = [
        ("today-pending", "pending_eval", "news", "2026-08-30T01:00:00Z"),
        ("today-evaluating", "evaluating", "news", "2026-08-30T02:00:00Z"),
        (duplicate_key, "evaluated", "news", "2026-08-30T03:00:00Z"),
        ("old-pending", "pending_eval", "news", "2026-08-28T03:00:00Z"),
        ("today-video", "pending_eval", "bilibili", "2026-08-30T04:00:00Z"),
        ("today-rejected", "rejected", "news", "2026-08-30T05:00:00Z"),
    ]
    database.conn.executemany(
        """
        INSERT INTO discovery_candidates (
            candidate_key, status, source_platform, published_at
        ) VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    database.conn.commit()

    keys = database.get_delight_processing_candidate_keys(
        published_after="2026-08-29T16:00:00Z",
        published_before="2026-08-30T16:00:00Z",
        source_platform="news",
        min_delight_score_for_reason=0.85,
    )

    assert keys == {
        "today-pending",
        "today-evaluating",
        duplicate_key,
        copy_key,
        score_zero_key,
        stale_score_key,
    }
    assert (
        database.count_delight_processing_candidates(
            published_after="2026-08-29T16:00:00Z",
            published_before="2026-08-30T16:00:00Z",
            source_platform="news",
            min_delight_score_for_reason=0.85,
        )
        == 6
    )

    database.conn.execute(
        """
        UPDATE content_cache
        SET pool_expression = '推荐理由',
            pool_topic_label = '宏观',
            delight_score = 0.9,
            delight_reason = '推荐理由',
            delight_hook = '宏观'
        WHERE bvid IN ('copy-pending', 'score-zero', 'stale-score', 'cross-stage-duplicate')
        """
    )
    database.conn.commit()
    assert (
        database.count_delight_processing_candidates(
            published_after="2026-08-29T16:00:00Z",
            published_before="2026-08-30T16:00:00Z",
            source_platform="news",
            min_delight_score_for_reason=0.85,
        )
        == 3
    )


def test_newsclaw_today_batch_rejects_old_rows_and_reports_processing(monkeypatch) -> None:
    class FakeDatabase:
        def __init__(self) -> None:
            self.bounds: tuple[str, str] | None = None
            self.processing_threshold = 0.0

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
                },
                {
                    "bvid": "today-news",
                    "title": "今日新闻",
                    "source_platform": "news",
                    "published_at": datetime.now(UTC).isoformat(),
                    "delight_score": 0.91,
                },
            ]

        def count_delight_processing_candidates(
            self,
            *,
            published_after: str,
            published_before: str,
            source_platform: str,
            min_delight_score_for_reason: float,
        ) -> int:
            assert source_platform == "news"
            assert (published_after, published_before) == self.bounds
            self.processing_threshold = min_delight_score_for_reason
            return 2

    class FakeRuntimeController:
        def get_runtime_status(self) -> dict[str, int]:
            return {
                "candidate_eval_pending": 99,
                "candidate_eval_in_flight": 99,
                "candidate_eval_pending_ready": 2,
                "candidate_eval_evaluating": 1,
                "candidate_eval_evaluated": 3,
                "candidate_eval_budget_remaining": 17,
            }

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
    assert [item["bvid"] for item in payload["items"]] == ["today-news"]
    assert payload["state"] == "processing"
    assert payload["pending_count"] == 2
    assert payload["pending_ready"] == 2
    assert payload["evaluating"] == 1
    assert payload["evaluated"] == 3
    assert payload["budget_remaining"] == 17
    assert database.processing_threshold > 0
    assert database.bounds is not None
    assert database.bounds[0]
    assert database.bounds[1]
