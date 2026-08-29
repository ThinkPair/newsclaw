"""Contract tests for NewsClaw P4 user-controlled personal memory."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.storage.database import (
    Database,
    PersonalMemoryConflictError,
    PersonalMemoryMergeRequiredError,
    PersonalMemoryRequestConflictError,
)

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "personal-memory.db")
    database.initialize()
    return database


def _candidate(database: Database, *, content: str = "用户持续关注黄金政策传导") -> dict:
    candidate, created = database.propose_personal_memory_candidate(
        proposed_kind="research_direction",
        title="黄金政策传导",
        content=content,
        proposal_reason="用户连续讨论同一研究主题",
        source_type="event",
        source_ref="event:gold-policy",
        confidence=0.72,
        evidence=[
            {
                "type": "event",
                "ref": "event:gold-policy",
                "excerpt": "连续三次打开黄金政策事件档案",
                "hash": "a" * 64,
                "observed_at": "2026-08-29T12:00:00+08:00",
            }
        ],
    )
    assert created is True
    return candidate


def test_existing_database_upgrade_adds_empty_personal_memory_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "pre-p4.db"
    connection = sqlite3.connect(database_path)
    connection.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO schema_version (version) VALUES (7)")
    connection.commit()
    connection.close()

    database = Database(database_path)
    database.initialize()

    assert database.list_personal_memory_items(status="all") == []
    assert database.list_personal_memory_candidates(status="all") == []
    assert database.list_personal_memory_revisions() == []


def test_personal_memory_schema_has_stable_identity_and_version_history(tmp_path: Path) -> None:
    database = _db(tmp_path)

    item_columns = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(personal_memory_items)")
    }
    request_columns = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(personal_memory_requests)")
    }
    indexes = {
        str(row["name"])
        for row in database.conn.execute("PRAGMA index_list(personal_memory_items)")
    }

    assert {"claim_key", "content_fingerprint", "version", "deleted_at"} <= item_columns
    assert "request_fingerprint" in request_columns
    assert {"idx_personal_memory_claim", "idx_personal_memory_content"} <= indexes


def test_only_explicit_user_create_can_write_active_memory(tmp_path: Path) -> None:
    database = _db(tmp_path)

    with pytest.raises(ValueError, match="explicit user action"):
        database.create_personal_memory_item(
            kind="confirmed_fact",
            title="职业",
            content="政策研究人员",
            source_type="system_inference",
            request_id="create-non-user",
        )

    assert database.list_personal_memory_items(status="all") == []
    assert database.list_personal_memory_revisions() == []
    assert database.conn.execute("SELECT COUNT(*) FROM personal_memory_requests").fetchone()[0] == 0


def test_manual_create_is_idempotent_without_storing_private_content_in_receipt(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    content = "只在本机保留的政策研究偏好"

    first = database.create_personal_memory_item(
        kind="confirmed_fact",
        title="工作偏好",
        content=content,
        request_id="create-fact-1",
    )
    replay = database.create_personal_memory_item(
        kind="confirmed_fact",
        title="工作偏好",
        content=content,
        request_id="create-fact-1",
    )

    assert first["created"] is True
    assert replay["replayed"] is True
    assert replay["item"]["memory_id"] == first["item"]["memory_id"]
    receipt = database.conn.execute(
        "SELECT response_json FROM personal_memory_requests WHERE request_id = ?",
        ("create-fact-1",),
    ).fetchone()[0]
    assert content not in str(receipt)
    assert json.loads(str(receipt))["target_id"] == first["item"]["memory_id"]


def test_request_id_cannot_be_reused_for_a_different_target_or_payload(tmp_path: Path) -> None:
    database = _db(tmp_path)
    first = database.create_personal_memory_item(
        kind="confirmed_fact",
        title="地区",
        content="关注长三角",
        request_id="create-region",
    )["item"]
    second = database.create_personal_memory_item(
        kind="confirmed_fact",
        title="行业",
        content="关注金融行业",
        request_id="create-industry",
    )["item"]

    database.update_personal_memory_item(
        first["memory_id"],
        expected_version=1,
        request_id="shared-update-key",
        content="关注长三角政策",
    )
    with pytest.raises(PersonalMemoryRequestConflictError):
        database.update_personal_memory_item(
            second["memory_id"],
            expected_version=1,
            request_id="shared-update-key",
            content="关注金融监管",
        )

    assert database.get_personal_memory_item(second["memory_id"])["content"] == "关注金融行业"


def test_edit_delete_restore_preserves_one_identity_and_appends_revisions(tmp_path: Path) -> None:
    database = _db(tmp_path)
    created = database.create_personal_memory_item(
        kind="confirmed_fact",
        title="职业方向",
        content="政策研究",
        request_id="identity-create",
    )["item"]
    claim_key = created["claim_key"]

    edited = database.update_personal_memory_item(
        created["memory_id"],
        expected_version=1,
        request_id="identity-edit",
        title="当前职业方向",
        content="政策与金融研究",
    )["item"]
    deleted = database.delete_personal_memory_item(
        created["memory_id"],
        expected_version=2,
        request_id="identity-delete",
    )["item"]

    assert edited["claim_key"] == claim_key
    assert deleted["status"] == "deleted"
    with pytest.raises(PersonalMemoryConflictError, match="restore"):
        database.create_personal_memory_item(
            kind="confirmed_fact",
            title="当前职业方向",
            content="政策与金融研究",
            request_id="identity-recreate",
        )

    restored = database.restore_personal_memory_item(
        created["memory_id"],
        expected_version=3,
        request_id="identity-restore",
    )["item"]
    assert restored["memory_id"] == created["memory_id"]
    assert restored["version"] == 4
    assert restored["status"] == "active"
    assert [row["action"] for row in reversed(database.list_personal_memory_revisions())] == [
        "create",
        "edit",
        "delete",
        "restore",
    ]


def test_new_request_must_match_current_version_even_for_a_noop(tmp_path: Path) -> None:
    database = _db(tmp_path)
    item = database.create_personal_memory_item(
        kind="confirmed_fact",
        title="信息边界",
        content="不自动确认系统推断",
        request_id="noop-create",
    )["item"]
    database.delete_personal_memory_item(
        item["memory_id"], expected_version=1, request_id="noop-delete"
    )

    with pytest.raises(PersonalMemoryConflictError, match="version"):
        database.delete_personal_memory_item(
            item["memory_id"], expected_version=1, request_id="noop-stale-delete"
        )


def test_candidate_requires_sanitized_traceable_evidence(tmp_path: Path) -> None:
    database = _db(tmp_path)

    with pytest.raises(ValueError, match="requires evidence"):
        database.propose_personal_memory_candidate(
            proposed_kind="confirmed_fact",
            title="职业",
            content="金融从业者",
            proposal_reason="聊天推断",
            source_type="chat",
            source_ref="chat:turn-1",
            evidence=[],
        )
    with pytest.raises(ValueError, match="unsupported fields"):
        database.propose_personal_memory_candidate(
            proposed_kind="confirmed_fact",
            title="职业",
            content="金融从业者",
            proposal_reason="聊天推断",
            source_type="chat",
            source_ref="chat:turn-1",
            evidence=[
                {"type": "chat", "ref": "chat:turn-1", "authorization": "secret"}
            ],
        )
    with pytest.raises(ValueError, match="local path"):
        database.propose_personal_memory_candidate(
            proposed_kind="confirmed_fact",
            title="职业",
            content="金融从业者",
            proposal_reason="导入推断",
            source_type="import",
            source_ref=r"C:\private\raw-chat.json",
            evidence=[{"type": "import", "ref": "import:raw-chat"}],
        )

    assert database.list_personal_memory_candidates(status="all") == []


def test_accept_candidate_is_atomic_versioned_and_the_only_inference_promotion(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    candidate = _candidate(database)

    accepted = database.resolve_personal_memory_candidate(
        candidate["candidate_id"],
        decision="accept",
        expected_version=1,
        request_id="accept-candidate",
    )
    replay = database.resolve_personal_memory_candidate(
        candidate["candidate_id"],
        decision="accept",
        expected_version=1,
        request_id="accept-candidate",
    )

    assert accepted["candidate"]["status"] == "accepted"
    assert accepted["item"]["status"] == "active"
    assert accepted["item"]["source_type"] == "confirmed_inference"
    assert accepted["item"]["origin_candidate_id"] == candidate["candidate_id"]
    assert replay["replayed"] is True
    assert database.personal_memory_counts()["research_directions"] == 1
    assert {row["action"] for row in database.list_personal_memory_revisions()} >= {
        "propose",
        "create_from_candidate",
        "accept_candidate",
    }


def test_candidate_cannot_overwrite_manual_memory_without_exact_target_confirmation(
    tmp_path: Path,
) -> None:
    database = _db(tmp_path)
    manual = database.create_personal_memory_item(
        kind="confirmed_fact",
        title="职业",
        content="政策研究",
        request_id="manual-career",
    )["item"]
    candidate, _created = database.propose_personal_memory_candidate(
        proposed_kind="confirmed_fact",
        title="职业",
        content="金融交易",
        proposal_reason="系统从聊天中推断",
        source_type="chat",
        source_ref="chat:career-change",
        evidence=[{"type": "chat", "ref": "chat:career-change"}],
    )

    with pytest.raises(PersonalMemoryMergeRequiredError) as preview:
        database.resolve_personal_memory_candidate(
            candidate["candidate_id"],
            decision="accept",
            expected_version=1,
            request_id="accept-without-target",
        )

    assert preview.value.target_item["memory_id"] == manual["memory_id"]
    assert database.get_personal_memory_item(manual["memory_id"])["content"] == "政策研究"
    assert database.get_personal_memory_candidate(candidate["candidate_id"])["status"] == "pending"

    database.update_personal_memory_item(
        manual["memory_id"],
        expected_version=1,
        request_id="concurrent-manual-edit",
        content="政策与产业研究",
    )
    with pytest.raises(PersonalMemoryConflictError, match="version"):
        database.resolve_personal_memory_candidate(
            candidate["candidate_id"],
            decision="accept",
            expected_version=1,
            request_id="accept-stale-target",
            target_memory_id=manual["memory_id"],
            expected_memory_version=1,
        )

    accepted = database.resolve_personal_memory_candidate(
        candidate["candidate_id"],
        decision="accept",
        expected_version=1,
        request_id="accept-exact-target",
        target_memory_id=manual["memory_id"],
        expected_memory_version=2,
    )
    assert accepted["item"]["memory_id"] == manual["memory_id"]
    assert accepted["item"]["content"] == "金融交易"
    assert accepted["item"]["version"] == 3


def test_rejected_candidate_is_durable_and_is_not_proposed_again(tmp_path: Path) -> None:
    database = _db(tmp_path)
    candidate = _candidate(database)
    rejected = database.resolve_personal_memory_candidate(
        candidate["candidate_id"],
        decision="reject",
        expected_version=1,
        request_id="reject-candidate",
        rejection_reason="这只是临时查看，不是长期方向",
    )

    duplicate, created = database.propose_personal_memory_candidate(
        proposed_kind="research_direction",
        title="黄金政策传导",
        content="用户持续关注黄金政策传导",
        proposal_reason="再次观察到同样模式",
        source_type="event",
        source_ref="event:gold-policy",
        confidence=0.8,
        evidence=[{"type": "event", "ref": "event:gold-policy"}],
    )

    assert rejected["candidate"]["status"] == "rejected"
    assert created is False
    assert duplicate["candidate_id"] == candidate["candidate_id"]
    assert database.list_personal_memory_items(status="active") == []


def test_concurrent_accept_and_reject_have_one_winner(tmp_path: Path) -> None:
    database = _db(tmp_path)
    candidate = _candidate(database)

    def decide(decision: str) -> str:
        try:
            database.resolve_personal_memory_candidate(
                candidate["candidate_id"],
                decision=decision,
                expected_version=1,
                request_id=f"race-{decision}",
                rejection_reason="不是长期方向" if decision == "reject" else "",
            )
        except PersonalMemoryConflictError:
            return "conflict"
        return "won"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(decide, ["accept", "reject"]))

    assert sorted(results) == ["conflict", "won"]
    resolved = database.get_personal_memory_candidate(candidate["candidate_id"])
    assert resolved["status"] in {"accepted", "rejected"}
    expected_items = 1 if resolved["status"] == "accepted" else 0
    assert len(database.list_personal_memory_items(status="active")) == expected_items


def test_revision_failure_rolls_back_item_and_idempotency_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _db(tmp_path)

    def fail_revision(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected revision failure")

    monkeypatch.setattr(database, "_insert_personal_memory_revision_on", fail_revision)
    with pytest.raises(RuntimeError, match="injected revision failure"):
        database.create_personal_memory_item(
            kind="confirmed_fact",
            title="回滚验证",
            content="不允许半写",
            request_id="rollback-create",
        )

    assert database.list_personal_memory_items(status="all") == []
    assert database.conn.execute("SELECT COUNT(*) FROM personal_memory_requests").fetchone()[0] == 0
