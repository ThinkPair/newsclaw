from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openbiliclaw.api.news_routes import NewsRuntime
from openbiliclaw.research.models import (
    ProviderUsage,
    ResearchEvidence,
    ResearchOperation,
    ResearchResult,
)
from openbiliclaw.research.routes import register_research_routes

if TYPE_CHECKING:
    from pathlib import Path


class _RouteService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def search(self, **kwargs: object) -> ResearchResult:
        self.calls.append(("search", dict(kwargs)))
        return _result("search")

    async def extract(self, **kwargs: object) -> ResearchResult:
        self.calls.append(("extract", dict(kwargs)))
        return _result("extract")

    async def probe(self) -> ProviderUsage:
        self.calls.append(("probe", {}))
        return ProviderUsage(key_usage=3, key_limit=100, account_plan="Researcher")


class _RouteRuntime:
    def __init__(self) -> None:
        self.local_key = ""
        self.research_service = _RouteService()

    def tavily_credential_status(self) -> dict[str, object]:
        configured = bool(self.local_key)
        return {
            "provider": "tavily",
            "configured": configured,
            "stored_locally": configured,
            "source": "local" if configured else "none",
            "masked": "••••••••" if configured else "",
        }

    def set_tavily_api_key(self, api_key: str) -> None:
        self.local_key = api_key

    def clear_tavily_api_key(self) -> None:
        self.local_key = ""


def _result(operation: str) -> ResearchResult:
    return ResearchResult(
        run_id="rrun_1",
        operation=cast("ResearchOperation", operation),
        query="public question",
        provider="tavily",
        provider_request_id="provider-1",
        cache_hit=False,
        fresh_until="2026-08-23T00:00:00+00:00",
        topic="general",
        time_range="",
        evidence=(
            ResearchEvidence(
                url="https://example.com/a",
                title="Evidence",
                content="Public evidence",
                source_domain="example.com",
            ),
        ),
        credits_used=1,
    )


def test_research_routes_keep_credential_write_only_and_forward_bounded_inputs() -> None:
    app = FastAPI()
    runtime = _RouteRuntime()
    register_research_routes(app, runtime=runtime)
    client = TestClient(app)
    secret = "tvly-route-secret"

    initial = client.get("/api/research/providers/tavily")
    saved = client.put(
        "/api/research/providers/tavily/credential",
        json={"api_key": secret},
    )
    search = client.post(
        "/api/research/search",
        json={
            "query": "public question",
            "topic": "news",
            "time_range": "week",
            "max_results": 5,
        },
    )
    extract = client.post(
        "/api/research/extract",
        json={
            "query": "what changed",
            "urls": ["https://example.com/a"],
            "chunks_per_source": 2,
        },
    )
    probe = client.post("/api/research/providers/tavily/probe")
    deleted = client.delete("/api/research/providers/tavily/credential")

    assert initial.json()["configured"] is False
    assert saved.json()["masked"] == "••••••••"
    assert secret not in saved.text
    assert search.json()["result_count"] == 1
    assert extract.json()["operation"] == "extract"
    assert probe.json() == {
        "ok": True,
        "provider": "tavily",
        "key_usage": 3.0,
        "key_limit": 100.0,
        "account_plan": "Researcher",
        "plan_usage": None,
        "plan_limit": None,
    }
    assert runtime.research_service.calls == [
        (
            "search",
            {
                "query": "public question",
                "topic": "news",
                "time_range": "week",
                "max_results": 5,
                "refresh": False,
            },
        ),
        (
            "extract",
            {
                "urls": ["https://example.com/a"],
                "query": "what changed",
                "chunks_per_source": 2,
                "refresh": False,
            },
        ),
        ("probe", {}),
    ]
    assert deleted.json()["configured"] is False


def test_research_route_schema_enforces_hard_limits() -> None:
    app = FastAPI()
    runtime = _RouteRuntime()
    register_research_routes(app, runtime=runtime)
    client = TestClient(app)

    assert (
        client.post(
            "/api/research/search",
            json={"query": "topic", "max_results": 9},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/research/extract",
            json={
                "query": "topic",
                "urls": [
                    "https://example.com/1",
                    "https://example.com/2",
                    "https://example.com/3",
                    "https://example.com/4",
                ],
            },
        ).status_code
        == 422
    )


def test_news_runtime_merges_tushare_and_tavily_credentials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    runtime = NewsRuntime(
        data_path=tmp_path,
        database=SimpleNamespace(),
        soul_engine=SimpleNamespace(),
        event_ingress=SimpleNamespace(),
        request_replenishment=lambda **_kwargs: None,
    )

    runtime.set_tushare_token("tushare-local-token")
    runtime.set_tavily_api_key("tvly-local-token")

    assert runtime._local_tushare_token() == "tushare-local-token"
    assert runtime._local_tavily_api_key() == "tvly-local-token"
    serialized = runtime.credentials_path.read_text(encoding="utf-8")
    assert "tushare_token" in serialized
    assert "tavily_api_key" in serialized

    runtime.clear_tushare_token()
    assert runtime._local_tushare_token() == ""
    assert runtime._local_tavily_api_key() == "tvly-local-token"
    runtime.clear_tavily_api_key()
    assert not runtime.credentials_path.exists()


def test_tavily_credential_environment_fallback_is_status_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-environment-token")
    runtime = NewsRuntime(
        data_path=tmp_path,
        database=SimpleNamespace(),
        soul_engine=SimpleNamespace(),
        event_ingress=SimpleNamespace(),
        request_replenishment=lambda **_kwargs: None,
    )

    status = runtime.tavily_credential_status()

    assert status == {
        "provider": "tavily",
        "configured": True,
        "stored_locally": False,
        "source": "environment",
        "masked": "••••••••",
    }
    assert "tvly-environment-token" not in str(status)
