"""Local-first orchestration, privacy checks, budgets, and cache policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from openbiliclaw.research.errors import (
    ResearchConfigurationError,
    ResearchProviderError,
    ResearchValidationError,
)
from openbiliclaw.research.models import (
    ExtractRequest,
    ProviderResult,
    ProviderUsage,
    ResearchEvidence,
    ResearchResult,
    ResearchTimeRange,
    ResearchTopic,
    SearchRequest,
)
from openbiliclaw.research.privacy import normalize_public_url, normalize_query
from openbiliclaw.research.provider import ResearchProvider, TavilyProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from openbiliclaw.research.store import ResearchStore

SEARCH_DEFAULT_RESULTS = 5
SEARCH_MAX_RESULTS = 8
SEARCH_SNIPPET_CHARS = 600
SEARCH_TOTAL_CHARS = 3_000
EXTRACT_DEFAULT_CHUNKS = 2
EXTRACT_MAX_CHUNKS = 3
EXTRACT_MAX_URLS = 3
EXTRACT_ITEM_CHARS = 1_500
EXTRACT_TOTAL_CHARS = 4_500

_CACHE_TTLS = {
    "news": timedelta(minutes=30),
    "finance": timedelta(minutes=15),
    "general": timedelta(hours=24),
}


class ResearchService:
    """Run bounded research and retain only locally clipped evidence."""

    def __init__(
        self,
        *,
        store: ResearchStore,
        api_key_provider: Callable[[], str],
        provider_factory: Callable[[str], ResearchProvider] = TavilyProvider,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._api_key_provider = api_key_provider
        self._provider_factory = provider_factory
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()

    @staticmethod
    def _request_key(operation: str, parameters: dict[str, object]) -> str:
        wire = json.dumps(parameters, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(f"{operation}\0{wire}".encode()).hexdigest()
        return f"rrq_{digest}"

    @staticmethod
    def _run_id() -> str:
        return f"rrun_{uuid.uuid4().hex}"

    def _provider(self) -> ResearchProvider:
        api_key = self._api_key_provider().strip()
        if not api_key:
            raise ResearchConfigurationError("请先在新闻爪桌面设置中配置 Tavily API Key")
        return self._provider_factory(api_key)

    async def probe(self) -> ProviderUsage:
        """Validate Tavily credentials without consuming a Search credit."""

        return await self._provider().usage()

    @staticmethod
    def _bounded_evidence(
        evidence: Sequence[ResearchEvidence],
        *,
        item_chars: int,
        total_chars: int,
        item_limit: int,
    ) -> tuple[ResearchEvidence, ...]:
        bounded: list[ResearchEvidence] = []
        seen: set[tuple[str, str]] = set()
        remaining = total_chars
        for item in evidence:
            if len(bounded) >= item_limit or remaining <= 0:
                break
            try:
                url = normalize_public_url(item.url)
            except (ResearchValidationError, ValueError):
                continue
            title = " ".join(item.title.split()).strip()[:300]
            content = " ".join(item.content.split()).strip()
            allowed = min(item_chars, remaining)
            content = content[:allowed]
            if not content:
                continue
            identity = (url, hashlib.sha256(content.encode()).hexdigest())
            if identity in seen:
                continue
            seen.add(identity)
            bounded.append(
                ResearchEvidence(
                    url=url,
                    title=title,
                    content=content,
                    source_domain=(urlsplit(url).hostname or "").casefold(),
                    published_at=" ".join(item.published_at.split()).strip()[:80],
                    score=item.score,
                )
            )
            remaining -= len(content)
        return tuple(bounded)

    def _empty_result(
        self,
        *,
        run_id: str,
        operation: str,
        query: str,
        provider_result: ProviderResult,
        topic: str,
        time_range: str,
    ) -> ResearchResult:
        return ResearchResult(
            run_id=run_id,
            operation=operation,  # type: ignore[arg-type]
            query=query,
            provider="tavily",
            provider_request_id=provider_result.request_id,
            cache_hit=False,
            fresh_until="",
            topic=topic,
            time_range=time_range,
            evidence=(),
            credits_used=provider_result.credits_used,
        )

    async def search(
        self,
        *,
        query: str,
        topic: ResearchTopic = "general",
        time_range: ResearchTimeRange | None = None,
        max_results: int = SEARCH_DEFAULT_RESULTS,
        refresh: bool = False,
    ) -> ResearchResult:
        """Search Tavily with a local TTL cache and a 3,000-character envelope."""

        normalized_query = normalize_query(query)
        if topic not in _CACHE_TTLS:
            raise ResearchValidationError("topic 只允许 general、news 或 finance")
        if time_range not in {None, "day", "week", "month", "year"}:
            raise ResearchValidationError("time_range 只允许 day、week、month 或 year")
        if not 1 <= max_results <= SEARCH_MAX_RESULTS:
            raise ResearchValidationError("max_results 必须在 1 到 8 之间")
        parameters: dict[str, object] = {
            "query": normalized_query.casefold(),
            "topic": topic,
            "time_range": time_range or "",
            "max_results": max_results,
            "search_depth": "basic",
        }
        request_key = self._request_key("search", parameters)

        async with self._lock:
            now = self._now_provider()
            if not refresh:
                cached = self.store.load_fresh(request_key=request_key, now=now)
                if cached is not None:
                    return cached
            run_id = self._run_id()
            requested_at = now.isoformat()
            try:
                provider_result = await self._provider().search(
                    SearchRequest(
                        query=normalized_query,
                        topic=topic,
                        time_range=time_range,
                        max_results=max_results,
                    )
                )
            except (ResearchConfigurationError, ResearchProviderError) as error:
                completed_at = self._now_provider().isoformat()
                self.store.record_terminal(
                    run_id=run_id,
                    request_key=request_key,
                    operation="search",
                    query=normalized_query,
                    topic=topic,
                    time_range=time_range or "",
                    parameters=parameters,
                    requested_at=requested_at,
                    completed_at=completed_at,
                    status="failed",
                    error_code=type(error).__name__,
                    error_message=str(error),
                )
                raise
            evidence = self._bounded_evidence(
                provider_result.evidence,
                item_chars=SEARCH_SNIPPET_CHARS,
                total_chars=SEARCH_TOTAL_CHARS,
                item_limit=max_results,
            )
            completed = self._now_provider()
            if not evidence:
                self.store.record_terminal(
                    run_id=run_id,
                    request_key=request_key,
                    operation="search",
                    query=normalized_query,
                    topic=topic,
                    time_range=time_range or "",
                    parameters=parameters,
                    requested_at=requested_at,
                    completed_at=completed.isoformat(),
                    status="empty",
                    provider_request_id=provider_result.request_id,
                    credits_used=provider_result.credits_used,
                )
                return self._empty_result(
                    run_id=run_id,
                    operation="search",
                    query=normalized_query,
                    provider_result=provider_result,
                    topic=topic,
                    time_range=time_range or "",
                )
            result = ResearchResult(
                run_id=run_id,
                operation="search",
                query=normalized_query,
                provider="tavily",
                provider_request_id=provider_result.request_id,
                cache_hit=False,
                fresh_until=(completed + _CACHE_TTLS[topic]).isoformat(),
                topic=topic,
                time_range=time_range or "",
                evidence=evidence,
                credits_used=provider_result.credits_used,
            )
            self.store.record_success(
                result=result,
                request_key=request_key,
                parameters=parameters,
                requested_at=requested_at,
                completed_at=completed.isoformat(),
            )
            return result

    async def extract(
        self,
        *,
        urls: Sequence[str],
        query: str,
        chunks_per_source: int = EXTRACT_DEFAULT_CHUNKS,
        refresh: bool = False,
    ) -> ResearchResult:
        """Extract bounded query-relevant evidence from one to three public URLs."""

        normalized_query = normalize_query(query)
        if not 1 <= len(urls) <= EXTRACT_MAX_URLS:
            raise ResearchValidationError("Extract 必须提供 1 到 3 个公开 URL")
        if not 1 <= chunks_per_source <= EXTRACT_MAX_CHUNKS:
            raise ResearchValidationError("chunks_per_source 必须在 1 到 3 之间")
        normalized_urls: list[str] = []
        for value in urls:
            normalized = normalize_public_url(value)
            if normalized not in normalized_urls:
                normalized_urls.append(normalized)
        if not normalized_urls:
            raise ResearchValidationError("Extract 必须提供至少 1 个公开 URL")
        parameters: dict[str, object] = {
            "query": normalized_query.casefold(),
            # URL order does not change the research question. Sorting only the
            # cache identity avoids paying again when callers reorder a set.
            "urls": sorted(normalized_urls),
            "chunks_per_source": chunks_per_source,
            "extract_depth": "basic",
        }
        request_key = self._request_key("extract", parameters)

        async with self._lock:
            now = self._now_provider()
            if not refresh:
                cached = self.store.load_fresh(request_key=request_key, now=now)
                if cached is not None:
                    return cached
            run_id = self._run_id()
            requested_at = now.isoformat()
            try:
                provider_result = await self._provider().extract(
                    ExtractRequest(
                        urls=tuple(normalized_urls),
                        query=normalized_query,
                        chunks_per_source=chunks_per_source,
                    )
                )
            except (ResearchConfigurationError, ResearchProviderError) as error:
                completed_at = self._now_provider().isoformat()
                self.store.record_terminal(
                    run_id=run_id,
                    request_key=request_key,
                    operation="extract",
                    query=normalized_query,
                    topic="general",
                    time_range="",
                    parameters=parameters,
                    requested_at=requested_at,
                    completed_at=completed_at,
                    status="failed",
                    error_code=type(error).__name__,
                    error_message=str(error),
                )
                raise
            evidence = self._bounded_evidence(
                provider_result.evidence,
                item_chars=EXTRACT_ITEM_CHARS,
                total_chars=EXTRACT_TOTAL_CHARS,
                item_limit=len(normalized_urls),
            )
            completed = self._now_provider()
            if not evidence:
                self.store.record_terminal(
                    run_id=run_id,
                    request_key=request_key,
                    operation="extract",
                    query=normalized_query,
                    topic="general",
                    time_range="",
                    parameters=parameters,
                    requested_at=requested_at,
                    completed_at=completed.isoformat(),
                    status="empty",
                    provider_request_id=provider_result.request_id,
                    credits_used=provider_result.credits_used,
                )
                return self._empty_result(
                    run_id=run_id,
                    operation="extract",
                    query=normalized_query,
                    provider_result=provider_result,
                    topic="general",
                    time_range="",
                )
            result = ResearchResult(
                run_id=run_id,
                operation="extract",
                query=normalized_query,
                provider="tavily",
                provider_request_id=provider_result.request_id,
                cache_hit=False,
                fresh_until=(completed + _CACHE_TTLS["general"]).isoformat(),
                topic="general",
                time_range="",
                evidence=evidence,
                credits_used=provider_result.credits_used,
            )
            self.store.record_success(
                result=result,
                request_key=request_key,
                parameters=parameters,
                requested_at=requested_at,
                completed_at=completed.isoformat(),
            )
            return result
