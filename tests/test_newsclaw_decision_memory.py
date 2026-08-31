"""Behavior tests for NewsClaw decision priority and local judgment memory."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.models import RecommendationSnapshotOut, SavedListItem
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _cache_news(db: Database, content_id: str = "article-1") -> None:
    db.cache_content(
        content_id,
        content_id=content_id,
        source_platform="news",
        source="news-test",
        content_type="article",
        title="产业政策发布",
        up_name="测试媒体",
        tags=["full_text", "fresh"],
        topic_group="产业政策",
        style_key="deep_focus",
        franchise_key="政策跟踪",
        relevance_score=0.82,
        published_at="2026-08-25T08:00:00+00:00",
    )


def _snapshot(*, score: float = 0.82, why: str = "与产业政策方向相关。") -> dict[str, object]:
    return {
        "snapshot_version": "newsclaw-judgment-v1",
        "captured_at": "2026-08-25T09:00:00+00:00",
        "source_platform": "news",
        "content_id": "article-1",
        "title": "产业政策发布",
        "topic_group": "产业政策",
        "franchise_key": "政策跟踪",
        "content_scope": "full_text",
        "freshness": "fresh",
        "temporal_class": "current",
        "temporal_state": "active",
        "display_tags": [
            {"type": "topic", "label": "产业政策", "origin": "model"},
            {"type": "evidence", "label": "全文级", "origin": "source"},
        ],
        "priority_level": "high",
        "priority_label": "优先关注",
        "priority_score": score,
        "priority_signals": {
            "match": 0.82,
            "evidence": 1.0,
            "timeliness": 0.95,
            "impact": 0.7,
        },
        "priority_reason": "匹配82% · 全文级 · 近期进展 · 影响信号70%",
        "priority_version": "decision-priority-v1",
        "why_recommended": why,
        "impact_chain": "政策变化可能影响行业预期。",
        "observation_points": "核验实施细则。",
    }


def test_recommendation_and_saved_snapshots_are_first_write_immutable(tmp_path: Path) -> None:
    db = Database(tmp_path / "immutable-snapshot.db")
    db.initialize()
    _cache_news(db)
    recommendation_id = db.insert_recommendation(
        "article-1",
        item_key="news:article-1",
        confidence=0.82,
        expression=(
            "为什么适合你：与产业政策方向相关。"
            "可能影响链：政策变化可能影响行业预期。"
            "后续观察点：核验实施细则。"
        ),
        topic="产业政策",
    )

    original = _snapshot()
    changed = _snapshot(score=0.1, why="算法后来改写的内容")
    db.store_recommendation_presentation_snapshots([(recommendation_id, original)])
    db.store_recommendation_presentation_snapshots([(recommendation_id, changed)])
    assert db.get_recommendation_presentation_snapshot(recommendation_id) == original

    saved = SavedItemInput(
        source_platform="news",
        content_id="article-1",
        title="产业政策发布",
        recommendation_snapshot=original,
    )
    db.upsert_saved_membership("favorite", saved)
    db.upsert_saved_membership(
        "favorite",
        SavedItemInput(
            source_platform="news",
            content_id="article-1",
            title="后来变化的标题",
            recommendation_snapshot=changed,
        ),
    )
    row = db.get_saved_membership("favorite", "news:article-1")
    assert row is not None
    assert '"priority_score":0.82' in str(row["recommendation_snapshot"])
    assert "算法后来改写的内容" not in str(row["recommendation_snapshot"])

    assert db.remove_saved_membership("favorite", "news:article-1") is True
    assert db.get_latest_saved_removal_snapshot("news:article-1") == original


def test_content_history_replays_first_click_judgment_after_content_changes(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "history-snapshot.db")
    memory = MemoryManager(tmp_path / "memory", database=db)
    memory.initialize()
    _cache_news(db)
    recommendation_id = db.insert_recommendation(
        "article-1",
        item_key="news:article-1",
        confidence=0.82,
        expression=(
            "为什么适合你：与产业政策方向相关。"
            "可能影响链：政策变化可能影响行业预期。"
            "后续观察点：核验实施细则。"
        ),
        topic="产业政策",
    )
    client = TestClient(create_app(database=db, memory_manager=memory, soul_engine=object()))

    clicked = client.post(
        "/api/recommendation-click",
        json={
            "recommendation_id": recommendation_id,
            "request_id": "history-click-1",
        },
    )
    assert clicked.status_code == 200
    captured = db.get_recommendation_presentation_snapshot(recommendation_id)
    assert captured is not None
    assert captured["snapshot_version"] == "newsclaw-judgment-v1"
    assert captured["why_recommended"] == "与产业政策方向相关。"

    # A later algorithm/content pass cannot rewrite what the user saw.
    db.store_recommendation_presentation_snapshots(
        [(recommendation_id, _snapshot(score=0.1, why="算法后来改写的内容"))]
    )
    db.conn.execute(
        "UPDATE recommendations SET expression = ?, confidence = ? WHERE id = ?",
        ("为什么适合你：算法后来改写的内容", 0.1, recommendation_id),
    )
    db.conn.commit()

    history = client.get(
        "/api/content-history",
        params={"category": "clicked", "limit": 10},
    )
    assert history.status_code == 200
    item = history.json()["items"][0]
    assert item["recommendation_snapshot"] == captured
    assert item["recommendation_snapshot"]["why_recommended"] == ("与产业政策方向相关。")
    assert "算法后来改写的内容" not in str(item["recommendation_snapshot"])


def test_supplied_saved_snapshot_with_wrong_identity_is_ignored(tmp_path: Path) -> None:
    db = Database(tmp_path / "saved-identity.db")
    db.initialize()
    _cache_news(db)
    recommendation_id = db.insert_recommendation(
        "article-1",
        item_key="news:article-1",
        confidence=0.82,
        expression="为什么适合你：可信的本地判断。",
        topic="产业政策",
    )
    db.store_recommendation_presentation_snapshots(
        [(recommendation_id, _snapshot(why="可信的本地判断。"))]
    )
    wrong = {**_snapshot(why="错误绑定的判断。"), "content_id": "article-other"}
    client = TestClient(create_app(database=db, memory_manager=object(), soul_engine=object()))

    response = client.post(
        "/api/saved/favorite",
        json={
            "source_platform": "news",
            "content_id": "article-1",
            "title": "产业政策发布",
            "recommendation_snapshot": wrong,
        },
    )
    assert response.status_code == 200
    row = db.get_saved_membership("favorite", "news:article-1")
    assert row is not None
    assert '"content_id":"article-1"' in str(row["recommendation_snapshot"])
    assert "可信的本地判断" in str(row["recommendation_snapshot"])
    assert "错误绑定的判断" not in str(row["recommendation_snapshot"])


def test_saved_list_item_serializes_one_optional_snapshot_field() -> None:
    item = SavedListItem(
        item_key="news:article-1",
        source_platform="news",
        content_id="article-1",
        recommendation_snapshot=RecommendationSnapshotOut.model_validate(_snapshot()),
    )
    payload = item.model_dump()
    assert list(payload).count("recommendation_snapshot") == 1
    assert payload["recommendation_snapshot"]["snapshot_version"] == ("newsclaw-judgment-v1")


def test_tracked_observation_is_idempotent_across_new_retry_request_and_archivable(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "tracked.db")
    db.initialize()
    original = _snapshot()
    kwargs = {
        "item_key": "news:article-1",
        "recommendation_id": 7,
        "topic_group": "产业政策",
        "story_key": "政策跟踪",
        "observation": "核验 实施细则",
        "latest_evidence": "第一版公告",
        "original_snapshot": original,
    }

    first, created = db.create_tracked_observation(request_id="track-1", **kwargs)
    exact_retry, exact_created = db.create_tracked_observation(request_id="track-1", **kwargs)
    response_loss_retry, loss_created = db.create_tracked_observation(
        request_id="track-2",
        **{**kwargs, "observation": "核验   实施细则"},
    )

    assert created is True
    assert exact_created is False
    assert loss_created is False
    assert first["tracking_id"] == exact_retry["tracking_id"]
    assert first["tracking_id"] == response_loss_retry["tracking_id"]
    assert len(db.list_tracked_observations("news:article-1")) == 1
    assert first["topic_group"] == "产业政策"
    assert first["story_key"] == "政策跟踪"
    assert first["original_snapshot"] == original

    with pytest.raises(ValueError, match="different observation"):
        db.create_tracked_observation(
            request_id="track-1",
            **{**kwargs, "observation": "不同观察点"},
        )

    archived, changed = db.archive_tracked_observation(str(first["tracking_id"]))
    assert changed is True
    assert archived is not None and archived["status"] == "archived"
    assert db.list_tracked_observations("news:article-1") == []
    assert len(db.list_tracked_observations("news:article-1", status="all")) == 1

    restarted, restarted_created = db.create_tracked_observation(
        request_id="track-3",
        **kwargs,
    )
    assert restarted_created is True
    assert restarted["tracking_id"] != first["tracking_id"]


def test_old_tracking_table_migrates_keys_and_archives_active_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "legacy-tracked.db"
    db = Database(path)
    db.initialize()
    db.close()

    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE tracked_observations")
        conn.execute(
            """
            CREATE TABLE tracked_observations (
                tracking_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                item_key TEXT NOT NULL,
                recommendation_id INTEGER,
                topic_group TEXT NOT NULL DEFAULT '',
                story_key TEXT NOT NULL DEFAULT '',
                observation TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                latest_evidence TEXT NOT NULL DEFAULT '',
                original_snapshot TEXT NOT NULL DEFAULT '{}',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO tracked_observations (
                tracking_id, request_id, item_key, observation
            ) VALUES (?, ?, 'news:article-1', ?)
            """,
            [
                ("legacy-1", "request-1", "核验 实施细则"),
                ("legacy-2", "request-2", "核验   实施细则"),
            ],
        )

    migrated = Database(path)
    migrated.initialize()
    rows = migrated.list_tracked_observations("news:article-1", status="all")
    assert {row["status"] for row in rows} == {"active", "archived"}
    assert len({row["observation_key"] for row in rows}) == 1


def test_tracking_api_enforces_news_gate_and_keeps_stopped_rows_queryable(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "tracking-api.db")
    db.initialize()
    _cache_news(db)
    recommendation_id = db.insert_recommendation(
        "article-1",
        item_key="news:article-1",
        confidence=0.82,
        expression=(
            "为什么适合你：与产业政策方向相关。"
            "可能影响链：政策变化可能影响行业预期。"
            "后续观察点：核验实施细则。"
        ),
        topic="产业政策",
    )
    client = TestClient(create_app(database=db, memory_manager=object(), soul_engine=object()))
    payload = {
        "request_id": "track-api-1",
        "item_key": "news:article-1",
        "recommendation_id": recommendation_id,
        "observation": "核验实施细则",
        "latest_evidence": "第一版公告",
    }

    response = client.post("/api/news/tracked-observations", json=payload)
    duplicate = client.post(
        "/api/news/tracked-observations",
        json={**payload, "request_id": "track-api-2"},
    )
    assert response.status_code == 200
    assert duplicate.status_code == 200
    assert response.json()["created"] is True
    assert duplicate.json()["duplicate"] is True
    assert response.json()["item"]["tracking_id"] == duplicate.json()["item"]["tracking_id"]
    assert response.json()["item"]["original_snapshot"]["snapshot_version"]

    tracking_id = response.json()["item"]["tracking_id"]
    global_list = client.get(
        "/api/news/tracked-observations",
        params={"status": "all"},
    )
    assert global_list.status_code == 200
    assert global_list.json()["active_count"] == 1
    assert global_list.json()["archived_count"] == 0
    assert global_list.json()["total"] == 1

    check, claimed = db.begin_tracked_observation_check(
        tracking_id=tracking_id,
        request_id="tracking-api-check-1",
        started_at="2026-08-28T08:00:00+00:00",
        lease_expires_at="2026-08-28T08:10:00+00:00",
        source_request_id="refresh-api-1",
        source_status="online",
    )
    assert claimed is True
    db.complete_tracked_observation_check(
        check_id=check["check_id"],
        tracking_id=tracking_id,
        claim_token=check["claim_token"],
        completed_at="2026-08-28T08:01:00+00:00",
        outcome="changed",
        matched_count=1,
        changes=[
            {
                "item_key": "news:article-2",
                "evidence_fingerprint": "evidence-fingerprint-2",
                "change_fingerprint": "change-fingerprint-2",
                "match_basis": "story_key",
                "match_score": 0.98,
                "change_type": "progress",
                "previous_state": "实施细则尚未发布",
                "new_state": "实施细则已经正式发布",
                "fact": "来源明确宣布实施细则正式发布。",
                "inference": "政策进入执行阶段。",
                "summary": "实施细则已经发布。",
                "evidence_quote": "实施细则正式发布",
                "confidence": 0.94,
                "title": "实施细则发布",
                "content_url": "https://example.com/policy/detail",
                "media": "测试媒体",
                "published_at": "2026-08-28T07:50:00+00:00",
                "content_scope": "full_text",
            }
        ],
    )
    detail = client.get(f"/api/news/tracked-observations/{tracking_id}")
    assert detail.status_code == 200
    assert detail.json()["last_check_status"] == "changed"
    assert detail.json()["unread_count"] == 1
    timeline = client.get(f"/api/news/tracked-observations/{tracking_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["checks"][0]["status"] == "changed"
    assert timeline.json()["checks"][0]["evidence"][0]["change_type"] == "progress"
    marked_read = client.post(f"/api/news/tracked-observations/{tracking_id}/read")
    assert marked_read.status_code == 200
    assert marked_read.json()["updated"] == 1
    assert marked_read.json()["item"]["unread_count"] == 0

    archived = client.delete(f"/api/news/tracked-observations/{tracking_id}")
    assert archived.status_code == 200
    assert archived.json()["item"]["status"] == "archived"
    listed = client.get(
        "/api/news/tracked-observations",
        params={"item_key": "news:article-1", "status": "all"},
    )
    assert [item["status"] for item in listed.json()["items"]] == ["archived"]
    assert listed.json()["total"] == 1
    assert listed.json()["active_count"] == 0
    assert listed.json()["archived_count"] == 1
    unmatched = client.get(
        "/api/news/tracked-observations",
        params={"item_key": "news:another-article", "status": "all"},
    ).json()
    assert unmatched["items"] == []
    assert unmatched["total"] == 0
    assert unmatched["active_count"] == 0
    assert unmatched["archived_count"] == 0
    assert unmatched["unread_count"] == 0

    restarted = client.post(
        f"/api/news/tracked-observations/{tracking_id}/restart",
        json={"request_id": "track-api-restart-1"},
    )
    assert restarted.status_code == 200
    assert restarted.json()["created"] is True
    assert restarted.json()["item"]["status"] == "active"
    assert restarted.json()["item"]["tracking_id"] != tracking_id
    cycles = client.get(
        "/api/news/tracked-observations",
        params={"item_key": "news:article-1", "status": "all"},
    ).json()
    assert {item["status"] for item in cycles["items"]} == {"active", "archived"}
    assert cycles["active_count"] == 1
    assert cycles["archived_count"] == 1

    rejected = client.post(
        "/api/news/tracked-observations",
        json={**payload, "request_id": "bad", "item_key": "reddit:article-1"},
    )
    assert rejected.status_code == 422
    blank = client.post(
        "/api/news/tracked-observations",
        json={**payload, "request_id": "blank", "observation": "   "},
    )
    assert blank.status_code == 422


def test_tracking_api_manual_check_returns_durable_receipt(tmp_path: Path) -> None:
    db = Database(tmp_path / "tracking-api-manual-check.db")
    db.initialize()
    _cache_news(db)
    client = TestClient(create_app(database=db, memory_manager=object(), soul_engine=object()))
    created = client.post(
        "/api/news/tracked-observations",
        json={
            "request_id": "track-manual-api-1",
            "item_key": "news:article-1",
            "observation": "核验实施细则",
            "latest_evidence": "第一版公告",
        },
    )
    assert created.status_code == 200
    tracking_id = created.json()["item"]["tracking_id"]

    class FakeRuntime:
        async def refresh(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["tracking_ids"] == {tracking_id}
            assert kwargs["force_tracking"] is True
            check, claimed = db.begin_tracked_observation_check(
                tracking_id=tracking_id,
                request_id="manual-refresh-receipt",
                started_at="2026-08-28T08:00:00+00:00",
                lease_expires_at="2026-08-28T08:10:00+00:00",
                source_request_id=str(kwargs["request_id"]),
                source_status="online",
                candidate_count=0,
            )
            assert claimed is True
            completed, _item = db.complete_tracked_observation_check(
                check_id=check["check_id"],
                tracking_id=tracking_id,
                claim_token=check["claim_token"],
                completed_at="2026-08-28T08:01:00+00:00",
                outcome="valid_empty",
                matched_count=0,
                changes=[],
            )
            return {
                "tracking": {
                    "ran": 1,
                    "changed": 0,
                    "failed": 0,
                    "results": [{"ran": True, "check": completed}],
                }
            }

    client.app.state.newsclaw_runtime = FakeRuntime()
    response = client.post(
        f"/api/news/tracked-observations/{tracking_id}/check",
        json={"request_id": "manual-check-api-1"},
    )

    assert response.status_code == 200
    assert response.json()["check"]["status"] == "valid_empty"
    assert response.json()["item"]["last_check_status"] == "valid_empty"
    assert response.json()["item"]["unread_count"] == 0


def test_recommendations_are_stably_sorted_by_decision_priority() -> None:
    class FakeDatabase:
        def get_recommendations(
            self,
            limit: int = 1000,
            *,
            exclude_processed: bool = False,
        ) -> list[dict[str, object]]:
            assert limit == 1000 and exclude_processed is True
            common = {
                "source_platform": "news",
                "content_type": "article",
                "presented": 0,
                "feedback_type": "",
                "expression": "",
            }
            return [
                {
                    **common,
                    "id": 1,
                    "bvid": "stale",
                    "item_key": "news:stale",
                    "content_id": "stale",
                    "title": "高匹配旧标题",
                    "confidence": 0.95,
                    "tags": ["title_only", "stale"],
                    "temporal_class": "historical",
                },
                {
                    **common,
                    "id": 2,
                    "bvid": "current-a",
                    "item_key": "news:current-a",
                    "content_id": "current-a",
                    "title": "当前全文 A",
                    "confidence": 0.78,
                    "tags": ["full_text", "fresh"],
                    "temporal_class": "current",
                    "temporal_state": "active",
                },
                {
                    **common,
                    "id": 3,
                    "bvid": "current-b",
                    "item_key": "news:current-b",
                    "content_id": "current-b",
                    "title": "当前全文 B",
                    "confidence": 0.78,
                    "tags": ["full_text", "fresh"],
                    "temporal_class": "current",
                    "temporal_state": "active",
                },
            ]

    response = TestClient(
        create_app(database=FakeDatabase(), memory_manager=object(), soul_engine=object())
    ).get("/api/recommendations")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["bvid"] for item in items] == ["current-a", "current-b", "stale"]
    scores = [item["priority_score"] for item in items]
    assert scores == sorted(scores, reverse=True)


def test_empty_and_broken_recommendation_services_have_distinct_statuses() -> None:
    degraded = TestClient(
        create_app(database=object(), memory_manager=object(), soul_engine=object())
    ).post("/api/recommendations/reshuffle")
    assert degraded.json() == {
        "items": [],
        "status": "degraded",
        "reason": "recommendation_service_unavailable",
        "retryable": True,
    }

    class EmptyDatabase:
        def count_pool_candidates(self, **_kwargs: object) -> int:
            return 0

    exhausted = TestClient(
        create_app(
            database=EmptyDatabase(),
            memory_manager=object(),
            soul_engine=SimpleNamespace(get_profile=lambda: {}),
            recommendation_engine=object(),
        )
    ).post("/api/recommendations/reshuffle")
    assert exhausted.json()["status"] == "exhausted"
    assert exhausted.json()["reason"] == "inventory_exhausted"
    assert exhausted.json()["retryable"] is True

    class NonEmptyDatabase:
        def count_pool_candidates(self, **_kwargs: object) -> int:
            return 1

    class BrokenProfile:
        async def get_profile(self) -> dict[str, object]:
            raise RuntimeError("profile store unavailable")

    profile_error = TestClient(
        create_app(
            database=NonEmptyDatabase(),
            memory_manager=object(),
            soul_engine=BrokenProfile(),
            recommendation_engine=object(),
        )
    ).post("/api/recommendations/reshuffle")
    assert profile_error.json() == {
        "items": [],
        "status": "error",
        "reason": "profile_unavailable",
        "retryable": True,
    }
    profile_append_error = TestClient(
        create_app(
            database=NonEmptyDatabase(),
            memory_manager=object(),
            soul_engine=BrokenProfile(),
            recommendation_engine=object(),
        )
    ).post("/api/recommendations/append", json={"excluded_bvids": []})
    assert profile_append_error.json()["reason"] == "profile_unavailable"

    class HealthyProfile:
        async def get_profile(self) -> dict[str, object]:
            return {"profile": "ok"}

    class BrokenSelection:
        async def reshuffle_recommendations(self, **_kwargs: object) -> list[object]:
            raise RuntimeError("selector unavailable")

        async def append_recommendations(self, **_kwargs: object) -> list[object]:
            raise RuntimeError("selector unavailable")

    selection_error = TestClient(
        create_app(
            database=NonEmptyDatabase(),
            memory_manager=object(),
            soul_engine=HealthyProfile(),
            recommendation_engine=BrokenSelection(),
        )
    ).post("/api/recommendations/reshuffle")
    assert selection_error.json() == {
        "items": [],
        "status": "error",
        "reason": "selection_failed",
        "retryable": True,
    }
    selection_append_error = TestClient(
        create_app(
            database=NonEmptyDatabase(),
            memory_manager=object(),
            soul_engine=HealthyProfile(),
            recommendation_engine=BrokenSelection(),
        )
    ).post("/api/recommendations/append", json={"excluded_bvids": []})
    assert selection_append_error.json()["reason"] == "selection_failed"
