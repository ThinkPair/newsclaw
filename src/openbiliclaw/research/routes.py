"""NewsClaw-only REST routes for Tavily Search and Extract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from openbiliclaw.research.errors import (
    ResearchConfigurationError,
    ResearchProviderError,
    ResearchValidationError,
)

if TYPE_CHECKING:
    from openbiliclaw.research.service import ResearchService


class TavilyCredentialIn(BaseModel):
    """A write-only local Tavily credential."""

    api_key: str = Field(min_length=8, max_length=512)


class ResearchSearchIn(BaseModel):
    """Bounded public-web search input."""

    query: str = Field(min_length=1, max_length=1_000)
    topic: Literal["general", "news", "finance"] = "general"
    time_range: Literal["day", "week", "month", "year"] | None = None
    max_results: int = Field(default=5, ge=1, le=8)
    refresh: bool = False


class ResearchExtractIn(BaseModel):
    """Bounded query-directed extraction input."""

    urls: list[str] = Field(min_length=1, max_length=3)
    query: str = Field(min_length=1, max_length=1_000)
    chunks_per_source: int = Field(default=2, ge=1, le=3)
    refresh: bool = False


class ResearchRuntime(Protocol):
    """Subset of NewsRuntime required by these additive routes."""

    research_service: ResearchService

    def tavily_credential_status(self) -> dict[str, object]:
        """Return a redacted credential state."""

    def set_tavily_api_key(self, api_key: str) -> None:
        """Merge one local credential into the shared credential file."""

    def clear_tavily_api_key(self) -> None:
        """Remove only the Tavily key from the shared credential file."""


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, ResearchValidationError):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, ResearchConfigurationError):
        return HTTPException(status_code=409, detail=str(error))
    return HTTPException(status_code=502, detail=str(error))


def register_research_routes(app: FastAPI, *, runtime: ResearchRuntime) -> None:
    """Register Tavily routes only inside a running NewsClaw application."""

    @app.get("/api/research/providers/tavily")
    async def tavily_credential_status() -> dict[str, object]:
        return runtime.tavily_credential_status()

    @app.put("/api/research/providers/tavily/credential")
    async def update_tavily_credential(payload: TavilyCredentialIn) -> dict[str, object]:
        try:
            runtime.set_tavily_api_key(payload.api_key)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"ok": True, **runtime.tavily_credential_status()}

    @app.delete("/api/research/providers/tavily/credential")
    async def delete_tavily_credential() -> dict[str, object]:
        runtime.clear_tavily_api_key()
        return {"ok": True, **runtime.tavily_credential_status()}

    @app.post("/api/research/providers/tavily/probe")
    async def probe_tavily_provider() -> dict[str, object]:
        try:
            usage = await runtime.research_service.probe()
        except (
            ResearchValidationError,
            ResearchConfigurationError,
            ResearchProviderError,
        ) as error:
            raise _http_error(error) from error
        return usage.to_dict()

    @app.post("/api/research/search")
    async def research_search(payload: ResearchSearchIn) -> dict[str, object]:
        try:
            result = await runtime.research_service.search(
                query=payload.query,
                topic=payload.topic,
                time_range=payload.time_range,
                max_results=payload.max_results,
                refresh=payload.refresh,
            )
        except (
            ResearchValidationError,
            ResearchConfigurationError,
            ResearchProviderError,
        ) as error:
            raise _http_error(error) from error
        return result.to_dict()

    @app.post("/api/research/extract")
    async def research_extract(payload: ResearchExtractIn) -> dict[str, object]:
        try:
            result = await runtime.research_service.extract(
                urls=payload.urls,
                query=payload.query,
                chunks_per_source=payload.chunks_per_source,
                refresh=payload.refresh,
            )
        except (
            ResearchValidationError,
            ResearchConfigurationError,
            ResearchProviderError,
        ) as error:
            raise _http_error(error) from error
        return result.to_dict()
