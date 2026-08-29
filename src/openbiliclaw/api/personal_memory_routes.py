"""NewsClaw P4 personal-memory HTTP contract.

The API deliberately exposes only user-controlled mutations. System inference
producers call the storage candidate method internally; no public endpoint can
write an inferred claim directly into active memory.
"""

from __future__ import annotations

from typing import Any, Literal, NoReturn

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from openbiliclaw.storage.database import (
    Database,
    PersonalMemoryConflictError,
    PersonalMemoryMergeRequiredError,
    PersonalMemoryRequestConflictError,
)


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonalMemoryCreateIn(_StrictBody):
    kind: Literal["confirmed_fact", "research_direction"]
    title: str = Field(default="", max_length=120)
    content: str = Field(min_length=1, max_length=4000)
    request_id: str = Field(min_length=1, max_length=128)


class PersonalMemoryEditIn(_StrictBody):
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    reason: str = Field(default="用户修改", max_length=1000)


class PersonalMemoryStatusIn(_StrictBody):
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=1000)


class PersonalMemoryCandidateAcceptIn(_StrictBody):
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    kind: Literal["confirmed_fact", "research_direction"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    target_memory_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_memory_version: int | None = Field(default=None, ge=1)


class PersonalMemoryCandidateRejectIn(_StrictBody):
    expected_version: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=128)
    rejection_reason: str = Field(default="用户拒绝系统推断", max_length=1000)


class PersonalMemoryEvidenceOut(BaseModel):
    type: str
    ref: str
    excerpt: str = ""
    hash: str = ""
    observed_at: str = ""


class PersonalMemoryItemOut(BaseModel):
    memory_id: str
    kind: Literal["confirmed_fact", "research_direction"]
    title: str
    content: str
    status: Literal["active", "deleted"]
    source_type: Literal["user", "confirmed_inference"]
    source_ref: str = ""
    proposal_reason: str = ""
    confidence: float
    evidence: list[PersonalMemoryEvidenceOut]
    origin_candidate_id: str = ""
    version: int
    created_at: str
    updated_at: str
    deleted_at: str = ""
    can_restore: bool = False


class PersonalMemoryCandidateOut(BaseModel):
    candidate_id: str
    proposed_kind: Literal["confirmed_fact", "research_direction"]
    title: str
    content: str
    status: Literal["pending", "accepted", "rejected"]
    source_type: Literal["system_inference", "chat", "event", "import"]
    source_ref: str
    proposal_reason: str
    confidence: float
    evidence: list[PersonalMemoryEvidenceOut]
    version: int
    created_at: str
    resolved_at: str = ""
    resolution_memory_id: str = ""
    rejection_reason: str = ""


class PersonalMemoryRevisionOut(BaseModel):
    revision_id: int
    target_type: Literal["memory", "candidate"]
    target_id: str
    action: str
    version: int
    actor: Literal["user", "system"]
    reason: str
    before: dict[str, Any]
    after: dict[str, Any]
    created_at: str
    can_restore: bool
    restore_version: int
    title: str
    content: str


class PersonalMemoryItemMutationOut(BaseModel):
    item: PersonalMemoryItemOut
    created: bool | None = None
    updated: bool | None = None
    changed: bool | None = None
    replayed: bool


class PersonalMemoryCandidateMutationOut(BaseModel):
    candidate: PersonalMemoryCandidateOut
    item: PersonalMemoryItemOut | None = None
    decision: Literal["accept", "reject"]
    changed: bool
    replayed: bool


class PersonalMemoryDashboardOut(BaseModel):
    contract_version: Literal["personal-memory-v1"] = "personal-memory-v1"
    local_only: Literal[True] = True
    confirmed_facts: list[PersonalMemoryItemOut]
    research_directions: list[PersonalMemoryItemOut]
    pending_inferences: list[PersonalMemoryCandidateOut]
    history: list[PersonalMemoryRevisionOut]
    counts: dict[str, int]


