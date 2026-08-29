from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from scripts.newsclaw_event_demo_backend import (
    CHANGE_IDS,
    EVENT_IDS,
    build_demo_app,
    close_demo_resources,
    isolated_demo_environment,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def demo_backend(tmp_path: Path) -> Iterator[tuple[TestClient, Any, Path]]:
    root = tmp_path / "isolated-demo"
    with isolated_demo_environment(root):
        app, database = build_demo_app(root)
        client = TestClient(app)
        yield client, database, root
        client.close()
        close_demo_resources(app, database)


def test_real_event_inbox_uses_isolated_sqlite(demo_backend: tuple[TestClient, Any, Path]) -> None:
    client, database, root = demo_backend

    response = client.get("/api/news/events")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 3
    assert response.json()["summary"] == {
        "event_count": 3,
        "unread_event_count": 3,
        "conflict_event_count": 1,
        "unread_change_count": 2,
        "unread_conflict_count": 2,
    }
    assert Path(database._db_path).resolve() == (root / "data" / "newsclaw-demo.db").resolve()
    assert (root / "data" / "newsclaw-demo.db").is_file()


def test_lpr_current_audit_and_evidence_are_closed_loop(
    demo_backend: tuple[TestClient, Any, Path],
) -> None:
    client, _database, _root = demo_backend
    event_id = EVENT_IDS["demo-event-lpr-2024"]

    current = client.get(f"/api/news/events/{event_id}/changes")
    audit = client.get(
        f"/api/news/events/{event_id}/changes",
        params={"view": "audit"},
    )
    evidence = client.get(
        f"/api/news/events/{event_id}/evidence",
        params={"change_id": CHANGE_IDS["demo-change-lpr-2024-10"]},
    )

    assert current.status_code == 200
    assert [row["change_id"] for row in current.json()["items"]] == [
        CHANGE_IDS["demo-change-lpr-2024-10"]
    ]
    assert [row["lifecycle"] for row in audit.json()["items"]] == [
        "active",
        "superseded",
        "superseded",
    ]
    assert evidence.status_code == 200
    assert evidence.json()["items"][0]["media"] == "中国人民银行"
    assert evidence.json()["items"][0]["canonical_url"].startswith("https://www.pbc.gov.cn/")


def test_rrr_change_has_two_real_source_memberships(
    demo_backend: tuple[TestClient, Any, Path],
) -> None:
    client, _database, _root = demo_backend
    event_id = EVENT_IDS["demo-event-rrr-2024"]
    change_id = CHANGE_IDS["demo-change-rrr-2024-09"]

    evidence = client.get(
        f"/api/news/events/{event_id}/evidence",
        params={"change_id": change_id},
    )

    assert evidence.status_code == 200
    assert len(evidence.json()["items"]) == 2
    assert {row["media"] for row in evidence.json()["items"]} == {"中国人民银行"}
    assert all("pbc.gov.cn" in row["canonical_url"] for row in evidence.json()["items"])


def test_conflict_freezes_current_state_and_read_route_is_idempotent(
    demo_backend: tuple[TestClient, Any, Path],
) -> None:
    client, _database, _root = demo_backend
    event_id = EVENT_IDS["demo-event-conflict-sample"]

    detail = client.get(f"/api/news/events/{event_id}")
    current = client.get(f"/api/news/events/{event_id}/changes")
    conflicts = client.get(f"/api/news/events/{event_id}/conflicts")

    assert detail.status_code == 200
    assert detail.json()["item"]["current_state"] == ""
    assert detail.json()["item"]["conflict_count"] == 2
    assert current.json()["items"] == []
    assert len(conflicts.json()["items"]) == 2
    assert {row["freeze_reason"] for row in conflicts.json()["items"]} == {
        "durable_evidence_conflict"
    }
    conflict_id = conflicts.json()["items"][0]["change_id"]
    first = client.post(f"/api/news/conflicts/{conflict_id}/read")
    second = client.post(f"/api/news/conflicts/{conflict_id}/read")
    assert first.status_code == 200 and first.json()["updated"] is True
    assert second.status_code == 200 and second.json()["updated"] is False


def test_demo_html_is_visibly_disclosed(demo_backend: tuple[TestClient, Any, Path]) -> None:
    client, _database, _root = demo_backend

    response = client.get("/web/")

    assert response.status_code == 200
    assert "NewsClaw · 隔离演示环境" in response.text
    assert "演示环境 · 正式库未连接" in response.text


def test_unavailable_profile_is_reported_without_a_server_error(
    demo_backend: tuple[TestClient, Any, Path],
) -> None:
    client, _database, _root = demo_backend

    response = client.get("/api/profile-summary")

    assert response.status_code == 200
    assert response.json()["initialized"] is False


def test_runtime_files_contain_no_credentials_or_enabled_sources(
    demo_backend: tuple[TestClient, Any, Path],
) -> None:
    _client, _database, root = demo_backend
    config_text = (root / "config.toml").read_text(encoding="utf-8")
    source_text = (root / "data" / "newsclaw_sources.json").read_text(encoding="utf-8")
    combined = f"{config_text}\n{source_text}".lower()

    assert '"sources": []' in source_text
    assert "api_key = \"\"" in config_text
    assert "token" not in combined
    assert "cookie" not in combined
    assert "secret" not in combined
    assert "enabled = true" not in combined
    assert all(path.resolve().is_relative_to(root.resolve()) for path in root.rglob("*"))


def test_environment_is_restored_after_demo_context(tmp_path: Path) -> None:
    original_root = os.environ.get("OPENBILICLAW_PROJECT_ROOT")
    original_mode = os.environ.get("NEWSCLAW_MODE")
    original_tavily = os.environ.get("TAVILY_API_KEY")
    os.environ["TAVILY_API_KEY"] = "test-parent-secret"
    try:
        with isolated_demo_environment(tmp_path / "environment"):
            assert os.environ["NEWSCLAW_MODE"] == "1"
            assert os.environ["OPENBILICLAW_PROJECT_ROOT"] == str(
                (tmp_path / "environment").resolve()
            )
            assert "TAVILY_API_KEY" not in os.environ
        assert os.environ.get("TAVILY_API_KEY") == "test-parent-secret"
        assert os.environ.get("OPENBILICLAW_PROJECT_ROOT") == original_root
        assert os.environ.get("NEWSCLAW_MODE") == original_mode
    finally:
        if original_tavily is None:
            os.environ.pop("TAVILY_API_KEY", None)
        else:
            os.environ["TAVILY_API_KEY"] = original_tavily


def test_windows_launcher_pins_current_source_and_real_backend() -> None:
    launcher = Path("scripts/start-newsclaw-event-demo.cmd").read_text(encoding="utf-8")

    assert 'set "PYTHONPATH=%CD%\\src"' in launcher
    assert "newsclaw_event_demo_backend.py" in launcher
    assert 'set "NEWSCLAW_DEMO_PYTHON=python"' in launcher
