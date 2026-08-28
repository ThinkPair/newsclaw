"""Tests for the additive NewsClaw recommendation-tag API contract."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.api.recommendation_tags import (
    build_recommendation_presentation_fields,
    build_recommendation_tag_fields,
    parse_news_expression,
)
from openbiliclaw.discovery.candidate_pool import DiscoveryCandidateWrite
from openbiliclaw.discovery.engine import DiscoveredContent
from openbiliclaw.recommendation.engine import Recommendation
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def test_decision_priority_is_not_a_confidence_alias_and_explains_signals() -> None:
    fields = build_recommendation_presentation_fields(
        {
            "source_platform": "news",
            "tags": ["full_text", "fresh"],
            "temporal_class": "current",
            "temporal_state": "active",
            "impact_score": 0.8,
        },
        expression=(
            "为什么适合你：与你的方向相关。"
            "可能影响链：事件可能改变行业预期。"
            "后续观察点：核验下一份公告。"
        ),
        confidence=0.76,
    )

    assert fields["priority_score"] != pytest.approx(0.76)
    assert fields["priority_score"] == pytest.approx(0.8515)
    assert fields["priority_level"] == "normal"
    assert fields["priority_version"] == "decision-priority-v2"
    assert fields["priority_signals"] == {
        "match": 0.76,
        "evidence": 1.0,
        "timeliness": pytest.approx(0.9475),
        "impact": 0.8,
    }
    assert "全文级" in str(fields["priority_reason"])


@pytest.mark.parametrize(
    ("confidence", "temporal_class", "impact", "expected_level", "expected_label"),
    [
        (0.95, "breaking", 0.90, "high", "优先关注"),
        (0.90, "current", 0.60, "medium", "值得关注"),
        (0.85, "current", 0.60, "normal", "常规参考"),
    ],
)
def test_decision_priority_v2_keeps_visible_full_text_news_distinguishable(
    confidence: float,
    temporal_class: str,
    impact: float,
    expected_level: str,
    expected_label: str,
) -> None:
    fields = build_recommendation_presentation_fields(
        {
            "source_platform": "news",
            "tags": ["full_text", "fresh"],
            "temporal_class": temporal_class,
            "temporal_state": "active",
            "impact_score": impact,
        },
        confidence=confidence,
    )

    assert fields["priority_level"] == expected_level
    assert fields["priority_label"] == expected_label
    assert fields["priority_version"] == "decision-priority-v2"


def test_current_full_text_event_outranks_high_match_stale_title() -> None:
    stale_title = build_recommendation_presentation_fields(
        {
            "source_platform": "news",
            "tags": ["title_only", "stale"],
            "temporal_class": "historical",
        },
        confidence=0.95,
    )
    current_full = build_recommendation_presentation_fields(
        {
            "source_platform": "news",
            "tags": ["full_text", "fresh"],
            "temporal_class": "current",
            "temporal_state": "active",
            "importance_score": 0.7,
        },
        confidence=0.78,
    )

    assert current_full["priority_score"] > stale_title["priority_score"]
    assert current_full["priority_level"] == "normal"
    assert stale_title["priority_level"] in {"normal", "low"}


@pytest.mark.parametrize("confidence", [None, True, "not-a-score", float("nan")])
def test_priority_projection_omits_invalid_or_missing_scores(confidence: object) -> None:
    fields = build_recommendation_presentation_fields(
        {"source_platform": "news"},
        confidence=confidence,
    )

    assert "priority_level" not in fields
    assert "priority_label" not in fields
    assert "priority_score" not in fields


def test_news_expression_is_parsed_once_at_the_api_boundary() -> None:
    expression = (
        "【摘要级信息】为什么适合你：连接你的宏观政策关注。\n"
        "可能影响链: 政策信号 → 行业预期，但仍需验证。\n"
        "后续观察点：继续核验正式文件与行业数据。"
    )

    assert parse_news_expression(expression) == {
        "why_recommended": "连接你的宏观政策关注。",
        "impact_chain": "政策信号 → 行业预期，但仍需验证。",
        "observation_points": "继续核验正式文件与行业数据。",
    }
    assert parse_news_expression("为什么适合你：只有一段旧文案") == {}


def test_explicit_non_news_content_gets_no_news_analysis_or_priority_projection() -> None:
    fields = build_recommendation_presentation_fields(
        {
            "source_platform": "reddit",
            "source_strategy": "news-test",
            "article_id": "legacy-news-id",
            "topic_group": "不应输出",
        },
        expression=("为什么适合你：不应输出。可能影响链：不应输出。后续观察点：不应输出。"),
        confidence=0.99,
    )

    assert fields == {}


def test_tag_projection_uses_existing_news_fields_in_display_priority_order() -> None:
    content = SimpleNamespace(
        source_platform="news",
        topic_group="宏观政策",
        franchise_key="数字人民币",
        style_key="deep_focus",
        tags=["央行", "summary", "fresh"],
        temporal_class="current",
        temporal_state="active",
    )

    fields = build_recommendation_tag_fields(content)

    assert fields == {
        "topic_group": "宏观政策",
        "franchise_key": "数字人民币",
        "style_key": "deep_focus",
        "content_scope": "summary",
        "freshness": "fresh",
        "temporal_class": "current",
        "temporal_state": "active",
        "display_tags": [
            {"type": "topic", "label": "宏观政策", "origin": "model"},
            {"type": "story", "label": "数字人民币", "origin": "model"},
            {"type": "temporal", "label": "近期进展", "origin": "model"},
            {"type": "evidence", "label": "摘要级", "origin": "source"},
        ],
    }


def test_tag_projection_recovers_canonical_fields_from_raw_payload() -> None:
    content = {
        "freshness": "unknown",
        "temporal_class": "unknown",
        "temporal_state": "unknown",
        "raw_payload": json.dumps(
            {
                "article_id": "news_raw",
                "source_platform": "news",
                "topic_group": "公司动态",
                "franchise_key": "某上市公司",
                "style_key": "quick_update",
                "content_scope": "full_text",
                "freshness": "aging",
                "temporal_class": "versioned",
                "temporal_state": "active",
            },
            ensure_ascii=False,
        ),
        "tags": "not-json",
    }

    fields = build_recommendation_tag_fields(content)

    assert fields["content_scope"] == "full_text"
    assert fields["freshness"] == "aging"
    assert fields["temporal_class"] == "versioned"
    assert fields["display_tags"] == [
        {"type": "topic", "label": "公司动态", "origin": "model"},
        {"type": "story", "label": "某上市公司", "origin": "model"},
        {"type": "temporal", "label": "版本相关", "origin": "model"},
        {"type": "evidence", "label": "全文级", "origin": "source"},
    ]


def test_tag_projection_deduplicates_labels_and_prioritizes_terminal_temporal_state() -> None:
    fields = build_recommendation_tag_fields(
        {
            "source_platform": "news",
            "topic_group": "数字人民币",
            "franchise_key": " 数字人民币 ",
            "tags": '["title_only", "fresh"]',
            "temporal_class": "breaking",
            "temporal_state": "superseded",
        }
    )

    assert fields["display_tags"] == [
        {"type": "topic", "label": "数字人民币", "origin": "model"},
        {"type": "temporal", "label": "已替代", "origin": "model"},
        {"type": "evidence", "label": "标题级", "origin": "source"},
    ]


def test_tag_projection_keeps_non_news_payload_unchanged() -> None:
    assert (
        build_recommendation_tag_fields(
            {
                "source_platform": "bilibili",
                "topic_group": "宏观政策",
                "tags": ["summary", "fresh"],
            }
        )
        == {}
    )


def test_explicit_non_news_platform_wins_over_legacy_news_signals() -> None:
    conflicting_rows = (
        {
            "source_platform": "reddit",
            "article_id": "legacy-article-id",
            "topic_group": "不应输出",
        },
        {
            "source_platform": "zhihu",
            "source_strategy": "news-test",
            "topic_group": "不应输出",
        },
        {
            "source_platform": "weibo",
            "raw_payload": {
                "source_platform": "news",
                "article_id": "legacy-payload-id",
                "topic_group": "不应输出",
            },
        },
    )

    for row in conflicting_rows:
        assert build_recommendation_tag_fields(row) == {}


def test_history_storage_retains_style_for_tag_serialization(tmp_path: Path) -> None:
    db = Database(tmp_path / "recommendation-tags.db")
    db.initialize()
    db.enqueue_discovery_candidates(
        [
            DiscoveryCandidateWrite(
                candidate_key="news:news_history",
                source_platform="news",
                source_strategy="news-test",
                content_type="article",
                bvid="news_history",
                content_id="news_history",
                title="数字人民币运营机构扩容",
                author_name="测试媒体",
                up_name="测试媒体",
                tags=["测试媒体", "summary", "fresh"],
                raw_payload={
                    "article_id": "news_history",
                    "content_scope": "summary",
                    "freshness": "fresh",
                },
            )
        ]
    )
    db.cache_content(
        "news_history",
        content_id="news_history",
        source_platform="news",
        source="news-test",
        content_type="article",
        title="数字人民币运营机构扩容",
        up_name="测试媒体",
        tags=["测试媒体", "summary", "fresh"],
        topic_group="宏观政策",
        style_key="deep_focus",
        franchise_key="数字人民币",
        relevance_score=0.9,
    )
    db.insert_recommendation(
        "news_history",
        confidence=0.9,
        expression="值得留意。",
        topic="宏观政策",
        presented=0,
    )

    row = db.get_recommendations(limit=1)[0]

    assert row["style_key"] == "deep_focus"
    db.close()


def test_history_endpoint_serializes_structured_news_tags() -> None:
    class FakeDatabase:
        def get_recommendations(
            self, limit: int = 20, *, exclude_processed: bool = False
        ) -> list[dict[str, object]]:
            assert limit == 1000
            assert exclude_processed is True
            return [
                {
                    "id": 91,
                    "bvid": "news_history",
                    "content_id": "news_history",
                    "source_platform": "news",
                    "content_type": "article",
                    "title": "数字人民币运营机构扩容",
                    "up_name": "测试媒体",
                    "topic_group": "宏观政策",
                    "franchise_key": "数字人民币",
                    "style_key": "deep_focus",
                    "tags": '["测试媒体", "summary", "fresh"]',
                    "temporal_class": "current",
                    "temporal_state": "active",
                    "confidence": 0.9,
                    "expression": (
                        "【摘要级信息】为什么适合你：连接宏观政策关注。"
                        "可能影响链：扩容或影响支付生态，但仍需验证。"
                        "后续观察点：核验央行文件和机构公告。"
                    ),
                }
            ]

    response = TestClient(
        create_app(
            database=FakeDatabase(),
            memory_manager=object(),
            soul_engine=object(),
        )
    ).get("/api/recommendations")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["topic_group"] == "宏观政策"
    assert item["franchise_key"] == "数字人民币"
    assert item["style_key"] == "deep_focus"
    assert item["content_scope"] == "summary"
    assert item["freshness"] == "fresh"
    assert item["priority_level"] == "normal"
    assert item["priority_label"] == "常规参考"
    assert item["priority_score"] == pytest.approx(0.8245)
    assert item["priority_score"] != pytest.approx(0.9)
    assert item["why_recommended"] == "连接宏观政策关注。"
    assert item["impact_chain"] == "扩容或影响支付生态，但仍需验证。"
    assert item["observation_points"] == "核验央行文件和机构公告。"
    assert [tag["type"] for tag in item["display_tags"]] == [
        "topic",
        "story",
        "temporal",
        "evidence",
    ]


def test_reshuffle_endpoint_serializes_the_same_structured_news_tags() -> None:
    class FakeDatabase:
        def count_pool_candidates(self, **_kwargs: object) -> int:
            return 1

    class FakeSoulEngine:
        async def get_profile(self) -> dict[str, object]:
            return {"profile": "ok"}

    class FakeRecommendationEngine:
        async def reshuffle_recommendations(
            self,
            *,
            profile: object,
            excluded_bvids: list[str],
            limit: int,
        ) -> list[Recommendation]:
            assert profile == {"profile": "ok"}
            assert excluded_bvids == []
            assert limit == 10
            return [
                Recommendation(
                    content=DiscoveredContent(
                        bvid="news_live",
                        content_id="news_live",
                        source_platform="news",
                        source_strategy="news-test",
                        content_type="article",
                        title="数字人民币运营机构扩容",
                        up_name="测试媒体",
                        tags=["测试媒体", "summary", "fresh"],
                        topic_group="宏观政策",
                        franchise_key="数字人民币",
                        style_key="deep_focus",
                        temporal_class="current",
                        temporal_state="active",
                    ),
                    recommendation_id=92,
                    confidence=0.9,
                    expression=(
                        "为什么适合你：连接宏观政策关注。"
                        "可能影响链：扩容或影响支付生态，但仍需验证。"
                        "后续观察点：核验央行文件和机构公告。"
                    ),
                )
            ]

    response = TestClient(
        create_app(
            database=FakeDatabase(),
            soul_engine=FakeSoulEngine(),
            recommendation_engine=FakeRecommendationEngine(),
        )
    ).post("/api/recommendations/reshuffle")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["topic_group"] == "宏观政策"
    assert item["content_scope"] == "summary"
    assert item["freshness"] == "fresh"
    assert item["temporal_class"] == "current"
    assert item["temporal_state"] == "active"
    assert item["priority_level"] == "normal"
    assert item["why_recommended"] == "连接宏观政策关注。"
    assert item["impact_chain"] == "扩容或影响支付生态，但仍需验证。"
    assert item["observation_points"] == "核验央行文件和机构公告。"
    assert [tag["type"] for tag in item["display_tags"]] == [
        "topic",
        "story",
        "temporal",
        "evidence",
    ]


def test_pending_delight_batch_reuses_news_projection_without_touching_non_news() -> None:
    expression = (
        "【全文级信息】为什么适合你：连接宏观政策关注。"
        "可能影响链：政策变化或影响行业预期，但仍需验证。"
        "后续观察点：核验正式文件和行业数据。"
    )

    class FakeDatabase:
        def get_delight_candidates(
            self,
            *,
            min_delight_score: float,
            limit: int,
            include_liked: bool = False,
        ) -> list[dict[str, object]]:
            assert min_delight_score >= 0.75
            assert limit == 20
            assert include_liked is True
            return [
                {
                    "bvid": "news_delight",
                    "content_id": "news_delight",
                    "source_platform": "news",
                    "source": "news-test",
                    "title": "政策观察",
                    "up_name": "测试媒体",
                    "tags": ["full_text", "fresh"],
                    "raw_payload": {"article_id": "news_delight"},
                    "topic_group": "宏观政策",
                    "franchise_key": "政策跟踪",
                    "style_key": "deep_focus",
                    "temporal_class": "current",
                    "temporal_state": "active",
                    "relevance_score": 0.83,
                    "delight_score": 0.84,
                    "delight_reason": expression,
                    "delight_hook": "政策跟踪",
                },
                {
                    "bvid": "reddit_legacy",
                    "content_id": "reddit_legacy",
                    "source_platform": "reddit",
                    "source": "news-test",
                    "article_id": "legacy-news-id",
                    "topic_group": "不应输出",
                    "relevance_score": 0.99,
                    "delight_score": 0.99,
                    "delight_reason": expression,
                    "delight_hook": "legacy",
                },
            ]

    response = TestClient(
        create_app(database=FakeDatabase(), memory_manager=object(), soul_engine=object())
    ).get("/api/delight/pending-batch")

    assert response.status_code == 200
    news, non_news = response.json()["items"]
    assert news["topic_group"] == "宏观政策"
    assert news["content_scope"] == "full_text"
    assert news["priority_level"] == "normal"
    assert news["priority_score"] == pytest.approx(0.853)
    assert news["why_recommended"] == "连接宏观政策关注。"
    assert news["impact_chain"] == "政策变化或影响行业预期，但仍需验证。"
    assert news["observation_points"] == "核验正式文件和行业数据。"
    assert [tag["type"] for tag in news["display_tags"]] == [
        "topic",
        "story",
        "temporal",
        "evidence",
    ]
    for field in (
        "article_id",
        "topic_group",
        "display_tags",
        "priority_level",
        "priority_label",
        "priority_score",
        "why_recommended",
        "impact_chain",
        "observation_points",
    ):
        assert field not in non_news


def test_pending_delight_response_projects_news_fields_and_excludes_none() -> None:
    class FakeRuntimeController:
        def get_pending_delight(self) -> dict[str, object]:
            return {
                "bvid": "news_pending",
                "content_id": "news_pending",
                "source_platform": "news",
                "source_strategy": "news-test",
                "title": "产业政策更新",
                "tags": ["summary", "aging"],
                "topic_group": "产业政策",
                "temporal_class": "versioned",
                "temporal_state": "active",
                "relevance_score": 0.79,
                "delight_score": 0.80,
                "delight_reason": (
                    "为什么适合你：连接产业政策关注。"
                    "可能影响链：政策变化或影响产业预期，但仍需验证。"
                    "后续观察点：核验实施细则。"
                ),
            }

    response = TestClient(
        create_app(
            database=object(),
            memory_manager=object(),
            soul_engine=object(),
            runtime_controller=FakeRuntimeController(),
        )
    ).get("/api/delight/pending")

    assert response.status_code == 200
    item = response.json()["item"]
    assert item["priority_level"] == "low"
    assert item["priority_label"] == "探索观察"
    assert item["priority_score"] == pytest.approx(0.738)
    assert item["why_recommended"] == "连接产业政策关注。"
    assert item["impact_chain"] == "政策变化或影响产业预期，但仍需验证。"
    assert item["observation_points"] == "核验实施细则。"
    assert item["content_scope"] == "summary"
    assert item["freshness"] == "aging"
