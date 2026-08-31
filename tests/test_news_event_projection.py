from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(tmp_path / "news-events.db")
    db.initialize()
    try:
        yield db
    finally:
        db.close()


def _create_tracker(
    db: Database,
    *,
    request_id: str,
    item_key: str,
    story_key: str = "story-policy",
    observation: str = "政策是否已经生效",
) -> dict[str, Any]:
    tracker, _ = db.create_tracked_observation(
        request_id=request_id,
        item_key=item_key,
        recommendation_id=None,
        topic_group="宏观政策",
        story_key=story_key,
        observation=observation,
        latest_evidence="仍处于征求意见阶段",
        original_snapshot={
            "title": f"政策原文 {item_key}",
            "content_url": f"https://example.test/{item_key}",
            "content_scope": "full_text",
            "published_at": "2026-08-01T00:00:00+00:00",
        },
    )
    return tracker


def _objective_change(
    *,
    item_key: str,
    evidence_fingerprint: str,
    previous_state: str = "征求意见",
    new_state: str = "正式生效",
    change_type: str = "progress",
    detected_at: str = "2026-08-28T08:00:00+00:00",
    effective_at: str = "",
    media: str = "测试媒体",
) -> dict[str, Any]:
    return {
        "item_key": item_key,
        "evidence_fingerprint": evidence_fingerprint,
        "change_fingerprint": f"legacy-{evidence_fingerprint}",
        "change_type": change_type,
        "previous_state": previous_state,
        "new_state": new_state,
        "fact": f"{previous_state}变为{new_state}",
        "summary": f"状态已从{previous_state}变为{new_state}",
        "title": f"证据 {item_key}",
        "body_text": f"来源正文确认：{previous_state}已经变为{new_state}。",
        "content_url": f"https://evidence.test/{item_key}/{evidence_fingerprint}",
        "content_scope": "full_text",
        "media": media,
        "role": "objective_change",
        "objectivity_status": "confirmed",
        "conflict_status": "none",
        "effective_at": effective_at or detected_at,
        "detected_at": detected_at,
        "source_scope": "single_source",
        "evidence_status": "source_supported",
        "confidence": 0.93,
    }


