"""NewsClaw prompt-mode contracts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from openbiliclaw.llm.news_prompts import (
    NEWSCLAW_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT,
    NEWSCLAW_RECOMMENDATION_EXPRESSION_SYSTEM_PROMPT,
)
from openbiliclaw.llm.prompts import (
    _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT,
    build_avoidance_generation_prompt,
    build_awareness_prompt,
    build_awareness_with_confusions_prompt,
    build_batch_content_evaluation_prompt,
    build_batch_expression_prompt,
    build_content_evaluation_prompt,
    build_insight_prompt,
    build_preference_analysis_prompt,
    build_recommendation_expression_prompt,
    build_socratic_dialogue_prompt,
    build_soul_profile_prompt,
    build_speculation_generation_prompt,
)

if TYPE_CHECKING:
    import pytest


_FORBIDDEN_LEGACY_SEMANTICS = (
    "OpenBiliClaw",
    "B 站",
    "B站",
    "老B友",
    "UP 主",
    "UP主",
    "bilibili",
    "bvid",
    "favorite_up_users",
    "视频",
)


def _system(messages: list[dict[str, str]]) -> str:
    assert messages and messages[0]["role"] == "system"
    return messages[0]["content"]


def _assert_news_native(system_prompt: str) -> None:
    assert "新闻" in system_prompt
    for legacy_term in _FORBIDDEN_LEGACY_SEMANTICS:
        assert legacy_term not in system_prompt


def test_newsclaw_mode_switches_every_active_news_prompt_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")

    prompts = [
        build_socratic_dialogue_prompt(
            user_message="我想理解政策怎样影响产业",
            core_memory_text="常看UP主: 历史兼容标签",
            tone_profile=None,
            history=[],
        ),
        build_preference_analysis_prompt(
            events=[{"event_type": "save", "title": "产业政策"}],
            existing_preference={},
        ),
        build_soul_profile_prompt(
            history_summary={"titles": ["产业政策"]},
            preference_summary={"interests": ["宏观经济"]},
            tone_profile=None,
        ),
        build_awareness_prompt(
            events=[{"id": 1, "event_type": "save", "title": "产业政策"}],
            preference_summary={},
            soul_profile={},
        ),
        build_awareness_with_confusions_prompt(
            events=[{"id": 1, "event_type": "open", "title": "产业政策"}],
            preference_summary={},
            soul_profile={},
        ),
        build_insight_prompt(
            awareness_notes=[{"observation": "连续保存政策报道"}],
            preference_summary={},
            soul_profile={},
        ),
        build_content_evaluation_prompt(
            profile_summary={"interests": ["宏观"]},
            content_summary={"title": "政策发布"},
            source_platform="bilibili",
        ),
        build_batch_content_evaluation_prompt(
            profile_summary={"interests": ["宏观"]},
            content_items=[{"title": "政策发布"}],
            source_platform="bilibili",
        ),
        build_recommendation_expression_prompt(
            profile_summary={"interests": ["宏观"]},
            content_summary={"title": "政策发布"},
            tone_profile=None,
            source_platform="bilibili",
        ),
        build_batch_expression_prompt(
            profile_summary={"interests": ["宏观"]},
            content_items=[{"title": "政策发布"}],
            tone_profile=None,
            source_platform="bilibili",
        ),
        build_speculation_generation_prompt(
            profile_summary="关心宏观政策",
            existing_speculations=[],
            cooldown_domains=[],
            confirmed_domains=["宏观政策"],
        ),
        build_avoidance_generation_prompt(
            profile_summary={"interests": ["宏观政策"]},
            existing_avoidances=[],
            cooldown_domains=[],
            confirmed_dislikes=[],
            confirmed_likes=["宏观政策"],
        ),
    ]

    for messages in prompts:
        _assert_news_native(_system(messages))


def test_news_evaluation_and_expression_prompts_define_research_and_safety_boundaries() -> None:
    evaluation = NEWSCLAW_BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
    expression = NEWSCLAW_RECOMMENDATION_EXPRESSION_SYSTEM_PROMPT

    for prompt in (evaluation, expression):
        assert "宏观" in prompt
        assert "投资研究辅助" in prompt
        assert "创作角度" in prompt
        assert "title_only" in prompt
        assert "summary" in prompt
        assert "full_text" in prompt
        assert "买入" in prompt
        assert "卖出" in prompt
        assert "交易指令" in prompt

    assert "为什么适合你：" in expression
    assert "可能影响链：" in expression
    assert "后续观察点：" in expression
    assert "待验证" in expression


def test_news_mode_projects_legacy_article_fields_and_enforces_summary_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    messages = build_batch_content_evaluation_prompt(
        profile_summary={},
        content_items=[
            {
                "bvid": "legacy-news-id",
                "up_name": "权威媒体",
                "title": "某项政策发布",
                "description": "这是已取得的摘要",
                "body_text": "这是本轮不应暴露的正文",
                "content_scope": "summary",
                "duration": 180,
                "view_count": 999,
            }
        ],
        source_platform="bilibili",
    )

    user_prompt = messages[1]["content"]
    assert "<source_platform>\n\nnews\n\n</source_platform>" in user_prompt
    content_json = user_prompt.split("<content_batch>\n\n", 1)[1].split("\n\n</content_batch>", 1)[
        0
    ]
    article = json.loads(content_json)[0]

    assert article["content_id"] == "legacy-news-id"
    assert article["media"] == "权威媒体"
    assert article["content_scope"] == "summary"
    assert article["summary_text"] == "这是已取得的摘要"
    assert "body_text" not in article
    assert "bvid" not in article
    assert "up_name" not in article
    assert "duration" not in article
    assert "view_count" not in article


def test_news_mode_downgrades_claimed_full_text_when_only_summary_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    messages = build_recommendation_expression_prompt(
        profile_summary={},
        content_summary={
            "content_id": "news:1",
            "title": "某项政策发布",
            "description": "只取得摘要",
            "content_scope": "full_text",
        },
        tone_profile=None,
        source_platform="news",
    )

    assert '"content_scope": "summary"' in messages[1]["content"]
    assert '"summary_text": "只取得摘要"' in messages[1]["content"]
    assert "full_text 写“【全文级信息】”" in messages[0]["content"]


def test_news_sparse_evaluator_keeps_request_local_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    messages = build_batch_content_evaluation_prompt(
        profile_summary={},
        content_items=[],
        candidate_block='{"defaults":{"source_platform":"news"},"items":[{"id":"0"}]}',
        local_result_ids=True,
    )

    system_prompt = messages[0]["content"]
    assert "请求内局部 id" in system_prompt
    assert '"id":"0"' in system_prompt
    _assert_news_native(system_prompt)


def test_news_system_prompts_remain_call_invariant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")

    first_eval = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["宏观"]},
        content_items=[{"title": "政策"}],
        source_platform="news",
    )
    second_eval = build_batch_content_evaluation_prompt(
        profile_summary={"interests": ["社会"]},
        content_items=[{"title": "民生"}],
        source_platform="bilibili",
    )
    first_copy = build_batch_expression_prompt(
        profile_summary={"interests": ["宏观"]},
        content_items=[{"title": "政策"}],
        tone_profile=None,
        source_platform="news",
    )
    second_copy = build_batch_expression_prompt(
        profile_summary={"interests": ["科技"]},
        content_items=[{"title": "芯片"}],
        tone_profile={
            "density": "dense",
            "warmth": "neutral",
            "playfulness": "low",
            "directness": "direct",
        },
        source_platform="bilibili",
    )

    assert _system(first_eval) == _system(second_eval)
    assert _system(first_copy) == _system(second_copy)


def test_non_newsclaw_mode_preserves_legacy_prompt_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEWSCLAW_MODE", raising=False)
    messages = build_batch_content_evaluation_prompt(
        profile_summary={},
        content_items=[{"bvid": "BV1legacy", "title": "旧候选"}],
        source_platform="news",
    )

    assert messages[0]["content"] == _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT
    assert '"bvid": "BV1legacy"' in messages[1]["content"]
