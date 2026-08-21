from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from openbiliclaw.news.errors import (
    SourceLeaseBusy,
    SourcePermissionDenied,
    SourceRateLimited,
    SourceSchemaError,
    SourceTimedOut,
)
from openbiliclaw.news.models import Article, build_article
from openbiliclaw.news.protocol import FetchWindow, SourceProbe
from openbiliclaw.news.reliability import RetryPolicy, SourceRunner, SourceTaskStore


class SequenceAdapter:
    source_id = "fixture:source"
    media = "测试媒体"
    provider = "fixture"

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def fetch(self, _window: FetchWindow) -> list[Article]:
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)  # type: ignore[arg-type]

    async def probe(self) -> SourceProbe:
        return SourceProbe(
            source_id=self.source_id,
            media=self.media,
            provider=self.provider,
            status="available",
            available=True,
            checked_at="2026-08-17T00:00:00Z",
        )


class SlowAdapter(SequenceAdapter):
    async def fetch(self, _window: FetchWindow) -> list[Article]:
        self.calls += 1
        await asyncio.sleep(1)
        return []


def _window() -> FetchWindow:
    return FetchWindow(
        datetime(2026, 8, 17, 0, 0, tzinfo=UTC),
        datetime(2026, 8, 17, 23, 59, tzinfo=UTC),
        20,
    )


def _article(title: str, source_identifier: str) -> Article:
    return build_article(
        media="测试媒体",
        provider="fixture",
        title=title,
        body="正文",
        published_at="2026-08-17T08:00:00Z",
        retrieved_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        content_scope="full_text",
        source_identifier=source_identifier,
    )


@pytest.mark.asyncio
async def test_transient_retry_dedupes_and_persists_idempotent_replay(tmp_path) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    first = _article("同一条新闻", "1")
    duplicate = _article("同一条新闻", "2")
    adapter = SequenceAdapter(
        [SourceRateLimited("slow", retry_after_seconds=0), [first, duplicate]]
    )
    store = SourceTaskStore(tmp_path / "tasks.db")
    runner = SourceRunner(
        store,
        policy=RetryPolicy(max_attempts=2, base_backoff_seconds=0.1),
        sleep=fake_sleep,
    )

    result = await runner.run(adapter, _window(), request_id="refresh-1", event_id="event-1")
    replay = await runner.run(adapter, _window(), request_id="refresh-1", event_id="event-1")

    assert result.status == "succeeded"
    assert result.attempt_count == 2
    assert result.duplicate_count == 1
    assert result.articles == (first,)
    assert sleeps == [0.1]
    assert replay.replayed is True
    assert replay.articles == (first,)
    assert adapter.calls == 2
    health = store.get_health(adapter.source_id)
    assert health is not None and health.status == "healthy"


@pytest.mark.asyncio
async def test_permission_failure_is_terminal_without_retry_and_marks_unavailable(tmp_path) -> None:
    adapter = SequenceAdapter([SourcePermissionDenied("news endpoint access denied")])
    store = SourceTaskStore(tmp_path / "tasks.db")
    runner = SourceRunner(store, policy=RetryPolicy(max_attempts=3, base_backoff_seconds=0))

    result = await runner.run(adapter, _window(), request_id="denied-1")

    assert result.status == "permission_denied"
    assert result.attempt_count == 1
    assert adapter.calls == 1
    health = store.get_health(adapter.source_id)
    assert health is not None
    assert health.status == "unavailable"
    assert health.available is False


@pytest.mark.asyncio
async def test_timeout_and_exhausted_rate_limit_have_distinct_terminal_states(tmp_path) -> None:
    timeout_store = SourceTaskStore(tmp_path / "timeout.db")
    timeout_result = await SourceRunner(
        timeout_store,
        policy=RetryPolicy(max_attempts=1, timeout_seconds=0.01, base_backoff_seconds=0),
    ).run(SlowAdapter([[]]), _window(), request_id="timeout-1")
    assert timeout_result.status == "timed_out"

    rate_adapter = SequenceAdapter([SourceRateLimited("429", retry_after_seconds=0)])
    rate_result = await SourceRunner(
        SourceTaskStore(tmp_path / "rate.db"),
        policy=RetryPolicy(max_attempts=2, base_backoff_seconds=0),
    ).run(rate_adapter, _window(), request_id="rate-1")
    assert rate_result.status == "rate_limited"
    assert rate_adapter.calls == 2


@pytest.mark.asyncio
async def test_terminal_error_redacts_secrets_before_persistence(tmp_path) -> None:
    adapter = SequenceAdapter([SourceSchemaError("token=super-secret upstream changed")])
    store = SourceTaskStore(tmp_path / "tasks.db")
    result = await SourceRunner(store, policy=RetryPolicy(max_attempts=1)).run(
        adapter,
        _window(),
        request_id="secret-1",
    )

    assert result.status == "failed"
    task = store.get_task(adapter.source_id, "secret-1")
    assert task is not None
    assert "super-secret" not in task.error_message
    assert "REDACTED" in task.error_message


def test_unexpired_task_lease_blocks_second_worker_and_expired_lease_is_reclaimable(
    tmp_path,
) -> None:
    store = SourceTaskStore(tmp_path / "tasks.db")
    first, acquired = store.acquire(
        source_id="fixture:source",
        request_id="request-1",
        event_id="event-1",
        owner="worker-a",
        lease_seconds=60,
        now_epoch=100,
    )
    assert acquired is True
    blocked, acquired = store.acquire(
        source_id="fixture:source",
        request_id="request-1",
        event_id="event-1",
        owner="worker-b",
        lease_seconds=60,
        now_epoch=101,
    )
    assert acquired is False
    assert blocked.lease_owner == "worker-a"

    reclaimed, acquired = store.acquire(
        source_id="fixture:source",
        request_id="request-1",
        event_id="event-1",
        owner="worker-b",
        lease_seconds=60,
        now_epoch=161,
    )
    assert acquired is True
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.attempt == first.attempt + 1

    with pytest.raises(SourceLeaseBusy):
        store.complete(
            source_id="fixture:source",
            request_id="request-1",
            owner="worker-a",
            status="failed",
            attempt=1,
            error=SourceTimedOut("late worker"),
        )


@pytest.mark.asyncio
async def test_expired_lease_past_retry_budget_becomes_terminal_without_unbound_attempt(
    tmp_path,
) -> None:
    store = SourceTaskStore(tmp_path / "tasks.db")
    store.acquire(
        source_id="fixture:source",
        request_id="expired-1",
        event_id="event-1",
        owner="crashed-worker",
        lease_seconds=1,
        now_epoch=0,
    )
    adapter = SequenceAdapter([[]])

    result = await SourceRunner(
        store,
        policy=RetryPolicy(max_attempts=1, base_backoff_seconds=0),
    ).run(adapter, _window(), request_id="expired-1")

    assert result.status == "timed_out"
    assert result.attempt_count == 2
    assert adapter.calls == 0