def test_schema_is_additive_and_initialize_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "migrated.db"
    raw = sqlite3.connect(path)
    raw.execute(
        """
        CREATE TABLE tracked_observations (
            tracking_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            item_key TEXT NOT NULL,
            recommendation_id INTEGER,
            topic_group TEXT NOT NULL DEFAULT '',
            story_key TEXT NOT NULL DEFAULT '',
            observation_key TEXT NOT NULL DEFAULT '',
            observation TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            latest_evidence TEXT NOT NULL DEFAULT '',
            original_snapshot TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    raw.execute(
        """
        INSERT INTO tracked_observations (
            tracking_id, request_id, item_key, observation_key, observation
        ) VALUES ('legacy-tracker', 'legacy-request', 'web:legacy', 'legacy-key', '旧观察')
        """
    )
    raw.commit()
    raw.close()

    db = Database(path)
    db.initialize()
    table_names = {
        str(row[0])
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert {
        "events",
        "news_events",
        "news_event_memberships",
        "news_event_changes",
        "news_event_aliases",
        "news_event_change_aliases",
        "news_event_lineage_claims",
        "news_event_backfill_state",
    } <= table_names
    tracked_columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(tracked_observations)").fetchall()
    }
    assert "event_id" in tracked_columns
    change_columns = {
        str(row["name"])
        for row in db.conn.execute("PRAGMA table_info(news_event_changes)").fetchall()
    }
    assert {"role", "objectivity_status", "conflict_status", "effective_at"} <= (change_columns)
    db.conn.execute("INSERT INTO events (event_type, title) VALUES ('view', '保留行为日志')")
    db.conn.commit()
    db.close()

    reopened = Database(path)
    reopened.initialize()
    try:
        assert reopened.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        migration_states = {
            str(row["contract_version"]): bool(int(row["completed"] or 0))
            for row in reopened.conn.execute(
                "SELECT contract_version, completed FROM news_event_backfill_state"
            ).fetchall()
        }
        assert migration_states["news-event-fact-gate-v4"] is True
        assert "news-event-v1" in migration_states
        assert "news-event-origin-v2" in migration_states
    finally:
        reopened.close()


def test_existing_supersede_pointer_backfills_lineage_claim_ledger(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lineage-ledger-upgrade.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="已有替代关系的旧库",
        event_key="lineage-ledger-upgrade",
    )
    predecessor = _objective_change(
        item_key="web:ledger-upgrade-predecessor",
        evidence_fingerprint="ledger-upgrade-predecessor-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=predecessor["detected_at"],
        changes=[predecessor],
    )
    predecessor_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    successor = _objective_change(
        item_key="web:ledger-upgrade-successor",
        evidence_fingerprint="ledger-upgrade-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-21T09:00:00+00:00",
    )
    successor["supersedes_change_id"] = predecessor_id
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=successor["detected_at"],
        changes=[successor],
    )
    successor_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
        )
        if row["new_state"] == "阶段C"
    )
    database.close()

    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE news_event_lineage_claims")
    raw.commit()
    raw.close()

    upgraded = Database(path)
    upgraded.initialize()
    try:
        claims = upgraded.list_news_event_lineage_claims(successor_id)
        assert len(claims) == 1
        assert claims[0]["predecessor_change_id"] == predecessor_id
        assert claims[0]["status"] == "active"
        assert claims[0]["source_event_id"] == event["event_id"]
        assert claims[0]["source_change_id"] == successor_id
        claim_id = claims[0]["claim_id"]
    finally:
        upgraded.close()

    reopened = Database(path)
    reopened.initialize()
    try:
        claims = reopened.list_news_event_lineage_claims(successor_id)
        assert [claim["claim_id"] for claim in claims] == [claim_id]
        reopened.conn.execute("DELETE FROM news_event_lineage_claims")
        reopened.conn.execute(
            "UPDATE news_event_backfill_state SET completed = 0, completed_at = '' "
            "WHERE contract_version = 'news-event-lineage-ledger-v1'"
        )
        reopened.conn.commit()
    finally:
        reopened.close()

    repaired = Database(path)
    repaired.initialize()
    try:
        claims = repaired.list_news_event_lineage_claims(successor_id)
        assert [claim["claim_id"] for claim in claims] == [claim_id]
        state = repaired.get_news_event_backfill_state("news-event-lineage-ledger-v1")
        assert state["completed"] is True
    finally:
        repaired.close()


def test_pre_release_membership_schema_adds_lineage_before_origin_index(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pre-release-origin-schema.db"
    database = Database(path)
    database.initialize()
    database.close()
    raw = sqlite3.connect(path)
    raw.execute("DROP INDEX idx_news_event_memberships_event_origin")
    raw.execute("ALTER TABLE news_event_memberships DROP COLUMN origin_tracking_id")
    raw.commit()
    raw.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        columns = {
            str(row["name"])
            for row in migrated.conn.execute("PRAGMA table_info(news_event_memberships)").fetchall()
        }
        index = migrated.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_news_event_memberships_event_origin'"
        ).fetchone()
        assert "origin_tracking_id" in columns
        assert index is not None and "origin_tracking_id" in str(index["sql"])
    finally:
        migrated.close()


def test_pre_release_lineage_schema_adds_missing_event_owner(tmp_path: Path) -> None:
    path = tmp_path / "pre-release-lineage-schema.db"
    database = Database(path)
    database.initialize()
    database.close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE news_event_lineage_claims DROP COLUMN event_id")
    raw.commit()
    raw.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        columns = {
            str(row["name"])
            for row in migrated.conn.execute(
                "PRAGMA table_info(news_event_lineage_claims)"
            ).fetchall()
        }
        assert "event_id" in columns
    finally:
        migrated.close()


def test_fresh_database_concurrent_initialize_negotiates_wal_once(tmp_path: Path) -> None:
    paths = [tmp_path / f"concurrent-fresh-initialize-{index}.db" for index in range(6)]

    for path in paths:

        def initialize_facade(_index: int, db_path: Path = path) -> None:
            facade = Database(db_path)
            try:
                facade.initialize()
            finally:
                facade.close()

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(initialize_facade, range(6)))

    for path in paths:
        verified = Database(path)
        verified.initialize()
        try:
            assert verified.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert verified.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            verified.close()


def test_pre_release_change_schema_adds_identity_column_before_fast_path(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pre-release-change-identity.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="缺列迁移仍保留的事实",
        event_key="migration:missing-fingerprint-version",
    )
    change = _objective_change(
        item_key="web:missing-fingerprint-version",
        evidence_fingerprint="missing-fingerprint-version-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=change["detected_at"],
        changes=[change],
    )
    database.close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE news_event_changes DROP COLUMN fingerprint_version")
    raw.commit()
    raw.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        columns = {
            str(row["name"])
            for row in migrated.conn.execute("PRAGMA table_info(news_event_changes)").fetchall()
        }
        row = migrated.conn.execute(
            "SELECT fingerprint_version, is_objective_default FROM news_event_changes"
        ).fetchone()
        detail = migrated.get_news_event(event["event_id"])
        assert "fingerprint_version" in columns
        assert row is not None and row["fingerprint_version"] == "news-event-change-v3"
        assert bool(int(row["is_objective_default"] or 0)) is True
        assert detail is not None and detail["current_state"] == "阶段B"
    finally:
        migrated.close()


def test_fact_gate_migration_self_heals_partial_upgrade_and_revalidates_depth(
    tmp_path: Path,
) -> None:
    path = tmp_path / "fact-gate-migration.db"
    database = Database(path)
    database.initialize()
    strong_event, _ = database.create_news_event(
        canonical_title="可恢复的客观变化",
        event_key="migration:strong",
    )
    strong = _objective_change(
        item_key="web:migration-strong",
        evidence_fingerprint="migration-strong-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=strong_event["event_id"],
        check_status="changed",
        detected_at=strong["detected_at"],
        changes=[strong],
    )
    strong_row = database.list_news_event_changes(event_id=strong_event["event_id"])[0]
    database.mark_news_event_change_read(strong_row["change_id"])

    fake_event, _ = database.create_news_event(
        canonical_title="旧版伪正文",
        event_key="migration:fake-depth",
    )
    fake = _objective_change(
        item_key="web:migration-fake",
        evidence_fingerprint="migration-fake-r1",
        detected_at="2025-08-20T10:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=fake_event["event_id"],
        check_status="changed",
        detected_at=fake["detected_at"],
        changes=[fake],
    )
    fake_row = database.list_news_event_changes(event_id=fake_event["event_id"])[0]
    fake_membership = database.list_news_event_memberships(
        fake_event["event_id"], change_id=fake_row["change_id"]
    )[0]
    database.conn.execute(
        "UPDATE news_event_memberships SET content_scope = 'full_text', "
        "evidence_snapshot = ?, evidence_completeness = 'complete' "
        "WHERE membership_id = ?",
        (
            json.dumps(
                {
                    "title": fake["title"],
                    "body_text": f"{fake['title']} {fake['title']}",
                },
                ensure_ascii=False,
            ),
            fake_membership["membership_id"],
        ),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET is_objective_default = 0, "
        "evidence_status = 'source_supported', is_read = 1 WHERE change_id IN (?, ?)",
        (strong_row["change_id"], fake_row["change_id"]),
    )
    database.conn.execute(
        "DELETE FROM news_event_backfill_state WHERE contract_version = 'news-event-fact-gate-v4'"
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        recovered = migrated.get_news_event_change(strong_row["change_id"])
        rejected = migrated.get_news_event_change(fake_row["change_id"])
        rejected_membership = migrated.list_news_event_memberships(
            fake_event["event_id"], change_id=fake_row["change_id"]
        )[0]
        assert recovered is not None
        assert recovered["is_objective_default"] is True
        assert recovered["is_read"] is True
        assert rejected is not None
        assert rejected["is_objective_default"] is False
        assert rejected["evidence_status"] == "provisional"
        assert rejected_membership["content_scope"] == "title_only"
        recovered_event = migrated.get_news_event(strong_event["event_id"])
        assert recovered_event is not None and recovered_event["current_state"] == "阶段B"
        version = recovered_event["state_version"]
    finally:
        migrated.close()

    repeated = Database(path)
    repeated.initialize()
    try:
        repeated_event = repeated.get_news_event(strong_event["event_id"])
        assert repeated_event is not None and repeated_event["state_version"] == version
    finally:
        repeated.close()


def test_fact_gate_downgrade_revokes_stale_lineage_authority(tmp_path: Path) -> None:
    path = tmp_path / "fact-gate-lineage-downgrade.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="伪正文后继不能抹掉前态",
        event_key="migration:fact-gate-lineage-downgrade",
    )
    predecessor = _objective_change(
        item_key="web:fact-gate-lineage-predecessor",
        evidence_fingerprint="fact-gate-lineage-predecessor-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-06-01",
        detected_at="2025-06-01T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=predecessor["detected_at"],
        changes=[predecessor],
    )
    predecessor_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    successor = _objective_change(
        item_key="web:fact-gate-lineage-successor",
        evidence_fingerprint="fact-gate-lineage-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-06-02",
        detected_at="2025-06-02T09:00:00+00:00",
    )
    successor["supersedes_change_id"] = predecessor_id
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=successor["detected_at"],
        changes=[successor],
    )
    successor_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            lifecycle="all",
        )
        if row["new_state"] == "阶段C"
    )
    membership = database.list_news_event_memberships(
        event["event_id"],
        change_id=successor_id,
    )[0]
    database.conn.execute(
        "UPDATE news_event_memberships SET content_scope = 'full_text', "
        "evidence_snapshot = ?, evidence_completeness = 'complete' "
        "WHERE membership_id = ?",
        (
            json.dumps(
                {
                    "title": successor["title"],
                    "body_text": f"{successor['title']} {successor['title']}",
                },
                ensure_ascii=False,
            ),
            membership["membership_id"],
        ),
    )
    database.conn.execute(
        "DELETE FROM news_event_backfill_state WHERE contract_version = 'news-event-fact-gate-v4'"
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        downgraded = migrated.get_news_event_change(successor_id)
        restored = migrated.get_news_event_change(predecessor_id)
        claims = migrated.list_news_event_lineage_claims(successor_id)
        detail = migrated.get_news_event(event["event_id"])
        assert downgraded is not None
        assert restored is not None
        assert detail is not None
        assert downgraded["evidence_status"] == "provisional"
        assert downgraded["is_objective_default"] is False
        assert not str(downgraded["supersedes_change_id"] or "")
        assert restored["lifecycle"] == "active"
        assert restored["is_objective_default"] is True
        assert {claim["status"] for claim in claims} == {"rejected"}
        assert detail["current_state"] == "阶段B"
        assert detail["conflict_count"] == 0
    finally:
        migrated.close()


def test_fact_gate_migration_normalizes_legacy_conflict_invariant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-conflict-invariant.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="旧争议字段不一致",
        event_key="migration:conflict-invariant",
    )
    change = _objective_change(
        item_key="web:migration-conflict",
        evidence_fingerprint="migration-conflict-r1",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=change["detected_at"],
        changes=[change],
    )
    row = database.list_news_event_changes(event_id=event["event_id"])[0]
    database.conn.execute(
        "UPDATE news_event_changes SET objectivity_status = 'disputed', "
        "conflict_status = 'none', evidence_status = 'source_supported', "
        "is_objective_default = 0, is_read = 1 WHERE change_id = ?",
        (row["change_id"],),
    )
    database.conn.execute(
        "DELETE FROM news_event_backfill_state WHERE contract_version = 'news-event-fact-gate-v4'"
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        repaired = migrated.get_news_event_change(row["change_id"])
        assert repaired is not None
        assert repaired["objectivity_status"] == "disputed"
        assert repaired["conflict_status"] == "contested"
        assert repaired["evidence_status"] == "contested"
        assert repaired["is_read"] is False
        conflicts = migrated.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            conflicts_only=True,
        )
        assert [item["change_id"] for item in conflicts] == [row["change_id"]]
        detail = migrated.get_news_event(event["event_id"])
        assert detail is not None and detail["current_state"] == ""
    finally:
        migrated.close()


def test_change_identity_migration_folds_pre_release_type_duplicates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "change-identity-migration.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="旧版重复变化",
        event_key="migration:change-identity",
    )
    change = _objective_change(
        item_key="web:migration-progress",
        evidence_fingerprint="migration-progress-r1",
        change_type="progress",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=change["detected_at"],
        changes=[change],
    )
    canonical = database.conn.execute(
        "SELECT * FROM news_event_changes WHERE event_id = ?",
        (event["event_id"],),
    ).fetchone()
    assert canonical is not None
    canonical_id = str(canonical["change_id"])
    database.conn.execute(
        "UPDATE news_event_changes SET change_fingerprint = ?, "
        "fingerprint_version = 'news-event-change-v2', role = 'analysis', "
        "objectivity_status = 'unconfirmed', is_objective_default = 0, "
        "is_read = 1 WHERE change_id = ?",
        (f"legacy-progress-{uuid4()}", canonical_id),
    )
    database.conn.execute(
        "UPDATE news_events SET current_state = '', last_change_at = '' WHERE event_id = ?",
        (event["event_id"],),
    )
    duplicate = dict(canonical)
    duplicate_id = str(uuid4())
    duplicate.update(
        {
            "change_id": duplicate_id,
            "change_fingerprint": f"legacy-confirmation-{uuid4()}",
            "fingerprint_version": "news-event-change-v2",
            "change_type": "confirmation",
            "origin_evidence_id": "",
        }
    )
    change_columns = [
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(news_event_changes)").fetchall()
    ]
    database.conn.execute(
        f"INSERT INTO news_event_changes ({', '.join(change_columns)}) "
        f"VALUES ({', '.join('?' for _ in change_columns)})",
        tuple(duplicate[column] for column in change_columns),
    )
    original_membership = database.conn.execute(
        "SELECT * FROM news_event_memberships WHERE change_id = ?",
        (canonical_id,),
    ).fetchone()
    assert original_membership is not None
    duplicate_membership = dict(original_membership)
    duplicate_membership.update(
        {
            "membership_id": str(uuid4()),
            "change_id": duplicate_id,
            "evidence_item_key": "web:migration-confirmation",
            "evidence_fingerprint": "migration-confirmation-r1",
            "media": "迁移媒体B",
        }
    )
    membership_columns = [
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(news_event_memberships)").fetchall()
    ]
    database.conn.execute(
        f"INSERT INTO news_event_memberships ({', '.join(membership_columns)}) "
        f"VALUES ({', '.join('?' for _ in membership_columns)})",
        tuple(duplicate_membership[column] for column in membership_columns),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET supersedes_change_id = ? WHERE change_id = ?",
        (duplicate_id, canonical_id),
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        changes = migrated.list_news_event_changes(event_id=event["event_id"], objective_only=False)
        assert len(changes) == 1
        assert changes[0]["fingerprint_version"] == "news-event-change-v3"
        assert changes[0]["is_objective_default"] is True
        resolved_alias = migrated.get_news_event_change(duplicate_id)
        assert resolved_alias is not None
        assert resolved_alias["change_id"] == changes[0]["change_id"]
        assert not str(changes[0]["supersedes_change_id"] or "")
        memberships = migrated.list_news_event_memberships(
            event["event_id"], change_id=changes[0]["change_id"]
        )
        assert len(memberships) == 2
        detail = migrated.get_news_event(event["event_id"])
        assert detail is not None and detail["current_state"] == "正式生效"
    finally:
        migrated.close()


def test_change_identity_migration_folds_controlled_state_paraphrases(
    tmp_path: Path,
) -> None:
    path = tmp_path / "change-identity-semantic-migration.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="政策执行状态",
        event_key="migration:controlled-state-paraphrases",
    )
    change = _objective_change(
        item_key="web:migration-policy-a",
        evidence_fingerprint="migration-policy-a-r1",
        previous_state="政策尚未落地",
        new_state="政策已落地",
        effective_at="2025-08-20",
        detected_at="2025-08-20T09:00:00+00:00",
        media="媒体A",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=change["detected_at"],
        changes=[change],
    )
    canonical = database.conn.execute(
        "SELECT * FROM news_event_changes WHERE event_id = ?",
        (event["event_id"],),
    ).fetchone()
    assert canonical is not None
    canonical_id = str(canonical["change_id"])
    database.conn.execute(
        "UPDATE news_event_changes SET change_fingerprint = ?, "
        "fingerprint_version = 'news-event-change-v2', created_at = ? "
        "WHERE change_id = ?",
        (f"legacy-policy-a-{uuid4()}", "2025-08-20 00:00:01", canonical_id),
    )
    duplicate_id = str(uuid4())
    duplicate = dict(canonical)
    duplicate.update(
        {
            "change_id": duplicate_id,
            "change_fingerprint": f"legacy-policy-b-{uuid4()}",
            "fingerprint_version": "news-event-change-v2",
            "change_type": "confirmation",
            "previous_state": "政策未实施",
            "new_state": "政策开始执行",
            "origin_evidence_id": "",
            "created_at": "2025-08-20 00:00:02",
        }
    )
    change_columns = [
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(news_event_changes)").fetchall()
    ]
    database.conn.execute(
        f"INSERT INTO news_event_changes ({', '.join(change_columns)}) "
        f"VALUES ({', '.join('?' for _ in change_columns)})",
        tuple(duplicate[column] for column in change_columns),
    )
    original_membership = database.conn.execute(
        "SELECT * FROM news_event_memberships WHERE change_id = ?",
        (canonical_id,),
    ).fetchone()
    assert original_membership is not None
    duplicate_membership = dict(original_membership)
    duplicate_membership.update(
        {
            "membership_id": str(uuid4()),
            "change_id": duplicate_id,
            "evidence_item_key": "web:migration-policy-b",
            "evidence_fingerprint": "migration-policy-b-r1",
            "media": "媒体B",
        }
    )
    membership_columns = [
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(news_event_memberships)").fetchall()
    ]
    database.conn.execute(
        f"INSERT INTO news_event_memberships ({', '.join(membership_columns)}) "
        f"VALUES ({', '.join('?' for _ in membership_columns)})",
        tuple(duplicate_membership[column] for column in membership_columns),
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        changes = migrated.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
        )
        assert len(changes) == 1
        assert changes[0]["change_id"] == canonical_id
        assert changes[0]["fingerprint_version"] == "news-event-change-v3"
        assert changes[0]["evidence_count"] == 2
        resolved_alias = migrated.get_news_event_change(duplicate_id)
        assert resolved_alias is not None
        assert resolved_alias["change_id"] == canonical_id
        detail = migrated.get_news_event(event["event_id"])
        assert detail is not None
        assert detail["current_state"] == "政策已落地"
    finally:
        migrated.close()


def test_change_identity_migration_resolves_cross_group_predecessor_alias(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-group-lineage-migration.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="跨重复组前置关系",
        event_key="migration:cross-group-lineage",
    )
    first = _objective_change(
        item_key="web:cross-group-a-b",
        evidence_fingerprint="cross-group-a-b-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-01-01",
        detected_at="2025-01-01T09:00:00+00:00",
    )
    second = _objective_change(
        item_key="web:cross-group-b-c",
        evidence_fingerprint="cross-group-b-c-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-01-02",
        detected_at="2025-01-02T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first["detected_at"],
        changes=[first],
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=second["detected_at"],
        changes=[second],
    )
    rows = database.conn.execute(
        "SELECT * FROM news_event_changes WHERE event_id = ?",
        (event["event_id"],),
    ).fetchall()
    by_state = {str(row["new_state"]): row for row in rows}
    c1 = by_state["阶段B"]
    c3 = by_state["阶段C"]
    c1_id = str(c1["change_id"])
    c3_id = str(c3["change_id"])
    c2_id = str(uuid4())
    c4_id = str(uuid4())
    change_columns = [
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(news_event_changes)").fetchall()
    ]

    def insert_duplicate(
        original: sqlite3.Row,
        *,
        duplicate_id: str,
        fingerprint: str,
        supersedes_change_id: str = "",
    ) -> None:
        duplicate = dict(original)
        duplicate.update(
            {
                "change_id": duplicate_id,
                "change_fingerprint": fingerprint,
                "fingerprint_version": "news-event-change-v2",
                "change_type": "confirmation",
                "origin_evidence_id": "",
                "supersedes_change_id": supersedes_change_id or None,
            }
        )
        database.conn.execute(
            f"INSERT INTO news_event_changes ({', '.join(change_columns)}) "
            f"VALUES ({', '.join('?' for _ in change_columns)})",
            tuple(duplicate[column] for column in change_columns),
        )

    database.conn.execute(
        "UPDATE news_event_changes SET change_fingerprint = ?, "
        "fingerprint_version = 'news-event-change-v2', created_at = ? "
        "WHERE change_id = ?",
        ("legacy-cross-c1", "2025-01-01 00:00:01", c1_id),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET change_fingerprint = ?, "
        "fingerprint_version = 'news-event-change-v2', created_at = ? "
        "WHERE change_id = ?",
        ("legacy-cross-c3", "2025-01-01 00:00:03", c3_id),
    )
    insert_duplicate(
        c1,
        duplicate_id=c2_id,
        fingerprint="legacy-cross-c2",
    )
    insert_duplicate(
        c3,
        duplicate_id=c4_id,
        fingerprint="legacy-cross-c4",
        supersedes_change_id=c2_id,
    )
    database.conn.execute(
        "UPDATE news_event_changes SET created_at = ? WHERE change_id = ?",
        ("2025-01-01 00:00:02", c2_id),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET created_at = ? WHERE change_id = ?",
        ("2025-01-01 00:00:04", c4_id),
    )
    database.conn.execute(
        """
        INSERT INTO news_event_lineage_claims (
            claim_id, event_id, successor_change_id, predecessor_change_id,
            supersedes_lifecycle, source_event_id, source_change_id, status
        ) VALUES (?, ?, ?, ?, 'superseded', ?, ?, 'active')
        """,
        (str(uuid4()), event["event_id"], c4_id, c2_id, event["event_id"], c4_id),
    )
    database.conn.execute(
        "DELETE FROM news_event_backfill_state WHERE contract_version = 'news-event-fact-gate-v4'"
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        changes = migrated.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            lifecycle="all",
        )
        assert {row["change_id"] for row in changes} == {c1_id, c3_id}
        migrated_successor = migrated.get_news_event_change(c3_id)
        assert migrated_successor is not None
        assert migrated_successor["supersedes_change_id"] == c1_id
        assert migrated.get_news_event_change(c2_id)["change_id"] == c1_id  # type: ignore[index]
        assert migrated.get_news_event_change(c4_id)["change_id"] == c3_id  # type: ignore[index]
        claims = migrated.list_news_event_lineage_claims(c3_id)
        assert claims
        assert {claim["successor_change_id"] for claim in claims} == {c3_id}
        assert {claim["predecessor_change_id"] for claim in claims} == {c1_id}
        assert {claim["source_change_id"] for claim in claims} == {c3_id, c4_id}
    finally:
        migrated.close()


def test_change_identity_migration_releases_collapsed_lineage_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "collapsed-lineage-conflict.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="等价前因折叠",
        event_key="migration:collapsed-lineage-conflict",
    )
    predecessor = _objective_change(
        item_key="web:collapsed-predecessor-a",
        evidence_fingerprint="collapsed-predecessor-a-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-02-01",
        detected_at="2025-02-01T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=predecessor["detected_at"],
        changes=[predecessor],
    )
    predecessor_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    successor = _objective_change(
        item_key="web:collapsed-successor",
        evidence_fingerprint="collapsed-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-02-02",
        detected_at="2025-02-02T09:00:00+00:00",
    )
    successor["supersedes_change_id"] = predecessor_id
    successor["supersedes_lifecycle"] = "superseded"
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=successor["detected_at"],
        changes=[successor],
    )
    successor_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            lifecycle="all",
        )
        if row["new_state"] == "阶段C"
    )
    original_predecessor = database.conn.execute(
        "SELECT * FROM news_event_changes WHERE change_id = ?",
        (predecessor_id,),
    ).fetchone()
    assert original_predecessor is not None
    predecessor_alias_id = str(uuid4())
    duplicate = dict(original_predecessor)
    duplicate.update(
        {
            "change_id": predecessor_alias_id,
            "change_fingerprint": "legacy-collapsed-predecessor-b",
            "fingerprint_version": "news-event-change-v2",
            "change_type": "confirmation",
            "origin_evidence_id": "",
            "lifecycle": "active",
            "created_at": "2025-02-01 00:00:02",
        }
    )
    change_columns = [
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(news_event_changes)").fetchall()
    ]
    database.conn.execute(
        f"INSERT INTO news_event_changes ({', '.join(change_columns)}) "
        f"VALUES ({', '.join('?' for _ in change_columns)})",
        tuple(duplicate[column] for column in change_columns),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET change_fingerprint = ?, "
        "fingerprint_version = 'news-event-change-v2', created_at = ? "
        "WHERE change_id = ?",
        ("legacy-collapsed-predecessor-a", "2025-02-01 00:00:01", predecessor_id),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET supersedes_change_id = NULL, "
        "objectivity_status = 'disputed', conflict_status = 'contested', "
        "evidence_status = 'contested', is_objective_default = 0 "
        "WHERE change_id = ?",
        (successor_id,),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET lifecycle = 'active' WHERE change_id = ?",
        (predecessor_id,),
    )
    database.conn.execute(
        "UPDATE news_event_lineage_claims SET status = 'contested' WHERE successor_change_id = ?",
        (successor_id,),
    )
    database.conn.execute(
        """
        INSERT INTO news_event_lineage_claims (
            claim_id, event_id, successor_change_id, predecessor_change_id,
            supersedes_lifecycle, source_event_id, source_change_id, status
        ) VALUES (?, ?, ?, ?, 'superseded', ?, ?, 'contested')
        """,
        (
            str(uuid4()),
            event["event_id"],
            successor_id,
            predecessor_alias_id,
            event["event_id"],
            str(uuid4()),
        ),
    )
    database.conn.execute(
        "DELETE FROM news_event_backfill_state WHERE contract_version = 'news-event-fact-gate-v4'"
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        canonical_successor = migrated.get_news_event_change(successor_id)
        canonical_predecessor = migrated.get_news_event_change(predecessor_id)
        claims = migrated.list_news_event_lineage_claims(successor_id)
        detail = migrated.get_news_event(event["event_id"])
        assert canonical_successor is not None
        assert canonical_predecessor is not None
        assert detail is not None
        assert migrated.get_news_event_change(predecessor_alias_id)["change_id"] == predecessor_id  # type: ignore[index]
        assert canonical_successor["conflict_status"] == "none"
        assert canonical_successor["supersedes_change_id"] == predecessor_id
        assert canonical_predecessor["lifecycle"] == "superseded"
        assert {claim["predecessor_change_id"] for claim in claims} == {predecessor_id}
        assert {claim["status"] for claim in claims} == {"active"}
        assert detail["conflict_count"] == 0
        assert detail["current_state"] == "阶段C"
    finally:
        migrated.close()


def test_change_identity_migration_quarantines_folded_successor_lineage_conflict(
    tmp_path: Path,
) -> None:
    path = tmp_path / "folded-successor-lineage-conflict.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="等价后继的不同前因",
        event_key="migration:folded-successor-lineage-conflict",
    )
    for prefix, previous_state, detected_at in (
        ("first", "阶段A", "2025-03-01T09:00:00+00:00"),
        ("second", "阶段X", "2025-03-01T10:00:00+00:00"),
    ):
        change = _objective_change(
            item_key=f"web:folded-{prefix}-predecessor",
            evidence_fingerprint=f"folded-{prefix}-predecessor-r1",
            previous_state=previous_state,
            new_state="阶段B",
            effective_at="2025-03-01",
            detected_at=detected_at,
        )
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )
    predecessors = {
        row["previous_state"]: row["change_id"]
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
        )
    }
    first_predecessor_id = predecessors["阶段A"]
    second_predecessor_id = predecessors["阶段X"]
    successor = _objective_change(
        item_key="web:folded-successor-one",
        evidence_fingerprint="folded-successor-one-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-03-02",
        detected_at="2025-03-02T09:00:00+00:00",
    )
    successor["supersedes_change_id"] = first_predecessor_id
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=successor["detected_at"],
        changes=[successor],
    )
    canonical_successor = next(
        row
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            lifecycle="all",
        )
        if row["new_state"] == "阶段C"
    )
    canonical_successor_id = canonical_successor["change_id"]
    successor_alias_id = str(uuid4())
    successor_duplicate = dict(
        database.conn.execute(
            "SELECT * FROM news_event_changes WHERE change_id = ?",
            (canonical_successor_id,),
        ).fetchone()
    )
    successor_duplicate.update(
        {
            "change_id": successor_alias_id,
            "change_fingerprint": "legacy-folded-successor-two",
            "fingerprint_version": "news-event-change-v2",
            "change_type": "confirmation",
            "origin_evidence_id": "",
            "supersedes_change_id": second_predecessor_id,
            "created_at": "2025-03-02 00:00:02",
        }
    )
    change_columns = [
        str(row["name"])
        for row in database.conn.execute("PRAGMA table_info(news_event_changes)").fetchall()
    ]
    database.conn.execute(
        f"INSERT INTO news_event_changes ({', '.join(change_columns)}) "
        f"VALUES ({', '.join('?' for _ in change_columns)})",
        tuple(successor_duplicate[column] for column in change_columns),
    )
    database.conn.execute(
        "UPDATE news_event_changes SET change_fingerprint = ?, "
        "fingerprint_version = 'news-event-change-v2', created_at = ? "
        "WHERE change_id = ?",
        ("legacy-folded-successor-one", "2025-03-02 00:00:01", canonical_successor_id),
    )
    database.conn.execute(
        "DELETE FROM news_event_backfill_state WHERE contract_version = 'news-event-fact-gate-v4'"
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        folded_successor = migrated.get_news_event_change(canonical_successor_id)
        claims = migrated.list_news_event_lineage_claims(canonical_successor_id)
        detail = migrated.get_news_event(event["event_id"])
        assert folded_successor is not None
        assert detail is not None
        assert (
            migrated.get_news_event_change(successor_alias_id)["change_id"]
            == canonical_successor_id
        )  # type: ignore[index]
        assert folded_successor["conflict_status"] == "contested"
        assert not str(folded_successor["supersedes_change_id"] or "")
        assert {claim["predecessor_change_id"] for claim in claims} == {
            first_predecessor_id,
            second_predecessor_id,
        }
        assert {claim["status"] for claim in claims} == {"contested"}
        for predecessor_id in (first_predecessor_id, second_predecessor_id):
            predecessor = migrated.get_news_event_change(predecessor_id)
            assert predecessor is not None
            assert predecessor["lifecycle"] == "active"
            assert predecessor["conflict_status"] == "none"
        assert detail["conflict_count"] == 1
    finally:
        migrated.close()


def test_initialize_quarantines_same_predecessor_different_lifecycle_claims(
    tmp_path: Path,
) -> None:
    path = tmp_path / "same-predecessor-lifecycle-conflict.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="同一前因的处理方式冲突",
        event_key="migration:same-predecessor-lifecycle-conflict",
    )
    predecessor = _objective_change(
        item_key="web:same-lifecycle-predecessor",
        evidence_fingerprint="same-lifecycle-predecessor-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-04-01",
        detected_at="2025-04-01T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=predecessor["detected_at"],
        changes=[predecessor],
    )
    predecessor_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    successor = _objective_change(
        item_key="web:same-lifecycle-successor",
        evidence_fingerprint="same-lifecycle-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-04-02",
        detected_at="2025-04-02T09:00:00+00:00",
    )
    successor["supersedes_change_id"] = predecessor_id
    successor["supersedes_lifecycle"] = "superseded"
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=successor["detected_at"],
        changes=[successor],
    )
    successor_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            lifecycle="all",
        )
        if row["new_state"] == "阶段C"
    )
    database.conn.execute(
        """
        INSERT INTO news_event_lineage_claims (
            claim_id, event_id, successor_change_id, predecessor_change_id,
            supersedes_lifecycle, source_event_id, source_change_id, status
        ) VALUES (?, ?, ?, ?, 'invalidated', ?, ?, 'active')
        """,
        (
            str(uuid4()),
            event["event_id"],
            successor_id,
            predecessor_id,
            event["event_id"],
            str(uuid4()),
        ),
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        contested = migrated.get_news_event_change(successor_id)
        restored = migrated.get_news_event_change(predecessor_id)
        claims = migrated.list_news_event_lineage_claims(successor_id)
        detail = migrated.get_news_event(event["event_id"])
        assert contested is not None
        assert restored is not None
        assert detail is not None
        assert contested["conflict_status"] == "contested"
        assert not str(contested["supersedes_change_id"] or "")
        assert restored["lifecycle"] == "active"
        assert {(claim["supersedes_lifecycle"], claim["status"]) for claim in claims} == {
            ("superseded", "contested"),
            ("invalidated", "contested"),
        }
        assert detail["conflict_count"] == 1
    finally:
        migrated.close()


def test_missing_lineage_endpoint_is_quarantined_not_hidden(tmp_path: Path) -> None:
    path = tmp_path / "missing-lineage-endpoint.db"
    database = Database(path)
    database.initialize()
    event, _ = database.create_news_event(
        canonical_title="缺失前因仍需显式冲突",
        event_key="migration:missing-lineage-endpoint",
    )
    predecessor = _objective_change(
        item_key="web:missing-endpoint-predecessor",
        evidence_fingerprint="missing-endpoint-predecessor-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-05-01",
        detected_at="2025-05-01T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=predecessor["detected_at"],
        changes=[predecessor],
    )
    predecessor_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    successor = _objective_change(
        item_key="web:missing-endpoint-successor",
        evidence_fingerprint="missing-endpoint-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-05-02",
        detected_at="2025-05-02T09:00:00+00:00",
    )
    successor["supersedes_change_id"] = predecessor_id
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=successor["detected_at"],
        changes=[successor],
    )
    successor_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            lifecycle="all",
        )
        if row["new_state"] == "阶段C"
    )
    missing_predecessor_id = str(uuid4())
    database.conn.execute(
        """
        INSERT INTO news_event_lineage_claims (
            claim_id, event_id, successor_change_id, predecessor_change_id,
            supersedes_lifecycle, source_event_id, source_change_id, status
        ) VALUES (?, ?, ?, ?, 'superseded', ?, ?, 'active')
        """,
        (
            str(uuid4()),
            event["event_id"],
            successor_id,
            missing_predecessor_id,
            event["event_id"],
            str(uuid4()),
        ),
    )
    database.conn.commit()
    database.close()

    migrated = Database(path)
    migrated.initialize()
    try:
        contested = migrated.get_news_event_change(successor_id)
        restored = migrated.get_news_event_change(predecessor_id)
        claims = migrated.list_news_event_lineage_claims(successor_id)
        detail = migrated.get_news_event(event["event_id"])
        assert contested is not None
        assert restored is not None
        assert detail is not None
        assert contested["conflict_status"] == "contested"
        assert not str(contested["supersedes_change_id"] or "")
        assert restored["lifecycle"] == "active"
        assert {claim["predecessor_change_id"] for claim in claims} == {
            predecessor_id,
            missing_predecessor_id,
        }
        assert {claim["status"] for claim in claims} == {"contested"}
        assert detail["conflict_count"] == 1
    finally:
        migrated.close()


def test_trackers_share_a_specific_story_transition_event(database: Database) -> None:
    first = _create_tracker(
        database,
        request_id="track-first",
        item_key="web:first",
    )
    second = _create_tracker(
        database,
        request_id="track-second",
        item_key="web:second",
    )
    different_transition = _create_tracker(
        database,
        request_id="track-third",
        item_key="web:third",
        observation="政策执行范围是否扩大",
    )
    normalized_story = _create_tracker(
        database,
        request_id="track-normalized-story",
        item_key="web:normalized-story",
        story_key="  STORY-POLICY  ",
    )
    different_story = _create_tracker(
        database,
        request_id="track-different-story",
        item_key="web:different-story",
        story_key="story-other-policy",
    )
    no_story_first = _create_tracker(
        database,
        request_id="track-no-story-first",
        item_key="web:no-story-first",
        story_key="",
    )
    no_story_second = _create_tracker(
        database,
        request_id="track-no-story-second",
        item_key="web:no-story-second",
        story_key="",
    )

    assert first["event_id"] == second["event_id"]
    assert different_transition["event_id"] == first["event_id"]
    assert normalized_story["event_id"] == first["event_id"]
    assert different_story["event_id"] != first["event_id"]
    assert no_story_first["event_id"] != no_story_second["event_id"]
    event = database.get_news_event(first["event_id"])
    assert event is not None
    assert event["event_key"].startswith("tracking-story-v2:")
    assert event["current_state"] == ""
    assert event["state_version"] == 0
    origins = [
        item
        for item in database.list_news_event_memberships(first["event_id"])
        if item["relation_kind"] == "origin"
    ]
    assert {item["origin_tracking_id"] for item in origins} == {
        first["tracking_id"],
        second["tracking_id"],
        different_transition["tracking_id"],
        normalized_story["tracking_id"],
    }


def test_same_origin_article_keeps_one_lineage_membership_per_tracker(
    database: Database,
) -> None:
    first = _create_tracker(
        database,
        request_id="shared-origin-first",
        item_key="web:shared-origin",
        observation="政策是否已经发布",
    )
    second = _create_tracker(
        database,
        request_id="shared-origin-second",
        item_key="web:shared-origin",
        observation="政策是否已经开始执行",
    )

    assert first["event_id"] == second["event_id"]
    origins = [
        item
        for item in database.list_news_event_memberships(first["event_id"])
        if item["relation_kind"] == "origin"
    ]
    assert {item["origin_tracking_id"] for item in origins} == {
        first["tracking_id"],
        second["tracking_id"],
    }


def test_origin_repair_restores_each_tracker_after_legacy_index_collapse(
    database: Database,
) -> None:
    first = _create_tracker(
        database,
        request_id="repair-origin-first",
        item_key="web:repair-shared-origin",
        observation="政策是否已经发布",
    )
    second = _create_tracker(
        database,
        request_id="repair-origin-second",
        item_key="web:repair-shared-origin",
        observation="政策是否已经开始执行",
    )
    event_id = first["event_id"]
    origins = [
        item
        for item in database.list_news_event_memberships(event_id)
        if item["relation_kind"] == "origin"
    ]
    first_origin = next(
        item for item in origins if item["origin_tracking_id"] == first["tracking_id"]
    )
    second_origin = next(
        item for item in origins if item["origin_tracking_id"] == second["tracking_id"]
    )
    database.conn.execute(
        "UPDATE news_event_memberships SET origin_tracking_id = '' WHERE membership_id = ?",
        (first_origin["membership_id"],),
    )
    database.conn.execute(
        "DELETE FROM news_event_memberships WHERE membership_id = ?",
        (second_origin["membership_id"],),
    )
    database.conn.execute(
        "UPDATE news_event_backfill_state SET completed = 1 "
        "WHERE contract_version = 'news-event-v1'"
    )
    database.conn.execute(
        """
        INSERT INTO news_event_backfill_state (
            contract_version, last_tracking_rowid, completed, completed_at
        ) VALUES ('news-event-origin-v2', 0, 0, '')
        ON CONFLICT(contract_version) DO UPDATE SET
            last_tracking_rowid = 0, completed = 0, completed_at = ''
        """
    )
    database.conn.commit()

    first_batch = database.repair_news_event_origin_memberships(batch_size=1)
    second_batch = database.repair_news_event_origin_memberships(batch_size=1)
    repeated = database.repair_news_event_origin_memberships(batch_size=1)

    assert first_batch["state"]["completed"] is False
    assert first_batch["claimed_legacy_memberships"] == 1
    assert second_batch["state"]["completed"] is True
    assert repeated["processed_trackers"] == 0
    repaired = [
        item
        for item in database.list_news_event_memberships(event_id)
        if item["relation_kind"] == "origin"
    ]
    assert len(repaired) == 2
    assert {item["origin_tracking_id"] for item in repaired} == {
        first["tracking_id"],
        second["tracking_id"],
    }
    completeness = {item["origin_tracking_id"]: item["evidence_completeness"] for item in repaired}
    assert completeness[first["tracking_id"]] == "complete"
    assert completeness[second["tracking_id"]] == "partial_legacy"
    assert database.get_news_event_backfill_state("news-event-v1")["completed"] is True


def test_projection_keeps_related_evidence_and_deduplicates_semantic_change(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="某项政策落地",
        event_key="policy:landed",
    )
    related = {
        "item_key": "web:related",
        "evidence_fingerprint": "related-r1",
        "title": "相关但没有带来新状态",
        "content_scope": "full_text",
    }
    related_result = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="unchanged",
        detected_at="2026-08-28T07:00:00+00:00",
        related_memberships=[related],
    )
    assert related_result == {
        "event_id": event["event_id"],
        "check_status": "unchanged",
        "created_change_count": 0,
        "created_default_change_count": 0,
        "created_membership_count": 1,
    }

    first_change = _objective_change(
        item_key="web:a",
        evidence_fingerprint="a-r1",
        media="媒体A",
    )
    first_result = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first_change["detected_at"],
        changes=[first_change],
    )
    assert first_result["created_change_count"] == 1
    assert first_result["created_default_change_count"] == 1
    change = database.list_news_event_changes(event_id=event["event_id"])[0]
    assert change["is_read"] is False

    second_source = _objective_change(
        item_key="web:b",
        evidence_fingerprint="b-r1",
        media="媒体B",
    )
    second_source.update(
        {
            "source_scope": "multi_source",
            "evidence_status": "cross_source_supported",
        }
    )
    duplicate_result = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=second_source["detected_at"],
        changes=[second_source],
    )
    assert duplicate_result["created_change_count"] == 0
    assert duplicate_result["created_default_change_count"] == 0

    revised_source = dict(second_source, evidence_fingerprint="b-r2")
    revision_result = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=second_source["detected_at"],
        changes=[revised_source],
    )
    assert revision_result["created_change_count"] == 0
    updated = database.get_news_event_change(change["change_id"], objective_only=True)
    assert updated is not None
    assert updated["source_scope"] == "multi_source"
    assert updated["evidence_status"] == "cross_source_supported"
    evidence = database.list_news_event_memberships(
        event["event_id"], change_id=change["change_id"]
    )
    assert {(item["evidence_item_key"], item["evidence_fingerprint"]) for item in evidence} == {
        ("web:a", "a-r1"),
        ("web:b", "b-r1"),
        ("web:b", "b-r2"),
    }
    assert database.news_event_change_summary(event_id=event["event_id"]) == {
        "total": 1,
        "unread_count": 1,
    }
    marked, changed = database.mark_news_event_change_read(change["change_id"])
    assert marked is not None and marked["is_read"] is True and changed is True
    _, changed_again = database.mark_news_event_change_read(change["change_id"])
    assert changed_again is False


def test_paraphrased_sources_with_publication_fallback_share_one_change(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="政策执行状态",
        event_key="policy:paraphrased-publication-fallback",
    )
    first = _objective_change(
        item_key="web:paraphrase-source-a",
        evidence_fingerprint="paraphrase-source-a-r1",
        previous_state="政策尚未落地",
        new_state="政策已落地",
        effective_at="2025-08-20T08:02:00+00:00",
        detected_at="2025-08-20T08:10:00+00:00",
        media="媒体A",
    )
    first["published_at"] = "2025-08-20T08:02:00+00:00"
    first["effective_basis"] = "published_at_fallback"
    second = _objective_change(
        item_key="web:paraphrase-source-b",
        evidence_fingerprint="paraphrase-source-b-r1",
        previous_state="政策未实施",
        new_state="政策开始执行",
        change_type="confirmation",
        effective_at="2025-08-20T14:35:00+00:00",
        detected_at="2025-08-20T14:40:00+00:00",
        media="媒体B",
    )
    second["published_at"] = "2025-08-20T14:35:00+00:00"
    second["effective_basis"] = "published_at_fallback"

    first_receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first["detected_at"],
        changes=[first],
    )
    second_receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=second["detected_at"],
        changes=[second],
    )

    changes = database.list_news_event_changes(event_id=event["event_id"])
    assert first_receipt["created_change_count"] == 1
    assert second_receipt["created_change_count"] == 0
    assert len(changes) == 1
    assert changes[0]["effective_at"] == "2025-08-20"
    assert changes[0]["effective_precision"] == "date"
    assert changes[0]["evidence_count"] == 2
    assert changes[0]["evidence_status"] == "cross_source_supported"
    assert database.news_event_change_summary(event_id=event["event_id"]) == {
        "total": 1,
        "unread_count": 1,
    }


def test_mixed_polarity_result_cannot_strengthen_opposite_pending_state(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="政策发布状态",
        event_key="policy:mixed-polarity-result",
    )
    published = _objective_change(
        item_key="web:mixed-polarity-published",
        evidence_fingerprint="mixed-polarity-published-r1",
        previous_state="政策状态待定",
        new_state="待发布政策已发布",
        effective_at="2025-08-20",
        detected_at="2025-08-20T09:00:00+00:00",
        media="媒体A",
    )
    pending = _objective_change(
        item_key="web:mixed-polarity-pending",
        evidence_fingerprint="mixed-polarity-pending-r1",
        previous_state="政策状态待定",
        new_state="政策尚未发布",
        effective_at="2025-08-20",
        detected_at="2025-08-20T10:00:00+00:00",
        media="媒体B",
    )

    first_receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=published["detected_at"],
        changes=[published],
    )
    second_receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=pending["detected_at"],
        changes=[pending],
    )

    changes = database.list_news_event_changes(
        event_id=event["event_id"],
        objective_only=False,
    )
    assert first_receipt["created_change_count"] == 1
    assert second_receipt["created_change_count"] == 1
    assert len(changes) == 2
    assert {change["new_state"] for change in changes} == {
        "待发布政策已发布",
        "政策尚未发布",
    }
    assert {change["evidence_count"] for change in changes} == {1}
    assert {change["conflict_status"] for change in changes} == {"contested"}


@pytest.mark.parametrize(
    ("case_name", "first_state", "second_state"),
    [
        ("decimal", "存款准备金率已下调至7.5%", "存款准备金率已下调至75%"),
        ("currency", "价格已上涨至$100", "价格已上涨至¥100"),
        ("unicode-minus", "价格已下降至−1%", "价格已下降至1%"),
        ("unicode-dash-minus", "收益率已上升至–1%", "收益率已上升至1%"),
        ("spaced-dash-minus", "收益率已上升至– 1%", "收益率已上升至1%"),
        ("accounting-negative", "利润率已下降至（1%）", "利润率已下降至1%"),
    ],
)
def test_financial_fact_tokens_cannot_merge_into_false_corroboration(
    database: Database,
    case_name: str,
    first_state: str,
    second_state: str,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="金融指标变化",
        event_key=f"finance:fact-token:{case_name}",
    )
    first = _objective_change(
        item_key=f"web:financial-token-{case_name}-a",
        evidence_fingerprint=f"financial-token-{case_name}-a-r1",
        previous_state="指标状态待定",
        new_state=first_state,
        effective_at="2025-08-20",
        detected_at="2025-08-20T09:00:00+00:00",
        media="媒体A",
    )
    second = _objective_change(
        item_key=f"web:financial-token-{case_name}-b",
        evidence_fingerprint=f"financial-token-{case_name}-b-r1",
        previous_state="指标状态待定",
        new_state=second_state,
        effective_at="2025-08-20",
        detected_at="2025-08-20T10:00:00+00:00",
        media="媒体B",
    )

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first["detected_at"],
        changes=[first],
    )
    second_receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=second["detected_at"],
        changes=[second],
    )

    changes = database.list_news_event_changes(
        event_id=event["event_id"],
        objective_only=False,
    )
    assert second_receipt["created_change_count"] == 1
    assert len(changes) == 2
    assert {change["new_state"] for change in changes} == {first_state, second_state}
    assert {change["evidence_count"] for change in changes} == {1}
    assert {change["conflict_status"] for change in changes} == {"contested"}


def test_semantic_identity_does_not_merge_different_state_subjects(database: Database) -> None:
    event, _ = database.create_news_event(
        canonical_title="公司经营变化",
        event_key="company:distinct-state-subjects",
    )
    profit = _objective_change(
        item_key="web:profit-growth",
        evidence_fingerprint="profit-growth-r1",
        previous_state="公司利润未增长",
        new_state="公司利润增长",
        effective_at="2025-08-20",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    revenue = _objective_change(
        item_key="web:revenue-growth",
        evidence_fingerprint="revenue-growth-r1",
        previous_state="公司营收未增长",
        new_state="公司营收增长",
        effective_at="2025-08-20",
        detected_at="2025-08-20T10:00:00+00:00",
    )

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=profit["detected_at"],
        changes=[profit],
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=revenue["detected_at"],
        changes=[revenue],
    )

    changes = database.list_news_event_changes(event_id=event["event_id"])
    assert len(changes) == 2
    assert {change["new_state"] for change in changes} == {"公司利润增长", "公司营收增长"}


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("未成年人保护法未生效", "未成年人保护法已生效"),
        ("未来规划尚未实施", "未来规划已实施"),
        ("待遇政策未落地", "待遇政策已落地"),
        ("存款准备金率已下调至7.5%", "存款准备金率已下调至7.0%"),
        ("政策未能生效", "政策已生效"),
        ("政策不再生效", "政策已生效"),
        ("政策停止执行", "政策已执行"),
        ("政策禁止实施", "政策已实施"),
        ("政策尚不能实施", "政策已实施"),
        ("申请不予批准", "申请已批准"),
        ("政策拒绝执行", "政策已执行"),
        ("政策暂不实施", "政策已实施"),
        ("政策不得实施", "政策已实施"),
        ("政策尚不具备实施条件", "政策已实施"),
        ("公司董事会批准方案甲", "公司董事会批准方案乙"),
        ("待发布政策已发布", "政策尚未发布"),
        ("待实施项目已实施", "项目尚未实施"),
        ("不予批准申请已批准", "申请不予批准"),
        ("停止执行项目已执行", "项目停止执行"),
        ("待发布政策发布", "政策尚未发布"),
        ("不予批准申请批准", "申请不予批准"),
        ("存款准备金率已下调至7.5%", "存款准备金率已下调至75%"),
        ("价格已下降至-1%", "价格已下降至1%"),
        ("汇率已下降至7.20", "汇率已下降至720"),
        ("存款准备金率已下调至7.5%", "存款准备金率已下调至7.5"),
        ("价格已上涨至$100", "价格已上涨至¥100"),
        ("价格已上涨至€100", "价格已上涨至$100"),
        ("政策阈值已下调至<5%", "政策阈值已下调至>5%"),
        ("政策阈值已下调至≤5%", "政策阈值已下调至≥5%"),
        ("政策阈值已下调至≈5%", "政策阈值已下调至5%"),
        ("政策阈值已下调至~5%", "政策阈值已下调至5%"),
        ("价格已下降至(1%)", "价格已下降至1%"),
        ("利润率已下降至（1%）", "利润率已下降至1%"),
        ("价格已下降至−1%", "价格已下降至1%"),
        ("价格已上涨至↑5%", "价格已上涨至↓5%"),
        ("收益率已上升至–1%", "收益率已上升至1%"),
        ("收益率已上升至‒1%", "收益率已上升至1%"),
        ("收益率已上升▲1%", "收益率已上升▼1%"),
        ("收益率已上升⬆1%", "收益率已上升⬇1%"),
        ("温度已上升至10°C", "温度已上升至10C"),
        ("收益率已上升至– 1%", "收益率已上升至1%"),
        ("收益率已上升至- 1%", "收益率已上升至1%"),
        ("收益率已上升至– 1%", "收益率已上升至1%"),
        ("收益率已上升至(– 1%)", "收益率已上升至(1%)"),
    ],
)
def test_semantic_state_identity_preserves_polarity_and_values(
    left: str,
    right: str,
) -> None:
    assert Database._news_event_states_semantically_equivalent(left, right) is False


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("政策已停止执行", "政策不再执行"),
        ("项目已禁止实施", "项目不得实施"),
        ("申请已被拒绝批准", "申请不予批准"),
    ],
)
def test_semantic_state_identity_normalizes_decorated_nonpositive_phrases(
    left: str,
    right: str,
) -> None:
    assert Database._news_event_states_semantically_equivalent(left, right) is True


def test_current_state_chain_accepts_controlled_paraphrased_predecessor(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="政策执行进度",
        event_key="policy:paraphrased-state-chain",
    )
    first = _objective_change(
        item_key="web:paraphrased-chain-first",
        evidence_fingerprint="paraphrased-chain-first-r1",
        previous_state="政策尚未落地",
        new_state="政策已落地",
        effective_at="2025-08-20",
        detected_at="2025-08-20T09:00:00+00:00",
        media="媒体A",
    )
    second = _objective_change(
        item_key="web:paraphrased-chain-second",
        evidence_fingerprint="paraphrased-chain-second-r1",
        previous_state="政策开始执行",
        new_state="政策已完成",
        effective_at="2025-08-21",
        detected_at="2025-08-21T09:00:00+00:00",
        media="媒体B",
    )

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first["detected_at"],
        changes=[first],
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=second["detected_at"],
        changes=[second],
    )

    projected = database.get_news_event(event["event_id"])
    assert projected is not None
    assert projected["current_state"] == "政策已完成"
    assert projected["last_change_at"] == "2025-08-21"


def test_current_state_uses_effective_order_and_state_version_is_monotonic(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="政策执行进度",
        event_key="policy:effective-order",
    )
    first = _objective_change(
        item_key="web:first-stage",
        evidence_fingerprint="first-stage-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-01T08:00:00+00:00",
        media="媒体A",
    )
    latest = _objective_change(
        item_key="web:latest-stage",
        evidence_fingerprint="latest-stage-r1",
        previous_state="阶段B",
        new_state="阶段C",
        detected_at="2025-08-20T08:00:00+00:00",
        media="媒体B",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first["detected_at"],
        changes=[first],
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=latest["detected_at"],
        changes=[latest],
    )
    before_late = database.get_news_event(event["event_id"])
    assert before_late is not None and before_late["current_state"] == "阶段C"
    version_before_late = before_late["state_version"]

    late_old = _objective_change(
        item_key="web:late-old",
        evidence_fingerprint="late-old-r1",
        previous_state="阶段A之前",
        new_state="阶段A",
        effective_at="2025-07-15T08:00:00+00:00",
        detected_at="2025-08-21T08:00:00+00:00",
        media="媒体C",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=late_old["detected_at"],
        changes=[late_old],
    )
    after_late = database.get_news_event(event["event_id"])
    assert after_late is not None and after_late["current_state"] == "阶段C"
    assert after_late["state_version"] == version_before_late

    disputed = dict(
        latest,
        item_key="web:latest-dispute",
        evidence_fingerprint="latest-dispute-r1",
        objectivity_status="disputed",
        conflict_status="contested",
        evidence_status="contested",
        media="媒体D",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-22T08:00:00+00:00",
        changes=[disputed],
    )
    after_dispute = database.get_news_event(event["event_id"])
    assert after_dispute is not None and after_dispute["current_state"] == ""
    assert after_dispute["conflict_count"] == 1
    assert after_dispute["state_version"] > after_late["state_version"]

    resolved = dict(
        latest,
        item_key="web:latest-resolved",
        evidence_fingerprint="latest-resolved-r1",
        objectivity_status="confirmed",
        conflict_status="resolved",
        evidence_status="source_supported",
        media="媒体E",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-23T08:00:00+00:00",
        changes=[resolved],
    )
    after_resolve = database.get_news_event(event["event_id"])
    assert after_resolve is not None and after_resolve["current_state"] == "阶段C"
    assert after_resolve["state_version"] > after_dispute["state_version"]


def test_title_only_and_metadata_corroboration_cannot_enter_fact_feed(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="尚待正文核验的消息",
        event_key="policy:title-only",
    )
    title_only = _objective_change(
        item_key="web:title-only",
        evidence_fingerprint="title-only-r1",
        detected_at="2025-08-20T08:00:00+00:00",
        media="媒体A",
    )
    title_only.update(
        {
            "content_scope": "title_only",
            "source_scope": "multi_source",
            "evidence_status": "cross_source_supported",
            "corroborating_sources": [
                {
                    "source": "媒体B",
                    "provider": "聚合索引",
                    "url": "https://example.test/metadata-only",
                    "content_scope": "full_text",
                }
            ],
        }
    )
    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=title_only["detected_at"],
        changes=[title_only],
    )

    assert receipt["created_default_change_count"] == 0
    assert database.list_news_event_changes(event_id=event["event_id"]) == []
    stored = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    assert len(stored) == 1 and stored[0]["evidence_status"] == "provisional"
    memberships = database.list_news_event_memberships(
        event["event_id"], change_id=stored[0]["change_id"]
    )
    assert len(memberships) == 2
    assert any(item["relation_basis"] == "corroborating_source_metadata" for item in memberships)
    assert all(
        item["content_scope"] == "unknown"
        for item in memberships
        if item["relation_basis"] == "corroborating_source_metadata"
    )


def test_declared_full_text_without_retained_body_cannot_enter_fact_feed(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="伪装正文深度的消息",
        event_key="policy:fake-full-text",
    )
    fake_full_text = _objective_change(
        item_key="web:fake-full-text",
        evidence_fingerprint="fake-full-text-r1",
        detected_at="2025-08-20T08:00:00+00:00",
    )
    fake_full_text.pop("body_text")
    fake_full_text["evidence_snapshot"] = {
        "title": fake_full_text["title"],
        "content_scope": "full_text",
    }
    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=fake_full_text["detected_at"],
        changes=[fake_full_text],
    )

    assert receipt["created_default_change_count"] == 0
    stored = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    assert len(stored) == 1 and stored[0]["evidence_status"] == "provisional"
    membership = database.list_news_event_memberships(
        event["event_id"], change_id=stored[0]["change_id"]
    )[0]
    assert membership["content_scope"] == "title_only"


@pytest.mark.parametrize(
    "body_text",
    [
        "证据 web:near-title。",
        "点击查看全文",
        "来源：测试媒体",
    ],
)
def test_near_title_and_boilerplate_text_cannot_fake_evidence_depth(
    database: Database,
    body_text: str,
) -> None:
    suffix = hashlib.sha256(body_text.encode("utf-8")).hexdigest()[:8]
    event, _ = database.create_news_event(
        canonical_title=f"样板正文 {suffix}",
        event_key=f"policy:boilerplate:{suffix}",
    )
    change = _objective_change(
        item_key="web:near-title",
        evidence_fingerprint=f"near-title-{suffix}",
        detected_at="2025-08-20T08:00:00+00:00",
    )
    change["title"] = "证据 web:near-title"
    change["body_text"] = body_text
    change["evidence_snapshot"] = {
        "title": change["title"],
        "body_text": body_text,
    }

    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=change["detected_at"],
        changes=[change],
    )

    assert receipt["created_default_change_count"] == 0
    stored = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)[0]
    membership = database.list_news_event_memberships(
        event["event_id"], change_id=stored["change_id"]
    )[0]
    assert membership["content_scope"] == "title_only"
    assert stored["evidence_status"] == "provisional"


def test_membership_evidence_bundle_cannot_launder_legacy_depth(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="证据深度升级规则",
        event_key="policy:evidence-bundle",
    )
    legacy = _objective_change(
        item_key="web:bundle",
        evidence_fingerprint="bundle-r1",
        detected_at="2025-08-20T08:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=legacy["detected_at"],
        changes=[legacy],
        evidence_completeness="partial_legacy",
    )
    shallow_live = dict(legacy)
    shallow_live.pop("body_text")
    shallow_live["content_scope"] = "title_only"
    shallow_live["evidence_snapshot"] = {"title": shallow_live["title"]}
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-21T08:00:00+00:00",
        changes=[shallow_live],
        evidence_completeness="complete",
    )
    stored = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)[0]
    membership = database.list_news_event_memberships(
        event["event_id"], change_id=stored["change_id"]
    )[0]
    assert membership["content_scope"] == "title_only"
    assert membership["evidence_completeness"] == "complete"
    assert stored["evidence_status"] == "provisional"

    summary_live = dict(shallow_live)
    summary_live["content_scope"] = "summary"
    summary_live["description"] = "正式文件已明确确认政策状态发生了实质变化。"
    summary_live["evidence_snapshot"] = {
        "title": summary_live["title"],
        "description": summary_live["description"],
    }
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-22T08:00:00+00:00",
        changes=[summary_live],
        evidence_completeness="complete",
    )
    supported = database.get_news_event_change(stored["change_id"])
    supported_membership = database.list_news_event_memberships(
        event["event_id"], change_id=stored["change_id"]
    )[0]
    assert supported is not None and supported["evidence_status"] == "source_supported"
    assert supported_membership["content_scope"] == "summary"
    assert supported_membership["evidence_completeness"] == "complete"

    partial_shallow = dict(shallow_live)
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-23T08:00:00+00:00",
        changes=[partial_shallow],
        evidence_completeness="partial_legacy",
    )
    retained = database.list_news_event_memberships(
        event["event_id"], change_id=stored["change_id"]
    )[0]
    assert retained["content_scope"] == "summary"
    assert retained["evidence_completeness"] == "complete"


def test_future_effective_change_stays_secondary_even_with_future_import_clock(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="未来生效政策",
        event_key="policy:future-effective",
    )
    future = _objective_change(
        item_key="web:future-effective",
        evidence_fingerprint="future-effective-r1",
        detected_at="2099-01-01T08:00:00+00:00",
        effective_at="2099-01-01T08:00:00+00:00",
    )
    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=future["detected_at"],
        changes=[future],
    )

    assert receipt["created_default_change_count"] == 0
    assert database.list_news_event_changes(event_id=event["event_id"]) == []
    stored = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    assert len(stored) == 1 and stored[0]["objectivity_status"] == "unconfirmed"


def test_change_identity_keeps_intraday_occurrences_and_dedupes_timezones(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="同日多次状态变化",
        event_key="market:intraday-transition",
    )
    morning = _objective_change(
        item_key="web:morning",
        evidence_fingerprint="morning-r1",
        detected_at="2025-08-20T09:01:00+00:00",
        effective_at="2025-08-20T09:00:30+00:00",
        media="媒体A",
    )
    same_instant = _objective_change(
        item_key="web:morning-confirmation",
        evidence_fingerprint="morning-confirmation-r1",
        detected_at="2025-08-20T09:05:00+00:00",
        effective_at="2025-08-20T17:00:45+08:00",
        media="媒体B",
    )
    afternoon = _objective_change(
        item_key="web:afternoon",
        evidence_fingerprint="afternoon-r1",
        detected_at="2025-08-20T15:01:00+00:00",
        effective_at="2025-08-20T15:00:00+00:00",
        media="媒体C",
    )
    first = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=morning["detected_at"],
        changes=[morning],
    )
    duplicate = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=same_instant["detected_at"],
        changes=[same_instant],
    )
    second = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=afternoon["detected_at"],
        changes=[afternoon],
    )

    assert first["created_change_count"] == 1
    assert duplicate["created_change_count"] == 0
    assert second["created_change_count"] == 1
    assert len(database.list_news_event_changes(event_id=event["event_id"])) == 2


def test_explicit_date_precision_folds_local_midnight_to_the_same_day(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="同一生效日",
        event_key="policy:date-midnight",
    )
    date_only = _objective_change(
        item_key="web:date-only",
        evidence_fingerprint="date-only-r1",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20",
        media="媒体A",
    )
    midnight = _objective_change(
        item_key="web:midnight",
        evidence_fingerprint="midnight-r1",
        detected_at="2025-08-20T10:00:00+00:00",
        effective_at="2025-08-20T00:00:00+08:00",
        media="媒体B",
    )
    midnight["effective_precision"] = "date"

    first = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=date_only["detected_at"],
        changes=[date_only],
    )
    duplicate = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=midnight["detected_at"],
        changes=[midnight],
    )

    assert first["created_change_count"] == 1
    assert duplicate["created_change_count"] == 0
    assert len(database.list_news_event_changes(event_id=event["event_id"])) == 1


def test_minute_precision_keeps_timezone_equivalent_midnight_as_one_instant(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="同一精确时刻",
        event_key="policy:timezone-midnight",
    )
    local_midnight = _objective_change(
        item_key="web:local-midnight",
        evidence_fingerprint="local-midnight-r1",
        detected_at="2025-08-20T01:00:00+00:00",
        effective_at="2025-08-20T00:00:00+08:00",
        media="媒体A",
    )
    utc_equivalent = _objective_change(
        item_key="web:utc-equivalent",
        evidence_fingerprint="utc-equivalent-r1",
        detected_at="2025-08-20T02:00:00+00:00",
        effective_at="2025-08-19T16:00:00+00:00",
        media="媒体B",
    )

    first = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=local_midnight["detected_at"],
        changes=[local_midnight],
    )
    duplicate = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=utc_equivalent["detected_at"],
        changes=[utc_equivalent],
    )

    assert first["created_change_count"] == 1
    assert duplicate["created_change_count"] == 0


def test_date_effective_gate_uses_newsclaw_business_timezone() -> None:
    assert Database._news_event_effective_has_started(
        "2025-08-20",
        "2025-08-19T16:00:01+00:00",
    )
    assert not Database._news_event_effective_has_started(
        "2025-08-20",
        "2025-08-19T15:59:59+00:00",
    )


def test_undated_provisional_change_is_upgraded_in_place_when_date_is_confirmed(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="待补生效日期的变化",
        event_key="policy:undated-upgrade",
    )
    provisional = _objective_change(
        item_key="web:undated",
        evidence_fingerprint="undated-r1",
        detected_at="2025-08-20T08:00:00+00:00",
    )
    provisional.update(
        {
            "role": "analysis",
            "objectivity_status": "unconfirmed",
            "effective_at": "",
        }
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=provisional["detected_at"],
        changes=[provisional],
    )
    before = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    assert len(before) == 1

    confirmed = _objective_change(
        item_key="web:dated-confirmation",
        evidence_fingerprint="dated-confirmation-r1",
        detected_at="2025-08-21T08:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
        media="媒体B",
    )
    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=confirmed["detected_at"],
        changes=[confirmed],
    )
    after = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)

    assert receipt["created_change_count"] == 0
    assert receipt["created_default_change_count"] == 1
    assert len(after) == 1 and after[0]["change_id"] == before[0]["change_id"]
    assert after[0]["objectivity_status"] == "confirmed"
    assert after[0]["is_read"] is False
    assert len(database.list_news_event_memberships(event["event_id"])) == 2


def test_default_feed_excludes_expectations_sentiment_and_unconfirmed_analysis(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="黄金价格观察",
        event_key="gold:observation",
    )
    secondary = {
        "item_key": "web:forecast",
        "evidence_fingerprint": "forecast-r1",
        "change_type": "expectation",
        "previous_state": "市场未形成共识",
        "new_state": "分析师预计上涨",
        "role": "expectation",
        "objectivity_status": "not_applicable",
        "conflict_status": "none",
        "evidence_status": "source_supported",
        "effective_at": "",
    }
    result = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2026-08-28T08:00:00+00:00",
        changes=[secondary],
    )
    assert result["created_change_count"] == 1
    assert result["created_default_change_count"] == 0
    assert database.list_news_event_changes(event_id=event["event_id"]) == []
    stored = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    assert len(stored) == 1
    assert stored[0]["role"] == "expectation"
    assert stored[0]["is_read"] is True
    assert database.get_news_event_change(stored[0]["change_id"]) is not None
    assert database.get_news_event_change(stored[0]["change_id"], objective_only=True) is None
    assert database.mark_news_event_change_read(stored[0]["change_id"]) == (None, False)

    broken_objective = _objective_change(
        item_key="web:broken",
        evidence_fingerprint="broken-r1",
    )
    broken_objective["effective_at"] = ""
    with pytest.raises(ValueError, match="require previous_state"):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at="2026-08-28T09:00:00+00:00",
            changes=[broken_objective],
        )
    assert len(database.list_news_event_changes(objective_only=False)) == 1
    with pytest.raises(ValueError, match="non-evidence"):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="failed",
            detected_at="2026-08-28T09:00:00+00:00",
            related_memberships=[secondary],
        )


def test_change_feed_uses_stable_keyset_and_supersedes_lifecycle(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="连续进展",
        event_key="timeline:keyset",
    )
    rows: list[dict[str, Any]] = []
    for index, (previous, current) in enumerate(
        (("阶段0", "阶段1"), ("阶段1", "阶段2"), ("阶段2", "阶段3")),
        start=1,
    ):
        change = _objective_change(
            item_key=f"web:{index}",
            evidence_fingerprint=f"revision-{index}",
            previous_state=previous,
            new_state=current,
            detected_at=f"2026-08-2{index}T08:00:00+00:00",
        )
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )
        rows = database.list_news_event_changes(event_id=event["event_id"])
    first_page = database.list_news_event_changes(event_id=event["event_id"], limit=2)
    assert len(first_page) == 2
    cursor = first_page[-1]
    second_page = database.list_news_event_changes(
        event_id=event["event_id"],
        before_detected_at=cursor["timeline_sort_at"],
        before_change_id=cursor["change_id"],
        limit=2,
    )
    assert len(second_page) == 1
    assert not ({item["change_id"] for item in first_page} & {second_page[0]["change_id"]})
    with pytest.raises(ValueError, match="both keyset"):
        database.list_news_event_changes(before_detected_at=cursor["detected_at"])

    oldest = rows[-1]
    replacement = _objective_change(
        item_key="web:correction",
        evidence_fingerprint="correction-r1",
        previous_state="阶段1",
        new_state="阶段1（修订口径）",
        change_type="invalidated",
        detected_at="2026-08-28T10:00:00+00:00",
    )
    replacement.update(
        {
            "supersedes_change_id": oldest["change_id"],
            "supersedes_lifecycle": "invalidated",
        }
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=replacement["detected_at"],
        changes=[replacement],
    )
    superseded = database.get_news_event_change(oldest["change_id"])
    assert superseded is not None and superseded["lifecycle"] == "invalidated"
    active_ids = {
        item["change_id"] for item in database.list_news_event_changes(event_id=event["event_id"])
    }
    assert oldest["change_id"] not in active_ids


def test_model_change_type_synonyms_do_not_split_one_transition(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="同一变化的不同模型标签",
        event_key="policy:type-synonyms",
    )
    first = _objective_change(
        item_key="web:type-progress",
        evidence_fingerprint="type-progress-r1",
        change_type="progress",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
        media="媒体A",
    )
    confirmation = _objective_change(
        item_key="web:type-confirmation",
        evidence_fingerprint="type-confirmation-r1",
        change_type="confirmation",
        detected_at="2025-08-20T10:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
        media="媒体B",
    )

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first["detected_at"],
        changes=[first],
    )
    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=confirmation["detected_at"],
        changes=[confirmation],
    )

    assert receipt["created_change_count"] == 0
    changes = database.list_news_event_changes(event_id=event["event_id"])
    assert len(changes) == 1
    assert changes[0]["evidence_status"] == "cross_source_supported"


def test_divergent_durable_transitions_freeze_state_until_explicit_resolution(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="互斥政策状态",
        event_key="policy:divergent-state",
    )
    effective_at = "2025-08-20T08:00:00+00:00"
    first = _objective_change(
        item_key="web:divergent-b",
        evidence_fingerprint="divergent-b-r1",
        previous_state="阶段A",
        new_state="阶段B",
        change_type="progress",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at=effective_at,
        media="媒体A",
    )
    second = _objective_change(
        item_key="web:divergent-c",
        evidence_fingerprint="divergent-c-r1",
        previous_state="阶段A",
        new_state="阶段C",
        change_type="invalidated",
        detected_at="2025-08-20T10:00:00+00:00",
        effective_at=effective_at,
        media="媒体B",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=first["detected_at"],
        changes=[first],
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=second["detected_at"],
        changes=[second],
    )

    assert database.list_news_event_changes(event_id=event["event_id"]) == []
    contested = database.list_news_event_changes(
        event_id=event["event_id"],
        objective_only=False,
        conflicts_only=True,
    )
    assert len(contested) == 2
    assert {item["objectivity_status"] for item in contested} == {"disputed"}
    assert {item["conflict_status"] for item in contested} == {"contested"}
    detail = database.get_news_event(event["event_id"])
    assert detail is not None
    assert detail["current_state"] == ""
    assert detail["conflict_count"] == 2
    assert detail["unread_conflict_count"] == 2

    _, first_read = database.mark_news_event_change_read(
        contested[0]["change_id"],
        objective_only=False,
        conflicts_only=True,
    )
    _, second_read = database.mark_news_event_change_read(
        contested[0]["change_id"],
        objective_only=False,
        conflicts_only=True,
    )
    assert first_read is True and second_read is False
    detail_after_read = database.get_news_event(event["event_id"])
    assert detail_after_read is not None
    assert detail_after_read["unread_conflict_count"] == 1

    first_id = next(item["change_id"] for item in contested if item["new_state"] == "阶段B")
    resolved = dict(
        second,
        item_key="web:divergent-c-resolution",
        evidence_fingerprint="divergent-c-resolution-r1",
        objectivity_status="confirmed",
        conflict_status="resolved",
        evidence_status="source_supported",
        supersedes_change_id=first_id,
        supersedes_lifecycle="invalidated",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-21T08:00:00+00:00",
        changes=[resolved],
    )

    active = database.list_news_event_changes(event_id=event["event_id"])
    assert len(active) == 1 and active[0]["new_state"] == "阶段C"
    resolved_detail = database.get_news_event(event["event_id"])
    assert resolved_detail is not None
    assert resolved_detail["current_state"] == "阶段C"
    assert resolved_detail["conflict_count"] == 0
    assert database.mark_news_event_change_read(
        active[0]["change_id"],
        objective_only=False,
        conflicts_only=True,
    ) == (None, False)

    assert database.get_news_event_change(first_id)["lifecycle"] == "invalidated"  # type: ignore[index]


@pytest.mark.parametrize("weak_kind", ["title_only", "future", "unconfirmed", "contested"])
def test_non_durable_superseding_claim_cannot_revoke_current_fact(
    database: Database,
    weak_kind: str,
) -> None:
    event, _ = database.create_news_event(
        canonical_title=f"弱替代声明 {weak_kind}",
        event_key=f"policy:weak-supersede:{weak_kind}",
    )
    base = _objective_change(
        item_key=f"web:base-{weak_kind}",
        evidence_fingerprint=f"base-{weak_kind}-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=base["detected_at"],
        changes=[base],
    )
    base_row = database.list_news_event_changes(event_id=event["event_id"])[0]
    weak = _objective_change(
        item_key=f"web:weak-{weak_kind}",
        evidence_fingerprint=f"weak-{weak_kind}-r1",
        previous_state="阶段B",
        new_state="阶段C",
        detected_at="2025-08-21T09:00:00+00:00",
        effective_at="2025-08-21T08:00:00+00:00",
    )
    weak.update(
        {
            "supersedes_change_id": base_row["change_id"],
            "supersedes_lifecycle": "invalidated",
        }
    )
    if weak_kind == "title_only":
        weak.pop("body_text")
        weak["content_scope"] = "title_only"
        weak["evidence_snapshot"] = {"title": weak["title"]}
    elif weak_kind == "future":
        weak["effective_at"] = "2099-08-21T08:00:00+00:00"
    elif weak_kind == "unconfirmed":
        weak["objectivity_status"] = "unconfirmed"
    else:
        weak["objectivity_status"] = "disputed"
        weak["conflict_status"] = "contested"
        weak["evidence_status"] = "contested"

    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=weak["detected_at"],
        changes=[weak],
    )

    assert receipt["created_default_change_count"] == 0
    base_after = database.get_news_event_change(base_row["change_id"])
    assert base_after is not None and base_after["lifecycle"] == "active"
    detail = database.get_news_event(event["event_id"])
    assert detail is not None
    assert detail["current_state"] == ("" if weak_kind == "contested" else "阶段B")


@pytest.mark.parametrize("secondary_kind", ["title_only", "expectation", "analysis", "future"])
def test_secondary_declared_conflict_cannot_freeze_objective_state(
    database: Database,
    secondary_kind: str,
) -> None:
    event, _ = database.create_news_event(
        canonical_title=f"次级冲突声明 {secondary_kind}",
        event_key=f"policy:secondary-conflict:{secondary_kind}",
    )
    base = _objective_change(
        item_key=f"web:secondary-conflict-base-{secondary_kind}",
        evidence_fingerprint=f"secondary-conflict-base-{secondary_kind}-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=base["detected_at"],
        changes=[base],
    )
    secondary = _objective_change(
        item_key=f"web:secondary-conflict-{secondary_kind}",
        evidence_fingerprint=f"secondary-conflict-{secondary_kind}-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at=(
            "2099-08-21T08:00:00+00:00"
            if secondary_kind == "future"
            else "2025-08-21T08:00:00+00:00"
        ),
        detected_at="2025-08-21T09:00:00+00:00",
    )
    secondary["objectivity_status"] = "disputed"
    secondary["conflict_status"] = "contested"
    secondary["evidence_status"] = "contested"
    if secondary_kind == "title_only":
        secondary.pop("body_text")
        secondary["content_scope"] = "title_only"
        secondary["evidence_snapshot"] = {"title": secondary["title"]}
    elif secondary_kind in {"expectation", "analysis"}:
        secondary["role"] = secondary_kind
        secondary["objectivity_status"] = "not_applicable"

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=secondary["detected_at"],
        changes=[secondary],
    )

    detail = database.get_news_event(event["event_id"])
    stored_secondary = next(
        row
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
        )
        if row["new_state"] == "阶段C"
    )
    assert detail is not None
    assert detail["current_state"] == "阶段B"
    assert detail["conflict_count"] == 0
    assert stored_secondary["conflict_status"] == "none"
    assert stored_secondary["objectivity_status"] != "disputed"
    assert stored_secondary["evidence_status"] != "contested"
    assert (
        database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
            conflicts_only=True,
        )
        == []
    )


def test_superseding_fact_applies_only_after_durable_evidence_arrives(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="补齐证据后才替代",
        event_key="policy:supersede-after-proof",
    )
    base = _objective_change(
        item_key="web:supersede-base",
        evidence_fingerprint="supersede-base-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=base["detected_at"],
        changes=[base],
    )
    base_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    weak = _objective_change(
        item_key="web:supersede-candidate",
        evidence_fingerprint="supersede-candidate-r1",
        previous_state="阶段B",
        new_state="阶段C",
        detected_at="2025-08-21T09:00:00+00:00",
        effective_at="2025-08-21T08:00:00+00:00",
    )
    weak.update(
        {
            "supersedes_change_id": base_id,
            "supersedes_lifecycle": "invalidated",
            "content_scope": "title_only",
            "evidence_snapshot": {"title": weak["title"]},
        }
    )
    weak.pop("body_text")
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=weak["detected_at"],
        changes=[weak],
    )
    strong = dict(
        weak,
        item_key="web:supersede-proof",
        evidence_fingerprint="supersede-proof-r1",
        content_scope="full_text",
        body_text="正式文件确认阶段B已经结束，当前状态已经进入阶段C。",
        evidence_snapshot={
            "title": weak["title"],
            "body_text": "正式文件确认阶段B已经结束，当前状态已经进入阶段C。",
        },
    )
    receipt = database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-22T09:00:00+00:00",
        changes=[strong],
    )

    assert receipt["created_change_count"] == 0
    assert receipt["created_default_change_count"] == 1
    assert database.get_news_event_change(base_id)["lifecycle"] == "invalidated"  # type: ignore[index]
    detail = database.get_news_event(event["event_id"])
    assert detail is not None and detail["current_state"] == "阶段C"


def test_title_only_duplicate_cannot_attach_supersede_to_existing_default(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="弱重复不能借强记录改历史",
        event_key="policy:weak-duplicate-supersede",
    )
    base = _objective_change(
        item_key="web:weak-duplicate-base",
        evidence_fingerprint="weak-duplicate-base-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    successor = _objective_change(
        item_key="web:weak-duplicate-successor",
        evidence_fingerprint="weak-duplicate-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        detected_at="2025-08-21T09:00:00+00:00",
        effective_at="2025-08-21T08:00:00+00:00",
    )
    for change in (base, successor):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )
    rows = database.list_news_event_changes(event_id=event["event_id"])
    base_row = next(row for row in rows if row["new_state"] == "阶段B")
    successor_row = next(row for row in rows if row["new_state"] == "阶段C")
    weak_duplicate = dict(
        successor,
        item_key="web:weak-duplicate-title",
        evidence_fingerprint="weak-duplicate-title-r1",
        content_scope="title_only",
        evidence_snapshot={"title": successor["title"]},
        supersedes_change_id=base_row["change_id"],
        supersedes_lifecycle="invalidated",
    )
    weak_duplicate.pop("body_text")

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=weak_duplicate["detected_at"],
        changes=[weak_duplicate],
    )

    assert database.get_news_event_change(base_row["change_id"])["lifecycle"] == "active"  # type: ignore[index]
    successor_after = database.get_news_event_change(successor_row["change_id"])
    assert successor_after is not None
    assert not str(successor_after["supersedes_change_id"] or "")
    assert database.get_news_event(event["event_id"])["current_state"] == "阶段C"  # type: ignore[index]


def test_strong_confirmation_without_supersede_does_not_activate_weak_lineage_claim(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="强证据不继承弱替代声明",
        event_key="policy:strong-without-supersede",
    )
    base = _objective_change(
        item_key="web:lineage-base",
        evidence_fingerprint="lineage-base-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=base["detected_at"],
        changes=[base],
    )
    base_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    weak = _objective_change(
        item_key="web:lineage-weak",
        evidence_fingerprint="lineage-weak-r1",
        previous_state="阶段B",
        new_state="阶段C",
        detected_at="2025-08-21T09:00:00+00:00",
        effective_at="2025-08-21T08:00:00+00:00",
    )
    weak.update(
        {
            "content_scope": "title_only",
            "evidence_snapshot": {"title": weak["title"]},
            "supersedes_change_id": base_id,
            "supersedes_lifecycle": "invalidated",
        }
    )
    weak.pop("body_text")
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=weak["detected_at"],
        changes=[weak],
    )
    weak_row = next(
        row
        for row in database.list_news_event_changes(
            event_id=event["event_id"], objective_only=False
        )
        if row["new_state"] == "阶段C"
    )
    assert not str(weak_row["supersedes_change_id"] or "")

    strong = dict(
        weak,
        item_key="web:lineage-strong",
        evidence_fingerprint="lineage-strong-r1",
        content_scope="full_text",
        body_text="正式文件确认阶段B已经变为阶段C，但没有声明旧变化无效。",
        evidence_snapshot={
            "title": weak["title"],
            "body_text": "正式文件确认阶段B已经变为阶段C，但没有声明旧变化无效。",
        },
    )
    strong.pop("supersedes_change_id")
    strong.pop("supersedes_lifecycle")
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-22T09:00:00+00:00",
        changes=[strong],
    )

    assert database.get_news_event_change(base_id)["lifecycle"] == "active"  # type: ignore[index]
    detail = database.get_news_event(event["event_id"])
    assert detail is not None and detail["current_state"] == "阶段C"


def test_title_only_claim_cannot_promote_durable_secondary_membership(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="弱声明不能拼接强证据",
        event_key="policy:weak-status-promotion",
    )
    secondary = _objective_change(
        item_key="web:secondary-full-text",
        evidence_fingerprint="secondary-full-text-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    secondary.update({"role": "analysis", "objectivity_status": "unconfirmed"})
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=secondary["detected_at"],
        changes=[secondary],
    )
    weak_claim = dict(
        secondary,
        item_key="web:secondary-title-claim",
        evidence_fingerprint="secondary-title-claim-r1",
        role="objective_change",
        objectivity_status="confirmed",
        content_scope="title_only",
        evidence_snapshot={"title": secondary["title"]},
    )
    weak_claim.pop("body_text")

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=weak_claim["detected_at"],
        changes=[weak_claim],
    )

    rows = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    assert len(rows) == 1
    assert rows[0]["role"] == "analysis"
    assert rows[0]["objectivity_status"] == "unconfirmed"
    assert rows[0]["is_objective_default"] is False
    assert database.get_news_event(event["event_id"])["current_state"] == ""  # type: ignore[index]


def test_conflict_freezes_descendants_and_resolution_uses_reachable_chain(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="争议状态的后继不可越过",
        event_key="policy:conflict-descendant",
    )
    base = _objective_change(
        item_key="web:conflict-base-b",
        evidence_fingerprint="conflict-base-b-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    descendant = _objective_change(
        item_key="web:conflict-descendant-d",
        evidence_fingerprint="conflict-descendant-d-r1",
        previous_state="阶段B",
        new_state="阶段D",
        detected_at="2025-08-21T09:00:00+00:00",
        effective_at="2025-08-21T08:00:00+00:00",
    )
    alternative = _objective_change(
        item_key="web:conflict-alternative-c",
        evidence_fingerprint="conflict-alternative-c-r1",
        previous_state="阶段A",
        new_state="阶段C",
        detected_at="2025-08-22T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    for change in (base, descendant, alternative):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )
    detail = database.get_news_event(event["event_id"])
    assert detail is not None
    assert detail["conflict_count"] == 2
    assert detail["current_state"] == ""
    assert detail["change_count"] == 0
    assert detail["unread_change_count"] == 0
    assert database.list_news_event_changes(event_id=event["event_id"]) == []
    all_rows = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    base_id = next(row["change_id"] for row in all_rows if row["new_state"] == "阶段B")
    resolution = dict(
        alternative,
        item_key="web:conflict-resolution-c",
        evidence_fingerprint="conflict-resolution-c-r1",
        conflict_status="resolved",
        objectivity_status="confirmed",
        evidence_status="source_supported",
        supersedes_change_id=base_id,
        supersedes_lifecycle="invalidated",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at="2025-08-23T09:00:00+00:00",
        changes=[resolution],
    )

    resolved = database.get_news_event(event["event_id"])
    assert resolved is not None
    assert resolved["conflict_count"] == 0
    assert resolved["current_state"] == "阶段C"
    assert resolved["change_count"] == 1
    current_rows = database.list_news_event_changes(event_id=event["event_id"])
    assert [row["new_state"] for row in current_rows] == ["阶段C"]
    audit_rows = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    descendant_row = next(row for row in audit_rows if row["new_state"] == "阶段D")
    assert descendant_row["is_state_applied"] is False
    assert database.get_news_event_change(base_id)["lifecycle"] == "invalidated"  # type: ignore[index]


def test_invalidated_pointer_cannot_bridge_an_unrelated_blocked_state(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="错误替代指针不能跨过失效事实",
        event_key="policy:wrong-invalidated-pointer",
    )
    unrelated = _objective_change(
        item_key="web:unrelated-x-y",
        evidence_fingerprint="unrelated-x-y-r1",
        previous_state="阶段X",
        new_state="阶段Y",
        detected_at="2025-08-19T09:00:00+00:00",
    )
    blocked_predecessor = _objective_change(
        item_key="web:blocked-a-b",
        evidence_fingerprint="blocked-a-b-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    for change in (unrelated, blocked_predecessor):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )
    rows = database.list_news_event_changes(event_id=event["event_id"])
    ids = {row["new_state"]: row["change_id"] for row in rows}

    wrong_descendant = _objective_change(
        item_key="web:wrong-bridge-b-d",
        evidence_fingerprint="wrong-bridge-b-d-r1",
        previous_state="阶段B",
        new_state="阶段D",
        detected_at="2025-08-21T09:00:00+00:00",
    )
    wrong_descendant.update(
        supersedes_change_id=ids["阶段Y"],
        supersedes_lifecycle="invalidated",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=wrong_descendant["detected_at"],
        changes=[wrong_descendant],
    )
    resolution = _objective_change(
        item_key="web:resolve-a-c",
        evidence_fingerprint="resolve-a-c-r1",
        previous_state="阶段A",
        new_state="阶段C",
        detected_at="2025-08-22T09:00:00+00:00",
    )
    resolution.update(
        supersedes_change_id=ids["阶段B"],
        supersedes_lifecycle="invalidated",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=resolution["detected_at"],
        changes=[resolution],
    )

    audit_rows = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    wrong_row = next(row for row in audit_rows if row["new_state"] == "阶段D")
    current_states = {
        row["new_state"] for row in database.list_news_event_changes(event_id=event["event_id"])
    }
    assert wrong_row["is_state_applied"] is False
    assert "阶段D" not in current_states
    assert "阶段C" in current_states


def test_future_safe_producer_cannot_rescue_earlier_invalidated_descendant(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="未来事实不能反向证明过去变化",
        event_key="policy:future-producer-causality",
    )
    predecessor = _objective_change(
        item_key="web:causal-a-b",
        evidence_fingerprint="causal-a-b-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    descendant = _objective_change(
        item_key="web:causal-b-d",
        evidence_fingerprint="causal-b-d-r1",
        previous_state="阶段B",
        new_state="阶段D",
        detected_at="2025-08-21T09:00:00+00:00",
    )
    future_producer = _objective_change(
        item_key="web:causal-x-b",
        evidence_fingerprint="causal-x-b-r1",
        previous_state="阶段X",
        new_state="阶段B",
        detected_at="2025-08-22T09:00:00+00:00",
    )
    for change in (predecessor, descendant, future_producer):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )
    predecessor_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(event_id=event["event_id"])
        if row["previous_state"] == "阶段A" and row["new_state"] == "阶段B"
    )
    resolution = _objective_change(
        item_key="web:causal-a-c",
        evidence_fingerprint="causal-a-c-r1",
        previous_state="阶段A",
        new_state="阶段C",
        detected_at="2025-08-23T09:00:00+00:00",
    )
    resolution.update(
        supersedes_change_id=predecessor_id,
        supersedes_lifecycle="invalidated",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=resolution["detected_at"],
        changes=[resolution],
    )

    audit_rows = database.list_news_event_changes(event_id=event["event_id"], objective_only=False)
    descendant_row = next(row for row in audit_rows if row["new_state"] == "阶段D")
    current_states = {
        row["new_state"] for row in database.list_news_event_changes(event_id=event["event_id"])
    }
    assert descendant_row["is_state_applied"] is False
    assert "阶段D" not in current_states
    assert "阶段B" in current_states
    assert "阶段C" in current_states


def test_incoming_supersede_that_becomes_contested_cannot_revoke_target(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="先隔离争议再决定替代",
        event_key="policy:contest-before-supersede",
    )
    target = _objective_change(
        item_key="web:contest-target",
        evidence_fingerprint="contest-target-r1",
        previous_state="阶段X",
        new_state="阶段A",
        detected_at="2025-08-19T09:00:00+00:00",
        effective_at="2025-08-19T08:00:00+00:00",
    )
    competitor = _objective_change(
        item_key="web:contest-competitor-b",
        evidence_fingerprint="contest-competitor-b-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    for change in (target, competitor):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )
    target_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(event_id=event["event_id"])
        if row["new_state"] == "阶段A"
    )
    incoming = _objective_change(
        item_key="web:contest-incoming-c",
        evidence_fingerprint="contest-incoming-c-r1",
        previous_state="阶段A",
        new_state="阶段C",
        detected_at="2025-08-21T09:00:00+00:00",
        effective_at="2025-08-20T08:00:00+00:00",
    )
    incoming.update(
        {
            "supersedes_change_id": target_id,
            "supersedes_lifecycle": "invalidated",
        }
    )

    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=incoming["detected_at"],
        changes=[incoming],
    )

    assert database.get_news_event_change(target_id)["lifecycle"] == "active"  # type: ignore[index]
    detail = database.get_news_event(event["event_id"])
    assert detail is not None and detail["conflict_count"] == 2
    assert detail["current_state"] == ""


def test_mixed_date_and_minute_effective_times_use_business_time_order(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="日期与分钟统一排序",
        event_key="policy:mixed-effective-precision",
    )
    date_change = _objective_change(
        item_key="web:mixed-date",
        evidence_fingerprint="mixed-date-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T05:00:00+00:00",
        effective_at="2025-08-20",
    )
    date_change["effective_precision"] = "date"
    minute_change = _objective_change(
        item_key="web:mixed-minute",
        evidence_fingerprint="mixed-minute-r1",
        previous_state="阶段B",
        new_state="阶段C",
        detected_at="2025-08-20T05:00:00+00:00",
        effective_at="2025-08-19T20:00:00+00:00",
    )
    minute_change["effective_precision"] = "minute"
    for change in (date_change, minute_change):
        database.record_news_event_projection(
            event_id=event["event_id"],
            check_status="changed",
            detected_at=change["detected_at"],
            changes=[change],
        )

    detail = database.get_news_event(event["event_id"])
    assert detail is not None and detail["current_state"] == "阶段C"


def test_tracking_completion_and_event_projection_share_one_transaction(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = _create_tracker(
        database,
        request_id="rollback-tracker",
        item_key="web:rollback",
    )
    check, _ = database.begin_tracked_observation_check(
        tracking_id=tracker["tracking_id"],
        request_id="rollback-check",
        started_at="2026-08-28T08:00:00+00:00",
        lease_expires_at="2026-08-28T09:00:00+00:00",
    )
    change = _objective_change(
        item_key="web:rollback-evidence",
        evidence_fingerprint="rollback-r1",
    )

    def fail_projection(*args: object, **kwargs: object) -> None:
        raise RuntimeError("projection fault")

    monkeypatch.setattr(database, "_upsert_news_event_change_on", fail_projection)
    with pytest.raises(RuntimeError, match="projection fault"):
        database.complete_tracked_observation_check(
            check_id=check["check_id"],
            tracking_id=tracker["tracking_id"],
            claim_token=check["claim_token"],
            completed_at="2026-08-28T08:10:00+00:00",
            outcome="changed",
            matched_count=1,
            changes=[change],
            candidates=[change],
            decisions=[
                {
                    "evidence_item_key": change["item_key"],
                    "decision": "changed",
                }
            ],
        )
    rolled_back_check = database.get_tracked_observation_check(check["check_id"])
    assert rolled_back_check is not None and rolled_back_check["status"] == "running"
    assert (
        database.conn.execute(
            "SELECT COUNT(*) FROM tracked_observation_evidence WHERE check_id = ?",
            (check["check_id"],),
        ).fetchone()[0]
        == 0
    )
    assert (
        database.conn.execute(
            "SELECT COUNT(*) FROM tracked_observation_candidates WHERE check_id = ?",
            (check["check_id"],),
        ).fetchone()[0]
        == 0
    )
    unchanged_event = database.get_news_event(tracker["event_id"])
    assert unchanged_event is not None and unchanged_event["total_change_count"] == 0


def test_tracking_completion_projects_verified_change_with_origin_lineage(
    database: Database,
) -> None:
    tracker = _create_tracker(
        database,
        request_id="success-tracker",
        item_key="web:success",
    )
    origins = [
        item
        for item in database.list_news_event_memberships(tracker["event_id"])
        if item["relation_kind"] == "origin"
    ]
    assert len(origins) == 1 and origins[0]["evidence_completeness"] == "complete"
    check, _ = database.begin_tracked_observation_check(
        tracking_id=tracker["tracking_id"],
        request_id="success-check",
        started_at="2026-08-28T08:00:00+00:00",
        lease_expires_at="2026-08-28T09:00:00+00:00",
    )
    change = _objective_change(
        item_key="web:success-evidence",
        evidence_fingerprint="success-r1",
    )
    completed, updated_tracker = database.complete_tracked_observation_check(
        check_id=check["check_id"],
        tracking_id=tracker["tracking_id"],
        claim_token=check["claim_token"],
        completed_at="2026-08-28T08:10:00+00:00",
        outcome="changed",
        matched_count=1,
        changes=[change],
        candidates=[change],
        decisions=[
            {
                "evidence_item_key": change["item_key"],
                "decision": "changed",
            }
        ],
    )

    assert completed["status"] == "changed"
    changes = database.list_news_event_changes(event_id=updated_tracker["event_id"])
    assert len(changes) == 1
    memberships = database.list_news_event_memberships(
        updated_tracker["event_id"],
        change_id=changes[0]["change_id"],
    )
    assert len(memberships) == 1
    assert memberships[0]["origin_tracking_id"] == tracker["tracking_id"]
    assert memberships[0]["origin_check_id"] == check["check_id"]
    assert memberships[0]["origin_evidence_id"]


def test_real_merge_rebinds_trackers_deduplicates_changes_and_keeps_lineage(
    database: Database,
) -> None:
    tracker = _create_tracker(
        database,
        request_id="merge-tracker",
        item_key="web:merge-origin",
        story_key="source-story",
    )
    source_id = tracker["event_id"]
    source_before = database.get_news_event(source_id, resolve_merged=False)
    assert source_before is not None
    source_event_key = source_before["event_key"]
    target, _ = database.create_news_event(
        canonical_title="目标事件",
        event_key="merge-target",
    )
    duplicate = _objective_change(
        item_key="web:source-duplicate",
        evidence_fingerprint="source-duplicate-r1",
        detected_at="2025-08-28T08:00:00+00:00",
    )
    duplicate["origin_tracking_id"] = tracker["tracking_id"]
    unique = _objective_change(
        item_key="web:source-unique",
        evidence_fingerprint="source-unique-r1",
        previous_state="正式生效",
        new_state="开始执行",
        detected_at="2025-08-29T08:00:00+00:00",
    )
    unique["origin_tracking_id"] = tracker["tracking_id"]
    database.record_news_event_projection(
        event_id=source_id,
        check_status="changed",
        detected_at=unique["detected_at"],
        changes=[duplicate, unique],
    )
    source_change_rows = database.list_news_event_changes(event_id=source_id)
    assert len(source_change_rows) == 2
    source_duplicate_id = next(
        item["change_id"]
        for item in source_change_rows
        if item["new_state"] == duplicate["new_state"]
    )
    target_duplicate = dict(
        duplicate,
        item_key="web:target-duplicate",
        evidence_fingerprint="target-duplicate-r1",
        origin_tracking_id="",
    )
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_duplicate["detected_at"],
        changes=[target_duplicate],
    )

    merged = database.merge_news_events(
        source_event_id=source_id,
        target_event_id=target["event_id"],
    )
    assert merged["merged"] is True
    assert merged["deduplicated_changes"] == 1
    assert merged["moved_changes"] == 1
    rebound_tracker = database.get_tracked_observation(tracker["tracking_id"])
    assert rebound_tracker is not None and rebound_tracker["event_id"] == target["event_id"]
    source = database.get_news_event(source_id, resolve_merged=False)
    target_after = database.get_news_event(target["event_id"])
    assert source is not None and source["status"] == "merged"
    assert source["merged_into_event_id"] == target["event_id"]
    assert target_after is not None and target_after["change_count"] == 2
    assert database.list_news_event_changes(event_id=source_id) == []
    target_memberships = database.list_news_event_memberships(target["event_id"], limit=100)
    assert any(item["origin_tracking_id"] == tracker["tracking_id"] for item in target_memberships)
    canonical_duplicate = database.get_news_event_change(source_duplicate_id)
    assert canonical_duplicate is not None
    assert canonical_duplicate["event_id"] == target["event_id"]
    assert canonical_duplicate["change_id"] != source_duplicate_id
    alias_memberships = database.list_news_event_memberships(
        target["event_id"], change_id=source_duplicate_id
    )
    assert len(alias_memberships) == 2
    marked_via_alias, marked = database.mark_news_event_change_read(source_duplicate_id)
    assert marked_via_alias is not None and marked is True
    recreated, created = database.create_news_event(
        canonical_title="不应重新分裂的源事件",
        event_key=source_event_key,
    )
    assert created is False and recreated["event_id"] == target["event_id"]
    alias_reuse = _objective_change(
        item_key="web:alias-reuse",
        evidence_fingerprint="alias-reuse-r1",
        previous_state="开始执行",
        new_state="执行完成",
        detected_at="2025-08-30T08:00:00+00:00",
    )
    alias_reuse["change_id"] = source_duplicate_id
    with pytest.raises(ValueError, match="durable alias"):
        database.record_news_event_projection(
            event_id=target["event_id"],
            check_status="changed",
            detected_at=alias_reuse["detected_at"],
            changes=[alias_reuse],
        )
    with pytest.raises(ValueError, match="merged news events"):
        database.set_news_event_status(source_id, status="active")
    repeated = database.merge_news_events(
        source_event_id=source_id,
        target_event_id=target["event_id"],
    )
    assert repeated["merged"] is False


def test_merge_duplicate_preserves_durable_source_fact_classification(
    database: Database,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="来源事实事件",
        event_key="merge:classification-source",
    )
    target, _ = database.create_news_event(
        canonical_title="目标次级事件",
        event_key="merge:classification-target",
    )
    source_change = _objective_change(
        item_key="web:merge-class-source",
        evidence_fingerprint="merge-class-source-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
        media="来源媒体",
    )
    target_change = dict(
        source_change,
        item_key="web:merge-class-target",
        evidence_fingerprint="merge-class-target-r1",
        media="目标媒体",
        role="analysis",
        objectivity_status="unconfirmed",
    )
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=source_change["detected_at"],
        changes=[source_change],
    )
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_change["detected_at"],
        changes=[target_change],
    )
    assert len(database.list_news_event_changes(event_id=source["event_id"])) == 1
    assert database.list_news_event_changes(event_id=target["event_id"]) == []

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    changes = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
    )
    assert len(changes) == 1
    assert changes[0]["role"] == "objective_change"
    assert changes[0]["objectivity_status"] == "confirmed"
    assert changes[0]["evidence_status"] == "cross_source_supported"
    assert changes[0]["is_objective_default"] is True
    memberships = database.list_news_event_memberships(
        target["event_id"],
        change_id=changes[0]["change_id"],
    )
    assert len(memberships) == 2
    detail = database.get_news_event(target["event_id"])
    assert detail is not None and detail["current_state"] == "阶段B"


def test_merge_title_only_duplicate_cannot_promote_secondary_fact(
    database: Database,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="弱来源事件",
        event_key="merge:weak-classification-source",
    )
    target, _ = database.create_news_event(
        canonical_title="强目标次级事件",
        event_key="merge:weak-classification-target",
    )
    source_change = _objective_change(
        item_key="web:merge-weak-class-source",
        evidence_fingerprint="merge-weak-class-source-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    source_change.pop("body_text")
    target_change = dict(
        _objective_change(
            item_key="web:merge-weak-class-target",
            evidence_fingerprint="merge-weak-class-target-r1",
            previous_state="阶段A",
            new_state="阶段B",
            effective_at="2025-08-20T08:00:00+00:00",
            detected_at="2025-08-20T09:00:00+00:00",
            media="目标媒体",
        ),
        role="analysis",
        objectivity_status="unconfirmed",
    )
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=source_change["detected_at"],
        changes=[source_change],
    )
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_change["detected_at"],
        changes=[target_change],
    )

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    changes = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
    )
    assert len(changes) == 1
    assert changes[0]["role"] == "analysis"
    assert changes[0]["objectivity_status"] == "unconfirmed"
    assert changes[0]["is_objective_default"] is False
    detail = database.get_news_event(target["event_id"])
    assert detail is not None and detail["current_state"] == ""


@pytest.mark.parametrize("durable", [True, False])
def test_merge_duplicate_only_propagates_durable_contradiction(
    database: Database,
    durable: bool,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="反证来源事件",
        event_key=f"merge:contradiction-source:{durable}",
    )
    target, _ = database.create_news_event(
        canonical_title="已确认目标事件",
        event_key=f"merge:contradiction-target:{durable}",
    )
    source_change = dict(
        _objective_change(
            item_key=f"web:merge-contradiction-source-{durable}",
            evidence_fingerprint=f"merge-contradiction-source-{durable}-r1",
            previous_state="阶段A",
            new_state="阶段B",
            effective_at="2025-08-20T08:00:00+00:00",
            detected_at="2025-08-20T10:00:00+00:00",
            media="反证媒体",
        ),
        objectivity_status="disputed",
        conflict_status="contested",
        evidence_status="contested",
    )
    if not durable:
        source_change.pop("body_text")
    target_change = _objective_change(
        item_key=f"web:merge-contradiction-target-{durable}",
        evidence_fingerprint=f"merge-contradiction-target-{durable}-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
        media="确认媒体",
    )
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=source_change["detected_at"],
        changes=[source_change],
    )
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_change["detected_at"],
        changes=[target_change],
    )

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    canonical_rows = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
    )
    assert len(canonical_rows) == 1
    detail = database.get_news_event(target["event_id"])
    assert detail is not None
    memberships = database.list_news_event_memberships(
        target["event_id"],
        change_id=canonical_rows[0]["change_id"],
    )
    assert {membership["relation_kind"] for membership in memberships} == {
        "supports_change",
        "contradicts_change",
    }
    if durable:
        assert canonical_rows[0]["conflict_status"] == "contested"
        assert canonical_rows[0]["is_objective_default"] is False
        assert detail["current_state"] == ""
        assert detail["conflict_count"] == 1
    else:
        assert canonical_rows[0]["conflict_status"] == "none"
        assert canonical_rows[0]["is_objective_default"] is True
        assert detail["current_state"] == "阶段B"
        assert detail["conflict_count"] == 0


def test_merge_duplicate_preserves_accepted_supersede_lineage(
    database: Database,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="带替代关系的来源事件",
        event_key="merge:lineage-source",
    )
    target, _ = database.create_news_event(
        canonical_title="缺少替代关系的目标事件",
        event_key="merge:lineage-target",
    )
    base = _objective_change(
        item_key="web:merge-lineage-source-base",
        evidence_fingerprint="merge-lineage-source-base-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=base["detected_at"],
        changes=[base],
    )
    source_base = database.list_news_event_changes(event_id=source["event_id"])[0]
    successor = _objective_change(
        item_key="web:merge-lineage-source-successor",
        evidence_fingerprint="merge-lineage-source-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-21T09:00:00+00:00",
    )
    successor["supersedes_change_id"] = source_base["change_id"]
    successor["supersedes_lifecycle"] = "invalidated"
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=successor["detected_at"],
        changes=[successor],
    )
    source_rows = database.list_news_event_changes(
        event_id=source["event_id"],
        objective_only=False,
    )
    source_successor = next(row for row in source_rows if row["new_state"] == "阶段C")

    target_base_input = dict(
        base,
        item_key="web:merge-lineage-target-base",
        evidence_fingerprint="merge-lineage-target-base-r1",
        media="目标媒体",
    )
    target_successor_input = dict(
        successor,
        item_key="web:merge-lineage-target-successor",
        evidence_fingerprint="merge-lineage-target-successor-r1",
        media="目标媒体",
    )
    target_successor_input.pop("supersedes_change_id")
    target_successor_input.pop("supersedes_lifecycle")
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_successor_input["detected_at"],
        changes=[target_base_input, target_successor_input],
    )
    target_rows = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
    )
    target_base = next(row for row in target_rows if row["new_state"] == "阶段B")
    target_successor = next(row for row in target_rows if row["new_state"] == "阶段C")

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    aliased_base = database.get_news_event_change(source_base["change_id"])
    aliased_successor = database.get_news_event_change(source_successor["change_id"])
    assert aliased_base is not None and aliased_base["change_id"] == target_base["change_id"]
    assert aliased_successor is not None
    assert aliased_successor["change_id"] == target_successor["change_id"]
    assert aliased_successor["supersedes_change_id"] == target_base["change_id"]
    assert database.get_news_event_change(target_base["change_id"])["lifecycle"] == (  # type: ignore[index]
        "invalidated"
    )
    detail = database.get_news_event(target["event_id"])
    assert detail is not None and detail["current_state"] == "阶段C"


def test_merge_duplicate_preserves_historical_supersede_chain(
    database: Database,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="链式来源事件",
        event_key="merge:lineage-chain-source",
    )
    target, _ = database.create_news_event(
        canonical_title="链式目标事件",
        event_key="merge:lineage-chain-target",
    )
    source_inputs = [
        _objective_change(
            item_key="web:chain-source-ab",
            evidence_fingerprint="chain-source-ab-r1",
            previous_state="阶段A",
            new_state="阶段B",
            effective_at="2025-08-20T08:00:00+00:00",
            detected_at="2025-08-20T09:00:00+00:00",
        ),
        _objective_change(
            item_key="web:chain-source-bc",
            evidence_fingerprint="chain-source-bc-r1",
            previous_state="阶段B",
            new_state="阶段C",
            effective_at="2025-08-21T08:00:00+00:00",
            detected_at="2025-08-21T09:00:00+00:00",
        ),
        _objective_change(
            item_key="web:chain-source-cd",
            evidence_fingerprint="chain-source-cd-r1",
            previous_state="阶段C",
            new_state="阶段D",
            effective_at="2025-08-22T08:00:00+00:00",
            detected_at="2025-08-22T09:00:00+00:00",
        ),
    ]
    source_ids: dict[str, str] = {}
    for index, source_input in enumerate(source_inputs):
        if index:
            source_input["supersedes_change_id"] = source_ids[source_input["previous_state"]]
            source_input["supersedes_lifecycle"] = "superseded"
        database.record_news_event_projection(
            event_id=source["event_id"],
            check_status="changed",
            detected_at=source_input["detected_at"],
            changes=[source_input],
        )
        source_row = next(
            row
            for row in database.list_news_event_changes(
                event_id=source["event_id"],
                objective_only=False,
            )
            if row["new_state"] == source_input["new_state"]
        )
        source_ids[source_input["new_state"]] = source_row["change_id"]

    target_inputs = [
        dict(
            source_input,
            item_key=f"web:chain-target-{index}",
            evidence_fingerprint=f"chain-target-{index}-r1",
            media="目标媒体",
        )
        for index, source_input in enumerate(source_inputs)
    ]
    for target_input in target_inputs:
        target_input.pop("supersedes_change_id", None)
        target_input.pop("supersedes_lifecycle", None)
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_inputs[-1]["detected_at"],
        changes=target_inputs,
    )

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    rows = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
        lifecycle="all",
    )
    by_new_state = {str(row["new_state"]): row for row in rows}
    assert by_new_state["阶段B"]["lifecycle"] == "superseded"
    assert by_new_state["阶段C"]["lifecycle"] == "superseded"
    assert by_new_state["阶段D"]["lifecycle"] == "active"
    assert by_new_state["阶段C"]["supersedes_change_id"] == by_new_state["阶段B"]["change_id"]
    assert by_new_state["阶段D"]["supersedes_change_id"] == by_new_state["阶段C"]["change_id"]
    detail = database.get_news_event(target["event_id"])
    assert detail is not None and detail["current_state"] == "阶段D"


def test_merge_distinct_durable_predecessor_claims_are_quarantined(
    database: Database,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="来源前置关系",
        event_key="merge:lineage-conflict-source",
    )
    target, _ = database.create_news_event(
        canonical_title="目标前置关系",
        event_key="merge:lineage-conflict-target",
    )

    def record_lineage(
        event_id: str,
        *,
        prefix: str,
        previous_state: str,
    ) -> tuple[str, str]:
        predecessor = _objective_change(
            item_key=f"web:{prefix}-predecessor",
            evidence_fingerprint=f"{prefix}-predecessor-r1",
            previous_state=previous_state,
            new_state="阶段B",
            effective_at="2025-08-20T08:00:00+00:00",
            detected_at="2025-08-20T09:00:00+00:00",
            media=f"{prefix}媒体",
        )
        database.record_news_event_projection(
            event_id=event_id,
            check_status="changed",
            detected_at=predecessor["detected_at"],
            changes=[predecessor],
        )
        predecessor_id = database.list_news_event_changes(event_id=event_id)[0]["change_id"]
        successor = _objective_change(
            item_key=f"web:{prefix}-successor",
            evidence_fingerprint=f"{prefix}-successor-r1",
            previous_state="阶段B",
            new_state="阶段C",
            effective_at="2025-08-21T08:00:00+00:00",
            detected_at="2025-08-21T09:00:00+00:00",
            media=f"{prefix}媒体",
        )
        successor["supersedes_change_id"] = predecessor_id
        successor["supersedes_lifecycle"] = "superseded"
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
        prefix="source-lineage",
        previous_state="阶段A",
    )
    target_predecessor_id, target_successor_id = record_lineage(
        target["event_id"],
        prefix="target-lineage",
        previous_state="阶段X",
    )

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    canonical_successor = database.get_news_event_change(source_successor_id)
    assert canonical_successor is not None
    assert canonical_successor["change_id"] == target_successor_id
    assert not str(canonical_successor["supersedes_change_id"] or "")
    assert canonical_successor["conflict_status"] == "contested"
    for predecessor_id in (source_predecessor_id, target_predecessor_id):
        predecessor = database.get_news_event_change(predecessor_id)
        assert predecessor is not None
        assert predecessor["lifecycle"] == "active"
        assert predecessor["conflict_status"] == "none"
    conflicts = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
        conflicts_only=True,
    )
    assert len(conflicts) == 1
    assert conflicts[0]["change_id"] == target_successor_id
    claims = database.list_news_event_lineage_claims(target_successor_id)
    assert {claim["predecessor_change_id"] for claim in claims} == {
        source_predecessor_id,
        target_predecessor_id,
    }
    assert {claim["status"] for claim in claims} == {"contested"}
    assert {claim["source_event_id"] for claim in claims} == {
        source["event_id"],
        target["event_id"],
    }
    detail = database.get_news_event(target["event_id"])
    assert detail is not None
    assert detail["current_state"] == ""
    assert detail["conflict_count"] == 1

    resolution = _objective_change(
        item_key="web:lineage-resolution",
        evidence_fingerprint="lineage-resolution-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-22T09:00:00+00:00",
        media="裁决证据源",
    )
    resolution["conflict_status"] = "resolved"
    resolution["supersedes_change_id"] = target_predecessor_id
    resolution["supersedes_lifecycle"] = "superseded"
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=resolution["detected_at"],
        changes=[resolution],
    )

    resolved_successor = database.get_news_event_change(target_successor_id)
    assert resolved_successor is not None
    assert resolved_successor["conflict_status"] == "resolved"
    assert resolved_successor["supersedes_change_id"] == target_predecessor_id
    resolved_claims = database.list_news_event_lineage_claims(target_successor_id)
    assert {claim["predecessor_change_id"]: claim["status"] for claim in resolved_claims} == {
        source_predecessor_id: "rejected",
        target_predecessor_id: "resolved",
    }
    chosen_predecessor = database.get_news_event_change(target_predecessor_id)
    rejected_predecessor = database.get_news_event_change(source_predecessor_id)
    assert chosen_predecessor is not None
    assert rejected_predecessor is not None
    assert chosen_predecessor["lifecycle"] == "superseded"
    assert rejected_predecessor["lifecycle"] == "active"
    resolved_detail = database.get_news_event(target["event_id"])
    assert resolved_detail is not None
    assert resolved_detail["current_state"] == "阶段C"
    assert resolved_detail["conflict_count"] == 0

    replayed_rejected_claim = _objective_change(
        item_key="web:source-lineage-successor",
        evidence_fingerprint="source-lineage-successor-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-21T09:00:00+00:00",
        media="source-lineage媒体",
    )
    replayed_rejected_claim["change_id"] = source_successor_id
    replayed_rejected_claim["source_event_id"] = source["event_id"]
    replayed_rejected_claim["supersedes_change_id"] = source_predecessor_id
    replayed_rejected_claim["supersedes_lifecycle"] = "superseded"
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=replayed_rejected_claim["detected_at"],
        changes=[replayed_rejected_claim],
    )

    replayed_detail = database.get_news_event(target["event_id"])
    replayed_successor = database.get_news_event_change(target_successor_id)
    replayed_claims = database.list_news_event_lineage_claims(target_successor_id)
    assert replayed_detail is not None
    assert replayed_successor is not None
    assert replayed_detail["current_state"] == "阶段C"
    assert replayed_detail["conflict_count"] == 0
    assert replayed_successor["conflict_status"] == "resolved"
    assert replayed_successor["supersedes_change_id"] == target_predecessor_id
    assert {claim["predecessor_change_id"]: claim["status"] for claim in replayed_claims} == {
        source_predecessor_id: "rejected",
        target_predecessor_id: "resolved",
    }


def test_conflicting_supersedes_lifecycle_for_same_edge_requires_resolution(
    database: Database,
) -> None:
    event, _ = database.create_news_event(
        canonical_title="同一前置关系的生命周期冲突",
        event_key="lineage-lifecycle-conflict",
    )
    predecessor = _objective_change(
        item_key="web:lifecycle-predecessor",
        evidence_fingerprint="lifecycle-predecessor-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=predecessor["detected_at"],
        changes=[predecessor],
    )
    predecessor_id = database.list_news_event_changes(event_id=event["event_id"])[0]["change_id"]
    superseded_claim = _objective_change(
        item_key="web:lifecycle-successor-superseded",
        evidence_fingerprint="lifecycle-successor-superseded-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-21T09:00:00+00:00",
    )
    superseded_claim["supersedes_change_id"] = predecessor_id
    superseded_claim["supersedes_lifecycle"] = "superseded"
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=superseded_claim["detected_at"],
        changes=[superseded_claim],
    )
    successor_id = next(
        row["change_id"]
        for row in database.list_news_event_changes(
            event_id=event["event_id"],
            objective_only=False,
        )
        if row["new_state"] == "阶段C"
    )
    invalidated_claim = _objective_change(
        item_key="web:lifecycle-successor-invalidated",
        evidence_fingerprint="lifecycle-successor-invalidated-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-22T09:00:00+00:00",
        media="独立复核媒体",
    )
    invalidated_claim["supersedes_change_id"] = predecessor_id
    invalidated_claim["supersedes_lifecycle"] = "invalidated"
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=invalidated_claim["detected_at"],
        changes=[invalidated_claim],
    )

    contested_successor = database.get_news_event_change(successor_id)
    restored_predecessor = database.get_news_event_change(predecessor_id)
    contested_claims = database.list_news_event_lineage_claims(successor_id)
    contested_detail = database.get_news_event(event["event_id"])
    assert contested_successor is not None
    assert restored_predecessor is not None
    assert contested_detail is not None
    assert contested_successor["conflict_status"] == "contested"
    assert not str(contested_successor["supersedes_change_id"] or "")
    assert restored_predecessor["lifecycle"] == "active"
    assert {(claim["supersedes_lifecycle"], claim["status"]) for claim in contested_claims} == {
        ("superseded", "contested"),
        ("invalidated", "contested"),
    }
    assert contested_detail["current_state"] == ""
    assert contested_detail["conflict_count"] == 1

    resolution = dict(invalidated_claim)
    resolution["item_key"] = "web:lifecycle-resolution"
    resolution["evidence_fingerprint"] = "lifecycle-resolution-r1"
    resolution["conflict_status"] = "resolved"
    resolution["detected_at"] = "2025-08-23T09:00:00+00:00"
    database.record_news_event_projection(
        event_id=event["event_id"],
        check_status="changed",
        detected_at=resolution["detected_at"],
        changes=[resolution],
    )

    resolved_successor = database.get_news_event_change(successor_id)
    invalidated_predecessor = database.get_news_event_change(predecessor_id)
    resolved_claims = database.list_news_event_lineage_claims(successor_id)
    resolved_detail = database.get_news_event(event["event_id"])
    assert resolved_successor is not None
    assert invalidated_predecessor is not None
    assert resolved_detail is not None
    assert resolved_successor["conflict_status"] == "resolved"
    assert resolved_successor["supersedes_change_id"] == predecessor_id
    assert resolved_successor["supersedes_lifecycle"] == "invalidated"
    assert invalidated_predecessor["lifecycle"] == "invalidated"
    assert {(claim["supersedes_lifecycle"], claim["status"]) for claim in resolved_claims} == {
        ("superseded", "rejected"),
        ("invalidated", "resolved"),
    }
    assert resolved_detail["current_state"] == "阶段C"
    assert resolved_detail["conflict_count"] == 0


def test_merge_conflict_releases_previously_applied_supersede(
    database: Database,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="冲突来源事件",
        event_key="merge:release-source",
    )
    target, _ = database.create_news_event(
        canonical_title="已有替代关系的目标事件",
        event_key="merge:release-target",
    )
    base = _objective_change(
        item_key="web:merge-release-base",
        evidence_fingerprint="merge-release-base-r1",
        previous_state="阶段A",
        new_state="阶段B",
        effective_at="2025-08-20T08:00:00+00:00",
        detected_at="2025-08-20T09:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=base["detected_at"],
        changes=[base],
    )
    target_base = database.list_news_event_changes(event_id=target["event_id"])[0]
    accepted = _objective_change(
        item_key="web:merge-release-c",
        evidence_fingerprint="merge-release-c-r1",
        previous_state="阶段B",
        new_state="阶段C",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-21T09:00:00+00:00",
    )
    accepted["supersedes_change_id"] = target_base["change_id"]
    accepted["supersedes_lifecycle"] = "invalidated"
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=accepted["detected_at"],
        changes=[accepted],
    )
    assert database.get_news_event_change(target_base["change_id"])["lifecycle"] == (  # type: ignore[index]
        "invalidated"
    )
    contradictory = _objective_change(
        item_key="web:merge-release-d",
        evidence_fingerprint="merge-release-d-r1",
        previous_state="阶段B",
        new_state="阶段D",
        change_type="contradiction",
        effective_at="2025-08-21T08:00:00+00:00",
        detected_at="2025-08-21T10:00:00+00:00",
    )
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=contradictory["detected_at"],
        changes=[contradictory],
    )

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    restored = database.get_news_event_change(target_base["change_id"])
    assert restored is not None
    assert restored["lifecycle"] == "active"
    assert restored["is_objective_default"] is True
    conflicts = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
        conflicts_only=True,
    )
    assert len(conflicts) == 2
    detail = database.get_news_event(target["event_id"])
    assert detail is not None and detail["current_state"] == ""


def test_merge_quarantines_divergent_durable_transitions(
    database: Database,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="来源事件",
        event_key="merge:divergent-source",
    )
    target, _ = database.create_news_event(
        canonical_title="目标事件",
        event_key="merge:divergent-target",
    )
    effective_at = "2025-08-20T08:00:00+00:00"
    source_change = _objective_change(
        item_key="web:merge-divergent-b",
        evidence_fingerprint="merge-divergent-b-r1",
        previous_state="阶段A",
        new_state="阶段B",
        detected_at="2025-08-20T09:00:00+00:00",
        effective_at=effective_at,
    )
    target_change = _objective_change(
        item_key="web:merge-divergent-c",
        evidence_fingerprint="merge-divergent-c-r1",
        previous_state="阶段A",
        new_state="阶段C",
        change_type="contradiction",
        detected_at="2025-08-20T10:00:00+00:00",
        effective_at=effective_at,
    )
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=source_change["detected_at"],
        changes=[source_change],
    )
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_change["detected_at"],
        changes=[target_change],
    )

    database.merge_news_events(
        source_event_id=source["event_id"],
        target_event_id=target["event_id"],
    )

    assert database.list_news_event_changes(event_id=target["event_id"]) == []
    conflicts = database.list_news_event_changes(
        event_id=target["event_id"],
        objective_only=False,
        conflicts_only=True,
    )
    assert len(conflicts) == 2
    detail = database.get_news_event(target["event_id"])
    assert detail is not None
    assert detail["current_state"] == ""
    assert detail["conflict_count"] == 2


def test_merge_fault_rolls_back_changes_memberships_and_event_status(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = database.create_news_event(
        canonical_title="合并源",
        event_key="merge-rollback-source",
    )
    target, _ = database.create_news_event(
        canonical_title="合并目标",
        event_key="merge-rollback-target",
    )
    source_change = _objective_change(
        item_key="web:merge-rollback-source",
        evidence_fingerprint="merge-rollback-source-r1",
    )
    target_change = dict(
        source_change,
        item_key="web:merge-rollback-target",
        evidence_fingerprint="merge-rollback-target-r1",
    )
    database.record_news_event_projection(
        event_id=source["event_id"],
        check_status="changed",
        detected_at=source_change["detected_at"],
        changes=[source_change],
    )
    database.record_news_event_projection(
        event_id=target["event_id"],
        check_status="changed",
        detected_at=target_change["detected_at"],
        changes=[target_change],
    )

    def fail_membership_move(*args: object, **kwargs: object) -> None:
        raise RuntimeError("merge projection fault")

    monkeypatch.setattr(database, "_upsert_news_event_membership_on", fail_membership_move)
    with pytest.raises(RuntimeError, match="merge projection fault"):
        database.merge_news_events(
            source_event_id=source["event_id"],
            target_event_id=target["event_id"],
        )

    source_after = database.get_news_event(source["event_id"], resolve_merged=False)
    target_after = database.get_news_event(target["event_id"], resolve_merged=False)
    assert source_after is not None and source_after["status"] == "active"
    assert target_after is not None and target_after["status"] == "active"
    assert source_after["total_change_count"] == 1
    assert target_after["total_change_count"] == 1
    assert len(database.list_news_event_memberships(source["event_id"])) == 1
    assert len(database.list_news_event_memberships(target["event_id"])) == 1


def test_backfill_is_resumable_idempotent_and_keeps_legacy_changes_secondary(
    database: Database,
) -> None:
    observation_key = hashlib.sha256("旧观察".encode()).hexdigest()
    snapshot = json.dumps(
        {
            "title": "旧政策原文",
            "content_scope": "full_text",
            "content_url": "https://legacy.test/origin",
        },
        ensure_ascii=False,
    )
    for index in (1, 2):
        database.conn.execute(
            """
            INSERT INTO tracked_observations (
                tracking_id, request_id, item_key, topic_group, story_key,
                observation_key, observation, latest_evidence, original_snapshot,
                status, created_at
            ) VALUES (?, ?, ?, '宏观', ?, ?, '旧观察', '旧证据', ?, 'archived', ?)
            """,
            (
                f"legacy-{index}",
                f"legacy-request-{index}",
                f"web:legacy-{index}",
                f"legacy-story-{index}",
                observation_key,
                snapshot,
                f"2026-08-0{index}T00:00:00+00:00",
            ),
        )
        database.conn.execute(
            """
            INSERT INTO tracked_observation_checks (
                check_id, request_id, tracking_id, status, is_read,
                started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"legacy-check-{index}",
                f"legacy-check-request-{index}",
                f"legacy-{index}",
                "changed" if index == 1 else "unchanged",
                0 if index == 1 else 1,
                f"2026-08-1{index}T00:00:00+00:00",
                f"2026-08-1{index}T00:10:00+00:00",
            ),
        )
    database.conn.execute(
        """
        INSERT INTO tracked_observation_candidates (
            candidate_record_id, check_id, tracking_id, evidence_item_key,
            evidence_fingerprint, decision, evidence_snapshot, examined_at
        ) VALUES (
            'legacy-candidate', 'legacy-check-1', 'legacy-1', 'web:related',
            'related-r1', 'related_no_change', ?, '2026-08-11T00:10:00+00:00'
        )
        """,
        (snapshot,),
    )
    database.conn.execute(
        """
        INSERT INTO tracked_observation_evidence (
            evidence_id, check_id, tracking_id, evidence_item_key,
            evidence_fingerprint, change_fingerprint, change_type,
            previous_state, new_state, fact, summary, evidence_snapshot, detected_at
        ) VALUES (
            'legacy-evidence', 'legacy-check-1', 'legacy-1', 'web:changed',
            'changed-r1', 'legacy-change-r1', 'progress',
            '阶段一', '阶段二', '旧系统认为有变化', '进入阶段二', ?,
            '2026-08-11T00:10:00+00:00'
        )
        """,
        (snapshot,),
    )
    database.conn.commit()

    first = database.backfill_news_event_projection(batch_size=1)
    assert first["processed_trackers"] == 1
    assert first["state"]["completed"] is False
    assert first["created_events"] == 1
    assert first["created_changes"] == 1
    second = database.backfill_news_event_projection(batch_size=1)
    assert second["processed_trackers"] == 1
    assert second["state"]["completed"] is True
    repeated = database.backfill_news_event_projection(batch_size=1)
    assert repeated["processed_trackers"] == 0
    assert repeated["created_events"] == 0
    assert repeated["created_changes"] == 0
    assert repeated["created_memberships"] == 0

    tracker = database.get_tracked_observation("legacy-1")
    assert tracker is not None and tracker["event_id"]
    assert database.list_news_event_changes(event_id=tracker["event_id"]) == []
    legacy_changes = database.list_news_event_changes(
        event_id=tracker["event_id"], objective_only=False
    )
    assert len(legacy_changes) == 1
    assert legacy_changes[0]["role"] == "analysis"
    assert legacy_changes[0]["objectivity_status"] == "unconfirmed"
    assert legacy_changes[0]["is_read"] is True
    memberships = database.list_news_event_memberships(tracker["event_id"])
    assert any(item["relation_kind"] == "related_no_change" for item in memberships)
    assert all(item["evidence_completeness"] == "partial_legacy" for item in memberships)
