from __future__ import annotations

import ast
import asyncio
import inspect
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

import openbiliclaw.news.tracking as tracking_module
from openbiliclaw.news.tracking import NewsTrackingService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


_NOW = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "newsclaw-tracking.db")
    database.initialize()
    return database


def _tracker(
    database: Database,
    *,
    suffix: str = "1",
    observation: str = "核验政策实施细则是否正式发布",
) -> dict[str, Any]:
    item, created = database.create_tracked_observation(
        request_id=f"track-{suffix}",
        item_key=f"news:baseline-{suffix}",
        recommendation_id=None,
        topic_group="产业政策",
        story_key="政策实施跟踪",
        observation=observation,
        latest_evidence="目前只有政策原则，实施细则尚未发布。",
        original_snapshot={
            "snapshot_version": "newsclaw-judgment-v1",
            "content_id": f"baseline-{suffix}",
            "title": f"政策发布（基线 {suffix}）",
            "published_at": "2026-08-27T08:00:00+00:00",
            "impact_chain": "政策原则 → 等待实施细则 → 观察行业执行",
        },
        check_interval_minutes=60,
    )
    assert created is True
    return item


def _candidate(*, suffix: str = "1", body: str = "实施细则已经正式发布。") -> dict[str, Any]:
    return {
        "article_id": f"update-{suffix}",
        "content_id": f"update-{suffix}",
        "item_key": f"news:update-{suffix}",
        "title": "政策实施细则正式发布",
        "body_text": body,
        "description": body,
        "content_url": f"https://example.com/policy/{suffix}",
        "media": "测试媒体",
        "published_at": "2026-08-28T08:00:00+00:00",
        "retrieved_at": "2026-08-28T08:10:00+00:00",
        "content_scope": "full_text",
        "topic_group": "产业政策",
        "franchise_key": "政策实施跟踪",
        "hash": f"content-hash-{suffix}",
    }


def _changed_decision(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "evidence_item_key": candidate["item_key"],
        "decision": "changed",
        "change_type": "progress",
        "previous_state": "实施细则尚未发布",
        "new_state": "实施细则已经正式发布",
        "fact": "来源正文明确写明实施细则已经正式发布。",
        "inference": "政策进入可执行阶段。",
        "summary": "实施细则从等待发布变为已经正式发布。",
        "evidence_quote": "实施细则已经正式发布",
        "confidence": 0.94,
    }


