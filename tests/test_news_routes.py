from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from openbiliclaw.api.news_routes import (
    DEFAULT_NEWS_SOURCES,
    NewsBootstrapIn,
    NewsRuntime,
    NewsSettingsStore,
    NewsSourceUpdateIn,
    _replace_source,
    backup_existing_database,
)
from openbiliclaw.news.bootstrap import (
    BootstrapResult,
    BootstrapSourceState,
    BootstrapStage,
    CandidateBatch,
    ManualPreferenceEvent,
    ManualProfileSeed,
)
from openbiliclaw.news.identity import stable_request_id
from openbiliclaw.news.models import build_article


class _FakeSoul:
    def __init__(self) -> None:
        self.ready = False
        self.edits: list[dict[str, Any]] = []

    def is_profile_ready(self) -> bool:
        return self.ready

    async def apply_user_edit(self, **kwargs: Any) -> dict[str, object]:
        self.edits.append(dict(kwargs))
        return {"ok": True}


class _FakeDatabase:
    def enqueue_discovery_candidates(self, *_args: object, **_kwargs: object) -> int:
        return 0


class _FakeIngress:
    async def accept_batch(self, *_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(rejected=0)


async def _noop_replenishment(**_kwargs: object) -> None:
    return None


def _runtime(tmp_path: Path) -> NewsRuntime:
    return NewsRuntime(
        data_path=tmp_path,
        database=_FakeDatabase(),
        soul_engine=_FakeSoul(),
        event_ingress=_FakeIngress(),
        request_replenishment=_noop_replenishment,
    )


@pytest.mark.asyncio
async def test_news_runtime_applies_and_reports_effective_settings(tmp_path: Path) -> None:
    applied: list[dict[str, object]] = []

    def apply_settings(settings: Mapping[str, object]) -> None:
        applied.append(dict(settings))

    runtime = NewsRuntime(
        data_path=tmp_path,
        database=_FakeDatabase(),
        soul_engine=_FakeSoul(),
        event_ingress=_FakeIngress(),
        request_replenishment=_noop_replenishment,
        apply_runtime_settings=apply_settings,
    )
    settings = runtime.settings()
    settings.update(
        {
            "pool_target_count": 420,
            "feedback_threshold": 5,
            "proactive_push": False,
            "background_llm": False,
        }
    )

    effective = await runtime.apply_settings(settings)

    assert applied == [effective]
    assert runtime.runtime_state()["effective_settings"] == effective


@pytest.mark.asyncio
async def test_scheduled_refresh_lanes_use_stable_request_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    now = datetime(2026, 8, 17, 8, 30, tzinfo=UTC)
    runtime._write_runtime_state(
        {
            "last_hot_refresh": (now - timedelta(hours=1)).isoformat(),
            "last_exploration_refresh": now.isoformat(),
        }
    )
    calls: list[dict[str, object]] = []

    async def fake_refresh(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "ok": True,
            "request_id": kwargs["request_id"],
            "status": "online",
            "completed_at": "2026-08-17T08:30:01Z",
        }

    monkeypatch.setattr(runtime, "refresh", fake_refresh)

    hot = await runtime._run_due_scheduled_refresh(now=now)

    assert hot["lane"] == "hot"
    assert calls == [
        {
            "request_id": runtime._scheduled_request_id(
                lane="hot",
                now=now,
                interval_minutes=30,
            ),
            "hours": 24,
            "per_source_limit": 80,
        }
    ]

    runtime._merge_runtime_state(
        {
            "last_hot_refresh": now.isoformat(),
            "last_exploration_refresh": (now - timedelta(days=1)).isoformat(),
        }
    )
    exploration = await runtime._run_due_scheduled_refresh(now=now)

    assert exploration["lane"] == "exploration"
    assert calls[-1] == {
        "request_id": runtime._scheduled_request_id(
            lane="exploration",
            now=now,
            interval_minutes=360,
        ),
        "hours": 24 * 7,
        "per_source_limit": 40,
    }
    assert runtime._scheduled_request_id(
        lane="exploration",
        now=now,
        interval_minutes=360,
    ) == runtime._scheduled_request_id(
        lane="exploration",
        now=now + timedelta(minutes=5),
        interval_minutes=360,
    )


@pytest.mark.asyncio
async def test_scheduled_refresh_runs_most_overdue_lane_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    now = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
    runtime._write_runtime_state(
        {
            "last_hot_refresh": (now - timedelta(minutes=30)).isoformat(),
            "last_exploration_refresh": (now - timedelta(days=2)).isoformat(),
        }
    )
    calls: list[dict[str, object]] = []

    async def fake_refresh(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "ok": True,
            "request_id": kwargs["request_id"],
            "status": "online",
            "completed_at": "2026-08-17T12:00:01Z",
        }

    monkeypatch.setattr(runtime, "refresh", fake_refresh)

    result = await runtime._run_due_scheduled_refresh(now=now)

    assert result["lane"] == "exploration"
    assert calls == [
        {
            "request_id": runtime._scheduled_request_id(
                lane="exploration",
                now=now,
                interval_minutes=360,
            ),
            "hours": 24 * 7,
            "per_source_limit": 40,
        }
    ]


def test_public_source_health_aggregates_without_probe_available_field() -> None:
    assert NewsRuntime._aggregate_source_status([{"status": "healthy"}]) == "online"
    assert (
        NewsRuntime._aggregate_source_status([{"status": "healthy"}, {"status": "degraded"}])
        == "degraded"
    )


def test_news_settings_store_defaults_and_last_good_recovery(tmp_path: Path) -> None:
    store = NewsSettingsStore(tmp_path / "newsclaw_sources.json")
    initial = store.load()
    assert len(initial["sources"]) == len(DEFAULT_NEWS_SOURCES)  # type: ignore[arg-type]

    updated = dict(initial)
    updated["settings"] = {**initial["settings"], "pool_target_count": 420}  # type: ignore[dict-item]
    store.save(updated)
    store.path.write_text("{broken", encoding="utf-8")

    recovered = store.load()
    assert recovered["settings"]["pool_target_count"] == 300  # type: ignore[index]
    assert store.path.read_text(encoding="utf-8").startswith("{")


def test_tushare_credential_is_local_masked_and_overrides_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "environment-token")
    runtime = _runtime(tmp_path)

    assert runtime._tushare_token() == "environment-token"
    runtime.set_tushare_token("local-newsclaw-token")

    assert runtime._tushare_token() == "local-newsclaw-token"
    assert runtime._tushare_token_configured() is True
    assert runtime.public_sources()[0]["credential_configured"] is True
    assert "local-newsclaw-token" not in str(runtime.public_sources())

    runtime.clear_tushare_token()
    assert runtime._tushare_token() == "environment-token"


