"""Provider protocol and direct Tavily Search/Extract HTTP adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx

from openbiliclaw.research.errors import ResearchProviderError
from openbiliclaw.research.models import (
    ExtractRequest,
    ProviderResult,
    ProviderUsage,
    ResearchEvidence,
    SearchRequest,
)

_TAVILY_API_BASE = "https://api.tavily.com"


class ResearchProvider(Protocol):
    """Minimal async provider contract used by the local research service."""

    async def search(self, request: SearchRequest) -> ProviderResult:
        """Search the public web and return normalized evidence."""

    async def extract(self, request: ExtractRequest) -> ProviderResult:
        """Extract query-relevant evidence from explicit public URLs."""

    async def usage(self) -> ProviderUsage:
        """Validate the key and return bounded account usage without searching."""


def _bounded_string(value: object, *, maximum: int) -> str:
    return " ".join(str(value or "").split()).strip()[:maximum]


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(cast("Any", value))
    except (TypeError, ValueError, OverflowError):
        return None


def _credits(payload: Mapping[str, object]) -> float | None:
    direct = _optional_float(payload.get("credits_used"))
    if direct is not None:
        return direct
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return None
    if "credits" in usage:
        return _optional_float(usage.get("credits"))
    return _optional_float(usage.get("credits_used"))


class TavilyProvider:
    """Call only Tavily Search and Extract with fixed low-cost settings."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 20.0,
        base_url: str = _TAVILY_API_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized = api_key.strip()
        if not normalized:
            raise ValueError("Tavily API Key is required")
        self._api_key = normalized
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def _post(self, endpoint: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                timeout=self._timeout_seconds,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                response = await client.post(f"{self._base_url}/{endpoint}", json=dict(payload))
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as error:
            raise ResearchProviderError("Tavily 请求超时，请稍后重试") from error
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in {401, 403}:
                message = "Tavily API Key 无效或没有权限"
            elif status == 429:
                message = "Tavily 当前限流或额度不足，请稍后重试"
            else:
                message = f"Tavily 服务暂时不可用（HTTP {status}）"
            raise ResearchProviderError(message) from error
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ResearchProviderError("Tavily 返回了不可用的响应") from error
        if not isinstance(body, Mapping):
            raise ResearchProviderError("Tavily 返回了不可用的响应")
        return cast("Mapping[str, object]", body)

    async def usage(self) -> ProviderUsage:
        """Validate credentials through Tavily's zero-search-credit Usage endpoint."""

        try:
            async with httpx.AsyncClient(
                trust_env=False,
                timeout=self._timeout_seconds,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._api_key}"},
            ) as client:
                response = await client.get(f"{self._base_url}/usage")
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException as error:
            raise ResearchProviderError("Tavily 请求超时，请稍后重试") from error
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in {401, 403}:
                message = "Tavily API Key 无效或没有权限"
            elif status == 429:
                message = "Tavily 当前限流，请稍后重试"
            else:
                message = f"Tavily 服务暂时不可用（HTTP {status}）"
            raise ResearchProviderError(message) from error
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise ResearchProviderError("Tavily 返回了不可用的响应") from error
        if not isinstance(body, Mapping):
            raise ResearchProviderError("Tavily 返回了不可用的响应")
        key = body.get("key")
        account = body.get("account")
        if not isinstance(key, Mapping) or not isinstance(account, Mapping):
            raise ResearchProviderError("Tavily 返回了不可用的用量结构")
        return ProviderUsage(
            key_usage=_optional_float(key.get("usage")),
            key_limit=_optional_float(key.get("limit")),
            account_plan=_bounded_string(account.get("current_plan"), maximum=80),
            plan_usage=_optional_float(account.get("plan_usage")),
            plan_limit=_optional_float(account.get("plan_limit")),
        )

    @staticmethod
    def _evidence_from_rows(
        rows: object,
        *,
        content_keys: Sequence[str],
    ) -> tuple[ResearchEvidence, ...]:
        if not isinstance(rows, list):
            return ()
        evidence: list[ResearchEvidence] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            url = _bounded_string(row.get("url"), maximum=4_096)
            if not url:
                continue
            content = ""
            for key in content_keys:
                content = str(row.get(key) or "").strip()
                if content:
                    break
            title = _bounded_string(row.get("title"), maximum=500)
            evidence.append(
                ResearchEvidence(
                    url=url,
                    title=title,
                    content=content,
                    source_domain=(urlsplit(url).hostname or "").casefold(),
                    published_at=_bounded_string(
                        row.get("published_date") or row.get("published_at"),
                        maximum=80,
                    ),
                    score=_optional_float(row.get("score")),
                )
            )
        return tuple(evidence)

    async def search(self, request: SearchRequest) -> ProviderResult:
        """Run Tavily Search with model-context-expanding fields disabled."""

        payload: dict[str, object] = {
            "query": request.query,
            "topic": request.topic,
            "search_depth": "basic",
            "chunks_per_source": 1,
            "max_results": request.max_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "auto_parameters": False,
            "include_usage": True,
        }
        if request.time_range:
            payload["time_range"] = request.time_range
        body = await self._post("search", payload)
        rows = body.get("results")
        if not isinstance(rows, list):
            raise ResearchProviderError("Tavily 返回了不可用的结果结构")
        return ProviderResult(
            request_id=_bounded_string(body.get("request_id"), maximum=200),
            evidence=self._evidence_from_rows(rows, content_keys=("content",)),
            credits_used=_credits(body),
        )

    async def extract(self, request: ExtractRequest) -> ProviderResult:
        """Run basic Tavily Extract for a small explicit public-URL set."""

        body = await self._post(
            "extract",
            {
                "urls": list(request.urls),
                "query": request.query,
                "extract_depth": "basic",
                "chunks_per_source": request.chunks_per_source,
                "include_images": False,
                "include_usage": True,
            },
        )
        rows = body.get("results")
        if not isinstance(rows, list):
            raise ResearchProviderError("Tavily 返回了不可用的结果结构")
        return ProviderResult(
            request_id=_bounded_string(body.get("request_id"), maximum=200),
            evidence=self._evidence_from_rows(
                rows,
                content_keys=("raw_content", "content"),
            ),
            credits_used=_credits(body),
        )
