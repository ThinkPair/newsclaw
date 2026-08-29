from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.web_monitor_routes import register_web_monitor_routes
from openbiliclaw.news.web_monitoring import FetchedDocument, WebMonitorService
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


class _SequenceFetcher:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls = 0

    async def fetch(self, **_kwargs: object) -> FetchedDocument:
        index = min(self.calls, len(self.texts) - 1)
        text = self.texts[index]
        self.calls += 1
        marker = chr(ord("a") + index)
        return FetchedDocument(
            final_url="https://example.com/policy",
            kind="html",
            mime_type="text/html",
            title="政策页",
            normalized_text=text,
            raw_hash=marker * 64,
            semantic_hash=marker * 64,
            byte_count=len(text.encode()),
            etag="",
            last_modified="",
            http_status=200,
        )


@pytest.fixture
def monitor_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[TestClient, Database, _SequenceFetcher]:
    database = Database(tmp_path / "routes.db")
    database.initialize()
    fetcher = _SequenceFetcher(["政策利率为 3%", "政策利率为 4%"])
    service = WebMonitorService(database=database, fetcher=fetcher, poll_seconds=3600)
    app = FastAPI()
    register_web_monitor_routes(app, service=service)

    async def _safe_url(value: str) -> str:
        assert value.startswith("https://")
        return value

    monkeypatch.setattr("openbiliclaw.api.web_monitor_routes.validate_public_url", _safe_url)
    client = TestClient(app)
    yield client, database, fetcher
    client.close()
    database.close()


def _create(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/news/monitors",
        json={
            "url": "https://example.com/policy",
            "label": "政策页",
            "kind": "auto",
            "interval_minutes": 60,
            "request_id": "route-create-request",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_monitor_routes_close_crud_check_history_and_notification_loop(
    monitor_api: tuple[TestClient, Database, _SequenceFetcher],
) -> None:
    client, database, _fetcher = monitor_api
    created = _create(client)
    assert created["created"] is True
    assert created["check"]["status"] == "baseline"
    monitor = created["item"]
    assert monitor["has_baseline"] is True
    assert "lease_token" not in monitor
    monitor_id = monitor["monitor_id"]

    changed = client.post(
        f"/api/news/monitors/{monitor_id}/check",
        json={"request_id": "route-change-request"},
    )
    assert changed.status_code == 200, changed.text
    changed_payload = changed.json()
    assert changed_payload["status"] == changed_payload["check"]["status"] == "changed"
    assert "lease_token" not in changed_payload["check"]
    replayed = client.post(
        f"/api/news/monitors/{monitor_id}/check",
        json={"request_id": "route-change-request"},
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == changed_payload

    inbox = client.get("/api/news/monitor-notifications")
    assert inbox.status_code == 200
    assert inbox.json()["unread_count"] == 1
    delivery = inbox.json()["items"][0]
    acknowledged = client.post(
        f"/api/news/monitor-notifications/{delivery['delivery_id']}",
        json={"action": "read", "request_id": "route-read-request"},
    )
    assert acknowledged.status_code == 200
    assert client.get("/api/news/monitor-notifications").json()["unread_count"] == 0

    history = client.get(f"/api/news/monitors/{monitor_id}/history")
    assert history.status_code == 200
    assert len(history.json()["versions"]) == 2
    assert history.json()["diffs"][0]["classification"] == "meaningful"
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 1

    assert (
        client.post(f"/api/news/monitors/{monitor_id}/pause").json()["item"]["status"] == "paused"
    )
    paused_check = client.post(
        f"/api/news/monitors/{monitor_id}/check",
        json={"request_id": "route-paused-check"},
    )
    assert paused_check.status_code == 409
    assert (
        client.post(f"/api/news/monitors/{monitor_id}/resume").json()["item"]["status"] == "active"
    )
    deleted = client.delete(f"/api/news/monitors/{monitor_id}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert client.get(f"/api/news/monitors/{monitor_id}").status_code == 404


def test_create_request_replay_is_idempotent_and_conflicting_reuse_is_409(
    monitor_api: tuple[TestClient, Database, _SequenceFetcher],
) -> None:
    client, _database, fetcher = monitor_api
    first = _create(client)
    replay = _create(client)
    assert replay["created"] is False
    assert replay["item"]["monitor_id"] == first["item"]["monitor_id"]
    assert fetcher.calls == 1

    conflict = client.post(
        "/api/news/monitors",
        json={
            "url": "https://example.com/another",
            "label": "另一个网页",
            "kind": "auto",
            "interval_minutes": 60,
            "request_id": "route-create-request",
        },
    )
    assert conflict.status_code == 409


def test_normal_product_mode_does_not_register_or_start_p5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEWSCLAW_MODE", raising=False)
    database = Database(tmp_path / "normal.db")
    database.initialize()
    app = create_app(database=database, memory_manager=object(), soul_engine=object())
    paths = {route.path for route in app.routes}
    assert "/api/news/monitors" not in paths
    assert "/api/news/monitor-notifications" not in paths
    database.close()
