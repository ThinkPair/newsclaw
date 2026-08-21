"""From-zero NewsClaw onboarding coordinator.

Only explicit manual directions and avoid terms enter this workflow.  There is
intentionally no parameter or code path for importing a legacy NewsClaw or
OpenBiliClaw profile.
"""

from __future__ import annotations

import asyncio
import inspect
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from openbiliclaw.news.dedupe import SemanticDeduplicator
from openbiliclaw.news.errors import safe_error_message
from openbiliclaw.news.identity import stable_event_id, stable_request_id
from openbiliclaw.news.protocol import FetchWindow, NewsSourceAdapter

if TYPE_CHECKING:
    from openbiliclaw.news.models import Article
    from openbiliclaw.news.reliability import SourceRunner, SourceRunResult

ManualPreferenceType = Literal["manual_interest", "manual_avoid"]
StageStatus = Literal["completed", "degraded", "failed"]
BootstrapStatus = Literal["completed", "degraded", "failed"]
StageName = Literal[
    "validate_preferences",
    "seed_manual_memory",
    "fetch_initial_news",
    "publish_candidate_pool",
]


@dataclass(frozen=True, slots=True)
class ManualPreferenceEvent:
    """One deterministic, user-owned memory seed event."""

    event_id: str
    request_id: str
    event_type: ManualPreferenceType
    value: str
    source: Literal["manual"] = "manual"
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ManualProfileSeed:
    """Framework-neutral payload for creating an empty manual-first profile.

    The API integration may translate this into OBC's current profile and
    override types.  Keeping those types out of this module prevents onboarding
    from becoming coupled to a particular profile schema revision.
    """

    seed_id: str
    request_id: str
    interests: tuple[str, ...]
    avoid_terms: tuple[str, ...]
    preference_events: tuple[ManualPreferenceEvent, ...]
    schema_version: Literal["news-manual-v1"] = "news-manual-v1"