class PersonalMemoryHistoryPageOut(BaseModel):
    items: list[PersonalMemoryRevisionOut]
    limit: int
    offset: int


_PUBLIC_ITEM_FIELDS = frozenset(PersonalMemoryItemOut.model_fields)
_PUBLIC_CANDIDATE_FIELDS = frozenset(PersonalMemoryCandidateOut.model_fields)


def _public_snapshot(value: object, *, target_type: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if "candidate" in value or "memory" in value:
        result: dict[str, Any] = {}
        candidate = value.get("candidate")
        memory = value.get("memory")
        if isinstance(candidate, dict):
            result["candidate"] = {
                key: candidate[key] for key in _PUBLIC_CANDIDATE_FIELDS if key in candidate
            }
        if isinstance(memory, dict):
            result["memory"] = {key: memory[key] for key in _PUBLIC_ITEM_FIELDS if key in memory}
        return result
    allowed = _PUBLIC_ITEM_FIELDS if target_type == "memory" else _PUBLIC_CANDIDATE_FIELDS
    return {key: value[key] for key in allowed if key in value}


def _public_revision(row: dict[str, Any]) -> dict[str, Any]:
    target_type = str(row.get("target_type") or "")
    return {
        **row,
        "before": _public_snapshot(row.get("before"), target_type=target_type),
        "after": _public_snapshot(row.get("after"), target_type=target_type),
    }


def _raise_memory_http_error(error: Exception) -> NoReturn:
    if isinstance(error, KeyError):
        raise HTTPException(
            status_code=404,
            detail={"code": "personal_memory_not_found", "message": "记忆不存在"},
        ) from error
    if isinstance(error, PersonalMemoryRequestConflictError):
        raise HTTPException(
            status_code=409,
            detail={"code": "personal_memory_request_conflict", "message": str(error)},
        ) from error
    if isinstance(error, PersonalMemoryMergeRequiredError):
        target = {
            key: error.target_item[key]
            for key in _PUBLIC_ITEM_FIELDS
            if key in error.target_item
        }
        raise HTTPException(
            status_code=409,
            detail={
                "code": "personal_memory_merge_required",
                "message": "确认后会更新一条已有个人记忆，请先核对目标内容。",
                "target_memory": target,
            },
        ) from error
    if isinstance(error, PersonalMemoryConflictError):
        code = (
            "personal_memory_version_conflict"
            if "version" in str(error).lower()
            else "personal_memory_state_conflict"
        )
        raise HTTPException(
            status_code=409,
            detail={"code": code, "message": str(error)},
        ) from error
    if isinstance(error, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"code": "personal_memory_invalid", "message": str(error)},
        ) from error
    raise error


