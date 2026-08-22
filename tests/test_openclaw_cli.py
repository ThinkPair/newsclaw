"""Tests for the OpenClaw adapter CLI bridge."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from openbiliclaw.integrations.openclaw.errors import AdapterOperationError, AdapterValidationError
from openbiliclaw.integrations.openclaw.operations import OpenClawAdapter
from openbiliclaw.integrations.openclaw.schemas import (
    AvoidanceProbeFeedbackResponse,
    AvoidanceProbeItem,
    AvoidanceProbeResponse,
    ChatRequest,
    ChatResponse,
    DelightFeedbackResponse,
    DelightItem,
    DelightResponse,
    FeedbackResponse,
    InterestProbeFeedbackResponse,
    InterestProbeItem,
    InterestProbeResponse,
    ProfileResponse,
    RecommendationItem,
    RecommendationResponse,
    RuntimeStatusResponse,
    SavedItemResponse,
    SavedRemoveResponse,
    SyncAccountResponse,
)
from openbiliclaw.llm.service import LLMResponseContentError

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILL_PACK_PATH = _REPO_ROOT / "skills" / "openbiliclaw-adapter" / "SKILL.md"


class _FakeCliAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def sync_account(self) -> SyncAccountResponse:
        self.calls.append(("sync_account",))
        return SyncAccountResponse(synced=True, new_event_count=8, errors=[])

    async def get_profile(self) -> ProfileResponse:
        self.calls.append(("get_profile",))
        return ProfileResponse(
            initialized=True,
            personality_portrait="你会自己往深处追问题。",
            core_traits=["深究"],
            deep_needs=["把问题想透"],
            top_interests=["国际时事"],
        )

    async def recommend(
        self,
        *,
        limit: int = 5,
        refresh_if_needed: bool = True,
    ) -> RecommendationResponse:
        self.calls.append(("recommend", limit, refresh_if_needed))
        return RecommendationResponse(
            items=[
                RecommendationItem(
                    recommendation_id=21,
                    bvid="BV1CLI",
                    title="把议题讲到结构层",
                    up_name="结构控",
                    cover_url="https://example.com/cover.jpg",
                    reason="这条会对上你最近那股继续往深处看的劲头。",
                    topic_label="你最近那股继续往深处看的劲头",
                    confidence=0.93,
                )
            ]
        )

    async def submit_feedback(self, request) -> FeedbackResponse:  # noqa: ANN001
        self.calls.append(
            (
                "submit_feedback",
                request.recommendation_id,
                request.feedback_type,
                request.note,
                request.request_id,
            )
        )
        return FeedbackResponse(
            ok=True,
            recommendation_id=request.recommendation_id,
            feedback_type=request.feedback_type,
        )

    async def get_delight(self) -> DelightResponse:
        self.calls.append(("get_delight",))
        return DelightResponse(
            item=DelightItem(
                bvid="BV1CLIDELIGHT",
                title="意外的跨域发现",
                delight_reason="这条会戳到你一直想搞明白的那个方向。",
                delight_score=0.91,
                delight_hook="跨域惊喜",
                cover_url="https://example.com/delight.jpg",
            ),
        )

    async def get_runtime_status(self) -> RuntimeStatusResponse:
        self.calls.append(("get_runtime_status",))
        return RuntimeStatusResponse(
            initialized=True,
            recommendation_count=4,
            pending_signal_events=1,
            unread_count=2,
            pool_available_count=12,
            pool_target_count=30,
            last_refresh_at="2026-03-15T12:00:00+08:00",
            last_account_sync_at="2026-03-15T12:05:00+08:00",
            last_account_sync_error="",
        )

    async def chat(self, request) -> ChatResponse:  # noqa: ANN001
        self.calls.append(("chat", request.message, request.session))
        return ChatResponse(
            reply="你说的这个方向我有个猜测——你是不是更在意底层结构？",
            session=request.session,
        )

    async def get_next_probe(self) -> InterestProbeResponse:
        self.calls.append(("get_next_probe",))
        return InterestProbeResponse(
            probe=InterestProbeItem(
                domain="建筑美学",
                category="人文",
                reason="你最近看了很多关于结构和空间的内容。",
                confidence=0.45,
                weight=0.4,
                specifics=["参数化设计"],
                question=(
                    "我从你最近的轨迹里嗅到你可能对【建筑美学】"
                    "（比如：参数化设计）感兴趣——"
                    "你最近看了很多关于结构和空间的内容。 "
                    "这个方向你自己认不认？"
                ),
            )
        )

    async def get_next_avoidance_probe(self) -> AvoidanceProbeResponse:
        self.calls.append(("get_next_avoidance_probe",))
        return AvoidanceProbeResponse(
            probe=AvoidanceProbeItem(
                domain="浅层热点复读",
                reason="用户可能想避开无信息增量的热点复读内容。",
                source_mode="negative_signal",
                source_signal="thumbs_down",
                confidence=0.55,
                weight=0.5,
                specifics=["标题党热点解读"],
                question="我猜【浅层热点复读】（比如：标题党热点解读）可能是你想避开的方向。",
            )
        )

    async def respond_avoidance_probe(self, request) -> AvoidanceProbeFeedbackResponse:  # noqa: ANN001
        self.calls.append(
            (
                "respond_avoidance_probe",
                request.domain,
                request.response,
                request.message,
                request.request_id,
            )
        )
        return AvoidanceProbeFeedbackResponse(
            ok=True,
            action="confirmed",
            domain=request.domain,
        )

    async def respond_interest_probe(self, request) -> InterestProbeFeedbackResponse:  # noqa: ANN001
        self.calls.append(
            (
                "respond_interest_probe",
                request.domain,
                request.response,
                request.message,
                request.request_id,
            )
        )
        return InterestProbeFeedbackResponse(
            ok=True,
            action="confirmed",
            domain=request.domain,
        )


def test_recommend_cli_emits_json_and_returns_zero(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(
        ["recommend", "--limit", "3", "--skip-refresh"],
        adapter=adapter,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    item = payload["data"]["items"][0]
    assert item["recommendation_id"] == 21
    assert item["bvid"] == "BV1CLI"
    assert item["source_platform"] == ""
    assert item["content_type"] == "video"
    assert adapter.calls == [("recommend", 3, False)]


def test_recommend_cli_defaults_to_fast_path_without_refresh(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(["recommend"], adapter=adapter)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["ok"] is True
    assert adapter.calls == [("recommend", 5, False)]


def test_recommend_cli_can_explicitly_enable_refresh(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(["recommend", "--refresh-if-needed"], adapter=adapter)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["ok"] is True
    assert adapter.calls == [("recommend", 5, True)]


def test_submit_feedback_cli_emits_error_json_and_returns_one(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    class BadAdapter(_FakeCliAdapter):
        async def submit_feedback(self, request) -> FeedbackResponse:  # noqa: ANN001
            raise AdapterValidationError(f"bad input: {request.feedback_type}")

    exit_code = main(
        [
            "submit-feedback",
            "--recommendation-id",
            "9",
            "--feedback-type",
            "like",
            "--request-id",
            "openclaw-cli-like-9",
        ],
        adapter=BadAdapter(),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out) == {
        "ok": False,
        "error": "bad input: like",
        "error_type": "validation_error",
    }


def test_submit_feedback_cli_requires_request_id() -> None:
    from openbiliclaw.integrations.openclaw.cli import _build_parser

    parser = _build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "submit-feedback",
                "--recommendation-id",
                "9",
                "--feedback-type",
                "like",
            ]
        )

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "command",
    ("respond-interest-probe", "respond-avoidance-probe"),
)
def test_probe_feedback_cli_requires_request_id(command: str) -> None:
    from openbiliclaw.integrations.openclaw.cli import _build_parser

    parser = _build_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                command,
                "--domain",
                "建筑美学",
                "--response",
                "confirm",
            ]
        )

    assert exc_info.value.code == 2


def test_doctor_cli_reports_skill_pack_and_registered_skill_names(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(["doctor"], adapter=adapter)

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    data = payload["data"]
    assert data["protocol_version"] == "agent-bridge/v2"
    assert data["host_names"] == ["openclaw", "hermes", "workbuddy"]
    assert data["skill_pack_path"] == str(_SKILL_PACK_PATH)
    assert data["skill_pack_exists"] is True
    assert data["skill_count"] == 24
    assert "openbiliclaw_respond_interest_probe" in data["skill_names"]
    assert "openbiliclaw_sync_saved" in data["skill_names"]
    assert data["cli_module"] == "openbiliclaw.integrations.openclaw.cli"
    assert adapter.calls == []


def test_emit_skill_descriptors_cli_outputs_serializable_descriptors(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(["emit-skill-descriptors"], adapter=adapter)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["data"]["skills"][0]["name"] == "openbiliclaw_sync_account"
    assert payload["data"]["skills"][3]["name"] == "openbiliclaw_recommend"
    assert "handler" not in payload["data"]["skills"][0]
    assert adapter.calls == []


def test_get_delight_cli_emits_json_and_returns_zero(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(["get-delight"], adapter=adapter)

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["data"]["item"]["bvid"] == "BV1CLIDELIGHT"
    assert payload["data"]["item"]["delight_hook"] == "跨域惊喜"
    assert payload["data"]["item"]["delight_score"] == 0.91
    assert adapter.calls == [("get_delight",)]


def test_listen_parser_accepts_default_flags() -> None:
    from openbiliclaw.integrations.openclaw.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["listen"])

    assert args.command == "listen"
    assert args.ws_url == "ws://127.0.0.1:8420/api/runtime-stream"
    assert "delight.candidate" in args.events


def test_listen_parser_accepts_custom_flags() -> None:
    from openbiliclaw.integrations.openclaw.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(
        [
            "listen",
            "--ws-url",
            "ws://custom:9999/api/runtime-stream",
            "--events",
            "delight.candidate,refresh.pool_updated",
        ]
    )

    assert args.ws_url == "ws://custom:9999/api/runtime-stream"
    assert "delight.candidate" in args.events
    assert "refresh.pool_updated" in args.events


def test_workspace_skill_pack_exists_and_mentions_cli_bridge() -> None:
    content = _SKILL_PACK_PATH.read_text(encoding="utf-8")

    assert "name: openbiliclaw-adapter" in content
    assert "uv run python -m openbiliclaw.integrations.openclaw.cli" in content
    assert "recommend --limit" in content
    assert "get-delight" in content
    assert "listen" in content


def test_chat_cli_emits_json_and_returns_zero(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(
        ["chat", "--message", "我最近对建筑很感兴趣"],
        adapter=adapter,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert "底层结构" in payload["data"]["reply"]
    assert payload["data"]["session"] == "openclaw"
    assert adapter.calls == [("chat", "我最近对建筑很感兴趣", "openclaw")]


def test_chat_cli_accepts_custom_session(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(
        ["chat", "--message", "你好", "--session", "my-session"],
        adapter=adapter,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["data"]["session"] == "my-session"


@pytest.mark.asyncio
async def test_chat_adapter_error_contains_safe_classification_without_raw_detail() -> None:
    class FailingService:
        async def complete_socratic_dialogue(self, **_kwargs: object) -> object:
            raise LLMResponseContentError("sk-live-secret")

    adapter = OpenClawAdapter(
        services=SimpleNamespace(
            soul_engine=SimpleNamespace(_llm=None),
            llm_service=FailingService(),
        )
    )

    with pytest.raises(AdapterOperationError) as caught:
        await adapter.chat(ChatRequest(message="继续聊", session="openclaw"))

    assert "空响应" in str(caught.value)
    assert "sk-live-secret" not in str(caught.value)


def test_next_probe_cli_emits_json_and_returns_zero(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(["next-probe"], adapter=adapter)

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["data"]["probe"]["domain"] == "建筑美学"
    assert "认不认" in payload["data"]["probe"]["question"]
    assert adapter.calls == [("get_next_probe",)]


def test_next_avoidance_probe_cli_emits_json_and_returns_zero(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(["next-avoidance-probe"], adapter=adapter)

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["data"]["probe"]["domain"] == "浅层热点复读"
    assert "避开" in payload["data"]["probe"]["question"]
    assert adapter.calls == [("get_next_avoidance_probe",)]


def test_respond_avoidance_probe_cli_emits_json_and_returns_zero(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(
        [
            "respond-avoidance-probe",
            "--domain",
            "浅层热点复读",
            "--response",
            "confirm",
            "--message",
            "对，就是不想看",
            "--request-id",
            "avoidance-cli-confirm-1",
        ],
        adapter=adapter,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["data"]["action"] == "confirmed"
    assert adapter.calls == [
        (
            "respond_avoidance_probe",
            "浅层热点复读",
            "confirm",
            "对，就是不想看",
            "avoidance-cli-confirm-1",
        )
    ]


def test_respond_interest_probe_cli_passes_request_id(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    adapter = _FakeCliAdapter()

    exit_code = main(
        [
            "respond-interest-probe",
            "--domain",
            "建筑美学",
            "--response",
            "confirm",
            "--request-id",
            "interest-cli-confirm-1",
        ],
        adapter=adapter,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out)["data"]["action"] == "confirmed"
    assert adapter.calls == [
        (
            "respond_interest_probe",
            "建筑美学",
            "confirm",
            "",
            "interest-cli-confirm-1",
        )
    ]


def test_listen_default_events_include_interest_probe() -> None:
    from openbiliclaw.integrations.openclaw.cli import _LISTEN_EVENT_TYPES

    assert "interest.probe" in _LISTEN_EVENT_TYPES
    assert "avoidance.probe" in _LISTEN_EVENT_TYPES
    assert "delight.candidate" in _LISTEN_EVENT_TYPES


def test_newsclaw_dsh_bridge_parser_accepts_all_22_tool_contracts() -> None:
    from openbiliclaw.integrations.openclaw.cli import _build_parser

    cases: dict[str, list[str]] = {
        "capabilities": [],
        "recommend": [
            "--limit",
            "1",
            "--media",
            "财联社",
            "--exclude-article-id",
            "na_1",
            "--refresh-if-needed",
            "--realtime",
        ],
        "reshuffle": ["--media", "财联社", "--exclude-article-id", "na_1"],
        "append": [
            "--limit",
            "1",
            "--media",
            "财联社",
            "--exclude-article-id",
            "na_1",
        ],
        "submit-feedback": [
            "--recommendation-id",
            "1",
            "--feedback-type",
            "like",
            "--request-id",
            "req-feedback-1",
        ],
        "get-spotlight": [],
        "respond-spotlight": [
            "--article-id",
            "na_1",
            "--media",
            "财联社",
            "--response",
            "like",
            "--request-id",
            "req-spotlight-1",
            "--title",
            "标题",
            "--message",
            "继续聊",
        ],
        "runtime-status": [],
        "activity-feed": ["--limit", "1"],
        "source-availability": [],
        "chat": ["--message", "你好", "--session", "dsh"],
        "chat-history": ["--session", "dsh", "--limit", "1"],
        "next-probe": [],
        "respond-interest-probe": [
            "--domain",
            "宏观",
            "--response",
            "confirm",
            "--request-id",
            "req-interest-1",
        ],
        "next-avoidance-probe": [],
        "respond-avoidance-probe": [
            "--domain",
            "谣言",
            "--response",
            "confirm",
            "--request-id",
            "req-avoid-1",
        ],
        "get-profile": [],
        "profile-edit-state": [],
        "edit-profile": [
            "--target",
            "likes",
            "--op",
            "add",
            "--value",
            "宏观",
            "--weight",
            "1",
        ],
        "save-local": [
            "--list-kind",
            "favorite",
            "--article-id",
            "na_1",
            "--media",
            "财联社",
            "--title",
            "标题",
            "--original-url",
            "https://example.com/a",
        ],
        "remove-saved": ["--list-kind", "favorite", "--article-id", "na_1"],
        "list-saved": ["--list-kind", "favorite", "--limit", "1"],
    }

    parser = _build_parser()
    parsed = {
        command: parser.parse_args([command, *argv]).command for command, argv in cases.items()
    }

    assert len(parsed) == 22
    assert parsed == {command: command for command in cases}


def test_newsclaw_recommend_maps_media_scope_and_article_aliases(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    class NewsAdapter:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] = {}

        async def recommend(self, **kwargs: object) -> RecommendationResponse:
            self.kwargs = kwargs
            return RecommendationResponse(
                items=[
                    RecommendationItem(
                        recommendation_id=1,
                        bvid="na_1",
                        content_id="na_1",
                        content_url="https://example.com/a",
                        source_platform="news",
                        title="匹配新闻",
                        author_name="财联社",
                        up_name="财联社",
                        expression="适合你的原因",
                    ),
                    RecommendationItem(
                        recommendation_id=2,
                        bvid="na_2",
                        content_id="na_2",
                        source_platform="news",
                        title="其他新闻",
                        author_name="第一财经",
                        up_name="第一财经",
                    ),
                ]
            )

    adapter = NewsAdapter()
    exit_code = main(
        [
            "recommend",
            "--limit",
            "5",
            "--media",
            "财联社",
            "--exclude-article-id",
            "na_old",
        ],
        adapter=adapter,
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert adapter.kwargs == {
        "limit": 50,
        "refresh_if_needed": False,
        "source_platform": "news",
        "excluded_item_ids": ["na_old"],
    }
    assert len(payload["data"]["items"]) == 1
    item = payload["data"]["items"][0]
    assert item["article_id"] == "na_1"
    assert item["media"] == "财联社"
    assert item["original_url"] == "https://example.com/a"
    assert item["recommendation_reason"] == "适合你的原因"


def test_newsclaw_spotlight_alias_maps_identity_and_actions(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    class NewsAdapter:
        def __init__(self) -> None:
            self.response_request = None

        async def get_delight(self) -> DelightResponse:
            return DelightResponse(
                item=DelightItem(
                    content_id="na_1",
                    source_platform="news",
                    title="今日焦点",
                    delight_reason="影响链正在扩散",
                    delight_score=0.88,
                    delight_hook="后续看政策回应",
                )
            )

        async def respond_delight(self, request) -> DelightFeedbackResponse:  # noqa: ANN001
            self.response_request = request
            return DelightFeedbackResponse(ok=True, action="dismissed")

    adapter = NewsAdapter()
    assert main(["get-spotlight"], adapter=adapter) == 0
    item = json.loads(capsys.readouterr().out)["data"]["item"]
    assert item["article_id"] == "na_1"
    assert item["spotlight_reason"] == "影响链正在扩散"
    assert item["spotlight_score"] == 0.88
    assert item["spotlight_hook"] == "后续看政策回应"

    assert (
        main(
            [
                "respond-spotlight",
                "--article-id",
                "na_1",
                "--response",
                "ignore",
                "--request-id",
                "req-ignore-1",
            ],
            adapter=adapter,
        )
        == 0
    )
    response_payload = json.loads(capsys.readouterr().out)
    assert response_payload["data"]["article_id"] == "na_1"
    assert adapter.response_request.content_id == "na_1"
    assert adapter.response_request.source_platform == "news"
    assert adapter.response_request.response == "dismiss"
    assert adapter.response_request.request_id == "req-ignore-1"

    assert (
        main(
            [
                "respond-spotlight",
                "--article-id",
                "na_1",
                "--response",
                "open",
                "--request-id",
                "req-open-1",
            ],
            adapter=adapter,
        )
        == 0
    )
    capsys.readouterr()
    assert adapter.response_request.response == "open"
    assert adapter.response_request.request_id == "req-open-1"


@pytest.mark.asyncio
async def test_newsclaw_source_availability_uses_runtime_source_health() -> None:
    from openbiliclaw.integrations.openclaw.cli import _build_parser, _run_command

    class Runtime:
        @staticmethod
        def public_sources() -> list[dict[str, object]]:
            return [
                {"id": "tushare:news", "media": "财联社", "status": "healthy"},
                {"id": "rss:stats", "media": "国家统计局", "status": "degraded"},
            ]

    args = _build_parser().parse_args(["source-availability"])
    payload = await _run_command(args, object(), news_runtime=Runtime())

    assert payload["ok"] is True
    assert payload["data"]["status"] == "degraded"  # type: ignore[index]
    assert len(payload["data"]["sources"]) == 2  # type: ignore[index]
    assert payload["data"]["sources"][0]["state"] == "online"  # type: ignore[index]


def test_newsclaw_saved_aliases_map_article_identity(capsys) -> None:
    from openbiliclaw.integrations.openclaw.cli import main

    class NewsAdapter:
        def __init__(self) -> None:
            self.saved_request = None
            self.removed_request = None

        async def save_local(self, request) -> SavedItemResponse:  # noqa: ANN001
            self.saved_request = request
            return SavedItemResponse(
                saved=True,
                list_kind=request.list_kind,
                item_key=f"news:{request.content_id}",
            )

        async def remove_saved(self, request) -> SavedRemoveResponse:  # noqa: ANN001
            self.removed_request = request
            return SavedRemoveResponse(
                removed=True,
                list_kind=request.list_kind,
                item_key=request.item_key,
            )

    adapter = NewsAdapter()
    assert (
        main(
            [
                "save-local",
                "--list-kind",
                "favorite",
                "--article-id",
                "na_1",
                "--media",
                "财联社",
                "--title",
                "标题",
                "--original-url",
                "https://example.com/a",
            ],
            adapter=adapter,
        )
        == 0
    )
    save_payload = json.loads(capsys.readouterr().out)
    assert save_payload["data"]["article_id"] == "na_1"
    assert adapter.saved_request.source_platform == "news"
    assert adapter.saved_request.content_id == "na_1"
    assert adapter.saved_request.content_url == "https://example.com/a"
    assert adapter.saved_request.content_type == "article"
    assert adapter.saved_request.author_name == "财联社"

    assert (
        main(
            ["remove-saved", "--list-kind", "favorite", "--article-id", "na_1"],
            adapter=adapter,
        )
        == 0
    )
    remove_payload = json.loads(capsys.readouterr().out)
    assert remove_payload["data"]["article_id"] == "na_1"
    assert adapter.removed_request.item_key == "news:na_1"


@pytest.mark.asyncio
async def test_newsclaw_research_commands_require_runtime_and_forward_bounded_flags() -> None:
    from typing import cast

    from openbiliclaw.integrations.openclaw.cli import _build_parser, _run_command
    from openbiliclaw.research.models import (
        ResearchEvidence,
        ResearchOperation,
        ResearchResult,
    )

    class ResearchService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def search(self, **kwargs: object) -> ResearchResult:
            self.calls.append(("search", dict(kwargs)))
            return self._result("search")

        async def extract(self, **kwargs: object) -> ResearchResult:
            self.calls.append(("extract", dict(kwargs)))
            return self._result("extract")

        @staticmethod
        def _result(operation: str) -> ResearchResult:
            return ResearchResult(
                run_id="rrun_cli",
                operation=cast("ResearchOperation", operation),
                query="question",
                provider="tavily",
                provider_request_id="provider-cli",
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
            )

    parser = _build_parser()
    service = ResearchService()
    runtime = SimpleNamespace(research_service=service)
    search_args = parser.parse_args(
        [
            "research-search",
            "--query",
            "question",
            "--topic",
            "finance",
            "--time-range",
            "month",
            "--max-results",
            "6",
            "--refresh",
        ]
    )
    extract_args = parser.parse_args(
        [
            "research-extract",
            "--query",
            "question",
            "--url",
            "https://example.com/a",
            "--url",
            "https://example.com/b",
            "--chunks-per-source",
            "3",
        ]
    )

    search = await _run_command(search_args, object(), news_runtime=runtime)
    extract = await _run_command(extract_args, object(), news_runtime=runtime)
    unavailable = await _run_command(search_args, object(), news_runtime=None)

    assert search["data"]["result_count"] == 1  # type: ignore[index]
    assert extract["data"]["operation"] == "extract"  # type: ignore[index]
    assert service.calls == [
        (
            "search",
            {
                "query": "question",
                "topic": "finance",
                "time_range": "month",
                "max_results": 6,
                "refresh": True,
            },
        ),
        (
            "extract",
            {
                "urls": ["https://example.com/a", "https://example.com/b"],
                "query": "question",
                "chunks_per_source": 3,
                "refresh": False,
            },
        ),
    ]
    assert unavailable == {
        "ok": False,
        "error": "NewsClaw research is available only while the NewsClaw runtime is running.",
        "error_type": "operation_error",
    }


@pytest.mark.asyncio
async def test_newsclaw_capabilities_include_research_only_with_runtime() -> None:
    from openbiliclaw.integrations.openclaw.cli import _build_parser, _run_command

    args = _build_parser().parse_args(["capabilities"])
    without_runtime = await _run_command(args, _FakeCliAdapter())
    with_runtime = await _run_command(
        args,
        _FakeCliAdapter(),
        news_runtime=SimpleNamespace(research_service=object()),
    )

    assert "newsclaw_search_web" not in without_runtime["data"]["skill_names"]  # type: ignore[index]
    assert "newsclaw_extract_evidence" not in without_runtime["data"]["skill_names"]  # type: ignore[index]
    assert "newsclaw_search_web" in with_runtime["data"]["skill_names"]  # type: ignore[index]
    assert "newsclaw_extract_evidence" in with_runtime["data"]["skill_names"]  # type: ignore[index]