@dataclass(frozen=True, slots=True)
class BootstrapStage:
    """Final state of one of the four onboarding phases."""

    name: StageName
    status: StageStatus
    completed: int
    total: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class BootstrapSourceState:
    """One source's contribution to the initial candidate pool."""

    source_id: str
    media: str
    status: str
    article_count: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    """Stable callback payload for the initial recommendation pool."""

    request_id: str
    event_id: str
    articles: tuple[Article, ...]
    source_states: tuple[BootstrapSourceState, ...]


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Four-stage onboarding outcome returned to ``/api/news/bootstrap``."""

    request_id: str
    status: BootstrapStatus
    stages: tuple[BootstrapStage, ...]
    preference_events: tuple[ManualPreferenceEvent, ...]
    profile_seed: ManualProfileSeed
    candidate_batch: CandidateBatch


class SourceRegistry:
    """Ordered registry of enabled news adapters for onboarding and refresh."""

    def __init__(self, adapters: Iterable[NewsSourceAdapter] = ()) -> None:
        self._adapters: dict[str, NewsSourceAdapter] = {}
        self._enabled: dict[str, bool] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: NewsSourceAdapter, *, enabled: bool = True) -> None:
        """Register one source without silently replacing its identity."""

        if adapter.source_id in self._adapters:
            raise ValueError(f"duplicate news source_id: {adapter.source_id}")
        self._adapters[adapter.source_id] = adapter
        self._enabled[adapter.source_id] = enabled

    def set_enabled(self, source_id: str, enabled: bool) -> None:
        """Enable/disable a configured source while retaining its settings."""

        if source_id not in self._adapters:
            raise KeyError(source_id)
        self._enabled[source_id] = enabled

    def enabled_adapters(self) -> tuple[NewsSourceAdapter, ...]:
        """Return enabled adapters in stable registration order."""

        return tuple(
            adapter
            for source_id, adapter in self._adapters.items()
            if self._enabled.get(source_id, False)
        )


PreferenceCallback = Callable[[ManualPreferenceEvent], object]
CandidateCallback = Callable[[CandidateBatch], object]
ProfileInitializer = Callable[[ManualProfileSeed], object]


def clean_bootstrap_terms(
    values: Iterable[object],
    *,
    label: str,
    maximum: int,
) -> tuple[str, ...]:
    """Return the canonical terms used for bootstrap IDs and persistence.

    The API coordinator and the asynchronous worker must use the same
    normalization.  Otherwise a whitespace-only change can make the status
    receipt advertise a different request than the durable preference events.
    """

    unique: dict[str, str] = {}
    for value in values:
        text = " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).strip()
        if not text:
            continue
        if len(text) > 80:
            raise ValueError(f"{label} terms must be at most 80 characters")
        unique.setdefault(text.casefold(), text)
    if len(unique) > maximum:
        raise ValueError(f"at most {maximum} {label} terms are allowed")
    return tuple(sorted(unique.values(), key=str.casefold))


async def _invoke(callback: Callable[[Any], object], payload: Any) -> None:
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


class NewsBootstrapCoordinator:
    """Seed manual memory and build the first news pool from no prior profile."""

    def __init__(
        self,
        *,
        registry: SourceRegistry,
        runner: SourceRunner,
        write_preference: PreferenceCallback,
        publish_candidates: CandidateCallback,
        profile_initializer: ProfileInitializer | None = None,
        deduplicator: SemanticDeduplicator | None = None,
        clock: Callable[[], datetime] | None = None,
        initial_window_hours: int = 72,
        per_source_limit: int = 40,
    ) -> None:
        if not 1 <= initial_window_hours <= 24 * 30:
            raise ValueError("initial_window_hours must be between 1 and 720")
        if not 1 <= per_source_limit <= 1000:
            raise ValueError("per_source_limit must be between 1 and 1000")
        self._registry = registry
        self._runner = runner
        self._write_preference = write_preference
        self._publish_candidates = publish_candidates
        self._profile_initializer = profile_initializer
        self._deduplicator = deduplicator or SemanticDeduplicator()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._initial_window_hours = initial_window_hours
        self._per_source_limit = per_source_limit

    async def bootstrap(
        self,
        *,
        interests: Iterable[object],
        avoid_terms: Iterable[object] = (),
        request_id: str = "",
    ) -> BootstrapResult:
        """Run validation, manual seeding, initial fetch, and pool publication."""

        clean_interests = clean_bootstrap_terms(interests, label="interest", maximum=10)
        clean_avoids = clean_bootstrap_terms(avoid_terms, label="avoid", maximum=10)
        if not 3 <= len(clean_interests) <= 10:
            raise ValueError("choose between 3 and 10 interest directions")
        overlap = {value.casefold() for value in clean_interests} & {
            value.casefold() for value in clean_avoids
        }
        if overlap:
            raise ValueError("the same term cannot be both an interest and an avoid term")
        normalized_request_id = stable_request_id(
            "news-bootstrap",
            clean_interests,
            clean_avoids,
            supplied=request_id,
        )
        stages: list[BootstrapStage] = [
            BootstrapStage(
                name="validate_preferences",
                status="completed",
                completed=len(clean_interests) + len(clean_avoids),
                total=len(clean_interests) + len(clean_avoids),
                detail="Manual directions validated; no prior profile imported",
            )
        ]
        preference_groups: tuple[
            tuple[ManualPreferenceType, tuple[str, ...]],
            tuple[ManualPreferenceType, tuple[str, ...]],
        ] = (
            ("manual_interest", clean_interests),
            ("manual_avoid", clean_avoids),
        )
        events = tuple(
            ManualPreferenceEvent(
                event_id=stable_event_id(
                    "news-bootstrap-preference",
                    normalized_request_id,
                    event_type,
                    value.casefold(),
                ),
                request_id=normalized_request_id,
                event_type=event_type,
                value=value,
            )
            for event_type, values in preference_groups
            for value in values
        )
        profile_seed = ManualProfileSeed(
            seed_id=stable_event_id(
                "news-bootstrap-profile",
                normalized_request_id,
                clean_interests,
                clean_avoids,
            ),
            request_id=normalized_request_id,
            interests=clean_interests,
            avoid_terms=clean_avoids,
            preference_events=events,
        )

        written = 0
        seed_total = len(events) + (1 if self._profile_initializer is not None else 0)
        try:
            if self._profile_initializer is not None:
                await _invoke(self._profile_initializer, profile_seed)
                written += 1
            for event in events:
                await _invoke(self._write_preference, event)
                written += 1
        except Exception as error:
            stages.append(
                BootstrapStage(
                    name="seed_manual_memory",
                    status="failed",
                    completed=written,
                    total=seed_total,
                    detail=safe_error_message(error),
                )
            )
            return self._failed_result(
                normalized_request_id,
                stages,
                events,
                profile_seed,
            )
        stages.append(
            BootstrapStage(
                name="seed_manual_memory",
                status="completed",
                completed=written,
                total=seed_total,
                detail=(
                    "Manual-first profile initialized and events written with stable IDs"
                    if self._profile_initializer is not None
                    else "Manual events written with stable event IDs"
                ),
            )
        )

        now = self._clock().astimezone(UTC)
        window = FetchWindow(
            now - timedelta(hours=self._initial_window_hours),
            now,
            self._per_source_limit,
        )
        adapters = self._registry.enabled_adapters()
        outcomes = await asyncio.gather(
            *(self._fetch_source(adapter, window, normalized_request_id) for adapter in adapters)
        )
        source_states = tuple(state for state, _articles in outcomes)
        all_articles = [article for _state, articles in outcomes for article in articles]
        dedupe = self._deduplicator.deduplicate(all_articles)
        failed_sources = sum(
            1 for state in source_states if state.status not in {"succeeded", "empty"}
        )
        fetch_status: StageStatus = "completed" if adapters and not failed_sources else "degraded"
        stages.append(
            BootstrapStage(
                name="fetch_initial_news",
                status=fetch_status,
                completed=len(adapters) - failed_sources,
                total=len(adapters),
                detail=(
                    f"Collected {len(dedupe.articles)} unique articles; "
                    f"folded {len(all_articles) - len(dedupe.articles)} duplicates"
                ),
            )
        )
        batch = CandidateBatch(
            request_id=normalized_request_id,
            event_id=stable_event_id(
                "news-bootstrap-candidates",
                normalized_request_id,
                tuple(article.article_id for article in dedupe.articles),
            ),
            articles=dedupe.articles,
            source_states=source_states,
        )
        try:
            await _invoke(self._publish_candidates, batch)
        except Exception as error:
            stages.append(
                BootstrapStage(
                    name="publish_candidate_pool",
                    status="failed",
                    completed=0,
                    total=1,
                    detail=safe_error_message(error),
                )
            )
            return BootstrapResult(
                request_id=normalized_request_id,
                status="failed",
                stages=tuple(stages),
                preference_events=events,
                profile_seed=profile_seed,
                candidate_batch=batch,
            )
        stages.append(
            BootstrapStage(
                name="publish_candidate_pool",
                status="completed",
                completed=1,
                total=1,
                detail=f"Published {len(batch.articles)} articles to the initial candidate pool",
            )
        )
        overall: BootstrapStatus = (
            "degraded" if any(stage.status == "degraded" for stage in stages) else "completed"
        )
        return BootstrapResult(
            request_id=normalized_request_id,
            status=overall,
            stages=tuple(stages),
            preference_events=events,
            profile_seed=profile_seed,
            candidate_batch=batch,
        )

    async def _fetch_source(
        self,
        adapter: NewsSourceAdapter,
        window: FetchWindow,
        bootstrap_request_id: str,
    ) -> tuple[BootstrapSourceState, tuple[Article, ...]]:
        request_id = stable_request_id(
            "news-bootstrap-source",
            bootstrap_request_id,
            adapter.source_id,
        )
        try:
            result: SourceRunResult = await self._runner.run(
                adapter,
                window,
                request_id=request_id,
            )
        except Exception as error:
            return (
                BootstrapSourceState(
                    source_id=adapter.source_id,
                    media=adapter.media,
                    status="failed",
                    article_count=0,
                    detail=safe_error_message(error),
                ),
                (),
            )
        return (
            BootstrapSourceState(
                source_id=adapter.source_id,
                media=adapter.media,
                status=result.status,
                article_count=len(result.articles),
            ),
            result.articles,
        )

    @staticmethod
    def _failed_result(
        request_id: str,
        stages: list[BootstrapStage],
        events: tuple[ManualPreferenceEvent, ...],
        profile_seed: ManualProfileSeed,
    ) -> BootstrapResult:
        while len(stages) < 4:
            name: StageName = "fetch_initial_news" if len(stages) == 2 else "publish_candidate_pool"
            stages.append(
                BootstrapStage(
                    name=name,
                    status="failed",
                    completed=0,
                    total=0,
                    detail="Skipped because manual memory seeding failed",
                )
            )
        batch = CandidateBatch(
            request_id=request_id,
            event_id=stable_event_id("news-bootstrap-candidates", request_id, ()),
            articles=(),
            source_states=(),
        )
        return BootstrapResult(
            request_id=request_id,
            status="failed",
            stages=tuple(stages),
            preference_events=events,
            profile_seed=profile_seed,
            candidate_batch=batch,
        )
