"""NewsClaw-only HTTP contract for webpage and PDF monitoring."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from openbiliclaw.news.web_monitoring import (
    MonitorFetchError,
    MonitorKind,
    WebMonitorService,
    validate_public_url,
)


class WebMonitorCreateIn(BaseModel):
    url: str = Field(min_length=8, max_length=4_096)
    kind: MonitorKind = "auto"
    label: str = Field(default="", max_length=500)
    focus: str = Field(default="", max_length=1_000)
    interval_minutes: int = Field(default=1_440, ge=5, le=10_080)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=8, max_length=200)


class WebMonitorUpdateIn(BaseModel):
    label: str | None = Field(default=None, max_length=500)
    focus: str | None = Field(default=None, max_length=1_000)
    interval_minutes: int | None = Field(default=None, ge=5, le=10_080)
    expected_version: int | None = Field(default=None, ge=0)


class WebMonitorCheckIn(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=8, max_length=200)


class WebMonitorNotificationActionIn(BaseModel):
    action: Literal["delivered", "read", "dismiss"]
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=8, max_length=200)


def _public_monitor(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    item = dict(value)
    for private in ("canonical_url", "lease_owner", "lease_token", "etag", "last_modified"):
        item.pop(private, None)
    return item


def _public_check_result(value: dict[str, Any]) -> dict[str, Any]:
    item = dict(value)
    monitor = item.get("monitor")
    if isinstance(monitor, dict):
        item["monitor"] = _public_monitor(monitor)
    check = item.get("check")
    if isinstance(check, dict):
        safe_check = dict(check)
        safe_check.pop("lease_token", None)
        item["check"] = safe_check
    return item


def register_web_monitor_routes(
    app: FastAPI,
    *,
    service: WebMonitorService,
) -> None:
    """Register P5 routes only from the NewsClaw product-mode registrar."""

    @app.get("/api/news/monitors")
    async def list_web_monitors() -> dict[str, Any]:
        rows = await asyncio.to_thread(service.store.list_monitors)
        runtime = await asyncio.to_thread(service.runtime_state)
        return {"items": [_public_monitor(row) for row in rows], "runtime": runtime}

    @app.post("/api/news/monitors", status_code=status.HTTP_201_CREATED)
    async def create_web_monitor(payload: WebMonitorCreateIn) -> dict[str, Any]:
        try:
            canonical_url = await validate_public_url(payload.url)
            monitor, created = await asyncio.to_thread(
                service.store.create_monitor,
                canonical_url=canonical_url,
                kind=payload.kind,
                label=" ".join(payload.label.split()).strip(),
                focus=" ".join(payload.focus.split()).strip(),
                interval_minutes=payload.interval_minutes,
                request_id=payload.request_id,
            )
        except MonitorFetchError as error:
            raise HTTPException(status_code=422, detail=error.safe_message) from error
        except ValueError as error:
            message = str(error)
            code = 409 if "request_id" in message else 422
            raise HTTPException(status_code=code, detail=message) from error
        check: dict[str, Any] | None = None
        if created or not bool(monitor.get("has_baseline")):
            check = await service.check_monitor(
                str(monitor["monitor_id"]),
                request_id=f"{payload.request_id}:baseline",
                manual=True,
            )
            monitor = service.store.get_monitor(str(monitor["monitor_id"])) or monitor
        return {
            "item": _public_monitor(monitor),
            "created": created,
            "check": _public_check_result(check) if check is not None else None,
        }

    @app.get("/api/news/monitors/runtime")
    async def web_monitor_runtime() -> dict[str, Any]:
        return await asyncio.to_thread(service.runtime_state)

    @app.get("/api/news/monitors/{monitor_id}")
    async def get_web_monitor(monitor_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(service.store.get_monitor, monitor_id)
        if item is None:
            raise HTTPException(status_code=404, detail="网页监控不存在")
        return {"item": _public_monitor(item)}

    @app.patch("/api/news/monitors/{monitor_id}")
    async def update_web_monitor(
        monitor_id: str,
        payload: WebMonitorUpdateIn,
    ) -> dict[str, Any]:
        try:
            item = await asyncio.to_thread(
                service.store.update_monitor,
                monitor_id,
                label=(
                    " ".join(payload.label.split()).strip() if payload.label is not None else None
                ),
                focus=(
                    " ".join(payload.focus.split()).strip() if payload.focus is not None else None
                ),
                interval_minutes=payload.interval_minutes,
                expected_version=payload.expected_version,
            )
        except RuntimeError as error:
            if str(error) == "monitor_version_conflict":
                raise HTTPException(
                    status_code=409, detail="监控已在别处更新，请刷新后重试"
                ) from error
            raise
        if item is None:
            raise HTTPException(status_code=404, detail="网页监控不存在")
        return {"item": _public_monitor(item)}

    @app.delete("/api/news/monitors/{monitor_id}")
    async def delete_web_monitor(monitor_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(service.store.set_status, monitor_id, "deleted")
        if item is None:
            raise HTTPException(status_code=404, detail="网页监控不存在")
        return {"item": _public_monitor(item), "deleted": True}

    @app.post("/api/news/monitors/{monitor_id}/pause")
    async def pause_web_monitor(monitor_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(service.store.set_status, monitor_id, "paused")
        if item is None:
            raise HTTPException(status_code=404, detail="网页监控不存在")
        return {"item": _public_monitor(item)}

    @app.post("/api/news/monitors/{monitor_id}/resume")
    async def resume_web_monitor(monitor_id: str) -> dict[str, Any]:
        item = await asyncio.to_thread(service.store.set_status, monitor_id, "active")
        if item is None:
            raise HTTPException(status_code=404, detail="网页监控不存在")
        return {"item": _public_monitor(item)}

    @app.post("/api/news/monitors/{monitor_id}/check")
    async def check_web_monitor(
        monitor_id: str,
        payload: WebMonitorCheckIn,
    ) -> dict[str, Any]:
        if service.store.get_monitor(monitor_id) is None:
            raise HTTPException(status_code=404, detail="网页监控不存在")
        try:
            result = await service.schedule_check(
                monitor_id, request_id=payload.request_id, manual=True
            )
        except KeyError as error:
            raise HTTPException(status_code=409, detail="监控已暂停或删除") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _public_check_result(result)

    @app.get("/api/news/monitors/{monitor_id}/history")
    async def web_monitor_history(
        monitor_id: str,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        result = await asyncio.to_thread(service.store.history, monitor_id, limit=limit)
        if result is None:
            raise HTTPException(status_code=404, detail="网页监控不存在")
        result["monitor"] = _public_monitor(result.get("monitor"))
        for check in result.get("checks", []):
            if isinstance(check, dict):
                check.pop("lease_token", None)
        return result

    @app.get("/api/news/monitor-notifications")
    async def web_monitor_notifications(
        include_read: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            service.store.notifications,
            include_read=include_read,
            limit=limit,
        )

    @app.post("/api/news/monitor-notifications/{delivery_id}")
    async def act_on_web_monitor_notification(
        delivery_id: str,
        payload: WebMonitorNotificationActionIn,
    ) -> dict[str, Any]:
        try:
            item = await asyncio.to_thread(
                service.store.mark_notification,
                delivery_id,
                action=payload.action,
                request_id=payload.request_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if item is None:
            raise HTTPException(status_code=404, detail="通知不存在")
        return {"item": item, "updated": True}
