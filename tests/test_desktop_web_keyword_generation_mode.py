"""NewsClaw does not run the inherited search-keyword discovery pipeline."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "src/openbiliclaw/web/desktop/index.html"
JS_PATH = ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js"


def test_newsclaw_removes_keyword_generation_mode_selector() -> None:
    html = HTML_PATH.read_text(encoding="utf-8")

    assert 'id="keywordGenerationMode"' not in html
    assert "搜索词生成模式" not in html
    assert "混合最贵" not in html


def test_newsclaw_does_not_load_or_write_keyword_generation_mode() -> None:
    js = JS_PATH.read_text(encoding="utf-8")

    assert 'setSelect("keywordGenerationMode"' not in js
    assert "keyword_generation_mode:" not in js
    assert "discovery: { ...(state.config?.discovery || {}) }" in js
