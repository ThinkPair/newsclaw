"""Public contracts for NewsClaw source adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openbiliclaw.news.models import Article

ProbeStatus = Literal[
    "available",
    "empty",
    "permission_denied",
    "rate_limited",
    "timed_out",
    "unavailable",
]


@dataclass(frozen=True, slots=True)
class FetchWindow:
    """A bounded provider request window."""

    start_at: datetime
    end_at: datetime
    limit: int = 50

    def __post_init__(self) -> None:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("fetch window timestamps must be timezone-aware")
        if self.end_at < self.start_at:
            raise ValueError("fetch window end_at must not precede start_at")
        if not 1 <= self.limit <= 1000:
            raise ValueError("fetch window limit must be between 1 and 1000")

    @classmethod
    def recent(cls, *, hours: int = 24, limit: int = 50) -> FetchWindow:
        """Build a recent UTC window for manual refresh and probes."""

        end_at = datetime.now(UTC)
        return cls(end_at - timedelta(hours=hours), end_at, limit)


@dataclass(frozen=True, slots=True)
class SourceProbe:
    """Permission/health result safe to expose in the source settings UI."""

    source_id: str
    media: str
    provider: str
    status: ProbeStatus
    available: bool
    checked_at: str
    detail: str = ""
    error_code: str = ""


@runtime_checkable
class NewsSourceAdapter(Protocol):
    """Interface implemented by Tushare and RSS/Atom sources."""

    @property
    def source_id(self) -> str:
        """Stable local source identifier."""
        ...

    @property
    def media(self) -> str:
        """User-facing media name."""
        ...

    @property
    def provider(self) -> str:
        """Technical provider shown as secondary source information."""
        ...

    async def fetch(self, window: FetchWindow) -> list[Article]:
        """Fetch and normalize a bounded article batch."""
        ...

    async def probe(self) -> SourceProbe:
        """Verify endpoint access without exposing credentials."""
        ...
