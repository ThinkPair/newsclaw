"""Deterministic presentation helpers for collected news material.

The canonical title and body stay untouched in storage.  These helpers only
derive bounded display and navigation fields so old Tushare rows can remain
auditable while the UI avoids repeating a provider title that already contains
the opening paragraph.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import TypeVar
from urllib.parse import quote_plus, urlsplit

from openbiliclaw.sources.platforms import PLATFORM_NEWS, source_family

_BRACKETED_HEADLINE = re.compile(r"^【\s*([^】\r\n]{4,180}?)\s*】")
_ASCII_BRACKETED_HEADLINE = re.compile(r"^\[\s*([^\]\r\n]{4,180}?)\s*\]")
_KEY_TOKEN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]")
_PUBLISHED_DAY = re.compile(r"(?<!\d)(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
_LEADING_DATELINE = re.compile(
    r"^(?:(?:20\d{2}\s*年\s*)?\d{1,2}\s*月\s*\d{1,2}\s*日\s*[,，:：;；|｜·—-]*\s*)+"
)
_Row = TypeVar("_Row")
NewsDisplayDuplicateKey = tuple[str, str, str, str, str, str]


def _provider_headline_match(text: str) -> re.Match[str] | None:
    """Match only a substantial provider headline, never a short section tag."""

    for pattern in (_BRACKETED_HEADLINE, _ASCII_BRACKETED_HEADLINE):
        match = pattern.match(text)
        if not match or not text[match.end() :].strip():
            continue
        semantic_length = sum(len(token) for token in _KEY_TOKEN.findall(match.group(1)))
        if semantic_length >= 12:
            return match
    return None


def compact_news_headline(value: object) -> str:
    """Return a scan-friendly headline without mutating canonical evidence.

    Some news providers emit ``【headline】lead paragraph`` in their ``title``
    field.  Only that explicit, bounded shape is shortened; ordinary titles are
    preserved verbatim apart from whitespace normalization.
    """

    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    match = _provider_headline_match(text)
    if match:
        return " ".join(match.group(1).split()).strip()
    return text


def safe_http_url(value: object) -> str:
    """Return a browser-safe absolute HTTP(S) URL, or an empty string."""

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def news_source_lookup_url(title: object, media: object) -> str:
    """Build an honest search fallback when the provider supplied no URL.

    This URL performs no network request by itself.  A request is made only if
    the user explicitly opens the link.  The exact-phrase query deliberately
    excludes private profile data and provider credentials.
    """

    headline = compact_news_headline(title)
    publisher = " ".join(str(media or "").split()).strip()
    if not headline or not publisher:
        return ""
    query = f'"{headline[:180]}" "{publisher[:80]}"'
    return f"https://www.bing.com/search?q={quote_plus(query)}"


def news_source_link_fields(
    *, title: object, media: object, content_url: object
) -> dict[str, str | None]:
    """Describe whether a news item has exact evidence or only a search path."""

    exact_url = safe_http_url(content_url)
    if exact_url:
        return {
            "source_link_status": "exact",
            "source_lookup_url": None,
        }
    lookup_url = news_source_lookup_url(title, media)
    if lookup_url:
        return {
            "source_link_status": "search",
            "source_lookup_url": lookup_url,
        }
    return {
        "source_link_status": "missing",
        "source_lookup_url": None,
    }


def _value(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        return row.get(name, "")
    return getattr(row, name, "")


def _normalized_key(value: object) -> str:
    """Normalize presentation noise while retaining fact-bearing symbols.

    Decimal points, percent signs, signs, and currency marks must survive:
    removing punctuation would make ``3.5%`` indistinguishable from ``35%``.
    NFKC still unifies full-width forms, and whitespace runs collapse without
    erasing word boundaries.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(text.split()).strip()


def _headline_and_lead(value: object) -> tuple[str, str]:
    text = " ".join(str(value or "").split()).strip()
    match = _provider_headline_match(text)
    if match:
        lead = _LEADING_DATELINE.sub("", text[match.end() :].strip())
        return match.group(1).strip(), lead
    return text, ""


def _body_fingerprint(row: object, raw_title: str) -> str:
    """Hash the complete normalized evidence body after removing its title echo.

    A first-sentence key is unsafe for evolving news: two dispatches can share
    the same lead while a later sentence changes the amount, timing, or policy
    decision.  Empty bodies deliberately have no fingerprint, so title-only
    rows are never merged on presentation heuristics alone.
    """

    body = " ".join(
        str(
            _value(row, "body_text") or _value(row, "body") or _value(row, "description") or ""
        ).split()
    ).strip()
    if not body:
        return ""
    if raw_title and body.startswith(raw_title):
        body = body[len(raw_title) :].lstrip(" ,，。:：;；|｜·—-")
    normalized = _normalized_key(body)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def news_display_duplicate_key(row: object) -> NewsDisplayDuplicateKey | None:
    """Return a conservative identity for a fact-identical NewsClaw item.

    The helper is intentionally limited to canonical news-family rows.  It is
    safe for the recommendation engine to call in mixed-product code because
    ordinary OpenBiliClaw sources return ``None`` and keep their legacy serving
    behavior.  Storage is never changed; callers only suppress duplicate cards.
    """

    platform = source_family(
        _value(row, "source") or _value(row, "source_strategy"),
        _value(row, "source_platform"),
    )
    if platform != PLATFORM_NEWS:
        return None

    day_match = _PUBLISHED_DAY.search(str(_value(row, "published_at") or ""))
    if day_match is None:
        return None
    year, month, day = (int(part) for part in day_match.groups())
    raw_title = " ".join(str(_value(row, "title") or "").split()).strip()
    raw_headline, lead = _headline_and_lead(raw_title)
    headline = str(_value(row, "display_title") or raw_headline)
    headline = unicodedata.normalize("NFKC", headline).casefold()
    # Providers sometimes publish the same headline twice, adding only the
    # publication year to one version. Ignore that one presentation-only
    # variation, but retain every other year/number so changed facts stay
    # separate.
    headline = re.sub(rf"(?<!\d){year:04d}\s*年", "", headline)
    headline_key = _normalized_key(headline)
    lead_key = _normalized_key(lead)
    body_fingerprint = _body_fingerprint(row, raw_title)
    media = unicodedata.normalize(
        "NFKC",
        str(
            _value(row, "media")
            or _value(row, "publisher_name")
            or _value(row, "author_name")
            or _value(row, "up_name")
            or _value(row, "source")
            or _value(row, "source_strategy")
            or ""
        ),
    ).casefold()
    media_key = _normalized_key(media)
    if not headline_key or not media_key or not body_fingerprint:
        return None
    exact_url = safe_http_url(
        _value(row, "original_url") or _value(row, "content_url") or _value(row, "canonical_url")
    )
    return (
        media_key,
        f"{year:04d}-{month:02d}-{day:02d}",
        headline_key,
        lead_key,
        body_fingerprint,
        exact_url,
    )


def collapse_news_display_duplicates(rows: Iterable[_Row]) -> list[_Row]:
    """Hide only deterministic same-provider/same-day headline duplicates.

    The first row wins, so callers should priority-sort before using this
    helper.  Storage remains untouched and different providers, dates, or
    changed numeric facts remain separate evidence.
    """

    kept: list[_Row] = []
    seen: set[NewsDisplayDuplicateKey] = set()
    for row in rows:
        key = news_display_duplicate_key(row)
        if key is not None and key in seen:
            continue
        kept.append(row)
        if key is not None:
            seen.add(key)
    return kept
