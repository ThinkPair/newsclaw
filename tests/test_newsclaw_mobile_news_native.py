"""NewsClaw mobile/shared-web contracts must remain news-native."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is required for shared web contract tests")
def test_news_urls_fail_closed_without_breaking_legacy_video_links() -> None:
    script = dedent(
        """
        import assert from "node:assert/strict";
        import {
          buildContentUrl,
          getRecommendationSourceLink,
          normalizeRecommendation,
        } from "./src/openbiliclaw/web/js/view-models.js";

        assert.equal(
          buildContentUrl({
            source_platform: "news",
            content_id: "article-local-1",
            bvid: "article-local-1",
          }),
          "",
        );
        assert.equal(
          buildContentUrl({ source_platform: "web", content_id: "web-local-1" }),
          "",
        );
        assert.equal(
          buildContentUrl({
            source_platform: "news",
            content_id: "article-2",
            canonical_url: "https://example.com/news/article-2",
          }),
          "https://example.com/news/article-2",
        );
        assert.equal(
          buildContentUrl({ bvid: "BV1LEGACY" }),
          "https://www.bilibili.com/video/BV1LEGACY",
        );
        assert.equal(
          buildContentUrl({
            source_platform: "news",
            original_url: "javascript:alert(1)",
            content_id: "unsafe-news",
          }),
          "",
        );
        assert.deepEqual(
          getRecommendationSourceLink({
            source_platform: "news",
            source_link_status: "search",
            source_lookup_url: "https://www.bing.com/search?q=example",
          }),
          {
            state: "search",
            url: "https://www.bing.com/search?q=example",
            label: "检索来源",
            statusLabel: "来源未提供直链",
          },
        );
        assert.equal(
          getRecommendationSourceLink({
            source_platform: "news",
            source_link_status: "search",
            source_lookup_url: "data:text/html,pwned",
          }).label,
          "暂无原文",
        );
        assert.deepEqual(
          normalizeRecommendation({
            id: 1,
            bvid: "news-1",
            source_platform: "news",
            observation_points: "跟踪下周数据",
          }).observation_points,
          ["跟踪下周数据"],
        );
        assert.deepEqual(
          normalizeRecommendation({
            id: 2,
            bvid: "news-2",
            source_platform: "news",
            observation_points: ["跟踪政策", "", "观察价格"],
          }).observation_points,
          ["跟踪政策", "观察价格"],
        );
        """
    )
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_mobile_profile_uses_news_native_media_and_reporter_copy() -> None:
    profile_js = (ROOT / "src/openbiliclaw/web/js/views/profile.js").read_text(encoding="utf-8")

    assert '"interest.favorite_up_users": "关注的媒体/记者"' in profile_js
    assert ">关注的媒体/记者</span>" in profile_js
    assert "关注的 UP" not in profile_js
    assert "UP主" not in profile_js
    assert "UP 主" not in profile_js


def test_mobile_recommendation_actions_render_truthful_source_states() -> None:
    recommend_js = (ROOT / "src/openbiliclaw/web/js/views/recommend.js").read_text(encoding="utf-8")
    delight_handler = recommend_js[
        recommend_js.index("async function handleDelightAction") : recommend_js.index("// ── Load More")
    ]

    assert "getRecommendationSourceLink" in recommend_js
    assert "sourceLink.label" in recommend_js
    assert 'sourceLink.state === "exact"' in recommend_js
    assert 'if (sourceLink.state === "exact" && url)' in recommend_js
    assert "openBtn.disabled = true" in recommend_js
    assert "openBtn.dataset.sourceLinkState = sourceLink.state" in recommend_js
    assert "delightSourceLink.label" in recommend_js
    assert 'delightSourceLink.state === "missing"' in recommend_js
    assert 'delightSourceLink.state === "exact"' in recommend_js
    assert 'viewSourceLink.state === "search"' in delight_handler
    assert 'viewSourceLink.state !== "exact"' in delight_handler
    assert delight_handler.index('viewSourceLink.state === "search"') < delight_handler.index(
        "getDelightActionState(action)"
    )
    assert "expressionIncludesImpact" in recommend_js
    assert "expressionIncludesObservation" in recommend_js
    assert "item.impact_chain && !expressionIncludesImpact" in recommend_js


def test_mobile_deep_link_fallback_cannot_keep_an_opener() -> None:
    launch_js = (ROOT / "src/openbiliclaw/web/js/app-launch.js").read_text(encoding="utf-8")

    assert 'window.open(webUrl, "_blank", "noopener,noreferrer")' in launch_js
    assert "if (opened) opened.opener = null" in launch_js
    assert 'link.rel = "noopener noreferrer"' in launch_js


def test_mobile_pending_notifications_use_truthful_source_actions() -> None:
    chat_js = (ROOT / "src/openbiliclaw/web/js/views/chat.js").read_text(encoding="utf-8")
    api_js = (ROOT / "src/openbiliclaw/web/js/api.js").read_text(encoding="utf-8")
    action_handler = chat_js[
        chat_js.index("async function handleNewsNotificationAction") : chat_js.index(
            "function updateBadgeCount"
        )
    ]

    assert "fetchPendingNotifications().catch(() => null)" in chat_js
    assert "if (notifData !== null)" in chat_js
    assert "const pendingNews = notifData?.item" in chat_js
    assert "getRecommendationSourceLink(n)" in chat_js
    assert 'data-news-notification="missing"' in chat_js
    assert 'data-source-link-state="missing" disabled' in chat_js
    assert 'sourceLink.state === "search"' in action_handler
    assert 'sourceLink.state !== "exact"' in action_handler
    assert "reportClick(buildRecommendationClickPayload" in action_handler
    assert action_handler.index('action === "search"') < action_handler.index("ackNotification")
    search_branch = action_handler[
        action_handler.index('if (action === "search")') : action_handler.index(
            'if (action === "missing")'
        )
    ]
    assert "openContentUrl(sourceLink.url)" in search_branch
    assert "ackNotification" not in search_branch
    assert "reportClick" not in search_branch
    assert 'requestJson("/notifications/ack"' in api_js
    assert "article_id: articleId" in api_js
    assert "request_id: requestId" in api_js
    assert 'requestJson("/notifications/sent"' not in api_js
    assert "newsNotificationRequestIds.get(articleId)" in action_handler
    assert "newsNotificationRequestIds.set(articleId, stableRequestId)" in action_handler
    assert "await ackNotification(articleId, stableRequestId)" in action_handler
