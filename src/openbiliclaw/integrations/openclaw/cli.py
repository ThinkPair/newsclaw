"""JSON CLI bridge for the OpenClaw adapter."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bootstrap import build_openclaw_adapter
from .capabilities import build_capabilities
from .errors import AdapterOperationError, AdapterValidationError
from .schemas import (
    AvoidanceProbeFeedbackRequest,
    ChatRequest,
    DelightFeedbackRequest,
    FeedbackRequest,
    InterestProbeFeedbackRequest,
    ProfileEditRequest,
    SavedItemRequest,
    SavedRemoveRequest,
    SavedSyncRequest,
)
from .skill import build_openclaw_skills

if TYPE_CHECKING:
    from collections.abc import Sequence

_SKILL_PACK_PATH = (
    Path(__file__).resolve().parents[4] / "skills" / "openbiliclaw-adapter" / "SKILL.md"
)

_RUNTIME_STREAM_URL = "ws://127.0.0.1:8420/api/runtime-stream"

# Event types that the ``listen`` command forwards to stdout.
#
# ``delight.candidate`` — proactive surprise recommendation push.
# ``interest.probe``    — the agent has a new speculative interest hypothesis
#                         it wants the user to confirm; payload mirrors the
#                         response of ``next-probe``.
_LISTEN_EVENT_TYPES = frozenset(
    {
        "delight.candidate",
        "delight.liked",
        "delight.disliked",
        "delight.chat",
        "delight.refreshed",
        "interest.probe",
        "interest.confirmed",
        "interest.rejected",
        "interest.deferred",
        "interest.chat",
        "avoidance.probe",
        "avoidance.confirmed",
        "avoidance.rejected",
        "avoidance.deferred",
        "avoidance.chat",
    }
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openbiliclaw-openclaw")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("sync-account")
    subparsers.add_parser("get-profile")
    subparsers.add_parser("capabilities")
    subparsers.add_parser("get-delight")
    subparsers.add_parser("get-spotlight")
    subparsers.add_parser("platform-availability")
    subparsers.add_parser("source-availability")
    subparsers.add_parser("profile-edit-state")
    subparsers.add_parser("next-probe")
    subparsers.add_parser("next-avoidance-probe")
    subparsers.add_parser("runtime-status")
    subparsers.add_parser("doctor")
    subparsers.add_parser("emit-skill-descriptors")

    chat_parser = subparsers.add_parser(
        "chat",
        help="Send one Socratic dialogue turn and print the agent's reply as JSON.",
    )
    chat_parser.add_argument(
        "--message",
        required=True,
        help="User message to send to the Socratic dialogue.",
    )
    chat_parser.add_argument(
        "--session",
        default="openclaw",
        help="Dialogue session label (default: 'openclaw').",
    )
    chat_parser.add_argument("--scope", default="chat")
    chat_parser.add_argument("--turn-id", default="")
    chat_parser.add_argument("--subject-id", default="")
    chat_parser.add_argument("--subject-title", default="")
    chat_parser.add_argument("--reply-to-turn-id", default="")

    chat_history_parser = subparsers.add_parser("chat-history")
    chat_history_parser.add_argument("--session", default="openclaw")
    chat_history_parser.add_argument("--scope", default="")
    chat_history_parser.add_argument("--limit", type=int, default=50)

    listen_parser = subparsers.add_parser(
        "listen",
        help="Stream proactive events (delight.candidate) via WebSocket as JSON lines.",
    )
    listen_parser.add_argument(
        "--ws-url",
        default=_RUNTIME_STREAM_URL,
        help="WebSocket URL for the runtime stream.",
    )
    listen_parser.add_argument(
        "--events",
        default=",".join(sorted(_LISTEN_EVENT_TYPES)),
        help="Comma-separated event types to forward (default: delight.candidate).",
    )

    recommend_parser = subparsers.add_parser("recommend")
    recommend_parser.add_argument("--limit", type=int, default=5)
    recommend_parser.add_argument("--source-platform", default="")
    recommend_parser.add_argument("--exclude-item-id", action="append", default=[])
    recommend_parser.add_argument("--media", default="")
    recommend_parser.add_argument("--exclude-article-id", action="append", default=[])
    recommend_parser.add_argument(
        "--realtime",
        action="store_true",
        help=(
            "Generate fresh per-item LLM expressions at request time (slow). "
            "Default serves precomputed pool copy (fast)."
        ),
    )
    refresh_group = recommend_parser.add_mutually_exclusive_group()
    refresh_group.add_argument(
        "--refresh-if-needed",
        action="store_true",
        help="Trigger runtime refresh before returning recommendations.",
    )
    refresh_group.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Skip runtime refresh and only read/generate recommendations.",
    )

    feedback_parser = subparsers.add_parser("submit-feedback")
    feedback_parser.add_argument("--recommendation-id", type=int, required=True)
    feedback_parser.add_argument("--feedback-type", required=True)
    feedback_parser.add_argument("--note", default="")
    feedback_parser.add_argument(
        "--request-id",
        required=True,
        help="Stable idempotency ID; reuse it for retries of the same action.",
    )

    for command, default_limit in (("reshuffle", 5), ("append", 10)):
        page_parser = subparsers.add_parser(command)
        page_parser.add_argument("--limit", type=int, default=default_limit)
        page_parser.add_argument("--source-platform", default="")
        page_parser.add_argument("--exclude-item-id", action="append", default=[])
        page_parser.add_argument("--media", default="")
        page_parser.add_argument("--exclude-article-id", action="append", default=[])

    activity_parser = subparsers.add_parser("activity-feed")
    activity_parser.add_argument("--limit", type=int, default=10)
    activity_parser.add_argument("--before", default="")

    avoidance_feedback_parser = subparsers.add_parser("respond-avoidance-probe")
    avoidance_feedback_parser.add_argument("--domain", required=True)
    avoidance_feedback_parser.add_argument(
        "--response",
        choices=["confirm", "reject", "defer", "chat"],
        required=True,
    )
    avoidance_feedback_parser.add_argument("--message", default="")
    avoidance_feedback_parser.add_argument(
        "--request-id",
        required=True,
        help="Stable idempotency ID; reuse it for retries of the same action.",
    )

    interest_feedback_parser = subparsers.add_parser("respond-interest-probe")
    interest_feedback_parser.add_argument("--domain", required=True)
    interest_feedback_parser.add_argument(
        "--response",
        choices=["confirm", "reject", "defer", "chat"],
        required=True,
    )
    interest_feedback_parser.add_argument("--message", default="")
    interest_feedback_parser.add_argument("--confirmation-source", default="")
    interest_feedback_parser.add_argument("--surface", default="agent")
    interest_feedback_parser.add_argument(
        "--request-id",
        required=True,
        help="Stable idempotency ID; reuse it for retries of the same action.",
    )

    for command in ("respond-delight", "respond-spotlight"):
        delight_feedback_parser = subparsers.add_parser(command)
        delight_feedback_parser.add_argument("--bvid", default="")
        delight_feedback_parser.add_argument("--content-id", default="")
        delight_feedback_parser.add_argument("--source-platform", default="")
        delight_feedback_parser.add_argument(
            "--article-id",
            default="",
            required=command == "respond-spotlight",
        )
        delight_feedback_parser.add_argument("--media", default="")
        delight_feedback_parser.add_argument(
            "--response",
            choices=["view", "open", "like", "dislike", "chat", "dismiss", "ignore"],
            required=True,
        )
        delight_feedback_parser.add_argument("--title", default="")
        delight_feedback_parser.add_argument("--message", default="")
        delight_feedback_parser.add_argument(
            "--request-id",
            default="",
            required=command == "respond-spotlight",
        )

    edit_parser = subparsers.add_parser("edit-profile")
    edit_parser.add_argument("--target", required=True)
    edit_parser.add_argument("--op", required=True)
    edit_parser.add_argument("--value", default=None)
    edit_parser.add_argument("--parent", default="")
    edit_parser.add_argument("--weight", type=float, default=None)

    save_parser = subparsers.add_parser("save-local")
    save_parser.add_argument("--list-kind", choices=["favorite", "watch_later"], required=True)
    save_parser.add_argument("--source-platform", default="")
    save_parser.add_argument("--content-id", default="")
    save_parser.add_argument("--content-url", default="")
    save_parser.add_argument("--content-type", default="video")
    save_parser.add_argument("--article-id", default="")
    save_parser.add_argument("--media", default="")
    save_parser.add_argument("--original-url", default="")
    save_parser.add_argument("--title", default="")
    save_parser.add_argument("--author-name", default="")
    save_parser.add_argument("--cover-url", default="")
    save_parser.add_argument("--note", default="")

    remove_saved_parser = subparsers.add_parser("remove-saved")
    remove_saved_parser.add_argument(
        "--list-kind", choices=["favorite", "watch_later"], required=True
    )
    remove_saved_parser.add_argument("--item-key", default="")
    remove_saved_parser.add_argument("--article-id", default="")

    list_saved_parser = subparsers.add_parser("list-saved")
    list_saved_parser.add_argument(
        "--list-kind", choices=["favorite", "watch_later"], required=True
    )
    list_saved_parser.add_argument("--limit", type=int, default=50)

    sync_saved_parser = subparsers.add_parser("sync-saved")
    sync_saved_parser.add_argument(
        "--list-kind", choices=["favorite", "watch_later"], required=True
    )
    sync_saved_parser.add_argument("--item-key", action="append", default=[])
    sync_saved_parser.add_argument(
        "--allow-state-changing",
        action="store_true",
        help="Explicitly authorize native account writes for this command.",
    )

    research_search_parser = subparsers.add_parser(
        "research-search",
        help="Run a bounded Tavily Search through the active NewsClaw runtime.",
    )
    research_search_parser.add_argument("--query", required=True)
    research_search_parser.add_argument(
        "--topic",
        choices=["general", "news", "finance"],
        default="general",
    )
    research_search_parser.add_argument(
        "--time-range",
        choices=["day", "week", "month", "year"],
        default=None,
    )
    research_search_parser.add_argument("--max-results", type=int, default=5)
    research_search_parser.add_argument("--refresh", action="store_true")

    research_extract_parser = subparsers.add_parser(
        "research-extract",
        help="Extract query-relevant evidence from up to three public URLs.",
    )
    research_extract_parser.add_argument("--query", required=True)
    research_extract_parser.add_argument(
        "--url",
        dest="urls",
        action="append",
        required=True,
    )
    research_extract_parser.add_argument("--chunks-per-source", type=int, default=2)
    research_extract_parser.add_argument("--refresh", action="store_true")
    return parser


def _print_payload(payload: dict[str, object]) -> None:
    sys.stdout.write(f"{json.dumps(payload, ensure_ascii=False)}\n")


def _uses_news_contract(args: argparse.Namespace) -> bool:
    """Return whether one invocation uses the additive NewsClaw vocabulary."""

    if os.environ.get("NEWSCLAW_MODE", "").strip() == "1":
        return True
    if args.command in {
        "get-spotlight",
        "respond-spotlight",
        "source-availability",
        "research-search",
        "research-extract",
    }:
        return True
    return any(
        bool(getattr(args, name, ""))
        for name in ("article_id", "media", "original_url", "exclude_article_id")
    )


def _filter_news_media(result: object, *, media: str, limit: int) -> None:
    """Apply a user-facing media filter to a recommendation response in place."""

    normalized = media.strip().casefold()
    if not normalized:
        return
    items = getattr(result, "items", None)
    if not isinstance(items, list):
        return
    matched = [
        item
        for item in items
        if normalized
        in {
            str(getattr(item, "author_name", "")).strip().casefold(),
            str(getattr(item, "up_name", "")).strip().casefold(),
        }
    ]
    mutable_result: Any = result
    mutable_result.items = matched[:limit]


def _serialize_result(result: object) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    raise AdapterOperationError("Agent bridge returned an unsupported result payload.")


def _with_news_aliases(
    data: dict[str, Any],
    *,
    command: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Add news names while retaining every legacy OpenBiliClaw field."""

    if command in {"recommend", "reshuffle", "append"}:
        for raw_item in data.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            article_id = str(
                raw_item.get("content_id") or raw_item.get("item_key") or raw_item.get("bvid") or ""
            )
            media = str(raw_item.get("author_name") or raw_item.get("up_name") or "")
            raw_item.setdefault("article_id", article_id)
            raw_item.setdefault("original_url", str(raw_item.get("content_url") or ""))
            raw_item.setdefault("media", media)
            raw_item.setdefault("publisher_name", media)
            raw_item.setdefault("technical_provider", "")
            raw_item.setdefault(
                "recommendation_reason",
                str(raw_item.get("expression") or raw_item.get("reason") or ""),
            )
    elif command == "get-spotlight":
        raw_item = data.get("item")
        if isinstance(raw_item, dict):
            raw_item.setdefault(
                "article_id",
                str(
                    raw_item.get("content_id")
                    or raw_item.get("item_key")
                    or raw_item.get("bvid")
                    or ""
                ),
            )
            raw_item.setdefault("original_url", str(raw_item.get("content_url") or ""))
            raw_item.setdefault("media", "")
            raw_item.setdefault("technical_provider", "")
            raw_item.setdefault("spotlight_reason", str(raw_item.get("delight_reason") or ""))
            raw_item.setdefault("spotlight_score", raw_item.get("delight_score", 0.0))
            raw_item.setdefault("spotlight_hook", str(raw_item.get("delight_hook") or ""))
    elif command in {"respond-spotlight", "save-local", "remove-saved"}:
        data.setdefault("article_id", str(getattr(args, "article_id", "") or ""))
    elif command == "list-saved":
        for raw_item in data.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            item_key = str(raw_item.get("item_key") or "")
            content_id = str(raw_item.get("content_id") or "")
            article_id = content_id or (item_key[5:] if item_key.startswith("news:") else item_key)
            raw_item.setdefault("article_id", article_id)
            raw_item.setdefault("original_url", str(raw_item.get("content_url") or ""))
            raw_item.setdefault("media", str(raw_item.get("author_name") or ""))
    return data


