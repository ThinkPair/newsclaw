"""NewsClaw-only API integration for sources, onboarding, and refresh.

This module deliberately sits beside the upstream-derived API instead of
teaching every OpenBiliClaw route about news.  It owns only local NewsClaw
state and is registered when ``NEWSCLAW_MODE=1``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import importlib
import json
import logging
import os
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from openbiliclaw.discovery.candidate_pool import (
    DiscoveryCandidateWrite,
    discovery_candidate_pending_cap,
)
from openbiliclaw.news.bootstrap import (
    BootstrapResult,
    CandidateBatch,
    ManualPreferenceEvent,
    ManualProfileSeed,
    NewsBootstrapCoordinator,
    SourceRegistry,
    clean_bootstrap_terms,
)
from openbiliclaw.news.dedupe import SemanticDeduplicator
from openbiliclaw.news.errors import safe_error_message
from openbiliclaw.news.identity import SQLiteIdempotencyLedger, stable_request_id
from openbiliclaw.news.models import Article, normalize_utc
from openbiliclaw.news.protocol import FetchWindow, NewsSourceAdapter
from openbiliclaw.news.reliability import RetryPolicy, SourceRunner, SourceTaskStore
from openbiliclaw.news.rss import RSSAdapter
from openbiliclaw.news.tracking import NewsTrackingService
from openbiliclaw.news.tushare import TushareNewsAdapter
from openbiliclaw.research.routes import register_research_routes
from openbiliclaw.research.service import ResearchService
from openbiliclaw.research.store import ResearchStore
from openbiliclaw.sources.event_format import build_event

if TYPE_CHECKING:
    from pathlib import Path

NewsSourceKind = Literal["tushare", "rss"]
MaybeAwaitable = Callable[..., Awaitable[object] | object]
RuntimeSettingsApplier = Callable[[Mapping[str, object]], Awaitable[object] | object]

_NEWS_CHANGE_CURSOR_VERSION = 1
_NEWS_CHANGE_CURSOR_MAX_LENGTH = 1_024

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _bounded_text(value: object, *, maximum: int) -> str:
    return " ".join(str(value or "").split()).strip()[:maximum]


def _as_int(value: object, default: int) -> int:
    try:
        return int(cast("Any", value))
    except (TypeError, ValueError, OverflowError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(cast("Any", value))
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(frozen=True, slots=True)
class NewsSourceConfig:
    """One redaction-safe source declaration."""

    source_id: str
    kind: NewsSourceKind
    media: str
    provider: str
    endpoint: str = ""
    url: str = ""
    enabled: bool = True
    weight: float = 1.0
    refresh_minutes: int = 60
    custom: bool = False

    def __post_init__(self) -> None:
        if not self.source_id or len(self.source_id) > 160:
            raise ValueError("source_id is required")
        if self.kind not in {"tushare", "rss"}:
            raise ValueError("unsupported source kind")
        if not self.media or not self.provider:
            raise ValueError("media and provider are required")
        if self.kind == "tushare" and self.endpoint not in {
            "news",
            "major_news",
            "cctv_news",
        }:
            raise ValueError("unsupported Tushare endpoint")
        if self.kind == "rss" and not self.url.startswith(("http://", "https://")):
            raise ValueError("RSS URL must use HTTP or HTTPS")
        if not 0 <= self.weight <= 3:
            raise ValueError("source weight must be between 0 and 3")
        if not 5 <= self.refresh_minutes <= 10_080:
            raise ValueError("refresh_minutes must be between 5 and 10080")

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> NewsSourceConfig:
        kind = str(payload.get("kind") or "")
        if kind not in {"tushare", "rss"}:
            raise ValueError("unsupported source kind")
        return cls(
            source_id=_bounded_text(payload.get("source_id"), maximum=160),
            kind=cast("NewsSourceKind", kind),
            media=_bounded_text(payload.get("media"), maximum=128),
            provider=_bounded_text(payload.get("provider"), maximum=128),
            endpoint=_bounded_text(payload.get("endpoint"), maximum=40),
            url=str(payload.get("url") or "").strip()[:4096],
            enabled=bool(payload.get("enabled", True)),
            weight=_as_float(payload.get("weight", 1.0), 1.0),
            refresh_minutes=_as_int(payload.get("refresh_minutes", 60), 60),
            custom=bool(payload.get("custom", False)),
        )


DEFAULT_NEWS_SOURCES: tuple[NewsSourceConfig, ...] = (
    NewsSourceConfig(
        source_id="tushare:news",
        kind="tushare",
        media="财经快讯（多媒体）",
        provider="Tushare Pro · news",
        endpoint="news",
        refresh_minutes=30,
    ),
    NewsSourceConfig(
        source_id="tushare:major_news",
        kind="tushare",
        media="财经长篇（多媒体）",
        provider="Tushare Pro · major_news",
        endpoint="major_news",
        refresh_minutes=120,
    ),
    NewsSourceConfig(
        source_id="tushare:cctv_news",
        kind="tushare",
        media="央视新闻联播",
        provider="Tushare Pro · cctv_news",
        endpoint="cctv_news",
        refresh_minutes=1_440,
    ),
    NewsSourceConfig(
        source_id="rss:stats:latest",
        kind="rss",
        media="国家统计局·最新发布",
        provider="官方 RSS/Atom",
        url="https://www.stats.gov.cn/sj/zxfb/rss.xml",
        refresh_minutes=180,
    ),
    NewsSourceConfig(
        source_id="rss:stats:interpretation",
        kind="rss",
        media="国家统计局·数据解读",
        provider="官方 RSS/Atom",
        url="https://www.stats.gov.cn/sj/sjjd/rss.xml",
        refresh_minutes=180,
    ),
    NewsSourceConfig(
        source_id="rss:people:all",
        kind="rss",
        media="人民网·全部新闻",
        provider="官方 RSS/Atom",
        url="http://www.people.com.cn/rss/ywkx.xml",
        refresh_minutes=30,
    ),
)


DEFAULT_NEWS_SETTINGS: dict[str, object] = {
    "version": 1,
    "pool_target_count": 300,
    "refresh_poll_minutes": 30,
    "hot_refresh_minutes": 30,
    "exploration_refresh_minutes": 360,
    "feedback_threshold": 3,
    "proactive_push": True,
    "background_llm": True,
    "browser_close_behavior": "continue",
    "retry_max_attempts": 3,
    "retry_base_seconds": 1.0,
    "retry_max_seconds": 30.0,
}


class NewsSettingsStore:
    """Atomic source/settings store with a last-known-good rollback copy."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_good_path = path.with_name(f"{path.stem}.last-good{path.suffix}")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _defaults() -> dict[str, object]:
        return {
            "settings": dict(DEFAULT_NEWS_SETTINGS),
            "sources": [asdict(source) for source in DEFAULT_NEWS_SOURCES],
        }

    @staticmethod
    def _validated(raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            raise ValueError("news settings must be an object")
        source_rows = raw.get("sources")
        if not isinstance(source_rows, list):
            raise ValueError("news settings sources must be a list")
        sources = [
            NewsSourceConfig.from_dict(cast("Mapping[str, object]", row))
            for row in source_rows
            if isinstance(row, dict)
        ]
        if len(sources) != len(source_rows):
            raise ValueError("every source must be an object")
        if len({source.source_id for source in sources}) != len(sources):
            raise ValueError("source_id values must be unique")
        settings_raw = raw.get("settings")
        settings = dict(DEFAULT_NEWS_SETTINGS)
        if isinstance(settings_raw, dict):
            settings.update(settings_raw)
        settings["pool_target_count"] = max(
            20, min(2_000, _as_int(settings.get("pool_target_count", 300), 300))
        )
        settings["refresh_poll_minutes"] = max(
            5, min(10_080, _as_int(settings.get("refresh_poll_minutes", 30), 30))
        )
        settings["hot_refresh_minutes"] = max(
            5, min(10_080, _as_int(settings.get("hot_refresh_minutes", 30), 30))
        )
        settings["exploration_refresh_minutes"] = max(
            15,
            min(
                43_200,
                _as_int(settings.get("exploration_refresh_minutes", 360), 360),
            ),
        )
        settings["feedback_threshold"] = max(
            1, min(100, _as_int(settings.get("feedback_threshold", 3), 3))
        )
        settings["retry_max_attempts"] = max(
            1, min(10, _as_int(settings.get("retry_max_attempts", 3), 3))
        )
        settings["retry_base_seconds"] = max(
            0.0,
            min(300.0, _as_float(settings.get("retry_base_seconds", 1.0), 1.0)),
        )
        settings["retry_max_seconds"] = max(
            _as_float(settings["retry_base_seconds"], 1.0),
            min(3_600.0, _as_float(settings.get("retry_max_seconds", 30.0), 30.0)),
        )
        settings["proactive_push"] = bool(settings.get("proactive_push", True))
        settings["background_llm"] = bool(settings.get("background_llm", True))
        behavior = str(settings.get("browser_close_behavior") or "continue").strip()
        settings["browser_close_behavior"] = (
            behavior if behavior in {"continue", "pause"} else "continue"
        )
        return {"settings": settings, "sources": [asdict(source) for source in sources]}

    @classmethod
    def _read_validated(cls, path: Path) -> dict[str, object]:
        return cls._validated(json.loads(path.read_text(encoding="utf-8")))

    def load(self) -> dict[str, object]:
        if not self.path.exists():
            payload = self._defaults()
            self.save(payload)
            return payload
        try:
            return self._read_validated(self.path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            if self.last_good_path.exists():
                recovered = self._read_validated(self.last_good_path)
                self._atomic_write(self.path, recovered)
                return recovered
            defaults = self._defaults()
            self._atomic_write(self.path, defaults)
            return defaults

    def save(self, payload: object) -> dict[str, object]:
        validated = self._validated(payload)
        if self.path.exists():
            try:
                current = self._read_validated(self.path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                current = None
            if current is not None:
                self._atomic_write(self.last_good_path, current)
        self._atomic_write(self.path, validated)
        return validated

    @staticmethod
    def _atomic_write(path: Path, payload: object) -> None:
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def sources(self) -> tuple[NewsSourceConfig, ...]:
        payload = self.load()
        rows = cast("list[dict[str, object]]", payload["sources"])
        return tuple(NewsSourceConfig.from_dict(row) for row in rows)


class NewsRefreshIn(BaseModel):
    request_id: str = ""
    hours: int = Field(default=72, ge=1, le=720)
    per_source_limit: int = Field(default=80, ge=1, le=500)


class NewsProbeIn(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class NewsTushareCredentialIn(BaseModel):
    token: str = Field(min_length=8, max_length=512)


class NewsSourceUpdateIn(BaseModel):
    enabled: bool | None = None
    weight: float | None = Field(default=None, ge=0, le=3)
    refresh_minutes: int | None = Field(default=None, ge=5, le=10_080)
    media: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, max_length=4096)


class NewsCustomRSSIn(BaseModel):
    media: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=8, max_length=4096)
    enabled: bool = True
    weight: float = Field(default=1.0, ge=0, le=3)
    refresh_minutes: int = Field(default=60, ge=5, le=10_080)


class NewsSettingsUpdateIn(BaseModel):
    pool_target_count: int | None = Field(default=None, ge=20, le=2_000)
    refresh_poll_minutes: int | None = Field(default=None, ge=5, le=10_080)
    hot_refresh_minutes: int | None = Field(default=None, ge=5, le=10_080)
    exploration_refresh_minutes: int | None = Field(default=None, ge=15, le=43_200)
    feedback_threshold: int | None = Field(default=None, ge=1, le=100)
    proactive_push: bool | None = None
    background_llm: bool | None = None
    browser_close_behavior: Literal["continue", "pause"] | None = None
    retry_max_attempts: int | None = Field(default=None, ge=1, le=10)
    retry_base_seconds: float | None = Field(default=None, ge=0, le=300)
    retry_max_seconds: float | None = Field(default=None, ge=0, le=3_600)


class NewsBootstrapIn(BaseModel):
    interests: list[str] = Field(min_length=3, max_length=10)
    avoid_terms: list[str] = Field(default_factory=list, max_length=10)
    request_id: str = ""


class NewsEventOut(BaseModel):
    """One durable real-world event, separate from articles and tracking runs."""

    event_id: str
    canonical_title: str
    event_type: str = "unknown"
    topic_group: str = ""
    story_key: str = ""
    entities: list[Any] = Field(default_factory=list)
    current_state: str = ""
    state_version: int = 0
    status: Literal["active", "resolved", "merged", "archived"] = "active"
    merged_into_event_id: str | None = None
    first_seen_at: str = ""
    last_seen_at: str = ""
    last_change_at: str = ""
    change_count: int = 0
    unread_change_count: int = 0
    conflict_count: int = 0
    unread_conflict_count: int = 0
    evidence_count: int = 0


class NewsObjectiveChangeOut(BaseModel):
    """A confirmed objective change; interpretation fields stay out of this DTO."""

    change_id: str
    event_id: str
    canonical_title: str = ""
    topic_group: str = ""
    story_key: str = ""
    change_type: str
    previous_state: str
    new_state: str
    fact: str = ""
    summary: str = ""
    evidence_quote: str = ""
    role: Literal["objective_change"]
    objectivity_status: Literal["confirmed"]
    conflict_status: Literal["none", "resolved"] = "none"
    source_scope: Literal["unknown", "single_source", "multi_source"] = "unknown"
    evidence_status: Literal["source_supported", "cross_source_supported"]
    supersedes_change_id: str | None = None
    is_read: bool = False
    effective_at: str
    effective_precision: Literal["unknown", "date", "minute"] = "unknown"
    detected_at: str = ""
    evidence_count: int = 0


class NewsEventEvidenceOut(BaseModel):
    """One immutable evidence revision attached to an event or change."""

    membership_id: str
    event_id: str
    change_id: str | None = None
    evidence_item_key: str
    relation_kind: Literal[
        "origin",
        "supports_change",
        "related_no_change",
        "contradicts_change",
    ]
    relation_basis: str = ""
    relation_score: float = 0.0
    content_scope: Literal["unknown", "title_only", "summary", "full_text"] = "unknown"
    media: str = ""
    provider: str = ""
    title: str = ""
    canonical_url: str = ""
    published_at: str = ""
    retrieved_at: str = ""
    evidence_snapshot: dict[str, Any] = Field(default_factory=dict)
    evidence_completeness: Literal["complete", "partial_legacy"] = "complete"
    first_seen_at: str = ""
    last_seen_at: str = ""


class NewsEventLineageClaimOut(BaseModel):
    """One retained candidate edge behind a successor's lineage decision."""

    claim_id: str
    event_id: str
    successor_change_id: str
    predecessor_change_id: str
    supersedes_lifecycle: Literal["superseded", "invalidated"]
    source_event_id: str
    source_change_id: str
    status: Literal["active", "contested", "resolved", "rejected"]
    created_at: str = ""
    updated_at: str = ""


class NewsEventConflictOut(BaseModel):
    """One unresolved clash between otherwise durable state transitions."""

    change_id: str
    event_id: str
    canonical_title: str = ""
    topic_group: str = ""
    story_key: str = ""
    change_type: str
    previous_state: str
    new_state: str
    fact: str = ""
    summary: str = ""
    evidence_quote: str = ""
    role: Literal["objective_change", "expectation", "sentiment", "analysis"]
    objectivity_status: Literal["disputed"]
    conflict_status: Literal["contested"]
    evidence_status: Literal["contested"]
    source_scope: Literal["unknown", "single_source", "multi_source"] = "unknown"
    is_read: bool = False
    effective_at: str
    effective_precision: Literal["unknown", "date", "minute"] = "unknown"
    detected_at: str = ""
    evidence_count: int = 0
    freeze_reason: Literal["durable_evidence_conflict"] = "durable_evidence_conflict"
    lineage_claims: list[NewsEventLineageClaimOut] = Field(default_factory=list)


class NewsChangeFeedResponse(BaseModel):
    items: list[NewsObjectiveChangeOut] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


class NewsEventDetailResponse(BaseModel):
    item: NewsEventOut
    resolved_from_event_id: str = ""


class NewsEventChangeTimelineResponse(BaseModel):
    event: NewsEventOut
    items: list[NewsObjectiveChangeOut] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False


class NewsEventEvidencePage(BaseModel):
    event_id: str
    change_id: str | None = None
    resolved_from_change_id: str = ""
    items: list[NewsEventEvidenceOut] = Field(default_factory=list)
    next_offset: int | None = None
    has_more: bool = False


class NewsEventChangeReadResponse(BaseModel):
    item: NewsObjectiveChangeOut
    updated: bool = False


class NewsEventConflictPage(BaseModel):
    event: NewsEventOut
    items: list[NewsEventConflictOut] = Field(default_factory=list)
    next_offset: int | None = None
    has_more: bool = False


class NewsEventConflictReadResponse(BaseModel):
    item: NewsEventConflictOut
    updated: bool = False


def _encode_news_change_cursor(
    *,
    event_id: str,
    unread_only: bool,
    position: tuple[str, str],
) -> str:
    """Bind one opaque keyset cursor to its event and objective-feed filters."""

    payload = {
        "v": _NEWS_CHANGE_CURSOR_VERSION,
        "scope": "event" if event_id else "global",
        "event_id": event_id,
        "unread_only": unread_only,
        "lifecycle": "active",
        "objective_only": True,
        "after": list(position),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if len(token) > _NEWS_CHANGE_CURSOR_MAX_LENGTH:  # pragma: no cover - trusted bound
        raise RuntimeError("news change cursor exceeds its encoded length bound")
    return token


def _decode_news_change_cursor(
    token: str,
    *,
    event_id: str,
    unread_only: bool,
) -> tuple[str, str]:
    """Strictly decode a restart-safe objective-change cursor."""

    if (
        not token
        or len(token) > _NEWS_CHANGE_CURSOR_MAX_LENGTH
        or re.fullmatch(r"[A-Za-z0-9_-]+", token) is None
    ):
        raise ValueError("invalid news change cursor")
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode(
            (token + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("invalid news change cursor") from error
    expected_keys = {
        "v",
        "scope",
        "event_id",
        "unread_only",
        "lifecycle",
        "objective_only",
        "after",
    }
    expected_scope = "event" if event_id else "global"
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload["v"] != _NEWS_CHANGE_CURSOR_VERSION
        or payload["scope"] != expected_scope
        or payload["event_id"] != event_id
        or payload["unread_only"] is not unread_only
        or payload["lifecycle"] != "active"
        or payload["objective_only"] is not True
    ):
        raise ValueError("invalid news change cursor")
    after = payload["after"]
    if (
        not isinstance(after, list)
        or len(after) != 2
        or any(not isinstance(value, str) or not value for value in after)
    ):
        raise ValueError("invalid news change cursor")
    return after[0], after[1]


async def _maybe_await(callback: MaybeAwaitable, *args: object, **kwargs: object) -> object:
    result = callback(*args, **kwargs)
    if isinstance(result, Awaitable):
        return await result
    return result


class NewsRuntime:
    """Application-owned NewsClaw source and bootstrap coordinator."""

    def __init__(
        self,
        *,
        data_path: Path,
        database: Any,
        soul_engine: Any,
        event_ingress: Any,
        request_replenishment: MaybeAwaitable,
        client_present: Callable[[], bool] | None = None,
        apply_runtime_settings: RuntimeSettingsApplier | None = None,
        llm_service_provider: Callable[[], Any] | None = None,
        llm_concurrency_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.data_path = data_path
        self.database = database
        self.soul_engine = soul_engine
        self.event_ingress = event_ingress
        self.request_replenishment = request_replenishment
        self.client_present = client_present or (lambda: True)
        self.apply_runtime_settings_callback = apply_runtime_settings
        self.settings_store = NewsSettingsStore(data_path / "newsclaw_sources.json")
        self.credentials_path = data_path / "newsclaw_credentials.json"
        self.research_service = ResearchService(
            store=ResearchStore(data_path / "newsclaw_research.db"),
            api_key_provider=self._tavily_api_key,
        )
        self.task_store = SourceTaskStore(data_path / "newsclaw_source_tasks.db")
        self.command_ledger = SQLiteIdempotencyLedger(data_path / "newsclaw_commands.db")
        self.tracking_service = NewsTrackingService(
            database=database,
            llm_service_provider=llm_service_provider,
            llm_concurrency_provider=llm_concurrency_provider,
            background_analysis_allowed=lambda: bool(self.settings().get("background_llm", True)),
        )
        self.bootstrap_state_path = data_path / "newsclaw_bootstrap.json"
        self.runtime_state_path = data_path / "newsclaw_runtime.json"
        self._refresh_lock = asyncio.Lock()
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._scheduler_task: asyncio.Task[None] | None = None
        self._event_backfill_task: asyncio.Task[object] | None = None
        self._background_tasks: set[asyncio.Task[object]] = set()
        self._stop_event = asyncio.Event()
        self._effective_settings: dict[str, object] = {}

    def _runner(self, *, bounded_tracking: bool = False) -> SourceRunner:
        settings = cast("dict[str, object]", self.settings_store.load()["settings"])
        max_attempts = _as_int(settings["retry_max_attempts"], 3)
        base_backoff_seconds = _as_float(settings["retry_base_seconds"], 1.0)
        max_backoff_seconds = _as_float(settings["retry_max_seconds"], 30.0)
        if bounded_tracking:
            # A desktop "check now" request has a five-minute client budget.
            # Keep its source phase below ~64 seconds so the 120-second model
            # comparison (or 130-second single-flight join) still has room to
            # return one honest durable receipt.  Scheduled refreshes continue
            # to use the user's normal reliability settings.
            max_attempts = min(max_attempts, 3)
            base_backoff_seconds = min(base_backoff_seconds, 1.0)
            max_backoff_seconds = min(max_backoff_seconds, 2.0)
        return SourceRunner(
            self.task_store,
            policy=RetryPolicy(
                max_attempts=max_attempts,
                timeout_seconds=20.0,
                base_backoff_seconds=base_backoff_seconds,
                max_backoff_seconds=max_backoff_seconds,
                minimum_interval_seconds=0.25,
                lease_seconds=180.0,
            ),
        )

    def _load_credentials(self) -> dict[str, object]:
        try:
            payload = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items()}

    def _local_credential(self, key: str) -> str:
        return str(self._load_credentials().get(key) or "").strip()

    def _set_local_credential(self, key: str, value: str) -> None:
        credentials = self._load_credentials()
        credentials[key] = value
        NewsSettingsStore._atomic_write(self.credentials_path, credentials)
        with suppress(OSError):
            self.credentials_path.chmod(0o600)

    def _clear_local_credential(self, key: str) -> None:
        credentials = self._load_credentials()
        credentials.pop(key, None)
        if credentials:
            NewsSettingsStore._atomic_write(self.credentials_path, credentials)
            with suppress(OSError):
                self.credentials_path.chmod(0o600)
            return
        with suppress(FileNotFoundError):
            self.credentials_path.unlink()

    def _local_tushare_token(self) -> str:
        return self._local_credential("tushare_token")

    def _tushare_token(self) -> str:
        # A token explicitly saved from the NewsClaw UI is the local product's
        # source of truth.  The environment variable remains a backwards-
        # compatible fallback for existing installations.
        return self._local_tushare_token() or os.environ.get("TUSHARE_TOKEN", "").strip()

    def _tushare_token_configured(self) -> bool:
        return bool(self._tushare_token())

    def set_tushare_token(self, token: str) -> None:
        normalized = token.strip()
        invalid = (
            len(normalized) < 8
            or len(normalized) > 512
            or any(char.isspace() for char in normalized)
        )
        if invalid:
            raise ValueError("Tushare Token 格式无效")
        self._set_local_credential("tushare_token", normalized)

    def clear_tushare_token(self) -> None:
        self._clear_local_credential("tushare_token")

    def _local_tavily_api_key(self) -> str:
        return self._local_credential("tavily_api_key")

    def _tavily_api_key(self) -> str:
        # The local NewsClaw credential wins; the environment remains a
        # non-persisted fallback for scripted and migrated installations.
        return self._local_tavily_api_key() or os.environ.get("TAVILY_API_KEY", "").strip()

    def set_tavily_api_key(self, api_key: str) -> None:
        normalized = api_key.strip()
        invalid = (
            len(normalized) < 8
            or len(normalized) > 512
            or any(char.isspace() for char in normalized)
        )
        if invalid:
            raise ValueError("Tavily API Key 格式无效")
        self._set_local_credential("tavily_api_key", normalized)

    def clear_tavily_api_key(self) -> None:
        self._clear_local_credential("tavily_api_key")

    def tavily_credential_status(self) -> dict[str, object]:
        local = bool(self._local_tavily_api_key())
        configured = bool(self._tavily_api_key())
        return {
            "provider": "tavily",
            "configured": configured,
            "stored_locally": local,
            "source": "local" if local else "environment" if configured else "none",
            "masked": "••••••••" if configured else "",
        }

    def _tushare_client(self) -> Any | None:
        token = self._tushare_token()
        if not token:
            return None
        module = importlib.import_module("tushare")
        return module.pro_api(token)

    def _build_adapters(
        self,
        *,
        source_ids: set[str] | None = None,
    ) -> tuple[list[NewsSourceAdapter], list[dict[str, object]]]:
        configs = [
            source
            for source in self.settings_store.sources()
            if source.enabled and (source_ids is None or source.source_id in source_ids)
        ]
        adapters: list[NewsSourceAdapter] = []
        unavailable: list[dict[str, object]] = []
        tushare_client: Any | None = None
        if any(source.kind == "tushare" for source in configs):
            try:
                tushare_client = self._tushare_client()
            except Exception as error:
                unavailable.append(
                    {
                        "source_id": "tushare",
                        "status": "unavailable",
                        "detail": safe_error_message(error),
                    }
                )
        for source in configs:
            if source.kind == "tushare":
                if tushare_client is None:
                    unavailable.append(
                        {
                            "source_id": source.source_id,
                            "media": source.media,
                            "provider": source.provider,
                            "status": "missing_credential",
                            "available": False,
                            "detail": "TUSHARE_TOKEN 未配置或不可用",
                        }
                    )
                    continue
                adapters.append(
                    TushareNewsAdapter(
                        client=tushare_client,
                        endpoint=cast("Any", source.endpoint),
                        media=source.media,
                        source_id=source.source_id,
                    )
                )
                continue
            adapters.append(
                RSSAdapter(
                    url=source.url,
                    media=source.media,
                    source_id=source.source_id,
                )
            )
        return adapters, unavailable

    @staticmethod
    async def _close_adapters(adapters: Iterable[NewsSourceAdapter]) -> None:
        for adapter in adapters:
            close = getattr(adapter, "aclose", None)
            if callable(close):
                with suppress(Exception):
                    await close()

    def _profile_ready(self) -> bool:
        ready = getattr(self.soul_engine, "is_profile_ready", None)
        if callable(ready):
            try:
                return bool(ready())
            except Exception:
                return False
        return False

    def public_sources(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        token_ready = self._tushare_token_configured()
        for source in self.settings_store.sources():
            health = self.task_store.get_health(source.source_id)
            row = asdict(source)
            row.update(
                {
                    "id": source.source_id,
                    "name": source.media,
                    "technical_provider": source.provider,
                    "credential_configured": token_ready if source.kind == "tushare" else True,
                    "health": asdict(health) if health is not None else None,
                    "status": health.status if health is not None else "unknown",
                    "last_success_at": health.last_success_at if health is not None else "",
                    "error": health.error_message if health is not None else "",
                }
            )
            rows.append(row)
        return rows

    def settings(self) -> dict[str, object]:
        return cast("dict[str, object]", self.settings_store.load()["settings"])

    async def apply_settings(
        self,
        settings: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Apply persisted settings to both NewsRuntime and the news-only controller."""

        effective = dict(settings or self.settings())
        callback = self.apply_runtime_settings_callback
        if callback is not None:
            await _maybe_await(callback, effective)
        self._effective_settings = effective
        return dict(effective)

    async def probe(self, source_ids: set[str] | None = None) -> dict[str, object]:
        adapters, unavailable = self._build_adapters(source_ids=source_ids)
        try:
            probes = await asyncio.gather(*(adapter.probe() for adapter in adapters))
            for probe in probes:
                self.task_store.record_probe(probe)
            results = [asdict(probe) for probe in probes]
            results.extend(unavailable)
            return {
                "ok": (
                    all(bool(result.get("available")) for result in results) if results else False
                ),
                "status": self._aggregate_source_status(results),
                "sources": results,
            }
        finally:
            await self._close_adapters(adapters)

    @staticmethod
    def _aggregate_source_status(results: Iterable[Mapping[str, object]]) -> str:
        rows = list(results)
        availability = [NewsRuntime._source_available(row) for row in rows]
        if rows and all(availability):
            return "online"
        if any(availability):
            return "degraded"
        return "offline"

    @staticmethod
    def _source_available(row: Mapping[str, object]) -> bool:
        """Accept probe rows and persisted public-health rows without guessing."""

        if "available" in row:
            return bool(row.get("available"))
        return str(row.get("status") or "").strip().casefold() == "healthy"

    @staticmethod
    def _candidate(article: Article) -> DiscoveryCandidateWrite:
        evidence_notice = {
            "title_only": "标题级信息：未取得正文，不得当作全文解读",
            "summary": "摘要级信息：只根据来源提供的摘要工作",
            "full_text": "正文级信息：来源返回了正文内容",
        }[article.content_scope]
        return DiscoveryCandidateWrite(
            candidate_key=f"news:{article.article_id}",
            source_platform="news",
            source_strategy=f"news-{article.provider.casefold().replace(' ', '-')}",
            source_context=article.media,
            content_type="article",
            body_text=article.body,
            bvid=article.article_id,
            content_id=article.article_id,
            content_url=article.canonical_url,
            title=article.title,
            author_name=article.media,
            up_name=article.media,
            description=article.body[:2_000],
            published_at=article.published_at,
            tags=[article.media, article.content_scope, article.freshness],
            candidate_tier="primary",
            score_threshold=0.0,
            raw_payload={
                **article.to_dict(),
                "content_id": article.article_id,
                "content_url": article.canonical_url,
                "source_platform": "news",
                "content_type": "article",
                "evidence_notice": evidence_notice,
            },
        )

    async def publish_candidates(self, articles: Iterable[Article]) -> int:
        writes = [self._candidate(article) for article in articles]
        if not writes:
            return 0
        enqueue = getattr(self.database, "enqueue_discovery_candidates", None)
        if not callable(enqueue):
            raise RuntimeError("recommendation candidate queue is unavailable")
        target = _as_int(self.settings().get("pool_target_count", 300), 300)
        try:
            inserted = int(
                await asyncio.to_thread(
                    enqueue,
                    writes,
                    max_pending_per_source=discovery_candidate_pending_cap(target),
                )
            )
        except TypeError:
            inserted = int(await asyncio.to_thread(enqueue, writes))
        task = asyncio.create_task(
            _maybe_await(self.request_replenishment, reason="news_refresh", force=True)
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return inserted

    def _stored_runtime_state(self) -> dict[str, object]:
        try:
            raw = json.loads(self.runtime_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raw = {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _write_runtime_state(self, payload: Mapping[str, object]) -> None:
        NewsSettingsStore._atomic_write(self.runtime_state_path, dict(payload))

    def _merge_runtime_state(self, payload: Mapping[str, object]) -> None:
        self._write_runtime_state({**self._stored_runtime_state(), **dict(payload)})

    def runtime_state(self) -> dict[str, object]:
        state = self._stored_runtime_state()
        state.update(
            {
                "profile_ready": self._profile_ready(),
                "refresh_running": self._refresh_lock.locked(),
                "bootstrap_running": bool(
                    self._bootstrap_task is not None and not self._bootstrap_task.done()
                ),
                "effective_settings": dict(self._effective_settings or self.settings()),
            }
        )
        return state

    async def _run_tracking_batch(
        self,
        articles: list[dict[str, object]],
        *,
        source_request_id: str,
        source_status: str,
        tracking_ids: set[str] | None,
        force: bool,
        now: datetime,
    ) -> dict[str, object]:
        """Run one bounded tracking pass without leaking failures to refresh."""

        try:
            return cast(
                "dict[str, object]",
                await self.tracking_service.run_due_from_articles(
                    articles,
                    source_request_id=source_request_id,
                    source_status=source_status,
                    tracking_ids=tracking_ids,
                    force=force,
                    now=now,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            safe_error = safe_error_message(error) or type(error).__name__
            logger.warning("NewsClaw tracking batch failed: %s", safe_error)
            return {
                "ran": 0,
                "changed": 0,
                "failed": 1,
                "error": safe_error,
                "results": [],
            }

    async def refresh(
        self,
        *,
        request_id: str = "",
        hours: int = 72,
        per_source_limit: int = 80,
        tracking_ids: set[str] | None = None,
        force_tracking: bool = False,
    ) -> dict[str, object]:
        if not self._profile_ready():
            raise RuntimeError("请先完成新闻爪首次设置并建立画像")
        if self._refresh_lock.locked():
            raise RuntimeError("已有新闻刷新正在运行")
        async with self._refresh_lock:
            started = _utc_now()
            normalized_request_id = stable_request_id(
                "news-refresh",
                started.replace(second=0, microsecond=0).isoformat(),
                supplied=request_id,
            )
            adapters, unavailable = self._build_adapters()
            runner = self._runner(bounded_tracking=True) if force_tracking else self._runner()
            window = FetchWindow(started - timedelta(hours=hours), started, per_source_limit)
            try:
                outcomes = await asyncio.gather(
                    *(
                        runner.run(
                            adapter,
                            window,
                            request_id=stable_request_id(
                                "news-refresh-source",
                                normalized_request_id,
                                adapter.source_id,
                            ),
                        )
                        for adapter in adapters
                    )
                )
            finally:
                await self._close_adapters(adapters)
            source_results: list[dict[str, object]] = [
                {
                    "source_id": adapter.source_id,
                    "media": adapter.media,
                    "provider": adapter.provider,
                    "status": outcome.status,
                    "available": outcome.status in {"succeeded", "empty"},
                    "article_count": len(outcome.articles),
                    "attempt_count": outcome.attempt_count,
                    "replayed": outcome.replayed,
                }
                for adapter, outcome in zip(adapters, outcomes, strict=True)
            ]
            source_results.extend(unavailable)
            articles = [article for outcome in outcomes for article in outcome.articles]
            dedupe = SemanticDeduplicator().deduplicate(articles)
            source_status = self._aggregate_source_status(source_results)
            inserted = await self.publish_candidates(dedupe.articles)
            tracking_articles: list[dict[str, object]] = []
            for cluster in dedupe.clusters:
                canonical = cluster.canonical
                payload: dict[str, object] = {
                    **canonical.to_dict(),
                    "item_key": f"news:{canonical.article_id}",
                    "body_text": canonical.body,
                    "content_url": canonical.canonical_url,
                    "tags": [canonical.media, canonical.content_scope, canonical.freshness],
                    "corroborating_sources": [
                        {
                            "source": duplicate.media,
                            "provider": duplicate.provider,
                            "url": duplicate.canonical_url,
                            "published_at": duplicate.published_at,
                            "content_scope": duplicate.content_scope,
                        }
                        for duplicate in cluster.duplicates[:5]
                    ],
                }
                tracking_articles.append(payload)
            if force_tracking:
                tracking = await self._run_tracking_batch(
                    tracking_articles,
                    source_request_id=normalized_request_id,
                    source_status=source_status,
                    tracking_ids=tracking_ids,
                    force=True,
                    now=started,
                )
            elif not bool(self.settings().get("background_llm", True)):
                tracking = {
                    "ran": 0,
                    "changed": 0,
                    "failed": 0,
                    "scheduled": False,
                    "reason": "background_disabled",
                    "results": [],
                }
            else:
                tracking_task = asyncio.create_task(
                    self._run_tracking_batch(
                        tracking_articles,
                        source_request_id=normalized_request_id,
                        source_status=source_status,
                        tracking_ids=tracking_ids,
                        force=False,
                        now=started,
                    )
                )
                self._background_tasks.add(tracking_task)
                tracking_task.add_done_callback(self._background_tasks.discard)
                tracking = {
                    "ran": 0,
                    "changed": 0,
                    "failed": 0,
                    "scheduled": True,
                    "results": [],
                }
            completed_at = normalize_utc(_utc_now(), allow_empty=False)
            result = {
                "ok": bool(dedupe.articles),
                "request_id": normalized_request_id,
                "status": source_status,
                "article_count": len(dedupe.articles),
                "duplicate_count": max(0, len(articles) - len(dedupe.articles)),
                "candidate_inserted": inserted,
                "completed_at": completed_at,
                "sources": source_results,
                "tracking": tracking,
            }
            self._merge_runtime_state(
                {
                    "last_refresh": completed_at,
                    "last_refresh_status": result["status"],
                    "last_article_count": result["article_count"],
                    "last_request_id": normalized_request_id,
                }
            )
            return result

    def bootstrap_state(self) -> dict[str, object]:
        try:
            raw = json.loads(self.bootstrap_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raw = {}
        state = dict(raw) if isinstance(raw, dict) else {}
        state, migrated = self._compact_legacy_bootstrap_state(state)
        if migrated:
            self._write_bootstrap_state(state)
        state.setdefault("status", "completed" if self._profile_ready() else "not_started")
        live_task = bool(self._bootstrap_task is not None and not self._bootstrap_task.done())
        # A process can stop after the durable ``running`` receipt is written
        # but before the terminal state lands.  Never leave the UI polling a
        # ghost run forever: make the interruption explicit and retryable.
        if str(state.get("status") or "") == "running" and not live_task:
            state.update(
                {
                    "status": "failed",
                    "running": False,
                    "detail": "The backend restarted before onboarding completed; retry is safe.",
                    "completed_at": normalize_utc(_utc_now(), allow_empty=False),
                    "recovered_after_restart": True,
                }
            )
            self._write_bootstrap_state(state)
        state["profile_ready"] = self._profile_ready()
        state["running"] = live_task
        return state

    @staticmethod
    def _compact_legacy_bootstrap_state(
        state: dict[str, object],
    ) -> tuple[dict[str, object], bool]:
        """Migrate pre-v0.1 status files that embedded every fetched article."""

        raw_batch = state.get("candidate_batch")
        if not isinstance(raw_batch, dict) or not isinstance(raw_batch.get("articles"), list):
            return state, False
        articles = cast("list[object]", raw_batch.get("articles"))
        scope_counts: dict[str, int] = {}
        for row in articles:
            if not isinstance(row, Mapping):
                continue
            scope = _bounded_text(row.get("content_scope"), maximum=32) or "title_only"
            scope_counts[scope] = scope_counts.get(scope, 0) + 1
        compact_batch = {
            "request_id": raw_batch.get("request_id", ""),
            "event_id": raw_batch.get("event_id", ""),
            "article_count": len(articles),
            "content_scope_counts": scope_counts,
            "source_states": raw_batch.get("source_states", []),
        }
        migrated_state = {**state, "candidate_batch": compact_batch}
        raw_seed = migrated_state.get("profile_seed")
        if isinstance(raw_seed, dict) and isinstance(raw_seed.get("preference_events"), list):
            preference_ids = [
                str(row.get("event_id") or "")
                for row in cast("list[object]", raw_seed.get("preference_events"))
                if isinstance(row, Mapping) and row.get("event_id")
            ]
            migrated_state["profile_seed"] = {
                key: value for key, value in raw_seed.items() if key != "preference_events"
            } | {"preference_event_ids": preference_ids}
        return migrated_state, True

    def _write_bootstrap_state(self, payload: Mapping[str, object]) -> None:
        NewsSettingsStore._atomic_write(self.bootstrap_state_path, dict(payload))

    @staticmethod
    def _compact_bootstrap_result(result: BootstrapResult) -> dict[str, object]:
        """Persist onboarding evidence without copying article bodies into status polls."""

        batch = result.candidate_batch
        scope_counts: dict[str, int] = {}
        for article in batch.articles:
            scope_counts[article.content_scope] = scope_counts.get(article.content_scope, 0) + 1
        return {
            "request_id": result.request_id,
            "status": result.status,
            "stages": [asdict(stage) for stage in result.stages],
            "preference_events": [asdict(event) for event in result.preference_events],
            "profile_seed": {
                "seed_id": result.profile_seed.seed_id,
                "request_id": result.profile_seed.request_id,
                "schema_version": result.profile_seed.schema_version,
                "interests": list(result.profile_seed.interests),
                "avoid_terms": list(result.profile_seed.avoid_terms),
                "preference_event_ids": [
                    event.event_id for event in result.profile_seed.preference_events
                ],
            },
            "candidate_batch": {
                "request_id": batch.request_id,
                "event_id": batch.event_id,
                "article_count": len(batch.articles),
                "content_scope_counts": scope_counts,
                "source_states": [asdict(source) for source in batch.source_states],
            },
        }

    async def _write_manual_preference(self, event: ManualPreferenceEvent) -> None:
        target = "likes" if event.event_type == "manual_interest" else "dislikes"
        apply_edit = getattr(self.soul_engine, "apply_user_edit", None)
        if not callable(apply_edit):
            raise RuntimeError("profile edit service is unavailable")
        await apply_edit(
            target=target,
            op="add",
            value=event.value,
            weight=1.0,
            database=self.database,
        )
        # ``add`` creates the user-owned domain; ``set`` pins its weight so a
        # later AI rebuild may add evidence but can never dilute the explicit
        # first-run direction.
        await apply_edit(
            target=target,
            op="set",
            value=event.value,
            weight=1.0,
            database=self.database,
        )

    @staticmethod
    def _manual_events(seed: ManualProfileSeed) -> list[dict[str, Any]]:
        now = normalize_utc(_utc_now(), allow_empty=False)
        events: list[dict[str, Any]] = []
        for preference in seed.preference_events:
            interest = preference.event_type == "manual_interest"
            value = preference.value
            metadata: dict[str, object] = {
                "timestamp": now,
                "source": "manual_onboarding",
                "signal_strength": 1.0,
            }
            if interest:
                metadata.update(
                    {
                        "query": value,
                        "profile_update_owner": "generic",
                    }
                )
            else:
                metadata.update(
                    {
                        "feedback_type": "dislike",
                        "reaction": "thumbs_down",
                        "profile_update_owner": "content_feedback",
                    }
                )
            event = build_event(
                event_type="search" if interest else "feedback",
                source_platform="news",
                title=(f"长期关注：{value}" if interest else f"不想看到：{value}"),
                context=(
                    f"首次设置中，我明确要求新闻爪长期关注“{value}”"
                    if interest
                    else f"首次设置中，我明确要求新闻爪避开“{value}”"
                ),
                metadata=metadata,
            )
            event["ingest_key"] = preference.event_id
            events.append(event)
        return events

    async def _initialize_profile(self, seed: ManualProfileSeed) -> None:
        if self._profile_ready():
            return
        events = self._manual_events(seed)
        analyze = getattr(self.soul_engine, "analyze_events", None)
        build_profile = getattr(self.soul_engine, "build_initial_profile", None)
        if not callable(analyze) or not callable(build_profile):
            raise RuntimeError("AI profile bootstrap service is unavailable")
        receipt = await self.event_ingress.accept_batch(
            events,
            producer="newsclaw-bootstrap",
        )
        if int(getattr(receipt, "rejected", 0)):
            raise RuntimeError("manual onboarding evidence was rejected")
        await analyze(events)
        history = [
            {
                "title": f"用户明确的长期新闻方向：{value}",
                "event_type": "search",
                "source_platform": "news",
                "description": "首次设置中的手工方向，权重高于 AI 推断",
            }
            for value in seed.interests
        ]
        await build_profile(history)

    async def _run_bootstrap(
        self,
        *,
        interests: tuple[str, ...],
        avoid_terms: tuple[str, ...],
        request_id: str,
        input_digest: str,
    ) -> None:
        adapters, unavailable = self._build_adapters()
        registry = SourceRegistry(adapters)
        runner = self._runner()

        async def _publish(batch: CandidateBatch) -> None:
            await self.publish_candidates(batch.articles)

        coordinator = NewsBootstrapCoordinator(
            registry=registry,
            runner=runner,
            write_preference=self._write_manual_preference,
            publish_candidates=_publish,
            profile_initializer=self._initialize_profile,
        )
        try:
            result = await coordinator.bootstrap(
                interests=interests,
                avoid_terms=avoid_terms,
                request_id=request_id,
            )
            state = self._compact_bootstrap_result(result)
            state.update(
                {
                    "status": result.status,
                    "running": False,
                    "profile_ready": self._profile_ready(),
                    "completed_at": normalize_utc(_utc_now(), allow_empty=False),
                    "unavailable_sources": unavailable,
                    "input_digest": input_digest,
                }
            )
            self._write_bootstrap_state(state)
        except asyncio.CancelledError:
            self._write_bootstrap_state(
                {
                    "request_id": request_id,
                    "status": "failed",
                    "running": False,
                    "profile_ready": self._profile_ready(),
                    "detail": "首次设置因后端停止而中断，可安全重试",
                    "completed_at": normalize_utc(_utc_now(), allow_empty=False),
                    "input_digest": input_digest,
                }
            )
            raise
        except Exception as error:
            self._write_bootstrap_state(
                {
                    "request_id": request_id,
                    "status": "failed",
                    "running": False,
                    "profile_ready": self._profile_ready(),
                    "detail": safe_error_message(error),
                    "completed_at": normalize_utc(_utc_now(), allow_empty=False),
                    "unavailable_sources": unavailable,
                    "input_digest": input_digest,
                }
            )
        finally:
            await self._close_adapters(adapters)

    def start_bootstrap(self, payload: NewsBootstrapIn) -> dict[str, object]:
        interests = clean_bootstrap_terms(payload.interests, label="interest", maximum=10)
        avoid_terms = clean_bootstrap_terms(payload.avoid_terms, label="avoid", maximum=10)
        if not 3 <= len(interests) <= 10:
            raise ValueError("choose between 3 and 10 interest directions")
        overlap = {value.casefold() for value in interests} & {
            value.casefold() for value in avoid_terms
        }
        if overlap:
            raise ValueError("the same term cannot be both an interest and an avoid term")
        request_id = stable_request_id(
            "news-bootstrap",
            interests,
            avoid_terms,
            supplied=payload.request_id,
        )
        input_digest = stable_request_id("news-bootstrap-input", interests, avoid_terms)
        previous = self.bootstrap_state()
        previous_request_id = str(previous.get("request_id") or "")
        previous_input_digest = str(previous.get("input_digest") or "")
        same_request = previous_request_id == request_id
        same_payload = not previous_input_digest or previous_input_digest == input_digest
        if same_request and not same_payload:
            raise RuntimeError("request_id was already used for different onboarding choices")
        # A failed run may already have created the base profile before a later
        # manual-pin or source stage failed.  Retrying that same deterministic
        # request is safe: the profile initializer becomes a no-op and every
        # preference/source write is idempotent.
        if self._bootstrap_task is not None and not self._bootstrap_task.done():
            if same_request and same_payload:
                return {**self.bootstrap_state(), "duplicate": True, "replayed": True}
            raise RuntimeError("another onboarding request is already running")
        previous_status = str(previous.get("status") or "")
        if self._profile_ready():
            if same_request and same_payload and previous_status in {"completed", "degraded"}:
                return {**previous, "duplicate": True, "replayed": True}
            if previous_status != "failed":
                raise RuntimeError(
                    "NewsClaw profile already exists; edit it in Profile instead of overwriting it"
                )
            # A partial failure may have created the profile.  Only the exact
            # same command is allowed to finish the remaining stages.
            if previous_input_digest and not same_request:
                raise RuntimeError("retry must use the original onboarding request")
        state = {
            "request_id": request_id,
            "input_digest": input_digest,
            "status": "running",
            "running": True,
            "duplicate": False,
            "replayed": False,
            "profile_ready": self._profile_ready(),
            "started_at": normalize_utc(_utc_now(), allow_empty=False),
            "stages": [
                {
                    "name": "validate_preferences",
                    "status": "completed",
                    "completed": len(interests) + len(avoid_terms),
                    "total": len(interests) + len(avoid_terms),
                },
                {
                    "name": "seed_manual_memory",
                    "status": "running",
                    "completed": 0,
                    "total": len(interests) + len(avoid_terms),
                },
                {
                    "name": "fetch_initial_news",
                    "status": "pending",
                    "completed": 0,
                    "total": 0,
                },
                {
                    "name": "publish_candidate_pool",
                    "status": "pending",
                    "completed": 0,
                    "total": 1,
                },
            ],
        }
        self._write_bootstrap_state(state)
        self._bootstrap_task = asyncio.create_task(
            self._run_bootstrap(
                interests=interests,
                avoid_terms=avoid_terms,
                request_id=request_id,
                input_digest=input_digest,
            )
        )
        return state

    @staticmethod
    def _state_time(state: Mapping[str, object], key: str) -> datetime:
        raw = str(state.get(key) or "")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.fromtimestamp(0, UTC)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _scheduled_request_id(
        *,
        lane: str,
        now: datetime,
        interval_minutes: int,
    ) -> str:
        bucket_seconds = max(60, interval_minutes * 60)
        bucket = int(now.timestamp()) // bucket_seconds
        return stable_request_id("news-scheduled-refresh", lane, bucket)

    async def _run_due_scheduled_refresh(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Run at most one due source lane with a stable, replay-safe request ID."""

        current = now or _utc_now()
        settings = self.settings()
        state = self._stored_runtime_state()
        hot_minutes = _as_int(settings.get("hot_refresh_minutes", 30), 30)
        exploration_minutes = _as_int(
            settings.get("exploration_refresh_minutes", 360),
            360,
        )
        hot_due_at = self._state_time(state, "last_hot_refresh") + timedelta(minutes=hot_minutes)
        exploration_due_at = self._state_time(
            state,
            "last_exploration_refresh",
        ) + timedelta(minutes=exploration_minutes)
        hot_due = current >= hot_due_at
        exploration_due = current >= exploration_due_at
        # Only one lane runs per scheduler tick.  When both are due, select the
        # lane whose deadline passed first instead of always selecting hot.
        # Otherwise poll==hot (a normal configuration) can starve exploration
        # forever because hot becomes due again at every check.
        exploration_first = exploration_due and (not hot_due or exploration_due_at <= hot_due_at)
        if exploration_first:
            lane = "exploration"
            interval_minutes = exploration_minutes
            # Seven days is the initial conservative exploration horizon: it
            # broadens topics without turning each scheduled run into an
            # unbounded archive import. Recalibrate after source API metrics.
            hours = 24 * 7
            per_source_limit = 40
        elif hot_due:
            lane = "hot"
            interval_minutes = hot_minutes
            hours = 24
            per_source_limit = 80
        else:
            return {"ran": False, "reason": "not_due"}

        request_id = self._scheduled_request_id(
            lane=lane,
            now=current,
            interval_minutes=interval_minutes,
        )
        result = await self.refresh(
            request_id=request_id,
            hours=hours,
            per_source_limit=per_source_limit,
        )
        completed_at = str(result.get("completed_at") or normalize_utc(current, allow_empty=False))
        update: dict[str, object] = {
            f"last_{lane}_refresh": completed_at,
            "last_schedule_lane": lane,
            "last_schedule_request_id": request_id,
            "last_schedule_status": result.get("status", "offline"),
        }
        self._merge_runtime_state(update)
        return {"ran": True, "lane": lane, **result}

    async def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=60.0)
                continue
            except TimeoutError:
                pass
            if not self._profile_ready() or self._refresh_lock.locked():
                continue
            settings = self.settings()
            if settings.get("browser_close_behavior") == "pause" and not self.client_present():
                continue
            poll_minutes = _as_int(settings.get("refresh_poll_minutes", 30), 30)
            state = self._stored_runtime_state()
            now = _utc_now()
            if now - self._state_time(state, "last_scheduler_check") < timedelta(
                minutes=poll_minutes
            ):
                continue
            self._merge_runtime_state(
                {"last_scheduler_check": normalize_utc(now, allow_empty=False)}
            )
            try:
                await self._run_due_scheduled_refresh(now=now)
            except Exception:
                self._merge_runtime_state(
                    {
                        "last_refresh_attempt": normalize_utc(_utc_now(), allow_empty=False),
                        "last_refresh_status": "failed",
                    }
                )

    async def _backfill_event_projection(self) -> dict[str, object]:
        """Migrate legacy tracking history in short, restart-safe local batches."""

        backfill = getattr(self.database, "backfill_news_event_projection", None)
        if not callable(backfill):
            return {"ran": False, "reason": "event_projection_unavailable"}
        repair_origins = getattr(
            self.database,
            "repair_news_event_origin_memberships",
            None,
        )
        processed = 0
        repaired_trackers = 0
        self._merge_runtime_state({"event_backfill_status": "running"})
        try:
            while not self._stop_event.is_set():
                batch_result = await asyncio.to_thread(backfill, batch_size=50)
                if not isinstance(batch_result, Mapping):
                    raise RuntimeError("event projection backfill returned an invalid result")
                processed += _as_int(batch_result.get("processed_trackers"), 0)
                state = batch_result.get("state")
                completed = bool(isinstance(state, Mapping) and state.get("completed", False))
                self._merge_runtime_state(
                    {
                        "event_backfill_status": "completed" if completed else "running",
                        "event_backfill_processed_trackers": processed,
                        "event_backfill_last_error": "",
                    }
                )
                if completed:
                    break
                await asyncio.sleep(0)
            if self._stop_event.is_set():
                return {"ran": True, "completed": False, "processed": processed}
            if callable(repair_origins):
                self._merge_runtime_state({"event_origin_repair_status": "running"})
                while not self._stop_event.is_set():
                    repair_result = await asyncio.to_thread(repair_origins, batch_size=50)
                    if not isinstance(repair_result, Mapping):
                        raise RuntimeError("event origin repair returned an invalid result")
                    repaired_trackers += _as_int(repair_result.get("processed_trackers"), 0)
                    repair_state = repair_result.get("state")
                    repair_completed = bool(
                        isinstance(repair_state, Mapping) and repair_state.get("completed", False)
                    )
                    self._merge_runtime_state(
                        {
                            "event_origin_repair_status": (
                                "completed" if repair_completed else "running"
                            ),
                            "event_origin_repair_processed_trackers": repaired_trackers,
                            "event_origin_repair_last_error": "",
                        }
                    )
                    if repair_completed:
                        break
                    await asyncio.sleep(0)
            final_result: dict[str, object] = {
                "ran": True,
                "completed": True,
                "processed": processed,
            }
            if callable(repair_origins):
                final_result["repaired_trackers"] = repaired_trackers
            return final_result
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = safe_error_message(error) or type(error).__name__
            self._merge_runtime_state(
                {
                    "event_backfill_status": "failed",
                    "event_backfill_processed_trackers": processed,
                    "event_backfill_last_error": message,
                    "event_origin_repair_status": "failed",
                    "event_origin_repair_processed_trackers": repaired_trackers,
                    "event_origin_repair_last_error": message,
                }
            )
            logger.warning("NewsClaw event backfill paused: %s", message)
            return {"ran": True, "completed": False, "processed": processed, "error": message}
        return {"ran": True, "completed": False, "processed": processed}

    async def start(self) -> None:
        self._stop_event.clear()
        await self.apply_settings()
        if self._event_backfill_task is None or self._event_backfill_task.done():
            self._event_backfill_task = asyncio.create_task(self._backfill_event_projection())
            self._background_tasks.add(self._event_backfill_task)
            self._event_backfill_task.add_done_callback(self._background_tasks.discard)
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        for lifecycle_task in (self._bootstrap_task, self._scheduler_task):
            if lifecycle_task is not None and not lifecycle_task.done():
                lifecycle_task.cancel()
                with suppress(asyncio.CancelledError):
                    await lifecycle_task
        if self._background_tasks:
            for background_task in self._background_tasks:
                background_task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()


def _replace_source(
    store: NewsSettingsStore,
    source_id: str,
    update: NewsSourceUpdateIn,
) -> NewsSourceConfig:
    payload = store.load()
    rows = cast("list[dict[str, object]]", payload["sources"])
    found: NewsSourceConfig | None = None
    updated_rows: list[dict[str, object]] = []
    changes = update.model_dump(exclude_none=True)
    for row in rows:
        source = NewsSourceConfig.from_dict(row)
        if source.source_id != source_id:
            updated_rows.append(asdict(source))
            continue
        if ("media" in changes or "url" in changes) and not source.custom:
            raise ValueError("内置来源只允许调整开关、权重和刷新频率")
        merged = {**asdict(source), **changes}
        found = NewsSourceConfig.from_dict(merged)
        updated_rows.append(asdict(found))
    if found is None:
        raise KeyError(source_id)
    payload["sources"] = updated_rows
    store.save(payload)
    return found


def register_newsclaw_routes(
    app: FastAPI,
    *,
    data_path: Path,
    database: Any,
    soul_engine: Any,
    event_ingress: Any,
    request_replenishment: MaybeAwaitable,
    client_present: Callable[[], bool] | None = None,
    apply_runtime_settings: RuntimeSettingsApplier | None = None,
    llm_service_provider: Callable[[], Any] | None = None,
    llm_concurrency_provider: Callable[[], Any] | None = None,
) -> NewsRuntime:
    """Register NewsClaw's additive API without altering legacy deployments."""

    runtime = NewsRuntime(
        data_path=data_path,
        database=database,
        soul_engine=soul_engine,
        event_ingress=event_ingress,
        request_replenishment=request_replenishment,
        client_present=client_present,
        apply_runtime_settings=apply_runtime_settings,
        llm_service_provider=llm_service_provider,
        llm_concurrency_provider=llm_concurrency_provider,
    )
    app.state.newsclaw_runtime = runtime
    register_research_routes(app, runtime=runtime)

    def _resolved_news_event(event_id: str) -> tuple[dict[str, Any], str]:
        try:
            row = database.get_news_event(event_id, resolve_merged=True)
        except RuntimeError as error:
            raise HTTPException(
                status_code=409, detail="news event merge chain is invalid"
            ) from error
        if not isinstance(row, Mapping):
            raise HTTPException(status_code=404, detail="news event not found")
        item = dict(row)
        resolved_id = str(item.get("event_id") or "")
        return item, event_id if resolved_id != event_id else ""

    def _uuid_text(value: str, *, field: str) -> str:
        try:
            return str(uuid.UUID(value))
        except (AttributeError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=f"invalid {field}") from error

    def _objective_change_out(row: Mapping[str, Any]) -> NewsObjectiveChangeOut:
        item = dict(row)
        if not item.get("canonical_title"):
            event, _resolved_from = _resolved_news_event(str(item.get("event_id") or ""))
            for field in ("canonical_title", "topic_group", "story_key"):
                item[field] = event.get(field, "")
        return NewsObjectiveChangeOut.model_validate(item)

    def _objective_change_page(
        *,
        event_id: str,
        unread_only: bool,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[NewsObjectiveChangeOut], str | None, bool]:
        before_detected_at = ""
        before_change_id = ""
        if cursor is not None:
            try:
                before_detected_at, before_change_id = _decode_news_change_cursor(
                    cursor,
                    event_id=event_id,
                    unread_only=unread_only,
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error)) from error
        rows = database.list_news_event_changes(
            event_id=event_id,
            unread_only=unread_only,
            lifecycle="active",
            before_detected_at=before_detected_at,
            before_change_id=before_change_id,
            limit=limit + 1,
            objective_only=True,
        )
        page = [dict(row) for row in rows[:limit] if isinstance(row, Mapping)]
        has_more = len(rows) > limit
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = _encode_news_change_cursor(
                event_id=event_id,
                unread_only=unread_only,
                position=(
                    str(last.get("detected_at") or ""),
                    str(last.get("change_id") or ""),
                ),
            )
        return [_objective_change_out(row) for row in page], next_cursor, has_more

    @app.get("/api/news/changes", response_model=NewsChangeFeedResponse)
    async def news_objective_changes(
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = Query(
            default=None,
            min_length=1,
            max_length=_NEWS_CHANGE_CURSOR_MAX_LENGTH,
        ),
        unread_only: bool = Query(default=False),
    ) -> NewsChangeFeedResponse:
        items, next_cursor, has_more = _objective_change_page(
            event_id="",
            unread_only=unread_only,
            limit=limit,
            cursor=cursor,
        )
        return NewsChangeFeedResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get("/api/news/events/{event_id}", response_model=NewsEventDetailResponse)
    async def news_event_detail(event_id: str) -> NewsEventDetailResponse:
        requested_id = _uuid_text(event_id, field="event_id")
        item, resolved_from = _resolved_news_event(requested_id)
        return NewsEventDetailResponse(
            item=NewsEventOut.model_validate(item),
            resolved_from_event_id=resolved_from,
        )

    @app.get(
        "/api/news/events/{event_id}/changes",
        response_model=NewsEventChangeTimelineResponse,
    )
    async def news_event_changes(
        event_id: str,
        limit: int = Query(default=30, ge=1, le=100),
        cursor: str | None = Query(
            default=None,
            min_length=1,
            max_length=_NEWS_CHANGE_CURSOR_MAX_LENGTH,
        ),
        unread_only: bool = Query(default=False),
    ) -> NewsEventChangeTimelineResponse:
        event, _resolved_from = _resolved_news_event(_uuid_text(event_id, field="event_id"))
        canonical_event_id = str(event["event_id"])
        items, next_cursor, has_more = _objective_change_page(
            event_id=canonical_event_id,
            unread_only=unread_only,
            limit=limit,
            cursor=cursor,
        )
        return NewsEventChangeTimelineResponse(
            event=NewsEventOut.model_validate(event),
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @app.get(
        "/api/news/events/{event_id}/conflicts",
        response_model=NewsEventConflictPage,
    )
    async def news_event_conflicts(
        event_id: str,
        limit: int = Query(default=30, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        unread_only: bool = Query(default=False),
    ) -> NewsEventConflictPage:
        event, _resolved_from = _resolved_news_event(_uuid_text(event_id, field="event_id"))
        canonical_event_id = str(event["event_id"])
        rows = database.list_news_event_changes(
            event_id=canonical_event_id,
            unread_only=unread_only,
            lifecycle="active",
            limit=limit + 1,
            offset=offset,
            objective_only=False,
            conflicts_only=True,
        )
        visible = rows[:limit]
        has_more = len(rows) > limit
        return NewsEventConflictPage(
            event=NewsEventOut.model_validate(event),
            items=[NewsEventConflictOut.model_validate(row) for row in visible],
            next_offset=offset + limit if has_more else None,
            has_more=has_more,
        )

    @app.get(
        "/api/news/events/{event_id}/evidence",
        response_model=NewsEventEvidencePage,
    )
    async def news_event_evidence(
        event_id: str,
        change_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> NewsEventEvidencePage:
        event, _resolved_from = _resolved_news_event(_uuid_text(event_id, field="event_id"))
        canonical_event_id = str(event["event_id"])
        normalized_change_id = (
            _uuid_text(change_id, field="change_id") if change_id is not None else ""
        )
        resolved_from_change_id = ""
        if normalized_change_id:
            requested_change_id = normalized_change_id
            try:
                change = database.get_news_event_change(requested_change_id)
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="news event change alias is inconsistent",
                ) from exc
            if (
                not isinstance(change, Mapping)
                or str(change.get("event_id") or "") != canonical_event_id
            ):
                raise HTTPException(status_code=404, detail="news event change not found")
            normalized_change_id = str(change.get("change_id") or "")
            if normalized_change_id != requested_change_id:
                resolved_from_change_id = requested_change_id
        rows = database.list_news_event_memberships(
            canonical_event_id,
            change_id=normalized_change_id,
            limit=limit + 1,
            offset=offset,
        )
        page = [dict(row) for row in rows[:limit] if isinstance(row, Mapping)]
        has_more = len(rows) > limit
        return NewsEventEvidencePage(
            event_id=canonical_event_id,
            change_id=normalized_change_id or None,
            resolved_from_change_id=resolved_from_change_id,
            items=[NewsEventEvidenceOut.model_validate(row) for row in page],
            next_offset=offset + limit if has_more else None,
            has_more=has_more,
        )

    @app.post(
        "/api/news/changes/{change_id}/read",
        response_model=NewsEventChangeReadResponse,
    )
    async def read_news_event_change(change_id: str) -> NewsEventChangeReadResponse:
        normalized_change_id = _uuid_text(change_id, field="change_id")
        row, updated = database.mark_news_event_change_read(
            normalized_change_id,
            objective_only=True,
        )
        if not isinstance(row, Mapping):
            raise HTTPException(status_code=404, detail="news event change not found")
        refreshed = database.get_news_event_change(
            normalized_change_id,
            objective_only=True,
        )
        if not isinstance(refreshed, Mapping):  # pragma: no cover - same transaction invariant
            raise HTTPException(status_code=409, detail="news event change is inconsistent")
        return NewsEventChangeReadResponse(
            item=_objective_change_out(refreshed),
            updated=updated,
        )

    @app.post(
        "/api/news/conflicts/{change_id}/read",
        response_model=NewsEventConflictReadResponse,
    )
    async def read_news_event_conflict(
        change_id: str,
    ) -> NewsEventConflictReadResponse:
        normalized_change_id = _uuid_text(change_id, field="change_id")
        row, updated = database.mark_news_event_change_read(
            normalized_change_id,
            objective_only=False,
            conflicts_only=True,
        )
        if not isinstance(row, Mapping):
            raise HTTPException(status_code=404, detail="news event conflict not found")
        return NewsEventConflictReadResponse(
            item=NewsEventConflictOut.model_validate(row),
            updated=updated,
        )

    @app.on_event("startup")
    async def _start_newsclaw_runtime() -> None:
        await runtime.start()

    @app.on_event("shutdown")
    async def _stop_newsclaw_runtime() -> None:
        await runtime.stop()

    @app.get("/api/news/sources")
    async def news_sources() -> dict[str, object]:
        return {
            "sources": runtime.public_sources(),
            "settings": runtime.settings(),
            "runtime": runtime.runtime_state(),
        }

    @app.get("/api/news/credentials/tushare")
    async def tushare_credential_status() -> dict[str, object]:
        return {
            "configured": runtime._tushare_token_configured(),
            "stored_locally": bool(runtime._local_tushare_token()),
            "masked": "••••••••" if runtime._tushare_token_configured() else "",
        }

    @app.put("/api/news/credentials/tushare")
    async def update_tushare_credential(
        payload: NewsTushareCredentialIn,
    ) -> dict[str, object]:
        try:
            runtime.set_tushare_token(payload.token)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"ok": True, "configured": True, "stored_locally": True, "masked": "••••••••"}

    @app.delete("/api/news/credentials/tushare")
    async def delete_tushare_credential() -> dict[str, object]:
        runtime.clear_tushare_token()
        return {
            "ok": True,
            "configured": runtime._tushare_token_configured(),
            "stored_locally": False,
            "masked": "••••••••" if runtime._tushare_token_configured() else "",
        }

    @app.get("/api/news/settings")
    async def news_settings() -> dict[str, object]:
        return runtime.settings()

    @app.put("/api/news/settings")
    async def update_news_settings(payload: NewsSettingsUpdateIn) -> dict[str, object]:
        current = runtime.settings_store.load()
        settings = cast("dict[str, object]", current["settings"])
        settings.update(payload.model_dump(exclude_none=True))
        current["settings"] = settings
        saved = runtime.settings_store.save(current)
        effective = await runtime.apply_settings(cast("Mapping[str, object]", saved["settings"]))
        return {"ok": True, "settings": saved["settings"], "effective_settings": effective}

    @app.put("/api/news/sources/{source_id:path}")
    async def update_news_source(
        source_id: str,
        payload: NewsSourceUpdateIn,
    ) -> dict[str, object]:
        try:
            source = _replace_source(runtime.settings_store, source_id, payload)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="新闻来源不存在") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"ok": True, "source": asdict(source)}

    @app.post("/api/news/sources/rss", status_code=status.HTTP_201_CREATED)
    async def add_custom_rss(payload: NewsCustomRSSIn) -> dict[str, object]:
        normalized_url = payload.url.strip()
        normalized_media = payload.media.strip()
        source_id = stable_request_id("custom-rss", normalized_url).replace("nrq_", "rss:custom:")
        source = NewsSourceConfig(
            source_id=source_id,
            kind="rss",
            media=normalized_media,
            provider="自定义 RSS/Atom",
            url=normalized_url,
            enabled=payload.enabled,
            weight=payload.weight,
            refresh_minutes=payload.refresh_minutes,
            custom=True,
        )
        current = runtime.settings_store.load()
        rows = cast("list[dict[str, object]]", current["sources"])
        existing = next(
            (row for row in rows if str(row.get("source_id")) == source_id),
            None,
        )
        if existing is not None:
            if asdict(NewsSourceConfig.from_dict(existing)) == asdict(source):
                return {"ok": True, "source": asdict(source), "duplicate": True}
            raise HTTPException(
                status_code=409,
                detail="This RSS URL already exists with different settings",
            )
        rows.append(asdict(source))
        current["sources"] = rows
        runtime.settings_store.save(current)
        return {"ok": True, "source": asdict(source), "duplicate": False}

    @app.delete("/api/news/sources/{source_id:path}")
    async def delete_custom_rss(source_id: str) -> dict[str, object]:
        current = runtime.settings_store.load()
        rows = cast("list[dict[str, object]]", current["sources"])
        target = next(
            (
                NewsSourceConfig.from_dict(row)
                for row in rows
                if str(row.get("source_id")) == source_id
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="新闻来源不存在")
        if not target.custom:
            raise HTTPException(status_code=403, detail="内置来源不能删除，只能关闭")
        current["sources"] = [row for row in rows if str(row.get("source_id")) != source_id]
        runtime.settings_store.save(current)
        return {"ok": True, "source_id": source_id}

    @app.post("/api/news/sources/probe")
    async def probe_news_sources(payload: NewsProbeIn) -> dict[str, object]:
        return await runtime.probe(set(payload.source_ids) or None)

    @app.post("/api/news/refresh")
    async def refresh_news(payload: NewsRefreshIn) -> dict[str, object]:
        try:
            return await runtime.refresh(
                request_id=payload.request_id,
                hours=payload.hours,
                per_source_limit=payload.per_source_limit,
            )
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/news/runtime")
    async def news_runtime_status() -> dict[str, object]:
        sources = runtime.public_sources()
        return {
            **runtime.runtime_state(),
            "status": runtime._aggregate_source_status(sources),
            "sources": sources,
        }

    @app.get("/api/news/bootstrap/status")
    async def news_bootstrap_status() -> dict[str, object]:
        return runtime.bootstrap_state()

    @app.post("/api/news/bootstrap", status_code=status.HTTP_202_ACCEPTED)
    async def start_news_bootstrap(payload: NewsBootstrapIn) -> dict[str, object]:
        try:
            return runtime.start_bootstrap(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/news/articles/{article_id}")
    async def news_article_evidence(article_id: str) -> dict[str, object]:
        row = (
            runtime.database.get_content(article_id)
            if hasattr(runtime.database, "get_content")
            else None
        )
        if not isinstance(row, Mapping):
            raise HTTPException(status_code=404, detail="新闻条目不存在")
        raw_payload = row.get("raw_payload")
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                raw_payload = {}
        return dict(raw_payload) if isinstance(raw_payload, Mapping) else {}

    return runtime


def backup_existing_database(source: Path, backup_dir: Path) -> Path:
    """Timestamp-copy an old database before an eventual explicit cutover."""

    if not source.is_file():
        raise FileNotFoundError(source)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{source.stem}-{stamp}{source.suffix}.bak"
    shutil.copy2(source, target)
    return target
