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
        import { buildContentUrl } from "./src/openbiliclaw/web/js/view-models.js";

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
