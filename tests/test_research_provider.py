from __future__ import annotations

import json

import httpx
import pytest

from openbiliclaw.research.errors import ResearchProviderError
from openbiliclaw.research.models import ExtractRequest, SearchRequest
from openbiliclaw.research.provider import TavilyProvider


@pytest.mark.asyncio
async def test_tavily_search_uses_bounded_low_cost_payload_and_no_proxy_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_client_options: dict[str, object] = {}
    captured_payload: dict[str, object] = {}
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer tvly-test-secret"
        return httpx.Response(
            200,
            json={
                "request_id": "tavily-request-1",
                "usage": {"credits": 1},
                "results": [
                    {
                        "title": "Evidence",
                        "url": "https://example.com/a",
                        "content": "A bounded search fragment",
                        "score": 0.91,
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        captured_client_options.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("openbiliclaw.research.provider.httpx.AsyncClient", client_factory)
    provider = TavilyProvider("tvly-test-secret", transport=transport)

    result = await provider.search(
        SearchRequest(query="AI regulation", topic="news", time_range="week", max_results=5)
    )

    assert captured_client_options["trust_env"] is False
    assert captured_payload == {
        "query": "AI regulation",
        "topic": "news",
        "search_depth": "basic",
        "chunks_per_source": 1,
        "max_results": 5,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "auto_parameters": False,
        "include_usage": True,
        "time_range": "week",
    }
    assert result.request_id == "tavily-request-1"
    assert result.credits_used == 1
    assert result.evidence[0].url == "https://example.com/a"


@pytest.mark.asyncio
async def test_tavily_extract_is_basic_query_directed_and_reports_direct_usage() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "request_id": "extract-1",
                "credits_used": 2,
                "results": [
                    {
                        "url": "https://example.com/report",
                        "raw_content": "relevant extracted text",
                    }
                ],
            },
        )

    provider = TavilyProvider(
        "tvly-test-secret",
        transport=httpx.MockTransport(handler),
    )
    result = await provider.extract(
        ExtractRequest(
            urls=("https://example.com/report",),
            query="What changed?",
            chunks_per_source=2,
        )
    )

    assert payloads == [
        {
            "urls": ["https://example.com/report"],
            "query": "What changed?",
            "extract_depth": "basic",
            "chunks_per_source": 2,
            "include_images": False,
            "include_usage": True,
        }
    ]
    assert result.evidence[0].content == "relevant extracted text"
    assert result.credits_used == 2


@pytest.mark.asyncio
async def test_tavily_usage_preserves_zero_credits() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request_id": "search-zero",
                "usage": {"credits": 0, "credits_used": 9},
                "results": [],
            },
        )

    provider = TavilyProvider(
        "tvly-test-secret",
        transport=httpx.MockTransport(handler),
    )

    result = await provider.search(SearchRequest(query="public topic"))

    assert result.credits_used == 0


@pytest.mark.asyncio
async def test_tavily_usage_uses_get_without_search_credit_or_proxy_inheritance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_client_options: dict[str, object] = {}
    requests: list[httpx.Request] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer tvly-test-secret"
        return httpx.Response(
            200,
            json={
                "key": {"usage": 7, "limit": 100},
                "account": {
                    "current_plan": "Researcher",
                    "plan_usage": 23,
                    "plan_limit": 1_000,
                },
            },
        )

    transport = httpx.MockTransport(handler)

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        captured_client_options.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("openbiliclaw.research.provider.httpx.AsyncClient", client_factory)
    provider = TavilyProvider("tvly-test-secret", transport=transport)

    usage = await provider.usage()

    assert captured_client_options["trust_env"] is False
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url == "https://api.tavily.com/usage"
    assert requests[0].content == b""
    assert usage.to_dict() == {
        "ok": True,
        "provider": "tavily",
        "key_usage": 7.0,
        "key_limit": 100.0,
        "account_plan": "Researcher",
        "plan_usage": 23.0,
        "plan_limit": 1_000.0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429])
async def test_tavily_usage_http_errors_are_redacted(status: int) -> None:
    secret = "tvly-usage-super-secret-value"
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(status, json={"detail": f"rejected {secret}"})

    provider = TavilyProvider(secret, transport=httpx.MockTransport(handler))

    with pytest.raises(ResearchProviderError) as captured:
        await provider.usage()

    assert methods == ["GET"]
    assert secret not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429])
async def test_tavily_http_errors_are_redacted(status: int) -> None:
    secret = "tvly-super-secret-value"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": f"rejected {secret}"})

    provider = TavilyProvider(secret, transport=httpx.MockTransport(handler))

    with pytest.raises(ResearchProviderError) as captured:
        await provider.search(SearchRequest(query="public topic"))

    assert secret not in str(captured.value)


@pytest.mark.asyncio
async def test_tavily_timeout_is_translated_without_transport_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("contains transport internals", request=request)

    provider = TavilyProvider("tvly-test-secret", transport=httpx.MockTransport(handler))

    with pytest.raises(ResearchProviderError, match="超时"):
        await provider.search(SearchRequest(query="public topic"))


@pytest.mark.asyncio
async def test_tavily_schema_drift_fails_instead_of_becoming_a_cached_empty_result() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"request_id": "broken", "results": {"unexpected": 1}})

    provider = TavilyProvider("tvly-test-secret", transport=httpx.MockTransport(handler))

    with pytest.raises(ResearchProviderError, match="结果结构"):
        await provider.search(SearchRequest(query="public topic"))
