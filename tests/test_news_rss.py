from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from openbiliclaw.news.errors import (
    SourcePermissionDenied,
    SourceRateLimited,
    SourceSchemaError,
    SourceTimedOut,
)
from openbiliclaw.news.protocol import FetchWindow
from openbiliclaw.news.rss import RSS_MAX_RESPONSE_BYTES, RSS_USER_AGENT, RSSAdapter


def _window() -> FetchWindow:
    return FetchWindow(
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 31, tzinfo=UTC),
        20,
    )


@pytest.mark.asyncio
async def test_rss_and_atom_entries_preserve_body_evidence_level() -> None:
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel><title>官方源</title>
        <item><guid>a-1</guid><title>完整正文</title>
          <link>https://example.com/a?utm_source=rss</link>
          <pubDate>Mon, 17 Aug 2026 08:00:00 GMT</pubDate>
          <content:encoded><![CDATA[<p>第一段。</p><p>第二段。</p>]]></content:encoded>
        </item>
        <item><guid>a-2</guid><title>只有标题</title>
          <pubDate>invalid-date</pubDate>
        </item>
      </channel>
    </rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == RSS_USER_AGENT
        return httpx.Response(200, text=rss, headers={"Content-Type": "application/rss+xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = RSSAdapter(
            url="https://example.com/feed.xml", media="官方媒体", http_client=client
        )
        articles = await adapter.fetch(_window())

    assert [article.content_scope for article in articles] == ["full_text", "title_only"]
    assert articles[0].body == "第一段。 第二段。"
    assert articles[0].canonical_url == "https://example.com/a"
    assert articles[1].published_at == ""


@pytest.mark.asyncio
async def test_atom_summary_is_not_claimed_as_full_text() -> None:
    atom = """<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>urn:1</id><title>摘要新闻</title>
        <link rel="alternate" href="https://example.com/atom/1" />
        <updated>2026-08-17T09:00:00Z</updated><summary>这只是一段摘要。</summary>
      </entry>
    </feed>"""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=atom, headers={"Content-Type": "application/atom+xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        article = (
            await RSSAdapter(
                url="https://example.com/atom.xml", media="官方媒体", http_client=client
            ).fetch(_window())
        )[0]

    assert article.content_scope == "summary"
    assert article.body == "这只是一段摘要。"


@pytest.mark.asyncio
async def test_rss_exposes_stale_latest_entry_even_when_window_is_empty() -> None:
    rss = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>历史源</title>
      <item><guid>old-1</guid><title>2016 年的最后一条</title>
        <pubDate>Mon, 01 Aug 2016 08:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=rss, headers={"Content-Type": "application/rss+xml"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = RSSAdapter(
            url="https://example.com/old.xml",
            media="历史源",
            http_client=client,
        )
        articles = await adapter.fetch(_window())

    assert articles == []
    assert adapter.latest_entry_published_at == "2016-08-01T08:00:00Z"
    assert adapter.feed_is_stale is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [(403, SourcePermissionDenied), (429, SourceRateLimited)],
)
async def test_rss_http_permission_and_rate_limit_are_explicit(
    status: int, expected: type[Exception]
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"Retry-After": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = RSSAdapter(url="https://example.com/feed", media="媒体", http_client=client)
        with pytest.raises(expected):
            await adapter.fetch(_window())


@pytest.mark.asyncio
async def test_rss_timeout_empty_and_malformed_contracts() -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
        adapter = RSSAdapter(url="https://example.com/feed", media="媒体", http_client=client)
        with pytest.raises(SourceTimedOut):
            await adapter.fetch(_window())

    responses = iter(
        [
            httpx.Response(
                200,
                text="<?xml version='1.0'?><rss><channel /></rss>",
                headers={"Content-Type": "application/rss+xml"},
            ),
            httpx.Response(
                200,
                text="<html>not a feed</html>",
                headers={"Content-Type": "text/html"},
            ),
        ]
    )

    async def sequence_handler(_request: httpx.Request) -> httpx.Response:
        return next(responses)

    async with httpx.AsyncClient(transport=httpx.MockTransport(sequence_handler)) as client:
        adapter = RSSAdapter(url="https://example.com/feed", media="媒体", http_client=client)
        assert await adapter.fetch(_window()) == []
        with pytest.raises(SourceSchemaError):
            await adapter.fetch(_window())


@pytest.mark.asyncio
async def test_rss_stream_aborts_when_body_exceeds_safe_limit() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (RSS_MAX_RESPONSE_BYTES + 1),
            headers={"Content-Type": "application/rss+xml"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = RSSAdapter(url="https://example.com/feed", media="媒体", http_client=client)
        with pytest.raises(SourceSchemaError, match="safe size"):
            await adapter.fetch(_window())