async def _changed_analysis(
    _tracker_item: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    return [_changed_decision(candidates[0])]


def _scalar(database: Database, statement: str, parameters: Sequence[object] = ()) -> int:
    database._ensure_fresh_read()
    row = database.conn.execute(statement, tuple(parameters)).fetchone()
    assert row is not None
    return int(row[0])


def _row(
    database: Database,
    statement: str,
    parameters: Sequence[object] = (),
) -> dict[str, Any]:
    database._ensure_fresh_read()
    row = database.conn.execute(statement, tuple(parameters)).fetchone()
    assert row is not None
    return dict(row)


def _unread_count(database: Database, tracking_id: str) -> int:
    return _scalar(
        database,
        """
        SELECT COUNT(*)
        FROM tracked_observation_checks
        WHERE tracking_id = ? AND is_read = 0
        """,
        (tracking_id,),
    )


@pytest.mark.asyncio
async def test_no_candidates_is_valid_empty_without_unread_update(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)

    async def unexpected_analysis(
        _tracker_item: Mapping[str, Any],
        _candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        raise AssertionError("valid_empty must not call the model")

    result = await NewsTrackingService(
        database=database,
        analysis_callback=unexpected_analysis,
    ).check_one_from_articles(
        tracker,
        [],
        source_request_id="refresh-empty",
        source_status="healthy",
        request_id="check-empty",
        now=_NOW,
    )

    assert result["ran"] is True
    assert result["check"]["status"] == "valid_empty"
    assert result["check"]["matched_count"] == 0
    assert result["check"]["new_evidence_count"] == 0
    assert result["check"]["is_read"] == 1
    assert _unread_count(database, tracker["tracking_id"]) == 0
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 0


@pytest.mark.asyncio
async def test_related_candidate_without_material_change_is_unchanged(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    candidate = _candidate()

    async def unchanged_analysis(
        _tracker_item: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        return [
            {
                "evidence_item_key": candidates[0]["item_key"],
                "decision": "related_no_change",
                "change_type": "",
                "confidence": 0.92,
            }
        ]

    result = await NewsTrackingService(
        database=database,
        analysis_callback=unchanged_analysis,
    ).check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-unchanged",
        source_status="healthy",
        request_id="check-unchanged",
        now=_NOW,
    )

    assert result["check"]["status"] == "unchanged"
    assert result["check"]["matched_count"] == 1
    assert result["check"]["new_evidence_count"] == 0
    assert result["check"]["is_read"] == 1
    assert _unread_count(database, tracker["tracking_id"]) == 0
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 0


@pytest.mark.asyncio
async def test_model_prompt_includes_bounded_corroborating_source_metadata(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    candidate = _candidate()
    candidate["corroborating_sources"] = [
        {
            "source": "独立媒体",
            "provider": "RSS/Atom",
            "url": "https://example.net/corroboration",
            "published_at": "2026-08-28T08:02:00+00:00",
            "content_scope": "summary",
        }
    ]
    captured: dict[str, Any] = {}

    class FakeLLM:
        async def complete_structured_task(self, **kwargs: Any) -> Any:
            captured.update(kwargs)

            class Response:
                content = json.dumps({"decisions": [_changed_decision(candidate)]})

            return Response()

    result = await NewsTrackingService(
        database=database,
        llm_service_provider=FakeLLM,
    ).check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-corroborated",
        source_status="healthy",
        request_id="check-corroborated",
        now=_NOW,
    )

    assert result["check"]["status"] == "changed"
    prompt = json.loads(captured["user_input"])
    assert prompt["candidate_evidence"][0]["corroborating_sources"] == [
        {
            "source": "独立媒体",
            "provider": "RSS/Atom",
            "url": "https://example.net/corroboration",
            "published_at": "2026-08-28T08:02:00+00:00",
            "content_scope": "summary",
        }
    ]


def test_candidate_fingerprint_is_stable_across_corroborating_source_order() -> None:
    candidate = _candidate()
    sources = [
        {
            "source": "乙媒体",
            "provider": "RSS",
            "url": "https://example.net/b",
            "published_at": "2026-08-28T08:03:00+00:00",
            "content_scope": "summary",
        },
        {
            "source": "甲媒体",
            "provider": "RSS",
            "url": "https://example.net/a",
            "published_at": "2026-08-28T08:02:00+00:00",
            "content_scope": "full_text",
        },
    ]

    without_sources = tracking_module._candidate_fingerprint(candidate)
    forward = tracking_module._candidate_fingerprint(
        {**candidate, "corroborating_sources": sources}
    )
    reversed_order = tracking_module._candidate_fingerprint(
        {**candidate, "corroborating_sources": list(reversed(sources))}
    )

    assert forward == reversed_order
    assert forward != without_sources


@pytest.mark.asyncio
async def test_already_checked_no_change_candidate_does_not_spend_model_again(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    candidate = _candidate()
    calls = 0

    async def unchanged_analysis(
        _tracker_item: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        return [
            {
                "evidence_item_key": candidates[0]["item_key"],
                "decision": "related_no_change",
                "change_type": "",
                "confidence": 0.92,
            }
        ]

    service = NewsTrackingService(database=database, analysis_callback=unchanged_analysis)
    first = await service.check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-seen-1",
        source_status="healthy",
        request_id="check-seen-1",
        now=_NOW,
    )
    second = await service.check_one_from_articles(
        database.get_tracked_observation(str(tracker["tracking_id"])),
        [candidate],
        source_request_id="refresh-seen-2",
        source_status="healthy",
        request_id="check-seen-2",
        now=_NOW + timedelta(hours=1),
    )

    assert first["check"]["status"] == "unchanged"
    assert second["check"]["status"] == "valid_empty"
    assert calls == 1
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_candidates") == 1


@pytest.mark.asyncio
async def test_new_corroborating_source_reopens_same_candidate_for_verification(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    candidate = _candidate()
    calls = 0

    async def unchanged_analysis(
        _tracker_item: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        return [
            {
                "evidence_item_key": candidates[0]["item_key"],
                "decision": "related_no_change",
                "change_type": "",
                "confidence": 0.92,
            }
        ]

    service = NewsTrackingService(database=database, analysis_callback=unchanged_analysis)
    first = await service.check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-before-corroboration",
        source_status="healthy",
        request_id="check-before-corroboration",
        now=_NOW,
    )
    corroborated = {
        **candidate,
        "corroborating_sources": [
            {
                "source": "另一家独立媒体",
                "provider": "RSS/Atom",
                "url": "https://example.net/independent-confirmation",
                "published_at": "2026-08-28T08:05:00+00:00",
                "content_scope": "summary",
            }
        ],
    }
    second = await service.check_one_from_articles(
        database.get_tracked_observation(str(tracker["tracking_id"])),
        [corroborated],
        source_request_id="refresh-after-corroboration",
        source_status="healthy",
        request_id="check-after-corroboration",
        now=_NOW + timedelta(hours=1),
    )

    assert first["check"]["status"] == "unchanged"
    assert second["check"]["status"] == "unchanged"
    assert calls == 2
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_candidates") == 2


@pytest.mark.asyncio
async def test_article_payload_without_internal_topic_keys_still_reaches_model(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    calls = 0

    async def unchanged_analysis(
        _tracker_item: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        assert "topic_group" not in candidates[0]
        assert "franchise_key" not in candidates[0]
        return [
            {
                "evidence_item_key": candidates[0]["item_key"],
                "decision": "related_no_change",
                "change_type": "",
                "confidence": 0.91,
            }
        ]

    article_payload = {
        "article_id": "real-shape-update",
        "item_key": "news:real-shape-update",
        "title": "产业政策进入执行：监管公布配套落地办法",
        "body_text": "有关部门今日公布实施办法，原有原则性文件开始进入落地阶段。",
        "description": "配套办法公布，产业政策由原则转向执行。",
        "content_url": "https://example.com/real-shape-update",
        "media": "测试媒体",
        "published_at": "2026-08-28T08:00:00+00:00",
        "content_scope": "full_text",
        "hash": "canonical-real-shape-hash",
    }
    result = await NewsTrackingService(
        database=database,
        analysis_callback=unchanged_analysis,
    ).check_one_from_articles(
        tracker,
        [article_payload],
        source_request_id="refresh-real-shape",
        source_status="healthy",
        request_id="check-real-shape",
        now=_NOW,
    )

    assert result["check"]["status"] == "unchanged"
    assert result["check"]["matched_count"] == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_incomplete_model_result_is_retry_not_false_unchanged(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)

    async def incomplete_analysis(
        _tracker_item: Mapping[str, Any],
        _candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        return []

    result = await NewsTrackingService(
        database=database,
        analysis_callback=incomplete_analysis,
    ).check_one_from_articles(
        tracker,
        [_candidate()],
        source_request_id="refresh-incomplete-model",
        source_status="healthy",
        request_id="check-incomplete-model",
        now=_NOW,
    )

    assert result["check"]["status"] == "retry_wait"
    assert result["item"]["last_checked_at"] == ""
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_candidates") == 0
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 0


@pytest.mark.asyncio
async def test_material_change_writes_one_evidence_and_one_unread_update(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    candidate = _candidate()

    result = await NewsTrackingService(
        database=database,
        analysis_callback=_changed_analysis,
    ).check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-changed",
        source_status="healthy",
        request_id="check-changed",
        now=_NOW,
    )

    assert result["check"]["status"] == "changed"
    assert result["check"]["new_evidence_count"] == 1
    assert result["check"]["is_read"] == 0
    assert _unread_count(database, tracker["tracking_id"]) == 1
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 1
    evidence = _row(database, "SELECT * FROM tracked_observation_evidence")
    assert evidence["tracking_id"] == tracker["tracking_id"]
    assert evidence["evidence_item_key"] == candidate["item_key"]
    assert evidence["change_type"] == "progress"
    assert evidence["previous_state"] == "实施细则尚未发布"
    assert evidence["new_state"] == "实施细则已经正式发布"
    assert evidence["fact"]
    assert evidence["inference"]
    snapshot = json.loads(str(evidence["evidence_snapshot"]))
    assert snapshot["content_url"] == candidate["content_url"]
    assert snapshot["content_scope"] == "full_text"
    current = result["item"]
    assert current["last_check_status"] == "changed"
    assert current["last_change_at"]
    assert current["latest_evidence"]


@pytest.mark.asyncio
async def test_request_replay_and_change_fingerprint_do_not_duplicate_updates(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    candidate = _candidate()
    service = NewsTrackingService(database=database, analysis_callback=_changed_analysis)

    first = await service.check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-one",
        source_status="healthy",
        request_id="check-one",
        now=_NOW,
    )
    exact_replay = await service.check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-one",
        source_status="healthy",
        request_id="check-one",
        now=_NOW,
    )
    fingerprint_replay = await service.check_one_from_articles(
        tracker,
        [candidate],
        source_request_id="refresh-two",
        source_status="healthy",
        request_id="check-two",
        now=_NOW + timedelta(hours=1),
    )

    assert first["check"]["new_evidence_count"] == 1
    assert exact_replay["ran"] is False
    assert exact_replay["reason"] == "replayed"
    assert fingerprint_replay["ran"] is True
    assert fingerprint_replay["check"]["new_evidence_count"] == 0
    assert fingerprint_replay["check"]["is_read"] == 1
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_checks") == 2
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 1
    assert _unread_count(database, tracker["tracking_id"]) == 1


@pytest.mark.asyncio
async def test_archiving_during_analysis_cancels_late_commit(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)

    async def archive_then_change(
        tracker_item: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        archived, changed = database.archive_tracked_observation(str(tracker_item["tracking_id"]))
        assert changed is True
        assert archived is not None and archived["status"] == "archived"
        return [_changed_decision(candidates[0])]

    result = await NewsTrackingService(
        database=database,
        analysis_callback=archive_then_change,
    ).check_one_from_articles(
        tracker,
        [_candidate()],
        source_request_id="refresh-race",
        source_status="healthy",
        request_id="check-race",
        now=_NOW,
    )

    assert result["check"]["status"] == "cancelled"
    assert result["item"]["status"] == "archived"
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 0
    assert _unread_count(database, tracker["tracking_id"]) == 0


def test_expired_check_lease_is_reclaimable_without_a_second_row(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    tracking_id = str(tracker["tracking_id"])

    first, acquired = database.begin_tracked_observation_check(
        tracking_id=tracking_id,
        request_id="lease-check",
        started_at=_NOW.isoformat(),
        lease_expires_at=(_NOW + timedelta(minutes=10)).isoformat(),
    )
    blocked, blocked_acquired = database.begin_tracked_observation_check(
        tracking_id=tracking_id,
        request_id="lease-check",
        started_at=(_NOW + timedelta(minutes=5)).isoformat(),
        lease_expires_at=(_NOW + timedelta(minutes=15)).isoformat(),
    )
    reclaimed, reclaimed_acquired = database.begin_tracked_observation_check(
        tracking_id=tracking_id,
        request_id="lease-check",
        started_at=(_NOW + timedelta(minutes=11)).isoformat(),
        lease_expires_at=(_NOW + timedelta(minutes=21)).isoformat(),
    )

    assert acquired is True
    assert blocked_acquired is False
    assert blocked["check_id"] == first["check_id"]
    assert reclaimed_acquired is True
    assert reclaimed["check_id"] == first["check_id"]
    assert reclaimed["attempt_count"] == 2
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_checks") == 1


def test_tracker_check_claim_is_single_flight_and_fences_expired_owner(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    first, first_claimed = database.begin_tracked_observation_check(
        tracking_id=str(tracker["tracking_id"]),
        request_id="single-flight-first",
        started_at=_NOW.isoformat(),
        lease_expires_at=(_NOW + timedelta(minutes=10)).isoformat(),
    )
    joined, joined_claimed = database.begin_tracked_observation_check(
        tracking_id=str(tracker["tracking_id"]),
        request_id="single-flight-second",
        started_at=(_NOW + timedelta(minutes=1)).isoformat(),
        lease_expires_at=(_NOW + timedelta(minutes=11)).isoformat(),
    )

    assert first_claimed is True
    assert joined_claimed is False
    assert joined["check_id"] == first["check_id"]
    assert (
        _scalar(
            database,
            "SELECT COUNT(*) FROM tracked_observation_checks WHERE status = 'running'",
        )
        == 1
    )

    replacement, replacement_claimed = database.begin_tracked_observation_check(
        tracking_id=str(tracker["tracking_id"]),
        request_id="single-flight-after-expiry",
        started_at=(_NOW + timedelta(minutes=11)).isoformat(),
        lease_expires_at=(_NOW + timedelta(minutes=21)).isoformat(),
    )

    assert replacement_claimed is True
    assert replacement["check_id"] != first["check_id"]
    fenced = database.get_tracked_observation_check(str(first["check_id"]))
    assert fenced is not None
    assert fenced["status"] == "cancelled"
    assert (
        _scalar(
            database,
            "SELECT COUNT(*) FROM tracked_observation_checks WHERE status = 'running'",
        )
        == 1
    )


def test_initialize_repairs_legacy_duplicate_running_checks(tmp_path: Path) -> None:
    path = tmp_path / "legacy-duplicate-running.db"
    database = Database(path)
    database.initialize()
    tracker = _tracker(database)
    tracking_id = str(tracker["tracking_id"])
    started = datetime.now(UTC)
    first, claimed = database.begin_tracked_observation_check(
        tracking_id=tracking_id,
        request_id="legacy-running-first",
        started_at=started.isoformat(),
        lease_expires_at=(started + timedelta(minutes=30)).isoformat(),
    )
    assert claimed is True
    database._execute_write("DROP INDEX idx_tracked_checks_one_running")
    database._execute_write(
        """
        INSERT INTO tracked_observation_checks (
            check_id, request_id, tracking_id, status,
            lease_expires_at, started_at
        ) VALUES (?, ?, ?, 'running', ?, ?)
        """,
        (
            "legacy-running-second-check",
            "legacy-running-second",
            tracking_id,
            (started + timedelta(minutes=31)).isoformat(),
            (started + timedelta(minutes=1)).isoformat(),
        ),
    )
    assert (
        _scalar(
            database,
            "SELECT COUNT(*) FROM tracked_observation_checks WHERE status = 'running'",
        )
        == 2
    )
    database.close()

    migrated = Database(path)
    migrated.initialize()
    rows = {
        row["request_id"]: row
        for row in migrated.list_tracked_observation_checks(tracking_id, limit=10)
    }

    assert rows["legacy-running-first"]["check_id"] == first["check_id"]
    assert rows["legacy-running-first"]["status"] == "cancelled"
    assert rows["legacy-running-second"]["status"] == "running"
    assert (
        _scalar(
            migrated,
            "SELECT COUNT(*) FROM tracked_observation_checks WHERE status = 'running'",
        )
        == 1
    )
    indexes = {
        str(row["name"])
        for row in migrated.conn.execute("PRAGMA index_list(tracked_observation_checks)")
    }
    assert "idx_tracked_checks_one_running" in indexes


@pytest.mark.asyncio
async def test_manual_check_joins_running_background_check_without_second_model_call(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)
    candidate = _candidate()
    model_entered = asyncio.Event()
    release_model = asyncio.Event()
    calls = 0

    async def blocked_change(
        _tracker_item: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        nonlocal calls
        calls += 1
        model_entered.set()
        await release_model.wait()
        return [_changed_decision(candidates[0])]

    service = NewsTrackingService(database=database, analysis_callback=blocked_change)
    background = asyncio.create_task(
        service.check_one_from_articles(
            tracker,
            [candidate],
            source_request_id="refresh-background",
            source_status="healthy",
            request_id="check-background",
            now=_NOW,
        )
    )
    await asyncio.wait_for(model_entered.wait(), timeout=1)
    manual = asyncio.create_task(
        service.run_due_from_articles(
            [candidate],
            source_request_id="refresh-manual",
            source_status="healthy",
            tracking_ids={str(tracker["tracking_id"])},
            force=True,
            now=_NOW + timedelta(seconds=1),
        )
    )
    await asyncio.sleep(0.05)

    assert calls == 1
    assert (
        _scalar(
            database,
            "SELECT COUNT(*) FROM tracked_observation_checks WHERE status = 'running'",
        )
        == 1
    )
    release_model.set()
    background_result, manual_result = await asyncio.gather(background, manual)

    joined = manual_result["results"][0]
    assert background_result["check"]["status"] == "changed"
    assert joined["reason"] == "joined_inflight"
    assert joined["check"]["check_id"] == background_result["check"]["check_id"]
    assert joined["check"]["status"] == "changed"
    assert calls == 1
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_checks") == 1
    current = database.get_tracked_observation(str(tracker["tracking_id"]))
    assert current is not None
    assert current["last_check_status"] == "changed"
    assert current["unread_count"] == 1


@pytest.mark.asyncio
async def test_degraded_refresh_never_commits_no_change(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)

    empty_result = await NewsTrackingService(database=database).check_one_from_articles(
        tracker,
        [],
        source_request_id="refresh-degraded",
        source_status="degraded",
        request_id="check-degraded",
        now=_NOW,
    )

    related_tracker = _tracker(database, suffix="degraded-related")

    async def unchanged_analysis(
        _tracker_item: Mapping[str, Any],
        candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        return [
            {
                "evidence_item_key": candidates[0]["item_key"],
                "decision": "related_no_change",
                "change_type": "",
                "confidence": 0.92,
            }
        ]

    related_result = await NewsTrackingService(
        database=database,
        analysis_callback=unchanged_analysis,
    ).check_one_from_articles(
        related_tracker,
        [_candidate(suffix="degraded-related")],
        source_request_id="refresh-degraded-related",
        source_status="degraded",
        request_id="check-degraded-related",
        now=_NOW,
    )

    for result in (empty_result, related_result):
        assert result["check"]["status"] in {"retry_wait", "failed"}
        assert result["check"]["status"] not in {"valid_empty", "unchanged"}
        assert result["check"]["error"]
        assert result["item"]["last_checked_at"] == ""
    assert _unread_count(database, tracker["tracking_id"]) == 0
    assert _unread_count(database, related_tracker["tracking_id"]) == 0
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 0


@pytest.mark.asyncio
async def test_tracking_failure_is_redacted_and_does_not_advance_success_cursor(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)

    async def leaking_failure(
        _tracker_item: Mapping[str, Any],
        _candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        raise RuntimeError("token=super-secret-value provider exploded")

    result = await NewsTrackingService(
        database=database,
        analysis_callback=leaking_failure,
    ).check_one_from_articles(
        tracker,
        [_candidate()],
        source_request_id="refresh-failed",
        source_status="healthy",
        request_id="check-failed",
        now=_NOW,
    )

    assert result["check"]["status"] in {"retry_wait", "failed"}
    assert "super-secret-value" not in result["check"]["error"]
    assert "REDACTED" in result["check"]["error"]
    assert result["item"]["last_checked_at"] == ""
    assert result["item"]["last_error"] == result["check"]["error"]
    assert _unread_count(database, tracker["tracking_id"]) == 0
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 0


@pytest.mark.asyncio
async def test_terminal_failure_stays_read_and_keeps_honest_automatic_retry(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    tracker = _tracker(database)

    async def failing_analysis(
        _tracker_item: Mapping[str, Any],
        _candidates: list[Mapping[str, Any]],
    ) -> list[Mapping[str, Any]]:
        raise RuntimeError("temporary provider failure")

    service = NewsTrackingService(database=database, analysis_callback=failing_analysis)
    result: dict[str, Any] = {}
    for index in range(3):
        current = database.get_tracked_observation(str(tracker["tracking_id"]))
        assert current is not None
        result = await service.check_one_from_articles(
            current,
            [_candidate()],
            source_request_id=f"refresh-failure-{index}",
            source_status="healthy",
            request_id=f"check-failure-{index}",
            now=_NOW + timedelta(hours=index),
        )

    assert result["check"]["status"] == "failed"
    assert result["check"]["is_read"] is True
    assert "继续重试" in result["check"]["summary"]
    assert result["item"]["status"] == "active"
    assert result["item"]["next_check_at"]
    assert result["item"]["failure_count"] == 3
    assert _unread_count(database, tracker["tracking_id"]) == 0
    assert database.tracked_observation_summary()["unread_count"] == 0


def test_initialize_repairs_legacy_failure_that_was_counted_as_unread(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-failure-unread.db"
    database = Database(path)
    database.initialize()
    tracker = _tracker(database)
    check, claimed = database.begin_tracked_observation_check(
        tracking_id=str(tracker["tracking_id"]),
        request_id="legacy-failed-check",
        started_at=_NOW.isoformat(),
        lease_expires_at=(_NOW + timedelta(minutes=10)).isoformat(),
    )
    assert claimed is True
    database._execute_write(
        """
        UPDATE tracked_observation_checks
        SET status = 'failed', is_read = 0, completed_at = ?
        WHERE check_id = ?
        """,
        (_NOW.isoformat(), str(check["check_id"])),
    )
    assert _unread_count(database, str(tracker["tracking_id"])) == 1
    database.close()

    migrated = Database(path)
    migrated.initialize()

    repaired = migrated.list_tracked_observation_checks(str(tracker["tracking_id"]))
    assert repaired[0]["status"] == "failed"
    assert repaired[0]["is_read"] is True
    assert migrated.get_tracked_observation(str(tracker["tracking_id"]))["unread_count"] == 0


@pytest.mark.asyncio
async def test_same_evidence_is_isolated_between_two_trackers(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = _tracker(database, suffix="first")
    second = _tracker(
        database,
        suffix="second",
        observation="核验实施细则发布后是否进入执行阶段",
    )
    service = NewsTrackingService(database=database, analysis_callback=_changed_analysis)
    candidate = _candidate(suffix="shared")

    first_result = await service.check_one_from_articles(
        first,
        [candidate],
        source_request_id="refresh-shared",
        source_status="healthy",
        request_id="check-first",
        now=_NOW,
    )
    second_result = await service.check_one_from_articles(
        second,
        [candidate],
        source_request_id="refresh-shared",
        source_status="healthy",
        request_id="check-second",
        now=_NOW,
    )

    assert first_result["check"]["status"] == "changed"
    assert second_result["check"]["status"] == "changed"
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 2
    assert (
        _scalar(
            database,
            "SELECT COUNT(DISTINCT tracking_id) FROM tracked_observation_evidence",
        )
        == 2
    )
    assert _unread_count(database, first["tracking_id"]) == 1
    assert _unread_count(database, second["tracking_id"]) == 1


@pytest.mark.asyncio
async def test_default_tracking_path_has_no_research_or_profile_dependency(tmp_path: Path) -> None:
    source = inspect.getsource(tracking_module)
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    )
    assert not any(
        name.startswith(("openbiliclaw.research", "openbiliclaw.soul")) for name in imported_modules
    )

    database = _database(tmp_path)
    tracker = _tracker(database)

    def forbidden_model_provider() -> Any:
        raise AssertionError("valid_empty must stay local and model-free")

    result = await NewsTrackingService(
        database=database,
        llm_service_provider=forbidden_model_provider,
    ).check_one_from_articles(
        tracker,
        [],
        source_request_id="refresh-local-only",
        source_status="healthy",
        request_id="check-local-only",
        now=_NOW,
    )

    assert result["check"]["status"] == "valid_empty"
    assert _scalar(database, "SELECT COUNT(*) FROM events") == 0
    assert _scalar(database, "SELECT COUNT(*) FROM tracked_observation_evidence") == 0