async def _run_command(
    args: argparse.Namespace,
    adapter: Any,
    *,
    news_runtime: Any | None = None,
) -> dict[str, object]:
    if args.command == "doctor":
        skills = build_openclaw_skills(adapter)
        return {
            "ok": True,
            "data": {
                "protocol_version": "agent-bridge/v2",
                "host_names": ["openclaw", "hermes", "workbuddy"],
                "skill_pack_path": str(_SKILL_PACK_PATH),
                "skill_pack_exists": _SKILL_PACK_PATH.exists(),
                "skill_count": len(skills),
                "skill_names": [item.name for item in skills],
                "cli_module": "openbiliclaw.integrations.openclaw.cli",
            },
        }
    if args.command == "capabilities":
        data = asdict(build_capabilities(adapter))
        research_service = getattr(news_runtime, "research_service", None)
        if research_service is not None:
            skill_names = list(data.get("skill_names", []))
            for name in ("newsclaw_search_web", "newsclaw_extract_evidence"):
                if name not in skill_names:
                    skill_names.append(name)
            data["skill_names"] = skill_names
        return {
            "ok": True,
            "data": data,
        }
    if args.command == "emit-skill-descriptors":
        skills = build_openclaw_skills(adapter)
        return {
            "ok": True,
            "data": {
                "skills": [
                    {
                        "name": item.name,
                        "description": item.description,
                        "input_schema": item.input_schema,
                    }
                    for item in skills
                ]
            },
        }
    try:
        if args.command == "sync-account":
            result = await adapter.sync_account()
        elif args.command == "get-profile":
            result = await adapter.get_profile()
        elif args.command in {"get-delight", "get-spotlight"}:
            result = await adapter.get_delight()
        elif args.command == "runtime-status":
            result = await adapter.get_runtime_status()
        elif args.command in {"research-search", "research-extract"}:
            research_service = getattr(news_runtime, "research_service", None)
            if research_service is None:
                raise AdapterOperationError(
                    "NewsClaw research is available only while the NewsClaw runtime is running."
                )
            from openbiliclaw.research.errors import (
                ResearchConfigurationError,
                ResearchProviderError,
                ResearchValidationError,
            )

            try:
                if args.command == "research-search":
                    result = await research_service.search(
                        query=args.query,
                        topic=args.topic,
                        time_range=args.time_range,
                        max_results=args.max_results,
                        refresh=bool(args.refresh),
                    )
                else:
                    result = await research_service.extract(
                        urls=args.urls,
                        query=args.query,
                        chunks_per_source=args.chunks_per_source,
                        refresh=bool(args.refresh),
                    )
                result = result.to_dict()
            except ResearchValidationError as error:
                raise AdapterValidationError(str(error)) from error
            except (ResearchConfigurationError, ResearchProviderError) as error:
                raise AdapterOperationError(str(error)) from error
        elif args.command == "recommend":
            news_contract = _uses_news_contract(args)
            media = str(getattr(args, "media", "") or "")
            requested_limit = args.limit
            recommend_kwargs: dict[str, object] = {
                "limit": 50 if media and args.limit < 50 else args.limit,
                "refresh_if_needed": bool(args.refresh_if_needed),
            }
            source_platform = args.source_platform or ("news" if news_contract else "")
            if source_platform:
                recommend_kwargs["source_platform"] = source_platform
            excluded_ids = [*args.exclude_item_id, *args.exclude_article_id]
            if excluded_ids:
                recommend_kwargs["excluded_item_ids"] = excluded_ids
            if getattr(args, "realtime", False):
                recommend_kwargs["realtime"] = True
            result = await adapter.recommend(**recommend_kwargs)
            _filter_news_media(result, media=media, limit=requested_limit)
        elif args.command in {"reshuffle", "append"}:
            method_name = "reshuffle" if args.command == "reshuffle" else "append_recommendations"
            method = getattr(adapter, method_name)
            news_contract = _uses_news_contract(args)
            media = str(getattr(args, "media", "") or "")
            requested_limit = args.limit
            result = await method(
                limit=50 if media and args.limit < 50 else args.limit,
                source_platform=args.source_platform or ("news" if news_contract else ""),
                excluded_item_ids=[*args.exclude_item_id, *args.exclude_article_id],
            )
            _filter_news_media(result, media=media, limit=requested_limit)
        elif args.command == "submit-feedback":
            request = FeedbackRequest(
                recommendation_id=args.recommendation_id,
                feedback_type=args.feedback_type,
                note=args.note,
                request_id=args.request_id,
            )
            result = await adapter.submit_feedback(request)
        elif args.command == "chat":
            chat_request = ChatRequest(
                message=args.message,
                session=getattr(args, "session", "openclaw"),
                scope=getattr(args, "scope", "chat"),
                turn_id=getattr(args, "turn_id", ""),
                subject_id=getattr(args, "subject_id", ""),
                subject_title=getattr(args, "subject_title", ""),
                reply_to_turn_id=getattr(args, "reply_to_turn_id", ""),
            )
            result = await adapter.chat(chat_request)
        elif args.command == "chat-history":
            result = await adapter.get_chat_history(
                session=args.session,
                scope=args.scope,
                limit=args.limit,
            )
        elif args.command == "activity-feed":
            result = await adapter.get_activity_feed(limit=args.limit, before=args.before)
        elif args.command == "platform-availability":
            result = await adapter.get_platform_availability()
        elif args.command == "source-availability":
            if news_runtime is not None and callable(
                public_sources := getattr(news_runtime, "public_sources", None)
            ):
                sources: list[dict[str, object]] = []
                for source in public_sources():
                    row = dict(source)
                    health = str(row.get("status") or "unknown")
                    row["state"] = {
                        "healthy": "online",
                        "degraded": "degraded",
                        "unavailable": "offline",
                    }.get(health, "unknown")
                    sources.append(row)
                states = {str(row.get("status") or "unknown") for row in sources}
                if sources and states == {"healthy"}:
                    aggregate_status = "online"
                elif states.intersection({"healthy", "degraded"}):
                    aggregate_status = "degraded"
                else:
                    aggregate_status = "offline"
                result = {"status": aggregate_status, "sources": sources}
            else:
                availability = await adapter.get_platform_availability()
                serialized = _serialize_result(availability)
                count = int(dict(serialized.get("by_platform", {})).get("news", 0) or 0)
                result = {
                    **serialized,
                    "status": "online" if count else "offline",
                    "sources": [
                        {
                            "id": "news",
                            "source_id": "news",
                            "media": "新闻候选池",
                            "technical_provider": "candidate_pool",
                            "status": "healthy" if count else "unknown",
                            "state": "online" if count else "unknown",
                            "article_count": count,
                        }
                    ],
                }
        elif args.command == "next-probe":
            result = await adapter.get_next_probe()
        elif args.command == "next-avoidance-probe":
            result = await adapter.get_next_avoidance_probe()
        elif args.command == "respond-avoidance-probe":
            avoidance_request = AvoidanceProbeFeedbackRequest(
                domain=args.domain,
                response=args.response,
                message=args.message,
                request_id=args.request_id,
            )
            result = await adapter.respond_avoidance_probe(avoidance_request)
        elif args.command == "respond-interest-probe":
            result = await adapter.respond_interest_probe(
                InterestProbeFeedbackRequest(
                    domain=args.domain,
                    response=args.response,
                    message=args.message,
                    confirmation_source=args.confirmation_source,
                    surface=args.surface,
                    request_id=args.request_id,
                )
            )
        elif args.command in {"respond-delight", "respond-spotlight"}:
            response_aliases = {"ignore": "dismiss"}
            response = response_aliases.get(args.response, args.response)
            result = await adapter.respond_delight(
                DelightFeedbackRequest(
                    bvid=args.bvid,
                    content_id=args.content_id or args.article_id,
                    source_platform=args.source_platform
                    or ("news" if args.command == "respond-spotlight" else ""),
                    response=response,
                    title=args.title,
                    message=args.message,
                    request_id=args.request_id,
                )
            )
        elif args.command == "profile-edit-state":
            result = await adapter.get_profile_edit_state()
        elif args.command == "edit-profile":
            result = await adapter.edit_profile(
                ProfileEditRequest(
                    target=args.target,
                    op=args.op,
                    value=args.value,
                    parent=args.parent,
                    weight=args.weight,
                )
            )
        elif args.command == "save-local":
            news_save = bool(args.article_id or args.media or args.original_url)
            result = await adapter.save_local(
                SavedItemRequest(
                    list_kind=args.list_kind,
                    source_platform=args.source_platform or ("news" if news_save else ""),
                    content_id=args.content_id or args.article_id,
                    content_url=args.content_url or args.original_url,
                    content_type="article" if news_save else args.content_type,
                    title=args.title,
                    author_name=args.author_name or args.media,
                    cover_url=args.cover_url,
                    note=args.note,
                )
            )
        elif args.command == "remove-saved":
            item_key = args.item_key
            if not item_key and args.article_id:
                from openbiliclaw.saved_sync.identity import make_item_key

                item_key = make_item_key("news", args.article_id)
            result = await adapter.remove_saved(
                SavedRemoveRequest(list_kind=args.list_kind, item_key=item_key)
            )
        elif args.command == "list-saved":
            result = await adapter.list_saved(list_kind=args.list_kind, limit=args.limit)
        elif args.command == "sync-saved":
            result = await adapter.sync_saved(
                SavedSyncRequest(
                    list_kind=args.list_kind,
                    item_keys=args.item_key,
                    allow_state_changing=bool(args.allow_state_changing),
                )
            )
        else:  # pragma: no cover - argparse guarantees command validity
            raise AdapterValidationError(f"Unsupported command: {args.command}")
    except AdapterValidationError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_type": "validation_error",
        }
    except AdapterOperationError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_type": "operation_error",
        }
    data = _serialize_result(result)
    if _uses_news_contract(args):
        data = _with_news_aliases(data, command=args.command, args=args)
    return {
        "ok": True,
        "data": data,
    }


