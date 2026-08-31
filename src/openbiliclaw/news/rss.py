"""Official and custom RSS/Atom feed adapter (no page crawling)."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast
from xml.etree import ElementTree

import httpx

from openbiliclaw.news.errors import (
    NewsSourceError,
    SourcePermissionDenied,
    SourceRateLimited,
    SourceSchemaError,
    SourceTimedOut,
    SourceTransientError,
)
from openbiliclaw.news.models import Article, ContentScope, build_article, normalize_utc
from openbiliclaw.news.protocol import FetchWindow, SourceProbe

# The two documented National Bureau of Statistics feeds are currently about
# 4.2–4.6 MB.  Keep a bounded ceiling above that real contract while still
# rejecting unexpectedly large custom feeds before XML parsing.
RSS_MAX_RESPONSE_BYTES = 8_000_000
RSS_USER_AGENT = "NewsClaw/0.1 (local personal RSS/Atom reader)"
_ALLOWED_TYPES = {
    "application/atom+xml",
    "application/rss+xml",
    "application/xml",
    "text/xml",
    "text/plain",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _strip_markup(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _child_text(element: ElementTree.Element, *names: str) -> str:
    for name in names:
        for child in _children(element, name):
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def _entry_link(element: ElementTree.Element) -> str:
    for child in _children(element, "link"):
        href = str(child.attrib.get("href") or "").strip()
        rel = str(child.attrib.get("rel") or "alternate").strip().casefold()
        if href and rel in {"", "alternate"}:
            return href
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def _parse_feed_time(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _feed_entries(xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise SourceSchemaError("RSS/Atom feed returned malformed XML") from error
    root_name = _local_name(root.tag)
    if root_name == "feed":
        entries = _children(root, "entry")
        feed_kind = "atom"
    else:
        entries = [element for element in root.iter() if _local_name(element.tag) == "item"]
        feed_kind = "rss"
    if root_name not in {"feed", "rss", "rdf"} and not entries:
        raise SourceSchemaError("RSS/Atom feed has an unsupported root element")

    rows: list[dict[str, Any]] = []
    for entry in entries[:1000]:
        title = _strip_markup(_child_text(entry, "title"))
        encoded = _child_text(entry, "encoded")
        atom_content = _child_text(entry, "content") if feed_kind == "atom" else ""
        summary = _child_text(entry, "summary", "description")
        body_source = encoded or atom_content or summary
        scope: ContentScope = "full_text" if (encoded or atom_content) else "summary"
        if not body_source:
            scope = "title_only"
        link = _entry_link(entry)
        guid = _child_text(entry, "id", "guid")
        published = _child_text(entry, "published", "pubdate", "updated", "date")
        rows.append(
            {
                "title": title,
                "body": _strip_markup(body_source),
                "content_scope": scope,
                "url": link,
                "guid": guid,
                "published_at": _parse_feed_time(published),
            }
        )
    return rows


class RSSAdapter:
    """Read one site-provided RSS or Atom subscription feed."""

    def __init__(
        self,
        *,
        url: str,
        media: str,
        source_id: str = "",
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        clean_url = url.strip()
        try:
            parsed = httpx.URL(clean_url)
        except Exception as error:
            raise ValueError("RSS URL is invalid") from error
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("RSS URL must use HTTP or HTTPS")
        clean_media = " ".join(media.split()).strip()
        if not clean_media:
            raise ValueError("media is required")
        self._url = clean_url
        self._media = clean_media
        suffix = hashlib.sha256(clean_url.encode()).hexdigest()[:20]
        self._source_id = source_id.strip() or f"rss:{suffix}"
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self._owns_http = http_client is None
        self._latest_entry_published_at = ""
        self._feed_is_stale = False
        self._http = http_client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=True,
            headers={"User-Agent": RSS_USER_AGENT},
        )

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def media(self) -> str:
        return self._media

    @property
    def provider(self) -> str:
        return "RSS/Atom"

    @property
    def latest_entry_published_at(self) -> str:
        """Newest dated entry observed in the feed, even outside the fetch window."""

        return self._latest_entry_published_at

    @property
    def feed_is_stale(self) -> bool:
        """Whether the feed's newest dated entry is stale by the shared news policy."""

        return self._feed_is_stale

    async def aclose(self) -> None:
        """Close only the internally owned HTTP client."""

        if self._owns_http:
            await self._http.aclose()

    async def fetch(self, window: FetchWindow) -> list[Article]:
        """Fetch a declared feed and normalize entries within the request window."""

        response = await self._request()
        rows = _feed_entries(response.text)
        retrieved_at = datetime.now(UTC)
        articles: list[Article] = []
        latest_dated_article: Article | None = None
        self._latest_entry_published_at = ""
        self._feed_is_stale = False
        for row in rows:
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            article = build_article(
                media=self.media,
                provider=self.provider,
                title=title,
                body=row.get("body") or "",
                canonical_url=row.get("url") or "",
                published_at=row.get("published_at") or "",
                retrieved_at=retrieved_at,
                content_scope=cast("ContentScope", row.get("content_scope") or "title_only"),
                source_identifier=row.get("guid") or row.get("url") or title,
            )
            if article.published_at and (
                latest_dated_article is None
                or article.published_at > latest_dated_article.published_at
            ):
                latest_dated_article = article
            if article.published_at:
                published = datetime.fromisoformat(article.published_at.replace("Z", "+00:00"))
                if published < window.start_at.astimezone(
                    UTC
                ) or published > window.end_at.astimezone(UTC):
                    continue
            articles.append(article)
        if latest_dated_article is not None:
            self._latest_entry_published_at = latest_dated_article.published_at
            # Reuse Article's calibrated 30-day policy instead of inventing a
            # second RSS-only threshold.  A reachable feed whose newest item
            # is already stale is not a healthy source of current news.
            self._feed_is_stale = latest_dated_article.freshness == "stale"
        articles.sort(key=lambda article: article.published_at, reverse=True)
        return articles[: window.limit]

    async def probe(self) -> SourceProbe:
        checked_at = normalize_utc(datetime.now(UTC), allow_empty=False)
        try:
            articles = await self.fetch(FetchWindow.recent(hours=24 * 30, limit=1))
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
            detail="Feed call succeeded" if articles else "Feed call succeeded; no current entries",
        )

    async def _request(self) -> httpx.Response:
        try:
            async with self._http.stream(
                "GET",
                self._url,
                timeout=self._timeout_seconds,
                follow_redirects=True,
                headers={
                    "Accept": "application/rss+xml, application/atom+xml, application/xml",
                    "User-Agent": RSS_USER_AGENT,
                },
            ) as streamed:
                declared = streamed.headers.get("Content-Length", "").strip()
                try:
                    if declared and int(declared) > RSS_MAX_RESPONSE_BYTES:
                        raise SourceSchemaError("RSS/Atom response exceeded the safe size limit")
                except ValueError:
                    pass
                body = bytearray()
                async for chunk in streamed.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > RSS_MAX_RESPONSE_BYTES:
                        raise SourceSchemaError("RSS/Atom response exceeded the safe size limit")
                response_headers = httpx.Headers(streamed.headers)
                # ``aiter_bytes`` already decodes transfer/content encodings.
                # Reusing those headers on a materialized response would make
                # httpx decode gzip/br a second time.
                for header in ("content-encoding", "content-length", "transfer-encoding"):
                    response_headers.pop(header, None)
                response = httpx.Response(
                    streamed.status_code,
                    headers=response_headers,
                    content=bytes(body),
                    request=streamed.request,
                )
        except httpx.TimeoutException as error:
            raise SourceTimedOut("RSS/Atom request timed out") from error
        except httpx.HTTPError as error:
            raise SourceTransientError("RSS/Atom network request failed") from error
        status = int(response.status_code)
        if status in {401, 403}:
            raise SourcePermissionDenied(f"RSS/Atom endpoint returned HTTP {status}")
        if status == 429:
            retry_after = response.headers.get("Retry-After", "").strip()
            try:
                retry_seconds = max(0.0, float(retry_after))
            except ValueError:
                retry_seconds = None
            raise SourceRateLimited(
                "RSS/Atom endpoint rate limited this client",
                retry_after_seconds=retry_seconds,
            )
        if status >= 500:
            raise SourceTransientError(f"RSS/Atom endpoint returned HTTP {status}")
        if not 200 <= status < 300:
            raise SourceSchemaError(f"RSS/Atom endpoint returned HTTP {status}")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        prefix = response.text.lstrip("\ufeff \t\r\n")[:32].casefold()
        if content_type and content_type not in _ALLOWED_TYPES:
            raise SourceSchemaError("RSS/Atom endpoint returned an unsupported content type")
        if not prefix.startswith(("<?xml", "<rss", "<feed", "<rdf")):
            raise SourceSchemaError("RSS/Atom endpoint did not return an XML feed")
        return response
