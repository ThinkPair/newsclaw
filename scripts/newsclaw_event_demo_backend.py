"""Run the NewsClaw event demo on the real isolated API and SQLite layer.

The public demo must prove the same persistence and route contracts as the
desktop product without ever opening the user's runtime directory.  This
module therefore creates a disposable project root, disables every active
source/background lane, seeds a small audited fixture through ``Database``'s
normal projection methods, and serves the normal desktop UI through
``create_app``.  The only presentation-only change is an always-visible demo
disclosure injected into the HTML response.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

if __package__:
    from scripts.newsclaw_event_demo import EventDemoState, inject_demo_disclosure
else:
    from newsclaw_event_demo import EventDemoState, inject_demo_disclosure

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8431

EVENT_IDS = {
    "demo-event-lpr-2024": "84310000-0000-4000-8000-000000000001",
    "demo-event-rrr-2024": "84310000-0000-4000-8000-000000000002",
    "demo-event-conflict-sample": "84310000-0000-4000-8000-000000000003",
}
CHANGE_IDS = {
    "demo-change-lpr-2024-02": "84310000-0000-4000-8000-000000000101",
    "demo-change-lpr-2024-07": "84310000-0000-4000-8000-000000000102",
    "demo-change-lpr-2024-10": "84310000-0000-4000-8000-000000000103",
    "demo-change-rrr-2024-09": "84310000-0000-4000-8000-000000000201",
    "demo-change-conflict-15": "84310000-0000-4000-8000-000000000301",
    "demo-change-conflict-20": "84310000-0000-4000-8000-000000000302",
}

_SENSITIVE_ENV_NAMES = {
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "EXA_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TAVILY_API_KEY",
    "TUSHARE_TOKEN",
    "YOU_API_KEY",
}

_DEMO_CONFIG = """\
[general]
language = "zh"
data_dir = "data"

[api]
host = "127.0.0.1"
port = 8431

[api.auth]
enabled = false
trust_loopback = true

[network]
mode = "direct"
proxy = ""

[llm]
routing_version = 2
default_chain = []

[llm.embedding]
provider = ""
model = ""
api_key = ""
base_url = ""
fallback_enabled = false
fallback_provider = ""
multimodal_enabled = false

[scheduler]
enabled = false
source_incremental_enabled = false
auto_update_enabled = false

[autostart]
enabled = false
manage_ollama = false

[saved_sync]
auto_sync_enabled = false

[sources.bilibili]
enabled = false

