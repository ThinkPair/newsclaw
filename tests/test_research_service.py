from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from openbiliclaw.research.errors import ResearchProviderError, ResearchValidationError
from openbiliclaw.research.models import (
    ProviderResult,
    ProviderUsage,
    ResearchEvidence,
    ResearchTopic,
)
from openbiliclaw.research.service import ResearchService
from openbiliclaw.research.store import ResearchStore

if TYPE_CHECKING:
    from pathlib import Path

    from openbiliclaw.research.models import ExtractRequest, SearchRequest


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 22, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _Provider:
    def __init__(
        self,
        results: list[ProviderResult | Exception],
        *,
        usage_result: ProviderUsage | None = None,
    ) -> None:
        self.results = results
        self.usage_result = usage_result or ProviderUsage()
        self.search_calls = 0
        self.extract_calls = 0
        self.usage_calls = 0

    def _next(self) -> ProviderResult:
        item = self.results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def search(self, _request: SearchRequest) -> ProviderResult:
        self.search_calls += 1
        return self._next()

    async def extract(self, _request: ExtractRequest) -> ProviderResult:
        self.extract_calls += 1
        return self._next()

    async def usage(self) -> ProviderUsage:
        self.usage_calls += 1
        return self.usage_result


def _evidence(content: str, *, url: str = "https://example.com/a") -> ResearchEvidence:
    return ResearchEvidence(
        url=url,
        title="Public evidence",
        content=content,
        source_domain="example.com",
        score=0.9,
    )


def _service(tmp_path: Path, provider: _Provider, clock: _Clock) -> ResearchService:
    return ResearchService(
        store=ResearchStore(tmp_path / "newsclaw_research.db"),
        api_key_provider=lambda: "tvly-test-secret",
        provider_factory=lambda _api_key: provider,
        now_provider=clock,
    )


@pytest.mark.asyncio
async def test_probe_uses_usage_endpoint_without_search_or_extract(tmp_path: Path) -> None:
    provider = _Provider(
        [],
        usage_result=ProviderUsage(key_usage=4, key_limit=100, account_plan="Researcher"),
    )
    service = _service(tmp_path, provider, _Clock())

    usage = await service.probe()

    assert usage.key_usage == 4
    assert usage.key_limit == 100
    assert provider.usage_calls == 1
    assert provider.search_calls == 0
    assert provider.extract_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("topic", "still_fresh", "expired"),
    [
        ("news", timedelta(minutes=29), timedelta(minutes=31)),
        ("finance", timedelta(minutes=14), timedelta(minutes=16)),
        ("general", timedelta(hours=23), timedelta(hours=25)),
    ],
)
async def test_search_reuses_non_empty_query_until_topic_ttl_expires(
    tmp_path: Path,
    topic: str,
    still_fresh: timedelta,
    expired: timedelta,
) -> None:
    clock = _Clock()
    provider = _Provider(
        [
            ProviderResult("request-1", (_evidence("first"),), 1),
            ProviderResult("request-2", (_evidence("second"),), 1),
        ]
    )
    service = _service(tmp_path, provider, clock)
    typed_topic = cast("ResearchTopic", topic)

    first = await service.search(query="Public policy", topic=typed_topic)
    clock.now += still_fresh
    cached = await service.search(query="  Public   policy ", topic=typed_topic)
    clock.now = datetime(2026, 8, 22, 8, 0, tzinfo=UTC) + expired
    refreshed = await service.search(query="Public policy", topic=typed_topic)

    assert first.cache_hit is False
    assert cached.cache_hit is True
    assert cached.run_id == first.run_id
    assert refreshed.run_id != first.run_id
    assert provider.search_calls == 2


@pytest.mark.asyncio
async def test_empty_and_failed_results_are_recorded_but_never_cached(tmp_path: Path) -> None:
    clock = _Clock()
    provider = _Provider(
        [
            ProviderResult("empty-1", (), 1),
            ProviderResult("empty-2", (), 1),
            ResearchProviderError("provider unavailable"),
            ResearchProviderError("provider unavailable"),
        ]
    )
    service = _service(tmp_path, provider, clock)

    assert not (await service.search(query="empty topic")).evidence
    assert not (await service.search(query="empty topic")).evidence
    with pytest.raises(ResearchProviderError):
        await service.search(query="failing topic")
    with pytest.raises(ResearchProviderError):
        await service.search(query="failing topic")

    assert provider.search_calls == 4
    assert service.store.status_counts() == {"empty": 2, "failed": 2}
    with sqlite3.connect(tmp_path / "newsclaw_research.db") as connection:
        empty_usage = connection.execute(
            """
            SELECT provider_request_id, credits_used
            FROM research_runs
            WHERE status = 'empty'
            ORDER BY completed_at, run_id
            """
        ).fetchall()
    assert {tuple(row) for row in empty_usage} == {("empty-1", 1.0), ("empty-2", 1.0)}


