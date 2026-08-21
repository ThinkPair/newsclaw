from __future__ import annotations

from datetime import UTC, datetime

import pytest

from openbiliclaw.news.identity import (
    IdempotencyConflict,
    SQLiteIdempotencyLedger,
    stable_event_id,
    stable_request_id,
)
from openbiliclaw.news.models import Article, build_article


def test_article_contract_normalizes_url_time_and_stable_identity() -> None:
    now = datetime(2026, 8, 17, 6, 0, tzinfo=UTC)
    first = build_article(
        media=" 财联社 ",
        provider="Tushare Pro · news",
        title=" 央行发布新规 ",
        body="这是正文。",
        canonical_url="HTTPS://EXAMPLE.COM/a?utm_source=x&id=7#fragment",
        published_at="2026-08-17T05:00:00Z",
        retrieved_at=now,
        content_scope="full_text",
        source_identifier="upstream-1",
    )
    second = build_article(
        media="财联社",
        provider="Tushare Pro · news",
        title="央行发布新规",
        body="这是正文。",
        canonical_url="https://example.com/a?id=7",
        published_at="2026-08-17T05:00:00Z",
        retrieved_at=now,
        content_scope="full_text",
        source_identifier="upstream-1",
    )

    assert first.article_id == second.article_id
    assert first.hash == second.hash
    assert first.canonical_url == "https://example.com/a?id=7"
    assert first.published_at == "2026-08-17T05:00:00Z"
    assert first.retrieved_at == "2026-08-17T06:00:00Z"
    assert first.freshness == "fresh"
    assert Article.from_dict(first.to_dict()) == first


def test_bad_publication_time_and_missing_body_are_explicit_not_invented() -> None:
    article = build_article(
        media="央视新闻",
        provider="Tushare Pro · cctv_news",
        title="晚间新闻",
        body="",
        published_at="not-a-real-time",
        retrieved_at=datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert article.published_at == ""
    assert article.freshness == "unknown"
    assert article.content_scope == "title_only"


def test_provider_missing_scalars_do_not_become_literal_nan_and_url_credentials_are_rejected() -> (
    None
):
    article = build_article(
        media="媒体",
        provider="provider",
        title="标题",
        body=float("nan"),
        canonical_url="https://user:secret@example.com/a",
        retrieved_at=datetime(2026, 8, 17, tzinfo=UTC),
    )

    assert article.body == ""
    assert article.content_scope == "title_only"
    assert article.canonical_url == ""


def test_article_rejects_false_full_text_claim() -> None:
    with pytest.raises(ValueError, match="require body"):
        Article(
            article_id="news_123",
            media="媒体",
            provider="RSS/Atom",
            title="标题",
            body="",
            canonical_url="",
            published_at="",
            retrieved_at="2026-08-17T00:00:00Z",
            content_scope="full_text",
            hash="0" * 64,
            freshness="unknown",
        )


def test_request_event_ids_are_stable_and_preserve_valid_caller_ids() -> None:
    assert stable_request_id("refresh", "source-a", 1) == stable_request_id(
        "refresh", "source-a", 1
    )
    assert stable_event_id("feedback", "article-a", "like") == stable_event_id(
        "feedback", "article-a", "like"
    )
    assert stable_request_id("refresh", supplied="client-request-1") == "client-request-1"
    with pytest.raises(ValueError, match="unsupported format"):
        stable_event_id("feedback", supplied="bad event id")


def test_sqlite_idempotency_ledger_claims_once_and_rejects_payload_reuse(tmp_path) -> None:
    ledger = SQLiteIdempotencyLedger(tmp_path / "events.db")
    payload = {"article_id": "news_1", "action": "like"}

    assert ledger.claim("feedback", "event-1", payload) is True
    assert ledger.claim("feedback", "event-1", dict(reversed(list(payload.items())))) is False
    with pytest.raises(IdempotencyConflict):
        ledger.claim("feedback", "event-1", {"article_id": "news_1", "action": "dislike"})
