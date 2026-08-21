from __future__ import annotations

import json
import shutil
import zipfile
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.config import Config, save_config
from openbiliclaw.memory.manager import MemoryManager
from openbiliclaw.saved_sync.models import SavedItemInput
from openbiliclaw.saved_sync.router import NativeSaveRouter
from openbiliclaw.saved_sync.service import SavedSyncService
from openbiliclaw.storage import migration
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _configure_newsclaw(monkeypatch: pytest.MonkeyPatch, root: Path) -> Config:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    config = Config(data_dir=str(root / "data"))
    config.llm.default_provider = "ollama"
    config.llm.ollama.model = "llama3"
    save_config(config, root / "config.toml")
    return config


def _open_app(root: Path, database_path: Path) -> tuple[TestClient, Database, MemoryManager]:
    database = Database(database_path)
    database.initialize()
    memory = MemoryManager(root / "memory", database=database)
    memory.initialize()
    runtime = SimpleNamespace(
        mark_notification_sent=database.mark_notification_sent,
        get_pending_notification=lambda: None,
    )
    app = create_app(
        memory_manager=memory,
        database=database,
        soul_engine=SimpleNamespace(is_profile_ready=lambda: False),
        runtime_controller=runtime,
    )
    app.state.runtime_context.saved_sync_service = SavedSyncService(
        database,
        NativeSaveRouter([]),
    )
    return (
        TestClient(
            app,
            client=("127.0.0.1", 5010),
            base_url="http://127.0.0.1:8430",
        ),
        database,
        memory,
    )


