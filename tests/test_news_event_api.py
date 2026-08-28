from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.news_routes import register_newsclaw_routes
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class _QuietSoul:
    def is_profile_ready(self) -> bool:
        return False


class _BombIngress:
    async def accept_batch(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("event read APIs must not collect or publish news")


async def _bomb_replenishment(**_kwargs: object) -> None:
    raise AssertionError("event read APIs must not replenish recommendations")


@pytest.fixture
def event_api(tmp_path: Path) -> tuple[TestClient, Database]:
    database = Database(tmp_path / "news-events.db")
    database.initialize()
    app = FastAPI()
    runtime = register_newsclaw_routes(
        app,
        data_path=tmp_path / "runtime",
        database=database,
        soul_engine=_QuietSoul(),
        event_ingress=_BombIngress(),
        request_replenishment=_bomb_replenishment,
    )

    async def _bomb_refresh(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("event read APIs must not start a source refresh")

    runtime.refresh = _bomb_refresh  # type: ignore[method-assign]
    client = TestClient(app)
    yield client, database
    client.close()


def _event(database: Database, *, suffix: str = "1") -> dict[str, Any]:
    event, created = database.create_news_event(
        event_id=str(uuid4()),
        event_key=f"policy-implementation-{suffix}",
        canonical_title=f"产业政策实施进展 {suffix}",
        event_type="policy",
        topic_group="产业政策",
        story_key=f"政策实施跟踪-{suffix}",
        current_state="实施细则尚未发布",
        first_seen_at="2026-08-27T08:00:00+00:00",
        last_seen_at="2026-08-27T08:00:00+00:00",
    )
    assert created is True
    return event


def _record_change(
    database: Database,
    event_id: str,
    *,
    suffix: str,
    detected_at: str,
    role: str = "objective_change",
    objectivity_status: str = "confirmed",
    conflict_status: str = "none",
    effective_at: str = "2026-08-28T08:00:00+00:00",
) -> dict[str, Any]:
    change = {
        "item_key": f"news:evidence-{suffix}",
        "evidence_fingerprint": f"evidence-fingerprint-{suffix}",
        "change_type": "progress",
        "previous_state": f"阶段 {suffix} 之前",
        "new_state": f"阶段 {suffix} 已经发生",
        "fact": f"来源确认阶段 {suffix} 已经发生。",
        "inference": "这是一段不应出现在客观变化列表的 AI 解释。",
        "summary": f"阶段 {suffix} 已经发生",
        "evidence_quote": f"阶段 {suffix} 已经发生",
        "confidence": 0.94,
        "role": role,
        "objectivity_status": objectivity_status,
        "conflict_status": conflict_status,
        "source_scope": "single_source",
        "evidence_status": "source_supported",
        "effective_at": effective_at,
        "content_scope": "full_text",
        "media": "测试媒体",
        "provider": "官方 RSS/Atom",
        "title": f"阶段 {suffix} 更新",
        "content_url": f"https://example.com/evidence/{suffix}",
        "published_at": detected_at,
        "retrieved_at": detected_at,
        "evidence_snapshot": {"body_text": f"阶段 {suffix} 已经发生"},
    }
    database.record_news_event_projection(
        event_id=event_id,
        check_status="changed",
        detected_at=detected_at,
        check_id=str(uuid4()),
        changes=[change],
    )
    rows = database.list_news_event_changes(
        event_id=event_id,
        objective_only=False,
        limit=100,
    )
    return next(row for row in rows if row["evidence_quote"] == change["evidence_quote"])


def test_news_event_routes_are_only_registered_in_newsclaw_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEWSCLAW_MODE", raising=False)
    database = Database(tmp_path / "normal-mode.db")
    database.initialize()

    app = create_app(database=database, memory_manager=object(), soul_engine=object())

    assert "/api/news/changes" not in {route.path for route in app.routes}


def test_objective_feed_excludes_expectations_and_internal_interpretation(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database)
    objective = _record_change(
        database,
        event["event_id"],
        suffix="objective",
        detected_at="2026-08-28T09:00:00+00:00",
    )
    _record_change(
        database,
        event["event_id"],
        suffix="expectation",
        detected_at="2026-08-28T10:00:00+00:00",
        role="expectation",
        objectivity_status="not_applicable",
        effective_at="",
    )

    response = client.get("/api/news/changes")

    assert response.status_code == 200
    assert [item["change_id"] for item in response.json()["items"]] == [objective["change_id"]]
    item = response.json()["items"][0]
    assert item["role"] == "objective_change"
    assert item["objectivity_status"] == "confirmed"
    assert "inference" not in item
    assert "confidence" not in item
    assert "change_fingerprint" not in item
    assert "evidence_snapshot" not in item


def test_change_cursor_is_filter_bound_and_stable_under_new_head_insert(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database)
    for suffix, detected_at in (
        ("oldest", "2026-08-28T08:00:00+00:00"),
        ("middle", "2026-08-28T09:00:00+00:00"),
        ("newest", "2026-08-28T10:00:00+00:00"),
    ):
        _record_change(
            database,
            event["event_id"],
            suffix=suffix,
            detected_at=detected_at,
            effective_at=detected_at,
        )

    first = client.get("/api/news/changes", params={"limit": 1}).json()
    assert first["has_more"] is True
    assert first["items"][0]["summary"] == "阶段 newest 已经发生"
    cursor = first["next_cursor"]

    _record_change(
        database,
        event["event_id"],
        suffix="new-head",
        detected_at="2026-08-28T11:00:00+00:00",
        effective_at="2026-08-28T11:00:00+00:00",
    )
    second = client.get(
        "/api/news/changes",
        params={"limit": 1, "cursor": cursor},
    )
    wrong_filter = client.get(
        "/api/news/changes",
        params={"limit": 1, "cursor": cursor, "unread_only": True},
    )

    assert second.status_code == 200
    assert second.json()["items"][0]["summary"] == "阶段 middle 已经发生"
    assert wrong_filter.status_code == 422
    assert wrong_filter.json()["detail"] == "invalid news change cursor"


def test_event_detail_evidence_and_read_are_closed_and_idempotent(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database)
    change = _record_change(
        database,
        event["event_id"],
        suffix="readable",
        detected_at="2026-08-28T09:00:00+00:00",
    )

    detail = client.get(f"/api/news/events/{event['event_id']}")
    timeline = client.get(f"/api/news/events/{event['event_id']}/changes")
    evidence = client.get(
        f"/api/news/events/{event['event_id']}/evidence",
        params={"change_id": change["change_id"]},
    )
    first_read = client.post(f"/api/news/changes/{change['change_id']}/read")
    second_read = client.post(f"/api/news/changes/{change['change_id']}/read")
    unread = client.get("/api/news/changes", params={"unread_only": True})

    assert detail.status_code == 200
    assert detail.json()["item"]["event_id"] == event["event_id"]
    assert [item["change_id"] for item in timeline.json()["items"]] == [change["change_id"]]
    assert evidence.status_code == 200
    assert evidence.json()["items"][0]["relation_kind"] == "supports_change"
    assert evidence.json()["items"][0]["evidence_snapshot"]["body_text"]
    assert first_read.status_code == 200
    assert first_read.json()["updated"] is True
    assert second_read.status_code == 200
    assert second_read.json()["updated"] is False
    assert unread.json()["items"] == []


def test_event_timeline_does_not_mix_events_and_secondary_read_is_hidden(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    first_event = _event(database, suffix="first")
    second_event = _event(database, suffix="second")
    first_change = _record_change(
        database,
        first_event["event_id"],
        suffix="first",
        detected_at="2026-08-28T09:00:00+00:00",
    )
    _record_change(
        database,
        second_event["event_id"],
        suffix="second",
        detected_at="2026-08-28T10:00:00+00:00",
    )
    secondary = _record_change(
        database,
        first_event["event_id"],
        suffix="sentiment",
        detected_at="2026-08-28T11:00:00+00:00",
        role="sentiment",
        objectivity_status="not_applicable",
        effective_at="",
    )

    response = client.get(f"/api/news/events/{first_event['event_id']}/changes")
    hidden_read = client.post(f"/api/news/changes/{secondary['change_id']}/read")
    malformed = client.get("/api/news/events/not-a-uuid")

    assert [item["change_id"] for item in response.json()["items"]] == [first_change["change_id"]]
    assert hidden_read.status_code == 404
    assert malformed.status_code == 422


def test_divergent_changes_have_visible_auditable_conflict_loop(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database, suffix="conflict")
    first = _record_change(
        database,
        event["event_id"],
        suffix="conflict-b",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    conflicting = {
        "item_key": "news:evidence-conflict-c",
        "evidence_fingerprint": "evidence-fingerprint-conflict-c",
        "change_type": "invalidated",
        "previous_state": first["previous_state"],
        "new_state": "互斥的阶段C已经发生",
        "fact": "另一份来源确认互斥的阶段C已经发生。",
        "summary": "出现与阶段B互斥的阶段C",
        "evidence_quote": "阶段C已经发生",
        "confidence": 0.94,
        "role": "objective_change",
        "objectivity_status": "confirmed",
        "conflict_status": "none",
        "source_scope": "single_source",
        "evidence_status": "source_supported",
        "effective_at": "2025-08-20T08:00:00+00:00",
        "content_scope": "full_text",
        "media": "另一测试媒体",
        "provider": "官方页面",
        "title": "阶段C更新",
        "content_url": "https://example.com/evidence/conflict-c",
        "published_at": "2025-08-20T10:00:00+00:00",
        "retrieved_at": "2025-08-20T10:00:00+00:00",
        "evidence_snapshot": {"body_text": "另一份正式文件明确确认互斥的阶段C已经发生。"},
    }
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-20T10:00:00+00:00",
        changes=[conflicting],
    )

    detail = client.get(f"/api/news/events/{event['event_id']}")
    objective = client.get(f"/api/news/events/{event['event_id']}/changes")
    conflicts = client.get(f"/api/news/events/{event['event_id']}/conflicts")

    assert detail.status_code == 200
    assert detail.json()["item"]["current_state"] == ""
    assert detail.json()["item"]["conflict_count"] == 2
    assert detail.json()["item"]["unread_conflict_count"] == 2
    assert objective.json()["items"] == []
    assert conflicts.status_code == 200
    assert len(conflicts.json()["items"]) == 2
    assert {item["freeze_reason"] for item in conflicts.json()["items"]} == {
        "durable_evidence_conflict"
    }
    conflict_id = conflicts.json()["items"][0]["change_id"]
    evidence = client.get(
        f"/api/news/events/{event['event_id']}/evidence",
        params={"change_id": conflict_id},
    )
    first_read = client.post(f"/api/news/conflicts/{conflict_id}/read")
    second_read = client.post(f"/api/news/conflicts/{conflict_id}/read")
    hidden_objective_read = client.post(f"/api/news/changes/{conflict_id}/read")
    unread = client.get(
        f"/api/news/events/{event['event_id']}/conflicts",
        params={"unread_only": True},
    )

    assert evidence.status_code == 200 and evidence.json()["items"]
    assert first_read.status_code == 200 and first_read.json()["updated"] is True
    assert second_read.status_code == 200 and second_read.json()["updated"] is False
    assert hidden_objective_read.status_code == 404
    assert len(unread.json()["items"]) == 1


def test_declared_conflict_is_canonical_visible_and_unread(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database, suffix="declared-conflict")
    conflict = _record_change(
        database,
        event["event_id"],
        suffix="declared-conflict",
        detected_at="2025-08-20T09:00:00+00:00",
        conflict_status="contested",
        objectivity_status="confirmed",
        effective_at="2025-08-20T08:00:00+00:00",
    )

    assert conflict["objectivity_status"] == "disputed"
    assert conflict["conflict_status"] == "contested"
    assert conflict["evidence_status"] == "contested"
    assert conflict["is_read"] is False
    detail = client.get(f"/api/news/events/{event['event_id']}")
    unread = client.get(
        f"/api/news/events/{event['event_id']}/conflicts",
        params={"unread_only": True},
    )
    assert detail.status_code == 200
    assert detail.json()["item"]["conflict_count"] == 1
    assert detail.json()["item"]["unread_conflict_count"] == 1
    assert [item["change_id"] for item in unread.json()["items"]] == [conflict["change_id"]]
    first_read = client.post(f"/api/news/conflicts/{conflict['change_id']}/read")
    second_read = client.post(f"/api/news/conflicts/{conflict['change_id']}/read")
    assert first_read.status_code == 200 and first_read.json()["updated"] is True
    assert second_read.status_code == 200 and second_read.json()["updated"] is False
    assert (
        client.get(
            f"/api/news/events/{event['event_id']}/conflicts",
            params={"unread_only": True},
        ).json()["items"]
        == []
    )


def test_lineage_conflict_api_exposes_both_auditable_candidate_edges(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    source = _event(database, suffix="lineage-source")
    target = _event(database, suffix="lineage-target")

    def record_lineage(
        event_id: str,
        *,
        suffix: str,
        predecessor_state: str,
    ) -> tuple[str, str]:
        predecessor = {
            "item_key": f"news:{suffix}-predecessor",
            "evidence_fingerprint": f"{suffix}-predecessor-r1",
            "change_type": "progress",
            "previous_state": predecessor_state,
            "new_state": "阶段B",
            "fact": f"{predecessor_state}已变为阶段B",
            "summary": "前置状态已变为阶段B",
            "role": "objective_change",
            "objectivity_status": "confirmed",
            "conflict_status": "none",
            "source_scope": "single_source",
            "evidence_status": "source_supported",
            "effective_at": "2025-08-20T08:00:00+00:00",
            "detected_at": "2025-08-20T09:00:00+00:00",
            "content_scope": "full_text",
            "media": f"{suffix}媒体",
            "title": f"{suffix}前置证据",
            "evidence_snapshot": {"body_text": f"来源确认{predecessor_state}已变为阶段B"},
        }
        database.record_news_event_projection(
            event_id=event_id,
            check_status="changed",
            detected_at=predecessor["detected_at"],
            changes=[predecessor],
        )
        predecessor_id = next(
            row["change_id"]
            for row in database.list_news_event_changes(
                event_id=event_id,
                objective_only=False,
            )
            if row["new_state"] == "阶段B"
        )
        successor = {
            **predecessor,
            "item_key": f"news:{suffix}-successor",
            "evidence_fingerprint": f"{suffix}-successor-r1",
            "previous_state": "阶段B",
            "new_state": "阶段C",
            "fact": "阶段B已变为阶段C",
            "summary": "后续状态已变为阶段C",
            "effective_at": "2025-08-21T08:00:00+00:00",
            "detected_at": "2025-08-21T09:00:00+00:00",
            "supersedes_change_id": predecessor_id,
            "supersedes_lifecycle": "superseded",
            "evidence_snapshot": {"body_text": "来源确认阶段B已变为阶段C"},
        }
        database.record_news_event_projection(
            event_id=event_id,
            check_status="changed",
            detected_at=successor["detected_at"],
            changes=[successor],
        )
        successor_id = next(
            row["change_id"]
            for row in database.list_news_event_changes(
                event_id=event_id,
                objective_only=False,
            )
            if row["new_state"] == "阶段C"
        )
        return str(predecessor_id), str(successor_id)

    source_predecessor_id, source_successor_id = record_lineage(
        source["event_id"],
        suffix="lineage-source",
        predecessor_state="阶段A",
    )
    target_predecessor_id, target_successor_id = record_lineage(
        target["event_id"],
        suffix="lineage-target",
        predecessor_state="阶段X",
    )
    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    response = client.get(f"/api/news/events/{target['event_id']}/conflicts")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["change_id"] == target_successor_id
    assert source_successor_id != target_successor_id
    claims = items[0]["lineage_claims"]
    assert {claim["predecessor_change_id"] for claim in claims} == {
        source_predecessor_id,
        target_predecessor_id,
    }
    assert {claim["status"] for claim in claims} == {"contested"}
    assert {claim["source_event_id"] for claim in claims} == {
        source["event_id"],
        target["event_id"],
    }


def test_merged_event_resolves_to_canonical_target_and_cursor_cannot_cross_events(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    source = _event(database, suffix="merge-source")
    target = _event(database, suffix="merge-target")
    other = _event(database, suffix="merge-other")
    for suffix, detected_at in (
        ("merge-a", "2026-08-28T08:00:00+00:00"),
        ("merge-b", "2026-08-28T09:00:00+00:00"),
    ):
        _record_change(
            database,
            target["event_id"],
            suffix=suffix,
            detected_at=detected_at,
            effective_at=detected_at,
        )
    _record_change(
        database,
        other["event_id"],
        suffix="other",
        detected_at="2026-08-28T10:00:00+00:00",
    )
    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    detail = client.get(f"/api/news/events/{source['event_id']}")
    first_page = client.get(
        f"/api/news/events/{source['event_id']}/changes",
        params={"limit": 1},
    )
    cross_event = client.get(
        f"/api/news/events/{other['event_id']}/changes",
        params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
    )
    missing = client.get(f"/api/news/events/{uuid4()}")

    assert detail.status_code == 200
    assert detail.json()["item"]["event_id"] == target["event_id"]
    assert detail.json()["resolved_from_event_id"] == source["event_id"]
    assert first_page.status_code == 200
    assert first_page.json()["event"]["event_id"] == target["event_id"]
    assert first_page.json()["has_more"] is True
    assert cross_event.status_code == 422
    assert missing.status_code == 404


def test_evidence_endpoint_reports_canonical_change_when_merge_alias_is_used(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    source = _event(database, suffix="alias-source")
    target = _event(database, suffix="alias-target")
    source_change = _record_change(
        database,
        source["event_id"],
        suffix="alias-duplicate",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    target_change = _record_change(
        database,
        target["event_id"],
        suffix="alias-duplicate",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    response = client.get(
        f"/api/news/events/{source['event_id']}/evidence",
        params={"change_id": source_change["change_id"]},
    )

    assert response.status_code == 200
    assert response.json()["event_id"] == target["event_id"]
    assert response.json()["change_id"] == target_change["change_id"]
    assert response.json()["resolved_from_change_id"] == source_change["change_id"]
    assert all(item["change_id"] == target_change["change_id"] for item in response.json()["items"])
