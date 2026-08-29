import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from scripts.newsclaw_event_demo import (
    CONFLICT_EVENT_ID,
    DEMO_STATUS_LABEL,
    LPR_EVENT_ID,
    EventDemoServer,
    EventDemoState,
    demo_payload,
)


def _json(origin: str, path: str, *, method: str = "GET") -> dict[str, object]:
    request = Request(f"{origin}{path}", method=method)
    with urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def test_demo_inbox_is_deterministic_and_labels_synthetic_conflict() -> None:
    status, payload = demo_payload("/api/news/events?status=active&attention=all&limit=30")
    assert status == 200
    assert [item["event_id"] for item in payload["items"]] == [
        LPR_EVENT_ID,
        "demo-event-rrr-2024",
        CONFLICT_EVENT_ID,
    ]
    assert payload["summary"] == {
        "event_count": 3,
        "unread_event_count": 3,
        "conflict_event_count": 1,
        "unread_change_count": 2,
        "unread_conflict_count": 2,
    }
    conflict = payload["items"][2]
    assert conflict["canonical_title"].startswith("【演示冲突样本】")
    assert conflict["topic_group"].endswith("演示")


def test_demo_replays_real_lpr_history_without_turning_history_into_current_state() -> None:
    state = EventDemoState()
    current_status, current = demo_payload(
        f"/api/news/events/{LPR_EVENT_ID}/changes?view=current&limit=30",
        state=state,
    )
    audit_status, audit = demo_payload(
        f"/api/news/events/{LPR_EVENT_ID}/changes?view=audit&limit=30",
        state=state,
    )
    assert current_status == audit_status == 200
    assert [item["change_id"] for item in current["items"]] == [
        "demo-change-lpr-2024-10"
    ]
    assert len(audit["items"]) == 3
    assert [item["lifecycle"] for item in audit["items"]] == [
        "active",
        "superseded",
        "superseded",
    ]
    assert current["event"]["current_state"] == "1年期 3.10%；5年期以上 3.60%"


def test_demo_evidence_uses_public_sources_and_never_claims_synthetic_sources_are_real() -> None:
    state = EventDemoState()
    _, lpr = demo_payload(
        f"/api/news/events/{LPR_EVENT_ID}/evidence"
        "?change_id=demo-change-lpr-2024-10&limit=30&offset=0",
        state=state,
    )
    assert urlsplit(lpr["items"][0]["canonical_url"]).hostname == "www.pbc.gov.cn"
    assert lpr["items"][0]["content_scope"] == "full_text"

    _, conflict = demo_payload(
        f"/api/news/events/{CONFLICT_EVENT_ID}/evidence"
        "?change_id=demo-change-conflict-20&limit=30&offset=0",
        state=state,
    )
    item = conflict["items"][0]
    assert urlsplit(item["canonical_url"]).hostname == "example.invalid"
    assert "非真实来源" in item["media"]
    assert "不代表真实政策" in item["evidence_snapshot"]["body_text"]


def test_demo_conflict_freezes_current_view_and_keeps_both_versions() -> None:
    state = EventDemoState()
    _, current = demo_payload(
        f"/api/news/events/{CONFLICT_EVENT_ID}/changes?view=current&limit=30",
        state=state,
    )
    _, conflicts = demo_payload(
        f"/api/news/events/{CONFLICT_EVENT_ID}/conflicts?limit=30&offset=0",
        state=state,
    )
    assert current["items"] == []
    assert current["event"]["current_state"] == "补贴比例待核验"
    assert {item["new_state"] for item in conflicts["items"]} == {
        "补贴比例 15%",
        "补贴比例 20%",
    }
    assert all(item["conflict_status"] == "contested" for item in conflicts["items"])


def test_demo_mark_read_is_in_memory_idempotent_and_updates_summary() -> None:
    state = EventDemoState()
    path = "/api/news/changes/demo-change-lpr-2024-10/read"
    first_status, first = demo_payload(path, method="POST", state=state)
    second_status, second = demo_payload(path, method="POST", state=state)
    _, summary = demo_payload("/api/news/events/summary?status=active", state=state)
    assert first_status == second_status == 200
    assert first["item"]["is_read"] is True
    assert second["item"]["is_read"] is True
    assert summary["unread_change_count"] == 1
    assert summary["unread_event_count"] == 2


def test_demo_server_serves_real_workspace_with_visible_isolation_disclosure() -> None:
    with EventDemoServer() as origin:
        with urlopen(f"{origin}/web/") as response:
            html = response.read().decode("utf-8")
        inbox = _json(origin, "/api/news/events?status=active&attention=all&limit=30")
        _json(
            origin,
            "/api/news/conflicts/demo-change-conflict-20/read",
            method="POST",
        )
        after = _json(origin, "/api/news/events/summary?status=active")
    assert "src/openbiliclaw/web" not in html
    assert "NewsClaw · 隔离演示环境" in html
    assert DEMO_STATUS_LABEL in html
    assert len(inbox["items"]) == 3
    assert after["unread_conflict_count"] == 1


def test_demo_module_never_loads_application_config_database_or_credentials() -> None:
    source = Path("scripts/newsclaw_event_demo.py").read_text(encoding="utf-8")
    assert "Database(" not in source
    assert "load_config(" not in source
    assert "os.environ" not in source
    _, payload = demo_payload("/api/news/events")
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    assert "api_key" not in serialized
    assert "cookie" not in serialized
    assert "token" not in serialized