[discovery]
unified_keyword_planner_enabled = false
inspiration_search_enabled = false
multimodal_evaluation_enabled = false
visual_profile_enabled = false
keyframe_enabled = false
danmaku_enabled = false
"""


class DemoSoul:
    """Small inert Soul adapter for UI status routes in the isolated demo."""

    unified_interest_line_enabled = False

    def is_profile_ready(self) -> bool:
        return False

    async def get_profile(self) -> dict[str, object]:
        return {}

    async def get_raw_profile(self) -> dict[str, object]:
        return {}

    def get_overrides(self) -> dict[str, object]:
        return {}

    def bind_dialogue_settlement_queue(self, _queue: object) -> None:
        return


def _write_isolated_runtime_files(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    data_path = root / "data"
    data_path.mkdir(parents=True, exist_ok=True)
    (root / "config.toml").write_text(_DEMO_CONFIG, encoding="utf-8")
    (data_path / "newsclaw_sources.json").write_text(
        json.dumps(
            {
                "settings": {
                    "version": 1,
                    "pool_target_count": 20,
                    "refresh_poll_minutes": 30,
                    "hot_refresh_minutes": 30,
                    "exploration_refresh_minutes": 360,
                    "feedback_threshold": 3,
                    "proactive_push": False,
                    "background_llm": False,
                    "browser_close_behavior": "pause",
                    "retry_max_attempts": 1,
                    "retry_base_seconds": 0.0,
                    "retry_max_seconds": 0.0,
                },
                "sources": [],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


@contextmanager
def isolated_demo_environment(root: Path) -> Iterator[None]:
    """Point all runtime reads at ``root`` while hiding inherited credentials."""

    resolved_root = root.expanduser().resolve()
    _write_isolated_runtime_files(resolved_root)
    governed_names = {
        name
        for name in os.environ
        if name.startswith("OPENBILICLAW_") or name in _SENSITIVE_ENV_NAMES
    }
    governed_names.update(_SENSITIVE_ENV_NAMES)
    governed_names.update({"NEWSCLAW_MODE", "OPENBILICLAW_PROJECT_ROOT"})
    snapshot = {name: os.environ.get(name) for name in governed_names}

    for name in governed_names:
        os.environ.pop(name, None)
    os.environ["NEWSCLAW_MODE"] = "1"
    os.environ["OPENBILICLAW_PROJECT_ROOT"] = str(resolved_root)
    try:
        yield
    finally:
        for name in governed_names:
            os.environ.pop(name, None)
        for name, value in snapshot.items():
            if value is not None:
                os.environ[name] = value


def _fixture_lookup() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    state = EventDemoState()
    events = {str(item["event_id"]): item for item in state.events}
    return events, state.changes, state.evidence


def _projection(
    *,
    change: Mapping[str, Any],
    evidence: Mapping[str, Any],
    supersedes_change_id: str = "",
    source_scope: str | None = None,
    evidence_status: str | None = None,
) -> dict[str, Any]:
    snapshot = evidence.get("evidence_snapshot")
    body_text = str(snapshot.get("body_text") or "") if isinstance(snapshot, Mapping) else ""
    published_at = str(evidence["published_at"])
    retrieved_at = str(evidence["retrieved_at"])
    relation_kind = str(evidence["relation_kind"])
    normalized_snapshot = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    if str(evidence["canonical_url"]).startswith("https://example.invalid/"):
        # A durable conflict requires retained, substantive evidence.  Keep the
        # sample unmistakably synthetic while giving storage enough local text
        # to exercise the same conflict gate as real documents.
        body_text = (
            f"{body_text} 本段为 NewsClaw 隔离演示专用的合成材料，"
            "机构名称、文件编号、补贴比例和发布时间均非真实政策事实；"
            "它只用于验证两份具备正文深度但结论互斥的材料进入系统时，"
            "当前状态会被冻结，双方证据会被完整保留并等待人工核验。"
        )
        normalized_snapshot["body_text"] = body_text
        published_at = str(change["detected_at"])
        retrieved_at = str(change["detected_at"])
        # Each document supports its own candidate statement.  Storage turns
        # the pair into contradictions only after it detects the divergent
        # durable states; pre-labelling both as contradictions would correctly
        # withhold them from the objective gate before that comparison exists.
        relation_kind = "supports_change"
    result = {
        "change_id": CHANGE_IDS[str(change["change_id"])],
        "item_key": str(evidence["evidence_item_key"]),
        "evidence_fingerprint": f"newsclaw-demo-v1:{evidence['membership_id']}",
        "change_type": str(change["change_type"]),
        "previous_state": str(change["previous_state"]),
        "new_state": str(change["new_state"]),
        "fact": str(change["fact"]),
        "summary": str(change["summary"]),
        "evidence_quote": body_text,
        "confidence": 1.0,
        "role": "objective_change",
        "objectivity_status": "confirmed",
        "conflict_status": "none",
        "source_scope": source_scope or str(change["source_scope"]),
        "evidence_status": evidence_status or str(change["evidence_status"]),
        "effective_at": str(change["effective_at"]),
        "effective_precision": str(change["effective_precision"]),
        "content_scope": str(evidence["content_scope"]),
        "media": str(evidence["media"]),
        "provider": str(evidence["provider"]),
        "title": str(evidence["title"]),
        "content_url": str(evidence["canonical_url"]),
        "published_at": published_at,
        "retrieved_at": retrieved_at,
        "evidence_snapshot": normalized_snapshot,
        "relation_kind": relation_kind,
        "is_read": bool(change.get("is_read", False)),
    }
    if supersedes_change_id:
        result["supersedes_change_id"] = supersedes_change_id
        result["supersedes_lifecycle"] = "superseded"
    return result


def _record(
    database: Any,
    *,
    event_id: str,
    change: Mapping[str, Any],
    evidence: Mapping[str, Any],
    supersedes_change_id: str = "",
    source_scope: str | None = None,
    evidence_status: str | None = None,
) -> None:
    database.record_news_event_projection(
        event_id=event_id,
        check_status="changed",
        detected_at=str(change["detected_at"]),
        changes=[
            _projection(
                change=change,
                evidence=evidence,
                supersedes_change_id=supersedes_change_id,
                source_scope=source_scope,
                evidence_status=evidence_status,
            )
        ],
        evidence_completeness="complete",
    )


def seed_demo_database(database: Any) -> None:
    """Seed the three stories through the production projection boundary."""

    events, changes, evidence = _fixture_lookup()

    baselines = {
        "demo-event-lpr-2024": "1年期 3.45%；5年期以上 4.20%",
        "demo-event-rrr-2024": "加权平均存款准备金率 7.0%",
        "demo-event-conflict-sample": "补贴比例待正式核验",
    }
    for fixture_id, event in events.items():
        database.create_news_event(
            event_id=EVENT_IDS[fixture_id],
            event_key=f"isolated-demo:{fixture_id}",
            canonical_title=str(event["canonical_title"]),
            event_type=str(event["event_type"]),
            topic_group=str(event["topic_group"]),
            story_key=str(event["story_key"]),
            entities=tuple(event.get("entities") or ()),
            current_state=baselines[fixture_id],
            first_seen_at=str(event["first_seen_at"]),
            last_seen_at=str(event["first_seen_at"]),
        )

    lpr_rows = list(reversed(changes["demo-event-lpr-2024"]))
    previous_id = ""
    for row in lpr_rows:
        fixture_change_id = str(row["change_id"])
        _record(
            database,
            event_id=EVENT_IDS["demo-event-lpr-2024"],
            change=row,
            evidence=evidence[fixture_change_id][0],
            supersedes_change_id=previous_id,
            source_scope="single_source",
            evidence_status="source_supported",
        )
        previous_id = CHANGE_IDS[fixture_change_id]

    rrr = changes["demo-event-rrr-2024"][0]
    rrr_evidence = evidence[str(rrr["change_id"])]
    _record(
        database,
        event_id=EVENT_IDS["demo-event-rrr-2024"],
        change=rrr,
        evidence=rrr_evidence[0],
        source_scope="single_source",
        evidence_status="source_supported",
    )
    _record(
        database,
        event_id=EVENT_IDS["demo-event-rrr-2024"],
        change=rrr,
        evidence=rrr_evidence[1],
        source_scope="multi_source",
        evidence_status="cross_source_supported",
    )

    conflict_rows = list(reversed(changes["demo-event-conflict-sample"]))
    for row in conflict_rows:
        fixture_change_id = str(row["change_id"])
        _record(
            database,
            event_id=EVENT_IDS["demo-event-conflict-sample"],
            change=row,
            evidence=evidence[fixture_change_id][0],
            source_scope="single_source",
            evidence_status="source_supported",
        )


def _demo_index_response(_request: object) -> HTMLResponse:
    index_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "openbiliclaw"
        / "web"
        / "desktop"
        / "index.html"
    )
    return HTMLResponse(
        inject_demo_disclosure(index_path.read_text(encoding="utf-8")),
        headers={"Cache-Control": "no-store"},
    )


def build_demo_app(root: Path) -> tuple[Starlette, Any]:
    """Build an isolated real-backend app while the demo environment is active."""

    from openbiliclaw.api.app import create_app
    from openbiliclaw.storage.database import Database

    resolved_root = root.expanduser().resolve()
    expected_root = Path(os.environ.get("OPENBILICLAW_PROJECT_ROOT", "")).resolve()
    if os.environ.get("NEWSCLAW_MODE") != "1" or expected_root != resolved_root:
        raise RuntimeError("build_demo_app must run inside isolated_demo_environment(root)")

    database = Database(resolved_root / "data" / "newsclaw-demo.db")
    database.initialize()
    seed_demo_database(database)
    backend = create_app(database=database, soul_engine=DemoSoul())
    app = Starlette(
        routes=[
            Route("/web", _demo_index_response, methods=["GET"]),
            Route("/web/", _demo_index_response, methods=["GET"]),
            Route("/web/index.html", _demo_index_response, methods=["GET"]),
            Mount("/", app=backend),
        ]
    )
    app.state.database = database
    app.state.backend = backend
    app.state.demo_root = resolved_root
    return app, database


def close_demo_resources(app: Starlette, database: Any) -> None:
    """Close the two long-lived SQLite facades created by the demo app."""

    with suppress(Exception):
        database.close()
    backend = getattr(app.state, "backend", None)
    context = getattr(getattr(backend, "state", None), "runtime_context", None)
    memory_manager = getattr(context, "memory_manager", None)
    memory_database = getattr(memory_manager, "_database", None)
    if memory_database is not database:
        with suppress(Exception):
            memory_database.close()


def _run(*, port: int) -> None:
    with tempfile.TemporaryDirectory(
        prefix="newsclaw-event-demo-",
        ignore_cleanup_errors=True,
    ) as temporary_root:
        root = Path(temporary_root)
        with isolated_demo_environment(root):
            app, database = build_demo_app(root)
            print(f"NewsClaw isolated SQLite demo: http://{LOOPBACK_HOST}:{port}/web/")
            print("Real API + disposable DB; no credentials, sources, scheduler, or LLM loaded.")
            try:
                uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host=LOOPBACK_HOST,
                        port=port,
                        workers=1,
                        lifespan="off",
                        log_level="warning",
                    )
                ).run()
            finally:
                close_demo_resources(app, database)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the isolated real NewsClaw event demo")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    _run(port=args.port)


if __name__ == "__main__":
    main()
