"""Canonical news article model shared by every NewsClaw source."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ContentScope = Literal["title_only", "summary", "full_text"]
Freshness = Literal["fresh", "aging", "stale", "future", "unknown"]

_SPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MISSING_TEXT = frozenset({"<na>", "nan", "nat", "none", "null"})
_TRACKING_QUERY_KEYS = frozenset(
    {
        "from",
        "spm",
        "utm_campaign",
        "utm_content",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)

# Product policy, not a learned relevance threshold: news is visibly "fresh"
# for three days, "aging" for thirty, and stale thereafter.  The labels affect
# expiry/review scheduling only and must be recalibrated if that policy changes.
_FRESH_WINDOW = timedelta(hours=72)
_AGING_WINDOW = timedelta(days=30)
_FUTURE_CLOCK_TOLERANCE = timedelta(minutes=10)


def _clean_text(value: object, *, maximum: int) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _CONTROL_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if text.casefold() in _MISSING_TEXT:
        return ""
    return text[:maximum]


def canonicalize_url(value: object) -> str:
    """Return a bounded HTTP(S) URL with common tracking parameters removed."""

    raw = _clean_text(value, maximum=4096)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    netloc = parsed.netloc.casefold()
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, urlencode(query), ""))[:4096]


def normalize_utc(value: object, *, allow_empty: bool = True) -> str:
    """Normalize supported timestamps to a seconds-precision UTC ISO string."""

    if value is None or value == "":
        if allow_empty:
            return ""
        raise ValueError("timestamp is required")
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        formats = (
            "%Y%m%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y%m%d",
            "%Y-%m-%d",
        )
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for format_text in formats:
                try:
                    parsed = datetime.strptime(text, format_text).replace(tzinfo=UTC)
                    break
                except ValueError:
                    continue
            else:
                if allow_empty:
                    return ""
                raise ValueError("timestamp is invalid") from None
    if parsed.tzinfo is None:
        # Generic canonical data treats an unspecified offset as UTC. Adapters
        # with a documented local clock (Tushare uses China Standard Time) must
        # attach that timezone before entering the shared model.
        parsed = parsed.replace(tzinfo=UTC)
    try:
        normalized = parsed.astimezone(UTC).replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        if allow_empty:
            return ""
        raise ValueError("timestamp is invalid") from None
    return normalized.isoformat().replace("+00:00", "Z")


def classify_freshness(published_at: str, *, now: datetime) -> Freshness:
    """Classify publication age without inventing a time for malformed input."""

    if not published_at:
        return "unknown"
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    age = now.astimezone(UTC) - published.astimezone(UTC)
    if age < -_FUTURE_CLOCK_TOLERANCE:
        return "future"
    if age <= _FRESH_WINDOW:
        return "fresh"
    if age <= _AGING_WINDOW:
        return "aging"
    return "stale"


@dataclass(frozen=True, slots=True)
class Article:
    """One normalized news item.

    ``content_scope`` is evidence, not presentation: an adapter must use
    ``title_only`` or ``summary`` when it did not obtain a full article body.
    """

    article_id: str
    media: str
    provider: str
    title: str
    body: str
    canonical_url: str
    published_at: str
    retrieved_at: str
    content_scope: ContentScope
    hash: str
    freshness: Freshness

    def __post_init__(self) -> None:
        if not self.article_id or len(self.article_id) > 160:
            raise ValueError("article_id must be a stable bounded identifier")
        if not self.media or not self.provider or not self.title:
            raise ValueError("media, provider, and title are required")
        if self.content_scope == "title_only" and self.body:
            raise ValueError("title_only articles cannot claim a body")
        if self.content_scope != "title_only" and not self.body:
            raise ValueError("summary/full_text articles require body evidence")
        if not re.fullmatch(r"[0-9a-f]{64}", self.hash):
            raise ValueError("article hash must be a SHA-256 hex digest")

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible representation."""

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Article:
        """Rehydrate a task result without accepting extra provider fields."""

        return cls(
            article_id=str(payload.get("article_id") or ""),
            media=str(payload.get("media") or ""),
            provider=str(payload.get("provider") or ""),
            title=str(payload.get("title") or ""),
            body=str(payload.get("body") or ""),
            canonical_url=str(payload.get("canonical_url") or ""),
            published_at=str(payload.get("published_at") or ""),
            retrieved_at=str(payload.get("retrieved_at") or ""),
            content_scope=str(payload.get("content_scope") or "title_only"),  # type: ignore[arg-type]
            hash=str(payload.get("hash") or ""),
            freshness=str(payload.get("freshness") or "unknown"),  # type: ignore[arg-type]
        )


def build_article(
    *,
    media: object,
    provider: object,
    title: object,
    body: object = "",
    canonical_url: object = "",
    published_at: object = "",
    retrieved_at: datetime | None = None,
    content_scope: ContentScope | None = None,
    source_identifier: object = "",
) -> Article:
    """Validate provider data and create a stable canonical article."""

    clean_media = _clean_text(media, maximum=128)
    clean_provider = _clean_text(provider, maximum=128)
    clean_title = _clean_text(title, maximum=1000)
    clean_body = _clean_text(body, maximum=2_000_000)
    if not clean_title:
        raise ValueError("title is required")
    normalized_url = canonicalize_url(canonical_url)
    retrieved = (retrieved_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    normalized_published = normalize_utc(published_at)
    scope: ContentScope = content_scope or ("summary" if clean_body else "title_only")
    if not clean_body:
        scope = "title_only"
    digest_payload = "\x1f".join(
        (clean_provider, clean_media, normalized_url, clean_title.casefold(), clean_body)
    )
    content_hash = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()
    raw_identifier = _clean_text(source_identifier, maximum=512)
    identity_payload = "\x1f".join(
        (clean_provider, clean_media, raw_identifier, normalized_url, clean_title.casefold())
    )
    article_id = "news_" + hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()[:40]
    return Article(
        article_id=article_id,
        media=clean_media,
        provider=clean_provider,
        title=clean_title,
        body=clean_body,
        canonical_url=normalized_url,
        published_at=normalized_published,
        retrieved_at=normalize_utc(retrieved, allow_empty=False),
        content_scope=scope,
        hash=content_hash,
        freshness=classify_freshness(normalized_published, now=retrieved),
    )