def register_personal_memory_routes(app: FastAPI, *, database: Database) -> None:
    """Register the local-only, versioned P4 personal-memory API."""

    @app.get("/api/personal-memory", response_model=PersonalMemoryDashboardOut)
    def personal_memory_dashboard(
        history_limit: int = Query(default=30, ge=1, le=100),
    ) -> PersonalMemoryDashboardOut:
        return PersonalMemoryDashboardOut(
            confirmed_facts=database.list_personal_memory_items(
                kind="confirmed_fact", status="active", limit=200
            ),
            research_directions=database.list_personal_memory_items(
                kind="research_direction", status="active", limit=200
            ),
            pending_inferences=database.list_personal_memory_candidates(
                status="pending", limit=200
            ),
            history=[
                _public_revision(row)
                for row in database.list_personal_memory_revisions(limit=history_limit)
            ],
            counts=database.personal_memory_counts(),
        )

    @app.get(
        "/api/personal-memory/history",
        response_model=PersonalMemoryHistoryPageOut,
    )
    def personal_memory_history(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        rows = [
            _public_revision(row)
            for row in database.list_personal_memory_revisions(limit=limit, offset=offset)
        ]
        return {"items": rows, "limit": limit, "offset": offset}

    @app.get(
        "/api/personal-memory/candidates/{candidate_id}",
        response_model=PersonalMemoryCandidateOut,
    )
    def personal_memory_candidate(candidate_id: str) -> dict[str, Any]:
        row = database.get_personal_memory_candidate(candidate_id)
        if row is None:
            _raise_memory_http_error(KeyError(candidate_id))
        return row

    @app.post(
        "/api/personal-memory/items",
        response_model=PersonalMemoryItemMutationOut,
    )
    def create_personal_memory(payload: PersonalMemoryCreateIn) -> dict[str, Any]:
        try:
            return database.create_personal_memory_item(
                kind=payload.kind,
                title=payload.title,
                content=payload.content,
                request_id=payload.request_id,
            )
        except Exception as error:
            _raise_memory_http_error(error)

    @app.patch(
        "/api/personal-memory/items/{memory_id}",
        response_model=PersonalMemoryItemMutationOut,
    )
    def edit_personal_memory(
        memory_id: str, payload: PersonalMemoryEditIn
    ) -> dict[str, Any]:
        if payload.title is None and payload.content is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "personal_memory_invalid",
                    "message": "title or content is required",
                },
            )
        try:
            return database.update_personal_memory_item(
                memory_id,
                expected_version=payload.expected_version,
                request_id=payload.request_id,
                title=payload.title,
                content=payload.content,
                reason=payload.reason,
            )
        except Exception as error:
            _raise_memory_http_error(error)

    @app.post(
        "/api/personal-memory/items/{memory_id}/delete",
        response_model=PersonalMemoryItemMutationOut,
    )
    def delete_personal_memory(
        memory_id: str, payload: PersonalMemoryStatusIn
    ) -> dict[str, Any]:
        try:
            return database.delete_personal_memory_item(
                memory_id,
                expected_version=payload.expected_version,
                request_id=payload.request_id,
                reason=payload.reason or "用户删除",
            )
        except Exception as error:
            _raise_memory_http_error(error)

    @app.post(
        "/api/personal-memory/items/{memory_id}/restore",
        response_model=PersonalMemoryItemMutationOut,
    )
    def restore_personal_memory(
        memory_id: str, payload: PersonalMemoryStatusIn
    ) -> dict[str, Any]:
        try:
            return database.restore_personal_memory_item(
                memory_id,
                expected_version=payload.expected_version,
                request_id=payload.request_id,
                reason=payload.reason or "用户恢复",
            )
        except Exception as error:
            _raise_memory_http_error(error)

    @app.post(
        "/api/personal-memory/candidates/{candidate_id}/accept",
        response_model=PersonalMemoryCandidateMutationOut,
    )
    def accept_personal_memory_candidate(
        candidate_id: str, payload: PersonalMemoryCandidateAcceptIn
    ) -> dict[str, Any]:
        try:
            return database.resolve_personal_memory_candidate(
                candidate_id,
                decision="accept",
                expected_version=payload.expected_version,
                request_id=payload.request_id,
                title=payload.title,
                content=payload.content,
                kind=payload.kind,
                target_memory_id=payload.target_memory_id,
                expected_memory_version=payload.expected_memory_version,
            )
        except Exception as error:
            _raise_memory_http_error(error)

    @app.post(
        "/api/personal-memory/candidates/{candidate_id}/reject",
        response_model=PersonalMemoryCandidateMutationOut,
    )
    def reject_personal_memory_candidate(
        candidate_id: str, payload: PersonalMemoryCandidateRejectIn
    ) -> dict[str, Any]:
        try:
            return database.resolve_personal_memory_candidate(
                candidate_id,
                decision="reject",
                expected_version=payload.expected_version,
                request_id=payload.request_id,
                rejection_reason=payload.rejection_reason,
            )
        except Exception as error:
            _raise_memory_http_error(error)
