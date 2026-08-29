"""HTTP contract tests for the NewsClaw P4 personal-memory page."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.personal_memory_routes import register_personal_memory_routes
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _client(tmp_path: Path) -> tuple[TestClient, Database]:
    database = Database(tmp_path / "personal-memory-api.db")
    database.initialize()
    app = FastAPI()
    register_personal_memory_routes(app, database=database)
    return TestClient(app), database


def test_dashboard_exposes_four_local_only_sections(tmp_path: Path) -> None:
    client, _database = _client(tmp_path)

    response = client.get("/api/personal-memory")

    assert response.status_code == 200
    assert response.json() == {
        "contract_version": "personal-memory-v1",
        "local_only": True,
        "confirmed_facts": [],
        "research_directions": [],
        "pending_inferences": [],
        "history": [],
        "counts": {
            "confirmed_facts": 0,
            "research_directions": 0,
            "pending_inferences": 0,
            "revisions": 0,
        },
    }


def test_manual_create_edit_delete_restore_are_real_backend_mutations(tmp_path: Path) -> None:
    client, _database = _client(tmp_path)
    created_response = client.post(
        "/api/personal-memory/items",
        json={
            "kind": "confirmed_fact",
            "title": "职业方向",
            "content": "政策与金融研究",
            "request_id": "api-create",
        },
    )
    assert created_response.status_code == 200
    item = created_response.json()["item"]
    assert "claim_key" not in item
    assert "content_fingerprint" not in item

    edited_response = client.patch(
        f"/api/personal-memory/items/{item['memory_id']}",
        json={
            "expected_version": 1,
            "request_id": "api-edit",
            "content": "政策、金融与产业研究",
        },
    )
    assert edited_response.status_code == 200
    assert edited_response.json()["item"]["version"] == 2

    deleted_response = client.post(
        f"/api/personal-memory/items/{item['memory_id']}/delete",
        json={"expected_version": 2, "request_id": "api-delete"},
    )
    assert deleted_response.status_code == 200
    assert deleted_response.json()["item"]["status"] == "deleted"

    history = client.get("/api/personal-memory/history").json()["items"]
    delete_revision = next(row for row in history if row["action"] == "delete")
    assert delete_revision["can_restore"] is True
    assert delete_revision["restore_version"] == 3
    assert "claim_key" not in delete_revision["before"]
    assert "content_fingerprint" not in delete_revision["after"]

    restored_response = client.post(
        f"/api/personal-memory/items/{item['memory_id']}/restore",
        json={"expected_version": 3, "request_id": "api-restore"},
    )
    assert restored_response.status_code == 200
    dashboard = client.get("/api/personal-memory").json()
    assert dashboard["confirmed_facts"][0]["content"] == "政策、金融与产业研究"
    assert [row["action"] for row in reversed(dashboard["history"])] == [
        "create",
        "edit",
        "delete",
        "restore",
    ]


def test_api_rejects_inference_bypass_stale_versions_and_reused_request_ids(
    tmp_path: Path,
) -> None:
    client, _database = _client(tmp_path)

    bypass = client.post(
        "/api/personal-memory/items",
        json={
            "kind": "confirmed_fact",
            "title": "职业",
            "content": "金融从业者",
            "request_id": "api-bypass",
            "source_type": "system_inference",
        },
    )
    assert bypass.status_code == 422

    first = client.post(
        "/api/personal-memory/items",
        json={
            "kind": "confirmed_fact",
            "title": "地区",
            "content": "长三角",
            "request_id": "api-first",
        },
    ).json()["item"]
    second = client.post(
        "/api/personal-memory/items",
        json={
            "kind": "confirmed_fact",
            "title": "行业",
            "content": "金融",
            "request_id": "api-second",
        },
    ).json()["item"]

    assert (
        client.patch(
            f"/api/personal-memory/items/{first['memory_id']}",
            json={
                "expected_version": 1,
                "request_id": "api-shared-edit",
                "content": "长三角政策",
            },
        ).status_code
        == 200
    )
    stale = client.patch(
        f"/api/personal-memory/items/{first['memory_id']}",
        json={
            "expected_version": 1,
            "request_id": "api-stale-edit",
            "content": "长三角政策更新",
        },
    )
    reused = client.patch(
        f"/api/personal-memory/items/{second['memory_id']}",
        json={
            "expected_version": 1,
            "request_id": "api-shared-edit",
            "content": "金融监管",
        },
    )
    assert stale.status_code == 409
    assert reused.status_code == 409
    assert stale.json()["detail"]["code"] == "personal_memory_version_conflict"
    assert reused.json()["detail"]["code"] == "personal_memory_request_conflict"


def test_http_replay_returns_the_same_mutation_without_a_second_revision(
    tmp_path: Path,
) -> None:
    client, database = _client(tmp_path)
    payload = {
        "kind": "confirmed_fact",
        "title": "信息边界",
        "content": "用户确认后才进入长期记忆",
        "request_id": "api-create-replay",
    }

    first = client.post("/api/personal-memory/items", json=payload)
    replay = client.post("/api/personal-memory/items", json=payload)

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["item"]["memory_id"] == first.json()["item"]["memory_id"]
    assert len(database.list_personal_memory_revisions()) == 1


def test_candidate_reject_is_durable_and_missing_targets_are_structured(
    tmp_path: Path,
) -> None:
    client, database = _client(tmp_path)
    candidate, _created = database.propose_personal_memory_candidate(
        proposed_kind="confirmed_fact",
        title="职业身份",
        content="用户是金融从业者",
        proposal_reason="模型从聊天中推断",
        source_type="chat",
        source_ref="chat:turn-reject",
        evidence=[{"type": "chat", "ref": "chat:turn-reject"}],
    )

    rejected = client.post(
        f"/api/personal-memory/candidates/{candidate['candidate_id']}/reject",
        json={
            "expected_version": 1,
            "request_id": "api-reject-candidate",
            "rejection_reason": "这不是我的真实职业",
        },
    )
    missing = client.post(
        "/api/personal-memory/items/missing-memory/delete",
        json={"expected_version": 1, "request_id": "api-delete-missing"},
    )

    assert rejected.status_code == 200
    assert rejected.json()["candidate"]["status"] == "rejected"
    assert client.get("/api/personal-memory").json()["pending_inferences"] == []
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "personal_memory_not_found"


def test_pending_candidate_can_only_enter_active_memory_after_accept(tmp_path: Path) -> None:
    client, database = _client(tmp_path)
    candidate, _created = database.propose_personal_memory_candidate(
        proposed_kind="research_direction",
        title="黄金政策传导",
        content="持续研究黄金政策传导",
        proposal_reason="连续查看同一事件",
        source_type="event",
        source_ref="event:gold-policy",
        confidence=0.7,
        evidence=[{"type": "event", "ref": "event:gold-policy"}],
    )

    before = client.get("/api/personal-memory").json()
    assert before["research_directions"] == []
    assert before["pending_inferences"][0]["candidate_id"] == candidate["candidate_id"]

    accepted = client.post(
        f"/api/personal-memory/candidates/{candidate['candidate_id']}/accept",
        json={"expected_version": 1, "request_id": "api-accept"},
    )
    assert accepted.status_code == 200
    resolved = client.get(
        f"/api/personal-memory/candidates/{candidate['candidate_id']}"
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "accepted"
    assert resolved.json()["resolution_memory_id"] == accepted.json()["item"]["memory_id"]
    after = client.get("/api/personal-memory").json()
    assert after["pending_inferences"] == []
    assert after["research_directions"][0]["source_type"] == "confirmed_inference"


def test_candidate_accept_requires_explicit_preview_before_replacing_manual_memory(
    tmp_path: Path,
) -> None:
    client, database = _client(tmp_path)
    manual = client.post(
        "/api/personal-memory/items",
        json={
            "kind": "confirmed_fact",
            "title": "职业",
            "content": "政策研究",
            "request_id": "api-manual-career",
        },
    ).json()["item"]
    candidate, _created = database.propose_personal_memory_candidate(
        proposed_kind="confirmed_fact",
        title="职业",
        content="金融交易",
        proposal_reason="系统推断职业变化",
        source_type="chat",
        source_ref="chat:api-career",
        evidence=[{"type": "chat", "ref": "chat:api-career"}],
    )

    preview = client.post(
        f"/api/personal-memory/candidates/{candidate['candidate_id']}/accept",
        json={"expected_version": 1, "request_id": "api-preview-career"},
    )
    assert preview.status_code == 409
    assert preview.json()["detail"]["code"] == "personal_memory_merge_required"
    target = preview.json()["detail"]["target_memory"]
    assert target["memory_id"] == manual["memory_id"]
    assert target["content"] == "政策研究"
    assert "claim_key" not in target

    accepted = client.post(
        f"/api/personal-memory/candidates/{candidate['candidate_id']}/accept",
        json={
            "expected_version": 1,
            "request_id": "api-confirm-career-replacement",
            "target_memory_id": target["memory_id"],
            "expected_memory_version": target["version"],
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["item"]["content"] == "金融交易"


def test_empty_edit_uses_the_structured_personal_memory_error_shape(tmp_path: Path) -> None:
    client, _database = _client(tmp_path)
    item = client.post(
        "/api/personal-memory/items",
        json={
            "kind": "confirmed_fact",
            "title": "边界",
            "content": "只有用户确认才进入",
            "request_id": "api-structured-edit-create",
        },
    ).json()["item"]

    response = client.patch(
        f"/api/personal-memory/items/{item['memory_id']}",
        json={"expected_version": 1, "request_id": "api-empty-edit"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "personal_memory_invalid"


def test_no_public_candidate_creation_route_exists(tmp_path: Path) -> None:
    client, _database = _client(tmp_path)

    response = client.post(
        "/api/personal-memory/candidates",
        json={"title": "不允许网页直接伪造系统候选"},
    )

    assert response.status_code == 404
