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


def _event(
    database: Database,
    *,
    suffix: str = "1",
    topic_group: str = "产业政策",
) -> dict[str, Any]:
    event, created = database.create_news_event(
        event_id=str(uuid4()),
        event_key=f"policy-implementation-{suffix}",
        canonical_title=f"产业政策实施进展 {suffix}",
        event_type="policy",
        topic_group=topic_group,
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
    supersedes_change_id: str = "",
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
    if supersedes_change_id:
        change["supersedes_change_id"] = supersedes_change_id
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


def test_event_inbox_has_server_filters_attention_summary_and_stable_cursor(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    policy = _event(database, suffix="policy", topic_group="产业政策")
    finance = _event(database, suffix="finance", topic_group="货币政策")
    conflict_event = _event(database, suffix="conflict-inbox", topic_group="国际政策")
    _record_change(
        database,
        policy["event_id"],
        suffix="policy",
        detected_at="2026-08-28T09:00:00+00:00",
    )
    _record_change(
        database,
        finance["event_id"],
        suffix="finance",
        detected_at="2026-08-28T10:00:00+00:00",
    )
    _record_change(
        database,
        conflict_event["event_id"],
        suffix="conflict-inbox",
        detected_at="2026-08-28T11:00:00+00:00",
        conflict_status="contested",
    )

    first = client.get("/api/news/events", params={"limit": 1})
    conflict_only = client.get("/api/news/events", params={"attention": "conflict"})
    policy_only = client.get(
        "/api/news/events",
        params={"topic_group": "产业政策"},
    )
    wrong_cursor_filter = client.get(
        "/api/news/events",
        params={
            "limit": 1,
            "cursor": first.json()["next_cursor"],
            "attention": "unread",
        },
    )

    assert first.status_code == 200
    assert first.json()["has_more"] is True
    assert first.json()["summary"] == {
        "event_count": 3,
        "unread_event_count": 3,
        "conflict_event_count": 1,
        "unread_change_count": 2,
        "unread_conflict_count": 1,
    }
    assert first.json()["items"][0]["event_id"] == conflict_event["event_id"]
    assert first.json()["items"][0]["latest_change"] is None
    assert {item["topic_group"] for item in first.json()["topics"]} == {
        "产业政策",
        "货币政策",
        "国际政策",
    }
    assert [item["event_id"] for item in conflict_only.json()["items"]] == [
        conflict_event["event_id"]
    ]
    assert [item["event_id"] for item in policy_only.json()["items"]] == [policy["event_id"]]
    assert wrong_cursor_filter.status_code == 422
    assert wrong_cursor_filter.json()["detail"] == "invalid news event cursor"


def test_event_inbox_cursor_requires_refresh_after_mutable_reorder(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    oldest = _event(database, suffix="cursor-oldest")
    middle = _event(database, suffix="cursor-middle")
    newest = _event(database, suffix="cursor-newest")
    for event, suffix, detected_at in (
        (oldest, "cursor-oldest", "2026-08-28T08:00:00+00:00"),
        (middle, "cursor-middle", "2026-08-28T09:00:00+00:00"),
        (newest, "cursor-newest", "2026-08-28T10:00:00+00:00"),
    ):
        _record_change(
            database,
            event["event_id"],
            suffix=suffix,
            detected_at=detected_at,
            effective_at=detected_at,
        )

    first = client.get("/api/news/events", params={"limit": 1})
    assert first.status_code == 200
    assert first.json()["items"][0]["event_id"] == newest["event_id"]
    cursor = first.json()["next_cursor"]
    unchanged_second = client.get(
        "/api/news/events",
        params={"limit": 1, "cursor": cursor},
    )
    assert unchanged_second.status_code == 200
    assert unchanged_second.json()["items"][0]["event_id"] == middle["event_id"]

    _record_change(
        database,
        oldest["event_id"],
        suffix="cursor-new-head",
        detected_at="2026-08-28T11:00:00+00:00",
        effective_at="2026-08-28T11:00:00+00:00",
    )

    stale = client.get(
        "/api/news/events",
        params={"limit": 1, "cursor": cursor},
    )
    refreshed = client.get("/api/news/events", params={"limit": 1})
    assert stale.status_code == 409
    assert stale.json()["detail"] == "news event inbox changed; refresh"
    assert refreshed.status_code == 200
    assert refreshed.json()["items"][0]["event_id"] == oldest["event_id"]


def test_event_inbox_cursor_falls_back_when_legacy_detected_at_is_empty(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    first_event = _event(database, suffix="empty-detected-a")
    second_event = _event(database, suffix="empty-detected-b")
    for event, suffix in (
        (first_event, "empty-detected-a"),
        (second_event, "empty-detected-b"),
    ):
        _record_change(
            database,
            event["event_id"],
            suffix=suffix,
            detected_at="",
        )

    first_page = client.get("/api/news/events", params={"limit": 1})

    assert first_page.status_code == 200
    assert first_page.json()["has_more"] is True
    assert first_page.json()["next_cursor"]

    second_page = client.get(
        "/api/news/events",
        params={"limit": 1, "cursor": first_page.json()["next_cursor"]},
    )
    assert second_page.status_code == 200
    assert len(second_page.json()["items"]) == 1


def test_event_change_cursor_falls_back_when_legacy_detected_at_is_empty(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database, suffix="empty-change-cursor")
    first = _record_change(
        database,
        event["event_id"],
        suffix="empty-change-a",
        detected_at="",
        effective_at="2026-08-28T09:00:00+00:00",
    )
    second = _record_change(
        database,
        event["event_id"],
        suffix="empty-change-b",
        detected_at="",
        effective_at="2026-08-28T10:00:00+00:00",
    )

    first_page = client.get(
        f"/api/news/events/{event['event_id']}/changes",
        params={"limit": 1},
    )
    assert first_page.status_code == 200
    assert first_page.json()["has_more"] is True
    cursor = first_page.json()["next_cursor"]
    assert cursor

    second_page = client.get(
        f"/api/news/events/{event['event_id']}/changes",
        params={"limit": 1, "cursor": cursor},
    )
    assert second_page.status_code == 200
    assert {
        first_page.json()["items"][0]["change_id"],
        second_page.json()["items"][0]["change_id"],
    } == {first["change_id"], second["change_id"]}


def test_event_inbox_uses_same_fallback_sort_for_mixed_legacy_change_times(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database, suffix="mixed-change-times")
    older = _record_change(
        database,
        event["event_id"],
        suffix="mixed-older",
        detected_at="2026-08-28T09:00:00+00:00",
        effective_at="2026-08-28T00:00:00+00:00",
    )
    newer = _record_change(
        database,
        event["event_id"],
        suffix="mixed-newer",
        detected_at="",
        effective_at="2026-08-29",
    )

    inbox = client.get("/api/news/events")

    assert inbox.status_code == 200
    item = next(row for row in inbox.json()["items"] if row["event_id"] == event["event_id"])
    assert item["latest_change"]["change_id"] == newer["change_id"]
    assert item["latest_change"]["change_id"] != older["change_id"]


def test_event_topic_chip_round_trips_normalized_whitespace_and_case(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    first = _event(database, suffix="topic-normalized-a", topic_group="  MACRO　 Policy ")
    second = _event(database, suffix="topic-normalized-b", topic_group="macro policy")

    inbox = client.get("/api/news/events")
    topic = next(
        item for item in inbox.json()["topics"] if item["topic_group"].casefold() == "macro policy"
    )
    filtered = client.get(
        "/api/news/events",
        params={"topic_group": topic["topic_group"]},
    )

    assert topic["event_count"] == 2
    assert {item["event_id"] for item in filtered.json()["items"]} == {
        first["event_id"],
        second["event_id"],
    }


def test_event_cursor_binds_maximum_length_topic_by_digest(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    topic_group = "宏" * 300
    first = _event(database, suffix="long-topic-a", topic_group=topic_group)
    second = _event(database, suffix="long-topic-b", topic_group=topic_group)

    page_one = client.get(
        "/api/news/events",
        params={"topic_group": topic_group, "limit": 1},
    )
    assert page_one.status_code == 200
    cursor = page_one.json()["next_cursor"]
    page_two = client.get(
        "/api/news/events",
        params={"topic_group": topic_group, "limit": 1, "cursor": cursor},
    )

    assert cursor and len(cursor) <= 1_024
    assert page_two.status_code == 200
    assert {
        page_one.json()["items"][0]["event_id"],
        page_two.json()["items"][0]["event_id"],
    } == {first["event_id"], second["event_id"]}


def test_event_audit_view_retains_superseded_objective_history(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database, suffix="audit")
    predecessor = _record_change(
        database,
        event["event_id"],
        suffix="audit-a",
        detected_at="2026-08-28T09:00:00+00:00",
    )
    successor = _record_change(
        database,
        event["event_id"],
        suffix="audit-b",
        detected_at="2026-08-28T10:00:00+00:00",
        supersedes_change_id=predecessor["change_id"],
    )

    current = client.get(f"/api/news/events/{event['event_id']}/changes")
    audit = client.get(
        f"/api/news/events/{event['event_id']}/changes",
        params={"view": "audit"},
    )

    assert [item["change_id"] for item in current.json()["items"]] == [successor["change_id"]]
    assert audit.status_code == 200
    assert audit.json()["view"] == "audit"
    assert [item["lifecycle"] for item in audit.json()["items"]] == [
        "active",
        "superseded",
    ]
    assert [item["is_state_applied"] for item in audit.json()["items"]] == [True, False]
    assert [item["can_mark_read"] for item in audit.json()["items"]] == [True, False]
    assert "inference" not in audit.json()["items"][1]
    assert "confidence" not in audit.json()["items"][1]


def test_resolved_conflict_loser_remains_in_public_audit_history(
    event_api: tuple[TestClient, Database],
) -> None:
    client, database = event_api
    event = _event(database, suffix="audit-conflict-loser")
    first = _record_change(
        database,
        event["event_id"],
        suffix="audit-conflict-b",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    competing = {
        "item_key": "news:audit-conflict-c",
        "evidence_fingerprint": "audit-conflict-c-r1",
        "change_type": "invalidated",
        "previous_state": first["previous_state"],
        "new_state": "互斥的阶段C已经发生",
        "fact": "另一份可靠来源确认阶段C已经发生。",
        "summary": "阶段C与阶段B形成互斥版本",
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
        "content_url": "https://example.com/evidence/audit-conflict-c",
        "published_at": "2025-08-20T10:00:00+00:00",
        "retrieved_at": "2025-08-20T10:00:00+00:00",
        "evidence_snapshot": {"body_text": "正式文件明确确认阶段C已经发生。"},
    }
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-20T10:00:00+00:00",
        changes=[competing],
    )
    conflict_rows = database.list_news_event_changes(
        event_id=event["event_id"],
        objective_only=False,
        conflicts_only=True,
    )
    loser_id = next(
        row["change_id"] for row in conflict_rows if row["new_state"] == first["new_state"]
    )
    resolution = dict(
        competing,
        item_key="news:audit-conflict-c-resolution",
        evidence_fingerprint="audit-conflict-c-resolution-r1",
        objectivity_status="confirmed",
        conflict_status="resolved",
        evidence_status="source_supported",
        supersedes_change_id=loser_id,
        supersedes_lifecycle="invalidated",
        published_at="2025-08-21T09:00:00+00:00",
        retrieved_at="2025-08-21T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-21T09:00:00+00:00",
        changes=[resolution],
    )

    current = client.get(f"/api/news/events/{event['event_id']}/changes")
    audit = client.get(
        f"/api/news/events/{event['event_id']}/changes",
        params={"view": "audit"},
    )
    conflicts = client.get(f"/api/news/events/{event['event_id']}/conflicts")
    detail = client.get(f"/api/news/events/{event['event_id']}")

    assert current.status_code == 200
    assert [item["new_state"] for item in current.json()["items"]] == ["互斥的阶段C已经发生"]
    assert conflicts.status_code == 200 and conflicts.json()["items"] == []
    assert audit.status_code == 200
    loser = next(item for item in audit.json()["items"] if item["change_id"] == loser_id)
    assert loser["lifecycle"] == "invalidated"
    assert loser["objectivity_status"] == "disputed"
    assert loser["conflict_status"] == "contested"
    assert loser["is_state_applied"] is False
    assert loser["can_mark_read"] is False
    assert detail.json()["item"]["audit_change_count"] == 2
    loser_evidence = client.get(
        f"/api/news/events/{event['event_id']}/evidence",
        params={"change_id": loser_id},
    )
    assert loser_evidence.status_code == 200 and loser_evidence.json()["items"]


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
