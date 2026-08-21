"""NewsClaw desktop settings must not expose inherited video controls."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "src/openbiliclaw/web/desktop/index.html"
JS_PATH = ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"
CSS_PATH = ROOT / "src/openbiliclaw/web/desktop/assets/css/app.css"

VIDEO_ONLY_CONTROL_IDS = (
    "visualProfileEnabled",
    "keyframeEnabled",
    "keyframeMaxFrames",
    "keyframeFetchLimit",
    "danmakuEnabled",
    "danmakuFetchLimit",
    "danmakuMaxChars",
    "embeddingMultimodalEnabled",
    "candidateEvalConcurrency",
    "multimodalEvaluationEnabled",
    "multimodalBatchSize",
    "multimodalImageMaxPx",
    "multimodalImageQuality",
    "multimodalImageTimeout",
    "multimodalEvaluationStatus",
)


def test_newsclaw_removes_video_only_advanced_controls_and_copy() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    for element_id in VIDEO_ONLY_CONTROL_IDS:
        assert f'id="{element_id}"' not in html
    for misleading_label in (
        "正文证据加权",
        "同事件聚类",
        "每个事件最多聚合媒体数",
        "正文证据预热新闻数",
        "候选封面参与 LLM 评估",
        "图像 Embedding 能力",
    ):
        assert misleading_label not in html


def test_newsclaw_has_no_advanced_video_tab_or_panel() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    css = CSS_PATH.read_text(encoding="utf-8")

    assert 'id="settingsTabAdvanced"' not in html
    assert 'id="settingsPanelAdvanced"' not in html
    assert len(re.findall(r'class="settings-tab(?:\s|")', html)) == 6
    assert "grid-template-columns: repeat(6, minmax(0, 1fr))" in css
    assert "grid-template-columns: repeat(6, minmax(92px, 1fr))" in css


def test_unrelated_newsclaw_save_preserves_legacy_discovery_snapshot() -> None:
    js = JS_PATH.read_text(encoding="utf-8")

    assert "discovery: { ...(state.config?.discovery || {}) }" in js
    for forbidden_write in (
        "keyword_generation_mode:",
        "multimodal_evaluation_enabled:",
        "candidate_eval_concurrency:",
        "visual_profile_enabled:",
        "keyframe_enabled:",
        "keyframe_max_frames:",
        "keyframe_fetch_limit:",
        "danmaku_enabled:",
        "danmaku_fetch_limit:",
        "danmaku_max_chars:",
    ):
        assert forbidden_write not in js

    # The embedding object is merged over its loaded snapshot later, so
    # removing the image-only input does not reset a legacy configuration.
    assert "embedding: { ...(state.config?.llm?.embedding || {}), ...embedding }" in js
    assert "multimodal_enabled:" not in js


def test_desktop_settings_save_requires_dirty_state_and_guards_reentry() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")
    js = JS_PATH.read_text(encoding="utf-8")

    save_button = re.search(r'id="settingsSaveBtn"[^>]*>', html)
    assert save_button is not None
    assert 'type="submit"' in save_button.group(0)
    assert "disabled" in save_button.group(0)
    assert "let settingsSaveInFlight = false;" in js
    assert "if (settingsSaveInFlight || settingsDirtyFields.size === 0)" in js
    assert "settingsSaveInFlight = true;" in js
    assert "settingsSaveInFlight = false;" in js