@pytest.mark.asyncio
async def test_search_and_extract_apply_local_character_and_url_budgets(tmp_path: Path) -> None:
    clock = _Clock()
    search_rows = tuple(
        _evidence("x" * 1_000, url=f"https://example.com/{index}") for index in range(10)
    )
    provider = _Provider(
        [
            ProviderResult("search-1", search_rows),
            ProviderResult(
                "extract-1",
                (
                    _evidence("a" * 5_000, url="https://example.com/a"),
                    _evidence("b" * 5_000, url="https://example.com/b"),
                    _evidence("c" * 5_000, url="https://example.com/c"),
                ),
            ),
        ]
    )
    service = _service(tmp_path, provider, clock)

    search = await service.search(query="budget", max_results=8)
    extracted = await service.extract(
        urls=["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        query="relevant facts",
    )

    assert len(search.evidence) <= 8
    assert all(len(item.content) <= 600 for item in search.evidence)
    assert sum(len(item.content) for item in search.evidence) <= 3_000
    assert all(len(item.content) <= 1_500 for item in extracted.evidence)
    assert sum(len(item.content) for item in extracted.evidence) <= 4_500


@pytest.mark.asyncio
async def test_extract_cache_identity_ignores_url_order(tmp_path: Path) -> None:
    provider = _Provider(
        [
            ProviderResult(
                "extract-1",
                (
                    _evidence("a", url="https://example.com/a"),
                    _evidence("b", url="https://example.com/b"),
                ),
            )
        ]
    )
    service = _service(tmp_path, provider, _Clock())

    first = await service.extract(
        urls=["https://example.com/a", "https://example.com/b"],
        query="relevant facts",
    )
    replay = await service.extract(
        urls=["https://example.com/b", "https://example.com/a"],
        query="relevant facts",
    )

    assert first.cache_hit is False
    assert replay.cache_hit is True
    assert provider.extract_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "email me at person@example.com",
        "phone 13800138000",
        r"read C:\\Users\\alice\\secret.txt",
        "use sk-abcdefghijklmnopqrstuvwxyz123456",
        "open http://localhost:8080/private",
        "open http://192.168.1.5/private",
    ],
)
async def test_privacy_guard_rejects_obvious_private_queries(
    tmp_path: Path,
    query: str,
) -> None:
    provider = _Provider([])
    service = _service(tmp_path, provider, _Clock())

    with pytest.raises(ResearchValidationError):
        await service.search(query=query)

    assert provider.search_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/secret.txt",
        "http://localhost/private",
        "http://127.0.0.1/private",
        "http://10.0.0.2/private",
        "http://intranet/private",
        "https://user:password@example.com/private",
        "https://example.com/report?api_key=supersecretvalue",
        "https://example.com/report?token=abc",
        "https://example.com/report?key=abc",
        "https://example.com/report?auth=abc",
        "https://example.com/report?password=abc",
        "https://example.com/report?sig=abc",
        "https://example.com/report?X-Amz-Signature=abc",
        "https://example.com/person@example.com",
        "https://example.com/13800138000",
        "https://example.com/C%3A%5CUsers%5Calice%5Csecret.txt",
        "http://example.com:bad/report",
    ],
)
async def test_extract_rejects_non_public_or_sensitive_urls(
    tmp_path: Path,
    url: str,
) -> None:
    provider = _Provider([])
    service = _service(tmp_path, provider, _Clock())

    with pytest.raises(ResearchValidationError):
        await service.extract(urls=[url], query="public question")

    assert provider.extract_calls == 0


@pytest.mark.asyncio
async def test_changed_page_content_creates_a_new_evidence_snapshot(tmp_path: Path) -> None:
    clock = _Clock()
    provider = _Provider(
        [
            ProviderResult("request-1", (_evidence("version one"),)),
            ProviderResult("request-2", (_evidence("version two"),)),
        ]
    )
    service = _service(tmp_path, provider, clock)

    await service.search(query="versioned page", refresh=True)
    await service.search(query="versioned page", refresh=True)

    with sqlite3.connect(tmp_path / "newsclaw_research.db") as connection:
        run_count = connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
        evidence_count = connection.execute("SELECT COUNT(*) FROM research_evidence").fetchone()[0]
    assert run_count == 2
    assert evidence_count == 2


@pytest.mark.asyncio
async def test_relevance_score_is_frozen_per_run_when_evidence_is_reused(tmp_path: Path) -> None:
    clock = _Clock()
    first_evidence = ResearchEvidence(
        url="https://example.com/shared",
        title="Shared page",
        content="unchanged content",
        source_domain="example.com",
        score=0.2,
    )
    second_evidence = ResearchEvidence(
        url="https://example.com/shared",
        title="Shared page",
        content="unchanged content",
        source_domain="example.com",
        score=0.9,
    )
    provider = _Provider(
        [
            ProviderResult("request-low", (first_evidence,)),
            ProviderResult("request-high", (second_evidence,)),
        ]
    )
    service = _service(tmp_path, provider, clock)

    low = await service.search(query="first question")
    high = await service.search(query="second question")
    low_replayed = await service.search(query="first question")

    assert low.evidence[0].score == 0.2
    assert high.evidence[0].score == 0.9
    assert low_replayed.cache_hit is True
    assert low_replayed.evidence[0].score == 0.2
    with sqlite3.connect(tmp_path / "newsclaw_research.db") as connection:
        evidence_count = connection.execute("SELECT COUNT(*) FROM research_evidence").fetchone()[0]
        scores = {
            row[0]
            for row in connection.execute(
                "SELECT provider_score FROM research_run_evidence"
            ).fetchall()
        }
    assert evidence_count == 1
    assert scores == {0.2, 0.9}
