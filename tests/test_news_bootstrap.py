from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openbiliclaw.news.bootstrap import (
    CandidateBatch,
    ManualPreferenceEvent,
    ManualProfileSeed,
    NewsBootstrapCoordinator,
    SourceRegistry,
)
from openbiliclaw.news.errors import SourcePermissionDenied
from openbiliclaw.news.models import Article, build_article
from openbiliclaw.news.protocol import FetchWindow, SourceProbe
from openbiliclaw.news.reliability import RetryPolicy, SourceRunner, SourceTaskStore


class BootstrapAdapter:
    provider = "fixture"

    def __init__(
        self,
        source_id: str,
        media: str,
        outcome: list[Article] | Exception,
    ) -> None:
        self.source_id = source_id
        self.media = media
        self.outcome = outcome
        self.calls = 0

    async def fetch(self, _window: FetchWindow) -> list[Article]:
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def probe(self) -> SourceProbe:
        return SourceProbe(
            source_id=self.source_id,
            media=self.media,
            provider=self.provider,
            status="available",
            available=True,
            checked_at="2026-08-17T00:00:00Z",
        )


def _article(media: str, *, full: bool) -> Article:
    return build_article(
        media=media,
        provider="fixture",
        title="同一条宏观新闻",
        body="完整正文" if full else "",
        canonical_url="https://example.com/shared",
        published_at="2026-08-17T08:00:00Z",
        retrieved_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        content_scope="full_text" if full else "title_only",
        source_identifier=media,
    )


@pytest.mark.asyncio
async def test_bootstrap_seeds_only_manual_events_and_publishes_deduped_pool(tmp_path) -> None:
    first = BootstrapAdapter("rss:a", "官方A", [_article("官方A", full=False)])
    second = BootstrapAdapter("tushare:b", "官方B", [_article("官方B", full=True)])
    registry = SourceRegistry([first, second])
    events: list[ManualPreferenceEvent] = []
    profile_seeds: list[ManualProfileSeed] = []
    batches: list[CandidateBatch] = []
    runner = SourceRunner(
        SourceTaskStore(tmp_path / "tasks.db"),
        policy=RetryPolicy(max_attempts=1),
    )
    coordinator = NewsBootstrapCoordinator(
        registry=registry,
        runner=runner,
        write_preference=events.append,
        publish_candidates=batches.append,
        profile_initializer=profile_seeds.append,
        clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
    )

    result = await coordinator.bootstrap(
        interests=["宏观", "科技", "创作", "宏观"],
        avoid_terms=["明星八卦"],
        request_id="onboarding-1",
    )

    assert result.status == "completed"
    assert [stage.name for stage in result.stages] == [
        "validate_preferences",
        "seed_manual_memory",
        "fetch_initial_news",
        "publish_candidate_pool",
    ]
    assert all(stage.status == "completed" for stage in result.stages)
    assert [(event.event_type, event.value) for event in events] == [
        ("manual_interest", "创作"),
        ("manual_interest", "宏观"),
        ("manual_interest", "科技"),
        ("manual_avoid", "明星八卦"),
    ]
    assert all(event.source == "manual" and event.confidence == 1.0 for event in events)
    assert profile_seeds == [result.profile_seed]
    assert profile_seeds[0].interests == ("创作", "宏观", "科技")
    assert profile_seeds[0].avoid_terms == ("明星八卦",)
    assert profile_seeds[0].schema_version == "news-manual-v1"
    assert result.stages[1].total == len(events) + 1
    assert batches == [result.candidate_batch]
    assert len(batches[0].articles) == 1
    assert batches[0].articles[0].media == "官方B"


@pytest.mark.asyncio
async def test_bootstrap_ids_are_stable_across_retry_without_profile_import(tmp_path) -> None:
    adapter = BootstrapAdapter("rss:a", "官方A", [])
    recorded: list[ManualPreferenceEvent] = []
    coordinator = NewsBootstrapCoordinator(
        registry=SourceRegistry([adapter]),
        runner=SourceRunner(
            SourceTaskStore(tmp_path / "tasks.db"),
            policy=RetryPolicy(max_attempts=1),
        ),
        write_preference=recorded.append,
        publish_candidates=lambda _batch: None,
        clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
    )

    first = await coordinator.bootstrap(interests=["科技", "宏观", "投资"])
    first_ids = [event.event_id for event in first.preference_events]
    second = await coordinator.bootstrap(interests=["投资", "科技", "宏观"])

    assert second.request_id == first.request_id
    assert [event.event_id for event in second.preference_events] == first_ids
    assert second.candidate_batch.event_id == first.candidate_batch.event_id
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_one_failed_source_degrades_bootstrap_without_hiding_success(tmp_path) -> None:
    good = BootstrapAdapter("rss:good", "官方源", [_article("官方源", full=True)])
    denied = BootstrapAdapter(
        "tushare:denied",
        "受限源",
        SourcePermissionDenied("endpoint permission denied"),
    )
    batches: list[CandidateBatch] = []
    coordinator = NewsBootstrapCoordinator(
        registry=SourceRegistry([good, denied]),
        runner=SourceRunner(
            SourceTaskStore(tmp_path / "tasks.db"),
            policy=RetryPolicy(max_attempts=1),
        ),
        write_preference=lambda _event: None,
        publish_candidates=batches.append,
        clock=lambda: datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
    )

    result = await coordinator.bootstrap(interests=["宏观", "投资", "科技"])

    assert result.status == "degraded"
    assert result.stages[2].status == "degraded"
    assert {state.status for state in result.candidate_batch.source_states} == {
        "succeeded",
        "permission_denied",
    }
    assert len(batches[0].articles) == 1


@pytest.mark.asyncio
async def test_bootstrap_input_and_callback_failures_are_explicit(tmp_path) -> None:
    coordinator = NewsBootstrapCoordinator(
        registry=SourceRegistry(),
        runner=SourceRunner(
            SourceTaskStore(tmp_path / "tasks.db"),
            policy=RetryPolicy(max_attempts=1),
        ),
        write_preference=lambda _event: (_ for _ in ()).throw(RuntimeError("write failed")),
        publish_candidates=lambda _batch: None,
    )
    with pytest.raises(ValueError, match="between 3 and 10"):
        await coordinator.bootstrap(interests=["宏观", "科技"])
    with pytest.raises(ValueError, match="both an interest"):
        await coordinator.bootstrap(
            interests=["宏观", "科技", "投资"],
            avoid_terms=["宏观"],
        )

    failed = await coordinator.bootstrap(interests=["宏观", "科技", "投资"])
    assert failed.status == "failed"
    assert len(failed.stages) == 4
    assert failed.stages[1].status == "failed"
    assert failed.stages[2].detail.startswith("Skipped")


@pytest.mark.asyncio
async def test_profile_initializer_failure_stops_before_event_writes(tmp_path) -> None:
    events: list[ManualPreferenceEvent] = []
    coordinator = NewsBootstrapCoordinator(
        registry=SourceRegistry(),
        runner=SourceRunner(
            SourceTaskStore(tmp_path / "tasks.db"),
            policy=RetryPolicy(max_attempts=1),
        ),
        profile_initializer=lambda _seed: (_ for _ in ()).throw(
            RuntimeError("profile init failed")
        ),
        write_preference=events.append,
        publish_candidates=lambda _batch: None,
    )

    result = await coordinator.bootstrap(interests=["宏观", "科技", "投资"])

    assert result.status == "failed"
    assert result.stages[1].status == "failed"
    assert result.stages[1].completed == 0
    assert events == []
