"""Tushare Pro news endpoint adapters.

The module never imports or initializes Tushare itself.  The application owns
credential loading and injects an already-configured ``pro_api`` client, which
keeps tokens out of this package, tests, logs, and task records.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from openbiliclaw.news.errors import NewsSourceError, classify_provider_exception
from openbiliclaw.news.models import Article, build_article, normalize_utc
from openbiliclaw.news.protocol import FetchWindow, SourceProbe

if TYPE_CHECKING:
    from collections.abc import Callable

TushareEndpoint = Literal["news", "major_news", "cctv_news"]
_CHINA_TZ = timezone(timedelta(hours=8))


class TushareClient(Protocol):
    """Subset of a configured Tushare Pro client used by NewsClaw."""

    def news(self, **kwargs: object) -> object:
        """Return short-form news rows."""
        ...

    def major_news(self, **kwargs: object) -> object:
        """Return long-form news rows."""
        ...

    def cctv_news(self, **kwargs: object) -> object:
        """Return CCTV News Broadcast rows for one day."""
        ...


def _records(payload: object) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list | tuple):
        return [dict(row) for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        nested = payload.get("data")
        if isinstance(nested, list | tuple):
            return [dict(row) for row in nested if isinstance(row, Mapping)]
        return [dict(payload)]
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict(orient="records")
        except TypeError:
            converted = to_dict("records")
        if isinstance(converted, list | tuple):
            return [dict(row) for row in converted if isinstance(row, Mapping)]
    raise TypeError("Tushare returned an unsupported table envelope")


def _first(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            continue
        text = str(value).strip()
        if text and text.casefold() not in {"<na>", "nan", "nat", "none", "null"}:
            return text
    return ""


def _tushare_timestamp(value: str) -> object:
    """Attach the endpoint's documented China Standard Time to naive rows."""

    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for format_text in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(value, format_text)
                break
            except ValueError:
                continue
        else:
            return value
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=_CHINA_TZ)


def _title_from_body(body: str) -> str:
    if not body:
        return ""
    for separator in ("。", "！", "？", "\n"):
        prefix = body.split(separator, 1)[0].strip()
        if prefix:
            return prefix[:120]
    return body[:120]


class TushareNewsAdapter:
    """Normalize one Tushare ``news``/``major_news``/``cctv_news`` feed."""

    def __init__(
        self,
        *,
        client: TushareClient,
        endpoint: TushareEndpoint,
        media: str,
        source_code: str = "",
        source_id: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if endpoint not in {"news", "major_news", "cctv_news"}:
            raise ValueError("unsupported Tushare news endpoint")
        clean_media = " ".join(media.split()).strip()
        if not clean_media:
            raise ValueError("media is required")
        self._client = client
        self._endpoint = endpoint
        self._media = clean_media
        self._source_code = source_code.strip()
        identity = f"{endpoint}\x1f{self._source_code}\x1f{clean_media}"
        suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
        self._source_id = source_id.strip() or f"tushare:{endpoint}:{suffix}"
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def media(self) -> str:
        return self._media

    @property
    def provider(self) -> str:
        return f"Tushare Pro · {self._endpoint}"

    async def fetch(self, window: FetchWindow) -> list[Article]:
        """Call the configured synchronous Tushare client off the event loop."""

        try:
            rows = await asyncio.to_thread(self._fetch_rows, window)
            articles = self._normalize_rows(rows, retrieved_at=self._clock())
        except Exception as error:
            raise classify_provider_exception(error) from error
        articles.sort(key=lambda article: article.published_at, reverse=True)
        return articles[: window.limit]

    async def probe(self) -> SourceProbe:
        """Verify this endpoint's independent news permission."""

        checked_at = normalize_utc(self._clock(), allow_empty=False)
        try:
            end_at = self._clock().astimezone(UTC)
            articles = await self.fetch(FetchWindow(end_at - timedelta(hours=24), end_at, limit=1))
        except NewsSourceError as error:
            status_map = {
                "permission_denied": "permission_denied",
                "rate_limited": "rate_limited",
                "timed_out": "timed_out",
            }
            status = status_map.get(error.code, "unavailable")
            return SourceProbe(
                source_id=self.source_id,
                media=self.media,
                provider=self.provider,
                status=cast("Any", status),
                available=False,
                checked_at=checked_at,
                detail=str(error),
                error_code=error.code,
            )
        return SourceProbe(
            source_id=self.source_id,
            media=self.media,
            provider=self.provider,
            status="available" if articles else "empty",
            available=True,
            checked_at=checked_at,
            detail="Endpoint call succeeded" if articles else "Endpoint call succeeded; no rows",
        )

    def _fetch_rows(self, window: FetchWindow) -> list[dict[str, Any]]:
        start = window.start_at.astimezone(_CHINA_TZ)
        end = window.end_at.astimezone(_CHINA_TZ)
        if self._endpoint in {"news", "major_news"}:
            method = cast("Callable[..., object]", getattr(self._client, self._endpoint))
            kwargs: dict[str, object] = {
                "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            }
            if self._source_code:
                kwargs["src"] = self._source_code
            return _records(method(**kwargs))

        method = cast("Callable[..., object]", self._client.cctv_news)
        rows: list[dict[str, Any]] = []
        day = end.date()
        first_day = start.date()
        # The official endpoint is day-scoped.  Bound one refresh to 31 calls
        # even if an invalid/overbroad window reaches the adapter.
        for _ in range(31):
            rows.extend(_records(method(date=day.strftime("%Y%m%d"))))
            if day <= first_day or len(rows) >= window.limit:
                break
            day -= timedelta(days=1)
        return rows

    def _normalize_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        retrieved_at: datetime,
    ) -> list[Article]:
        articles: list[Article] = []
        for row in rows:
            body = _first(row, "content", "body", "summary", "description")
            title = _first(row, "title", "name", "headline") or _title_from_body(body)
            if not title:
                continue
            # ``news`` and ``major_news`` can return a mixed-media batch when
            # ``src`` is omitted.  Preserve the provider-owned row label so the
            # UI never attributes every row to the adapter's fallback card.
            row_media = _first(row, "src", "source", "media") or self.media
            published = _tushare_timestamp(
                _first(row, "datetime", "pub_time", "published_at", "date", "time")
            )
            url = _first(row, "url", "link", "canonical_url")
            source_identifier = _first(row, "id", "news_id", "ann_id", "guid")
            if not source_identifier:
                source_identifier = f"{published}\x1f{url}\x1f{title}"
            articles.append(
                build_article(
                    media=row_media,
                    provider=self.provider,
                    title=title,
                    body=body,
                    canonical_url=url,
                    published_at=published,
                    retrieved_at=retrieved_at,
                    content_scope="full_text" if body else "title_only",
                    source_identifier=source_identifier,
                )
            )
        return articles
