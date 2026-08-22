"""Provider-neutral data contracts for bounded NewsClaw research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ResearchTopic = Literal["general", "news", "finance"]
ResearchTimeRange = Literal["day", "week", "month", "year"]
ResearchOperation = Literal["search", "extract"]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """One bounded Tavily Search request."""

    query: str
    topic: ResearchTopic = "general"
    time_range: ResearchTimeRange | None = None
    max_results: int = 5


@dataclass(frozen=True, slots=True)
class ExtractRequest:
    """One bounded Tavily Extract request."""

    urls: tuple[str, ...]
    query: str
    chunks_per_source: int = 2


@dataclass(frozen=True, slots=True)
class ResearchEvidence:
    """A locally bounded evidence fragment with explicit provenance."""

    url: str
    title: str
    content: str
    source_domain: str
    published_at: str = ""
    score: float | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the public, credential-free evidence representation."""

        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "source_domain": self.source_domain,
            "published_at": self.published_at,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Normalized response returned by a research provider."""

    request_id: str
    evidence: tuple[ResearchEvidence, ...]
    credits_used: float | None = None


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Redaction-safe subset of Tavily account usage returned by ``GET /usage``."""

    key_usage: float | None = None
    key_limit: float | None = None
    account_plan: str = ""
    plan_usage: float | None = None
    plan_limit: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": True,
            "provider": "tavily",
            "key_usage": self.key_usage,
            "key_limit": self.key_limit,
            "account_plan": self.account_plan,
            "plan_usage": self.plan_usage,
            "plan_limit": self.plan_limit,
        }


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """One persisted or cache-replayed research result."""

    run_id: str
    operation: ResearchOperation
    query: str
    provider: str
    provider_request_id: str
    cache_hit: bool
    fresh_until: str
    topic: str
    time_range: str
    evidence: tuple[ResearchEvidence, ...]
    credits_used: float | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the REST and Agent Bridge payload."""

        return {
            "run_id": self.run_id,
            "operation": self.operation,
            "query": self.query,
            "provider": self.provider,
            "provider_request_id": self.provider_request_id,
            "cache_hit": self.cache_hit,
            "fresh_until": self.fresh_until,
            "topic": self.topic,
            "time_range": self.time_range,
            "result_count": len(self.evidence),
            "credits_used": self.credits_used,
            "evidence": [item.to_dict() for item in self.evidence],
        }
