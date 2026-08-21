from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from openbiliclaw.news.errors import SourcePermissionDenied, SourceRateLimited
from openbiliclaw.news.protocol import FetchWindow
from openbiliclaw.news.tushare import TushareNewsAdapter


class StubTushareClient:
    def __init__(self, payloads: dict[str, object]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _call(self, endpoint: str, kwargs: dict[str, object]) -> object:
        self.calls.append((endpoint, kwargs))
        payload = self.payloads.get(endpoint, [])
        if isinstance(payload, Exception):
            raise payload
        return payload

    def news(self, **kwargs: object) -> object:
        return self._call("news", kwargs)

    def major_news(self, **kwargs: object) -> object:
        return self._call("major_news", kwargs)

    def cctv_news(self, **kwargs: object) -> object:
        return self._call("cctv_news", kwargs)


class TableFixture:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def to_dict(self, orient: str = "") -> list[dict[str, Any]]:
        assert orient == "records"
        return self.rows


def _window(limit: int = 20) -> FetchWindow:
    return FetchWindow(
        datetime(2026, 8, 16, tzinfo=UTC),
        datetime(2026, 8, 17, tzinfo=UTC),
        limit,
    )


@pytest.mark.asyncio
async def test_tushare_news_normalizes_dataframe_rows_and_request_shape() -> None:
    client = StubTushareClient(
        {
            "news": TableFixture(
                [
                    {
                        "id": "cls-1",
                        "src": "财联社",
                        "title": "政策快讯",
                        "content": "政策快讯正文",
                        "datetime": "2026-08-17 07:00:00",
                        "url": "https://example.com/news/1",
                    }
                ]
            )
        }
    )
    adapter = TushareNewsAdapter(
        client=client,
        endpoint="news",
        media="财联社",
        source_code="cls",
        clock=lambda: datetime(2026, 8, 17, 8, 0, tzinfo=UTC),
    )

    articles = await adapter.fetch(_window())

    assert len(articles) == 1
    assert articles[0].media == "财联社"
    assert articles[0].provider == "Tushare Pro · news"
    assert articles[0].content_scope == "full_text"
    assert articles[0].published_at == "2026-08-16T23:00:00Z"
    assert client.calls == [
        (
            "news",
            {
                "start_date": "2026-08-16 08:00:00",
                "end_date": "2026-08-17 08:00:00",
                "src": "cls",
            },
        )
    ]


@pytest.mark.asyncio
async def test_tushare_mixed_batch_preserves_each_row_media_without_src_filter() -> None:
    client = StubTushareClient(
        {
            "major_news": [
                {"id": "1", "src": "新华社", "title": "A", "content": "正文A"},
                {"id": "2", "source": "第一财经", "title": "B", "content": "正文B"},
                {"id": "3", "media": "财新网", "title": "C", "content": "正文C"},
                {"id": "4", "title": "D", "content": "正文D"},
            ]
        }
    )
    adapter = TushareNewsAdapter(
        client=client,
        endpoint="major_news",
        media="Tushare 长篇新闻",
    )

    articles = await adapter.fetch(_window())

    assert [article.media for article in articles] == [
        "新华社",
        "第一财经",
        "财新网",
        "Tushare 长篇新闻",
    ]
    assert "src" not in client.calls[0][1]


@pytest.mark.asyncio
async def test_tushare_nan_fields_are_missing_not_literal_provider_values() -> None:
    client = StubTushareClient(
        {
            "news": [
                {
                    "id": "1",
                    "src": float("nan"),
                    "title": "标题",
                    "content": float("nan"),
                }
            ]
        }
    )
    adapter = TushareNewsAdapter(client=client, endpoint="news", media="回退媒体")

    article = (await adapter.fetch(_window()))[0]

    assert article.media == "回退媒体"
    assert article.body == ""
    assert article.content_scope == "title_only"


@pytest.mark.asyncio
async def test_tushare_empty_result_is_available_and_body_absence_is_labeled() -> None:
    empty_client = StubTushareClient({"major_news": []})
    empty_adapter = TushareNewsAdapter(
        client=empty_client,
        endpoint="major_news",
        media="新华社",
    )
    assert await empty_adapter.fetch(_window()) == []
    probe = await empty_adapter.probe()
    assert probe.available is True
    assert probe.status == "empty"

    client = StubTushareClient(
        {"major_news": [{"id": "xinhua-1", "title": "仅标题", "pub_time": "bad-time"}]}
    )
    adapter = TushareNewsAdapter(client=client, endpoint="major_news", media="新华社")
    article = (await adapter.fetch(_window()))[0]
    assert article.content_scope == "title_only"
    assert article.published_at == ""
    assert article.freshness == "unknown"


@pytest.mark.asyncio
async def test_tushare_permission_and_rate_limit_are_distinct_contract_failures() -> None:
    denied = TushareNewsAdapter(
        client=StubTushareClient({"news": RuntimeError("该接口无权限")}),
        endpoint="news",
        media="财联社",
    )
    with pytest.raises(SourcePermissionDenied):
        await denied.fetch(_window())
    probe = await denied.probe()
    assert probe.status == "permission_denied"
    assert probe.available is False

    limited = TushareNewsAdapter(
        client=StubTushareClient({"major_news": RuntimeError("429 请求频率超限")}),
        endpoint="major_news",
        media="第一财经",
    )
    with pytest.raises(SourceRateLimited):
        await limited.fetch(_window())


@pytest.mark.asyncio
async def test_cctv_news_uses_day_scoped_contract_and_stops_at_limit() -> None:
    client = StubTushareClient(
        {
            "cctv_news": [
                {
                    "date": "20260817",
                    "title": "新闻联播",
                    "content": "完整节目文字",
                }
            ]
        }
    )
    adapter = TushareNewsAdapter(
        client=client,
        endpoint="cctv_news",
        media="央视新闻联播",
    )

    articles = await adapter.fetch(_window(limit=1))

    assert len(articles) == 1
    assert client.calls == [("cctv_news", {"date": "20260817"})]