def test_builtin_source_cannot_change_url_but_custom_source_can(tmp_path: Path) -> None:
    store = NewsSettingsStore(tmp_path / "newsclaw_sources.json")
    store.load()

    with pytest.raises(ValueError, match="内置来源"):
        _replace_source(
            store,
            "rss:stats:latest",
            NewsSourceUpdateIn(url="https://example.com/feed.xml"),
        )

    current = store.load()
    rows = current["sources"]
    assert isinstance(rows, list)
    rows.append(
        {
            "source_id": "rss:custom:test",
            "kind": "rss",
            "media": "测试源",
            "provider": "自定义 RSS/Atom",
            "url": "https://example.com/feed.xml",
            "enabled": True,
            "weight": 1.0,
            "refresh_minutes": 60,
            "custom": True,
        }
    )
    store.save(current)
    changed = _replace_source(
        store,
        "rss:custom:test",
        NewsSourceUpdateIn(url="https://example.org/rss.xml"),
    )
    assert changed.url == "https://example.org/rss.xml"


def test_news_candidate_carries_evidence_level_and_news_identity(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    article = build_article(
        media="国家统计局",
        provider="官方 RSS/Atom",
        title="7 月国民经济运行情况",
        canonical_url="https://www.stats.gov.cn/example.html",
        published_at=datetime(2026, 8, 17, 1, tzinfo=UTC),
        source_identifier="nbs-1",
    )

    candidate = runtime._candidate(article)

    assert candidate.source_platform == "news"
    assert candidate.content_type == "article"
    assert candidate.content_id == article.article_id
    assert candidate.raw_payload["content_scope"] == "title_only"
    assert "标题级信息" in str(candidate.raw_payload["evidence_notice"])


@pytest.mark.asyncio
async def test_manual_preference_is_added_then_weight_pinned(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    event = ManualPreferenceEvent(
        event_id="nev_manual_one",
        request_id="nrq_manual_one",
        event_type="manual_interest",
        value="宏观经济",
    )

    await runtime._write_manual_preference(event)

    soul = runtime.soul_engine
    assert [edit["op"] for edit in soul.edits] == ["add", "set"]
    assert all(edit["target"] == "likes" for edit in soul.edits)
    assert soul.edits[-1]["weight"] == 1.0


def test_manual_evidence_uses_bootstrap_event_ids(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    interest = ManualPreferenceEvent(
        event_id="nev_interest_one",
        request_id="nrq_bootstrap_one",
        event_type="manual_interest",
        value="科技产业",
    )
    avoid = ManualPreferenceEvent(
        event_id="nev_avoid_one",
        request_id="nrq_bootstrap_one",
        event_type="manual_avoid",
        value="娱乐八卦",
    )
    seed = ManualProfileSeed(
        seed_id="nev_seed_one",
        request_id="nrq_bootstrap_one",
        interests=("科技产业",),
        avoid_terms=("娱乐八卦",),
        preference_events=(interest, avoid),
    )

    events = runtime._manual_events(seed)

    assert [event["ingest_key"] for event in events] == [interest.event_id, avoid.event_id]
    assert events[0]["event_type"] == "search"
    assert events[1]["metadata"]["feedback_type"] == "dislike"


def test_bootstrap_status_keeps_evidence_summary_without_article_bodies(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    preference = ManualPreferenceEvent(
        event_id="nev_interest_one",
        request_id="nrq_bootstrap_one",
        event_type="manual_interest",
        value="宏观经济",
    )
    seed = ManualProfileSeed(
        seed_id="nev_seed_one",
        request_id="nrq_bootstrap_one",
        interests=("宏观经济",),
        avoid_terms=(),
        preference_events=(preference,),
    )
    article = build_article(
        media="国家统计局",
        provider="官方 RSS/Atom",
        title="经济运行情况",
        body="不应被复制进轮询状态的长正文",
        content_scope="summary",
        source_identifier="nbs-summary-one",
    )
    result = BootstrapResult(
        request_id="nrq_bootstrap_one",
        status="completed",
        stages=(BootstrapStage("validate_preferences", "completed", 1, 1),),
        preference_events=(preference,),
        profile_seed=seed,
        candidate_batch=CandidateBatch(
            request_id="nrq_bootstrap_one",
            event_id="nev_batch_one",
            articles=(article,),
            source_states=(BootstrapSourceState("rss:stats:latest", "国家统计局", "succeeded", 1),),
        ),
    )

    compact = runtime._compact_bootstrap_result(result)

    serialized = str(compact)
    assert "不应被复制进轮询状态的长正文" not in serialized
    assert compact["candidate_batch"]["article_count"] == 1  # type: ignore[index]
    assert compact["candidate_batch"]["content_scope_counts"] == {  # type: ignore[index]
        "summary": 1
    }


def test_legacy_bootstrap_status_is_compacted_on_read(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime._write_bootstrap_state(
        {
            "status": "completed",
            "candidate_batch": {
                "request_id": "nrq_old",
                "event_id": "nev_old",
                "articles": [
                    {"content_scope": "full_text", "body": "large legacy body"},
                    {"content_scope": "title_only", "body": ""},
                ],
                "source_states": [],
            },
            "profile_seed": {
                "preference_events": [{"event_id": "nev_preference"}],
            },
        }
    )

    state = runtime.bootstrap_state()

    serialized = str(state)
    assert "large legacy body" not in serialized
    assert state["candidate_batch"]["article_count"] == 2  # type: ignore[index]
    assert state["profile_seed"]["preference_event_ids"] == [  # type: ignore[index]
        "nev_preference"
    ]


@pytest.mark.asyncio
async def test_failed_bootstrap_can_retry_after_base_profile_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.soul_engine.ready = True
    runtime._write_bootstrap_state(
        {
            "request_id": "nrq_previous",
            "status": "failed",
            "profile_ready": True,
        }
    )

    async def _complete_retry(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(runtime, "_run_bootstrap", _complete_retry)
    state = runtime.start_bootstrap(NewsBootstrapIn(interests=["宏观", "投资", "创作"]))

    assert state["status"] == "running"
    assert state["profile_ready"] is True
    assert runtime._bootstrap_task is not None
    await runtime._bootstrap_task


def test_bootstrap_status_recovers_orphaned_running_state_after_restart(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime._write_bootstrap_state(
        {
            "request_id": "bootstrap-restart-001",
            "input_digest": "nrq_input_digest",
            "status": "running",
            "running": True,
            "started_at": "2026-08-17T08:00:00Z",
        }
    )

    restarted = _runtime(tmp_path)
    state = restarted.bootstrap_state()

    assert state["status"] == "failed"
    assert state["running"] is False
    assert state["recovered_after_restart"] is True
    assert "retry is safe" in str(state["detail"])
    assert (
        json.loads(restarted.bootstrap_state_path.read_text(encoding="utf-8"))["status"] == "failed"
    )


@pytest.mark.asyncio
async def test_bootstrap_start_replays_same_command_and_rejects_request_id_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    release = asyncio.Event()

    async def _hold_bootstrap(**_kwargs: object) -> None:
        await release.wait()

    monkeypatch.setattr(runtime, "_run_bootstrap", _hold_bootstrap)
    payload = NewsBootstrapIn(
        interests=[" macro ", "Investing", "Creation"],
        request_id="bootstrap-command-001",
    )

    first = runtime.start_bootstrap(payload)
    replay = runtime.start_bootstrap(payload)

    assert first["status"] == "running"
    assert first["duplicate"] is False
    assert replay["request_id"] == first["request_id"]
    assert replay["duplicate"] is True
    assert replay["replayed"] is True
    with pytest.raises(RuntimeError, match="different onboarding choices"):
        runtime.start_bootstrap(
            NewsBootstrapIn(
                interests=["Macro", "Investing", "Different"],
                request_id="bootstrap-command-001",
            )
        )

    release.set()
    assert runtime._bootstrap_task is not None
    await runtime._bootstrap_task


def test_completed_bootstrap_command_replays_without_overwriting_profile(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.soul_engine.ready = True
    interests = ("Creation", "Investing", "Macro")
    input_digest = stable_request_id("news-bootstrap-input", interests, ())
    runtime._write_bootstrap_state(
        {
            "request_id": "bootstrap-completed-001",
            "input_digest": input_digest,
            "status": "completed",
            "running": False,
            "profile_ready": True,
        }
    )

    replay = runtime.start_bootstrap(
        NewsBootstrapIn(
            interests=list(reversed(interests)),
            request_id="bootstrap-completed-001",
        )
    )

    assert replay["status"] == "completed"
    assert replay["duplicate"] is True
    assert replay["replayed"] is True


def test_backup_existing_database_keeps_source_and_creates_timestamp_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "old.db"
    source.write_bytes(b"sqlite-evidence")

    backup = backup_existing_database(source, tmp_path / "backups")

    assert source.read_bytes() == b"sqlite-evidence"
    assert backup.read_bytes() == b"sqlite-evidence"
    assert backup.name.startswith("old-")
    assert backup.suffix == ".bak"


def test_news_source_serialization_is_credential_free() -> None:
    serialized = [asdict(source) for source in DEFAULT_NEWS_SOURCES]
    assert all("token" not in key.casefold() for row in serialized for key in row)