def _seed_article(database: Database, article_id: str = "article-001") -> None:
    database.cache_content(
        article_id,
        title="Policy signal",
        up_name="Official Daily",
        author_name="Official Daily",
        content_id=article_id,
        content_url="https://news.example/policy-signal",
        content_type="article",
        source_platform="news",
        source="news-official-rss",
    )
    database._execute_write(
        """
        INSERT INTO discovery_candidates (
            candidate_key, status, source_platform, source_strategy,
            source_context, content_type, bvid, content_id, content_url,
            title, author_name, up_name, raw_payload
        ) VALUES (?, 'cached', 'news', 'news-official-rss', ?, 'article', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"news:{article_id}",
            "Official Daily",
            article_id,
            article_id,
            "https://news.example/policy-signal",
            "Policy signal",
            "Official Daily",
            "Official Daily",
            json.dumps(
                {
                    "article_id": article_id,
                    "media": "Official Daily",
                    "provider": "Official RSS",
                    "technical_provider": "Official RSS",
                }
            ),
        ),
    )


def test_news_actions_use_article_identity_and_replay_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    _configure_newsclaw(monkeypatch, root)
    database_path = root / "data" / "openbiliclaw.db"
    client, database, memory = _open_app(root, database_path)
    _seed_article(database)
    recommendation_id = database.insert_recommendation(
        "article-001",
        item_key="news:article-001",
        confidence=0.91,
        expression="Relevant to your macro watchlist",
        topic="Macro",
    )

    evidence = client.get("/api/news/articles/article-001")
    viewed = client.post(
        "/api/spotlight/respond",
        json={
            "article_id": "article-001",
            "media": "Official Daily",
            "title": "Policy signal",
            "response": "view",
            "request_id": "spotlight-view-001",
        },
    )

    assert evidence.status_code == 200, evidence.text
    assert evidence.json() == {
        "article_id": "article-001",
        "media": "Official Daily",
        "provider": "Official RSS",
        "technical_provider": "Official RSS",
    }
    assert viewed.status_code == 200, viewed.text
    assert viewed.json()["action"] == "viewed"
    assert viewed.json()["article_id"] == "article-001"
    assert memory.query_events(event_types=["click"], limit=20) == []

    saved = client.post(
        "/api/favorites",
        json={
            "article_id": "article-001",
            "original_url": "https://news.example/policy-signal",
            "title": "Policy signal",
            "media": "Official Daily",
        },
    )
    watch_later = client.post(
        "/api/watch-later",
        json={
            "article_id": "article-001",
            "original_url": "https://news.example/policy-signal",
            "title": "Policy signal",
            "media": "Official Daily",
        },
    )
    feedback = client.post(
        "/api/feedback",
        json={
            "recommendation_id": recommendation_id,
            "feedback_type": "like",
            "request_id": "feedback-like-001",
        },
    )
    opened = client.post(
        "/api/spotlight/respond",
        json={
            "article_id": "article-001",
            "media": "Official Daily",
            "title": "Policy signal",
            "response": "open",
            "request_id": "spotlight-open-001",
        },
    )
    acknowledged = client.post(
        "/api/notifications/ack",
        json={
            "article_id": "article-001",
            "request_id": "notification-ack-001",
        },
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["item_key"] == "news:article-001"
    assert saved.json()["article_id"] == "article-001"
    assert watch_later.status_code == 200, watch_later.text
    assert watch_later.json()["item_key"] == "news:article-001"
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["duplicate"] is False
    assert opened.status_code == 200, opened.text
    assert opened.json()["action"] == "opened"
    assert opened.json()["article_id"] == "article-001"
    assert opened.json()["duplicate"] is False
    assert acknowledged.status_code == 200, acknowledged.text
    assert acknowledged.json()["duplicate"] is False
    assert database.get_saved_membership("favorite", "news:article-001") is not None
    removed_watch_later = client.delete("/api/watch-later/article-001")
    assert removed_watch_later.status_code == 200
    assert removed_watch_later.json()["saved"] is False
    events = memory.query_events(event_types=["click"], limit=20)
    assert len(events) == 1
    metadata = json.loads(str(events[0]["metadata"]))
    assert metadata == {
        **metadata,
        "article_id": "article-001",
        "content_id": "article-001",
        "media": "Official Daily",
        "technical_provider": "Official RSS",
    }
    feedback_events = memory.query_events(event_types=["feedback"], limit=20)
    assert len(feedback_events) == 1
    feedback_metadata = json.loads(str(feedback_events[0]["metadata"]))
    assert feedback_metadata["article_id"] == "article-001"
    assert feedback_metadata["media"] == "Official Daily"
    assert feedback_metadata["technical_provider"] == "Official RSS"

    client.close()
    database.close()

    replay_client, reopened, reopened_memory = _open_app(root, database_path)
    replay_open = replay_client.post(
        "/api/spotlight/respond",
        json={
            "article_id": "article-001",
            "media": "Official Daily",
            "title": "Policy signal",
            "response": "open",
            "request_id": "spotlight-open-001",
        },
    )
    replay_ack = replay_client.post(
        "/api/notifications/ack",
        json={
            "article_id": "article-001",
            "request_id": "notification-ack-001",
        },
    )
    replay_feedback = replay_client.post(
        "/api/feedback",
        json={
            "recommendation_id": recommendation_id,
            "feedback_type": "like",
            "request_id": "feedback-like-001",
        },
    )
    history = replay_client.get("/api/content-history", params={"category": "clicked"})
    favorites = replay_client.get("/api/favorites")
    watch_later_list = replay_client.get("/api/watch-later")
    pending_message = replay_client.get("/api/notifications/pending")

    assert replay_open.status_code == 200, replay_open.text
    assert replay_open.json()["duplicate"] is True
    assert replay_ack.status_code == 200, replay_ack.text
    assert replay_ack.json()["duplicate"] is True
    assert replay_feedback.status_code == 200, replay_feedback.text
    assert replay_feedback.json()["duplicate"] is True
    assert len(reopened_memory.query_events(event_types=["click"], limit=20)) == 1
    assert len(reopened_memory.query_events(event_types=["feedback"], limit=20)) == 1
    assert reopened.get_saved_membership("favorite", "news:article-001") is not None
    assert history.status_code == 200
    assert history.json()["items"][0]["article_id"] == "article-001"
    assert history.json()["items"][0]["media"] == "Official Daily"
    assert history.json()["items"][0]["technical_provider"] == "Official RSS"
    assert favorites.json()["items"][0]["article_id"] == "article-001"
    assert watch_later_list.json()["items"] == []
    assert pending_message.status_code == 200, pending_message.text
    assert pending_message.json().get("item") is None

    _seed_article(reopened, "article-other")
    conflict = replay_client.post(
        "/api/notifications/ack",
        json={"article_id": "article-other", "request_id": "notification-ack-001"},
    )
    assert conflict.status_code == 409
    replay_client.close()
    reopened.close()


def test_newsclaw_rejects_video_initializer_with_native_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    _configure_newsclaw(monkeypatch, root)
    client, database, _memory = _open_app(root, root / "data" / "openbiliclaw.db")

    response = client.post("/api/init", json={"force": True})

    assert response.status_code == 409
    assert response.json() == {
        "error": "newsclaw_bootstrap_required",
        "detail": (
            "NewsClaw does not run the video-platform guided initializer. "
            "Use POST /api/news/bootstrap and poll GET /api/news/bootstrap/status."
        ),
        "start_endpoint": "/api/news/bootstrap",
        "status_endpoint": "/api/news/bootstrap/status",
    }
    client.close()
    database.close()


def test_custom_rss_add_is_idempotent_and_survives_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    _configure_newsclaw(monkeypatch, root)
    database_path = root / "data" / "openbiliclaw.db"
    client, database, _memory = _open_app(root, database_path)
    payload = {
        "media": "Official Wire",
        "url": "https://news.example/feed.xml",
        "enabled": True,
        "weight": 1.0,
        "refresh_minutes": 60,
    }

    created = client.post("/api/news/sources/rss", json=payload)
    replay = client.post("/api/news/sources/rss", json=payload)
    conflict = client.post(
        "/api/news/sources/rss",
        json={**payload, "media": "Different Publisher"},
    )

    assert created.status_code == 201, created.text
    assert created.json()["duplicate"] is False
    assert replay.status_code == 201, replay.text
    assert replay.json()["duplicate"] is True
    assert replay.json()["source"]["source_id"] == created.json()["source"]["source_id"]
    assert conflict.status_code == 409
    client.close()
    database.close()

    reopened_client, reopened, _reopened_memory = _open_app(root, database_path)
    sources = reopened_client.get("/api/news/sources")
    matching = [
        row
        for row in sources.json()["sources"]
        if row["source_id"] == created.json()["source"]["source_id"]
    ]
    assert len(matching) == 1
    assert matching[0]["media"] == "Official Wire"
    reopened_client.close()
    reopened.close()


def test_migration_round_trips_news_credentials_as_credentials(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source = Config(data_dir=str(source_root / "data"))
    save_config(source, source_root / "config.toml")
    source.data_path.mkdir(parents=True, exist_ok=True)
    source_database = Database(source.data_path / "openbiliclaw.db")
    source_database.initialize()
    source_database.close()
    credentials = {"tushare_token": "test-token-never-log"}
    (source.data_path / "newsclaw_credentials.json").write_text(
        json.dumps(credentials),
        encoding="utf-8",
    )

    exported = migration.create_migration_archive(
        source,
        project_root=source_root,
        product="newsclaw",
    )
    try:
        with zipfile.ZipFile(exported.path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            entry = next(
                row
                for row in manifest["entries"]
                if row["path"] == "data/newsclaw_credentials.json"
            )
            assert entry["kind"] == "credential"
            assert json.loads(archive.read(entry["path"])) == credentials

        target_root = tmp_path / "target"
        target = Config(data_dir=str(target_root / "data"))
        save_config(target, target_root / "config.toml")
        target.data_path.mkdir(parents=True, exist_ok=True)
        target_database = Database(target.data_path / "openbiliclaw.db")
        target_database.initialize()
        target_database.close()
        migration.stage_migration_archive(
            exported.path,
            target,
            project_root=target_root,
            expected_product="newsclaw",
        )
        result = migration.apply_pending_migration(project_root=target_root)

        assert result is not None
        assert result.state == "applied"
        restored = json.loads(
            (target.data_path / "newsclaw_credentials.json").read_text(encoding="utf-8")
        )
        assert restored == credentials
    finally:
        shutil.rmtree(exported.path.parent, ignore_errors=True)


def test_newsclaw_read_models_exclude_legacy_video_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    _configure_newsclaw(monkeypatch, root)
    client, database, _memory = _open_app(root, root / "data" / "openbiliclaw.db")
    _seed_article(database, "news-1")
    _seed_article(database, "news-2")
    database.cache_content(
        "BV1LEGACY",
        title="Legacy video",
        content_id="BV1LEGACY",
        content_url="https://www.bilibili.com/video/BV1LEGACY",
        source_platform="bilibili",
        content_type="video",
    )
    old_recommendation = database.insert_recommendation(
        "BV1LEGACY",
        item_key="bilibili:BV1LEGACY",
        confidence=0.99,
        expression="legacy",
        topic="legacy",
    )
    news_recommendations = [
        database.insert_recommendation(
            article_id,
            item_key=f"news:{article_id}",
            confidence=0.9 - index / 100,
            expression="news",
            topic="macro",
        )
        for index, article_id in enumerate(("news-1", "news-2"))
    ]
    for recommendation_id, platform, content_id in (
        (old_recommendation, "bilibili", "BV1LEGACY"),
        (news_recommendations[0], "news", "news-1"),
        (news_recommendations[1], "news", "news-2"),
    ):
        database.insert_event(
            "click",
            title=content_id,
            metadata={
                "source": "recommendation_click",
                "event_namespace": "recommendation",
                "recommendation_id": recommendation_id,
                "source_platform": platform,
                "content_id": content_id,
            },
        )
    for list_kind in ("favorite", "watch_later"):
        database.upsert_saved_membership(
            list_kind,
            SavedItemInput(source_platform="bilibili", content_id="BV1LEGACY"),
        )
        database.upsert_saved_membership(
            list_kind,
            SavedItemInput(source_platform="news", content_id="news-1"),
        )

    first_history = client.get(
        "/api/content-history",
        params={"category": "clicked", "limit": 1},
    ).json()
    second_history = client.get(
        "/api/content-history",
        params={"category": "clicked", "limit": 1, "cursor": first_history["next_cursor"]},
    ).json()
    notification_candidate = database.get_notification_candidate(
        min_confidence=0.82,
        source_platform="news",
    )
    pending = client.get("/api/notifications/pending").json()["item"]

    assert first_history["total"] == 2
    assert first_history["has_more"] is True
    assert second_history["total"] == 2
    assert {
        first_history["items"][0]["source_platform"],
        second_history["items"][0]["source_platform"],
    } == {"news"}
    assert {first_history["items"][0]["article_id"], second_history["items"][0]["article_id"]} == {
        "news-1",
        "news-2",
    }
    assert notification_candidate is not None
    assert notification_candidate["source_platform"] == "news"
    assert pending is not None
    assert pending["article_id"] in {"news-1", "news-2"}
    assert pending["bvid"] != "BV1LEGACY"
    for route in (
        "/api/saved/favorite",
        "/api/saved/watch_later",
        "/api/favorites",
        "/api/watch-later",
    ):
        payload = client.get(route).json()
        assert payload["total"] == 1
        assert len(payload["items"]) == 1
        assert payload["items"][0]["source_platform"] == "news"

    legacy_status = client.get(
        "/api/saved/favorite/status",
        params={"item_key": "bilibili:BV1LEGACY"},
    ).json()
    assert legacy_status["saved"] is False
    assert legacy_status["item_key"] == "bilibili:BV1LEGACY"
    client.close()
    database.close()


def test_newsclaw_migration_rejects_unbranded_openbiliclaw_archive(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    source = Config(data_dir=str(source_root / "data"))
    save_config(source, source_root / "config.toml")
    source.data_path.mkdir(parents=True, exist_ok=True)
    source_database = Database(source.data_path / "openbiliclaw.db")
    source_database.initialize()
    source_database.close()
    exported = migration.create_migration_archive(source, project_root=source_root)
    target_root = tmp_path / "target"
    target = Config(data_dir=str(target_root / "data"))
    save_config(target, target_root / "config.toml")
    target.data_path.mkdir(parents=True, exist_ok=True)
    target_database = Database(target.data_path / "openbiliclaw.db")
    target_database.initialize()
    target_database.close()
    try:
        with pytest.raises(migration.MigrationError) as error:
            migration.stage_migration_archive(
                exported.path,
                target,
                project_root=target_root,
                expected_product="newsclaw",
            )
        assert error.value.code == "wrong_product"
        assert "旧视频行为与画像" in str(error.value)
        assert migration.migration_status(project_root=target_root)["state"] == "idle"
    finally:
        shutil.rmtree(exported.path.parent, ignore_errors=True)