# ---------------------------------------------------------------------------
# ``listen`` — long-running WebSocket event stream
# ---------------------------------------------------------------------------

_WS_RECONNECT_DELAY = 3.0
_DELIGHT_ACK_URL = "http://127.0.0.1:8420/api/delight/sent"


async def _acknowledge_delight(bvid: str) -> None:
    """POST acknowledgment so the backend marks the item as notified."""
    try:
        import aiohttp

        async with (
            aiohttp.ClientSession(trust_env=False) as session,
            session.post(
                _DELIGHT_ACK_URL,
                json={"bvid": bvid},
            ) as resp,
        ):
            resp.raise_for_status()
    except Exception:
        # Fallback to synchronous urllib when aiohttp is unavailable
        try:
            import urllib.request

            req = urllib.request.Request(
                _DELIGHT_ACK_URL,
                data=json.dumps({"bvid": bvid}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
        except Exception:
            pass


async def _listen_ws(ws_url: str, event_types: frozenset[str]) -> None:
    """Connect to the runtime WebSocket stream and forward matching events.

    Each matching event is written to stdout as a single JSON line:

        {"type": "delight.candidate", "bvid": "BV1xxx", ...}

    The connection auto-reconnects on failure. Press Ctrl-C to stop.
    """
    try:
        import websockets
    except ModuleNotFoundError:
        _print_payload(
            {
                "ok": False,
                "error": (
                    "The 'listen' command requires the 'websockets' package. "
                    "Install it with:  pip install websockets"
                ),
                "error_type": "dependency_error",
            }
        )
        return

    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                _print_payload(
                    {
                        "ok": True,
                        "data": {
                            "status": "connected",
                            "ws_url": ws_url,
                            "event_types": sorted(event_types),
                        },
                    }
                )
                sys.stdout.flush()
                async for raw_message in ws:
                    try:
                        event = json.loads(raw_message)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = str(event.get("type", ""))
                    if event_type not in event_types:
                        continue
                    _print_payload({"ok": True, "data": event})
                    sys.stdout.flush()
                    # Auto-ACK delight candidates so cooldown starts immediately
                    if event_type == "delight.candidate":
                        bvid = str(event.get("bvid", ""))
                        if bvid:
                            await _acknowledge_delight(bvid)
        except Exception:
            _print_payload(
                {
                    "ok": False,
                    "error": "WebSocket disconnected, reconnecting...",
                    "error_type": "connection_error",
                }
            )
            sys.stdout.flush()
            await asyncio.sleep(_WS_RECONNECT_DELAY)


def main(argv: Sequence[str] | None = None, *, adapter: Any | None = None) -> int:
    """Run the OpenClaw adapter CLI and print JSON output."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    # ``listen`` is a long-running stream — handle separately
    if args.command == "listen":
        event_types = (
            frozenset(t.strip() for t in args.events.split(",") if t.strip()) or _LISTEN_EVENT_TYPES
        )
        with suppress(KeyboardInterrupt):
            asyncio.run(_listen_ws(args.ws_url, event_types))
        return 0

    if adapter is not None:
        resolved_adapter = adapter
    elif args.command in {"doctor", "capabilities", "emit-skill-descriptors"}:
        resolved_adapter = object()
    else:
        resolved_adapter = build_openclaw_adapter()
    payload = asyncio.run(_run_command(args, resolved_adapter))
    _print_payload(payload)
    return 0 if bool(payload.get("ok", False)) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
