import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css").read_text(encoding="utf-8")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    match = re.search(
        rf"(?:async )?function {re.escape(name)}\([^)]*\) \{{(?P<body>.*?)\n    \}}",
        APP_JS,
        flags=re.S,
    )
    assert match is not None, f"desktop {name} function not found"
    return match.group("body")


def test_desktop_normalizes_news_tag_contract() -> None:
    normalize = _function_body("normalizeRecommendation")
    for field in (
        "topic_group",
        "franchise_key",
        "style_key",
        "content_scope",
        "freshness",
        "temporal_class",
        "temporal_state",
        "display_tags",
    ):
        assert f"{field}:" in normalize

    display_tags = _function_body("normalizeDisplayTags")
    assert "DISPLAY_TAG_TYPES.has(type)" in display_tags
    assert "tags.length === 4" in display_tags
    assert "type," in display_tags
    assert "label," in display_tags
    assert "origin:" in display_tags


def test_coverless_news_uses_tags_but_real_cover_stays_an_image() -> None:
    fingerprint_rule = _function_body("recommendationUsesTagFingerprint")
    assert "recommendationHasVisualCover(item)" in fingerprint_rule
    assert "if (recommendationHasVisualCover(item)) return false;" in fingerprint_rule
    assert 'return platform === "news";' in fingerprint_rule
    assert "contentType" not in fingerprint_rule

    media = _function_body("recommendationMediaHtml")
    fingerprint_index = media.index("recommendationUsesTagFingerprint(item)")
    text_excerpt_index = media.index("recommendationIsTextCard(item)")
    cover_index = media.index("coverImg(item, { eager })")
    assert fingerprint_index < text_excerpt_index < cover_index
    assert "recommendationTagFingerprintHtml(item)" in media

    cover_class = _function_body("recommendationCoverClass")
    assert 'return " is-tag-card is-coverless";' in cover_class
    assert "is-text-card" in cover_class


def test_non_news_text_cards_do_not_enter_the_news_fingerprint_path() -> None:
    fingerprint_rule = _function_body("recommendationUsesTagFingerprint")
    assert 'return platform === "news";' in fingerprint_rule
    assert 'contentType === "article"' not in fingerprint_rule

    media = _function_body("recommendationMediaHtml")
    assert "recommendationIsTextCard(item)" in media
    assert "recommendationTextCardText(item)" in media


def test_tag_fingerprint_is_compact_typed_and_never_empty() -> None:
    tags = _function_body("recommendationDisplayTags")
    assert "if (tags.length >= 4) return tags.slice(0, 4);" in tags
    assert 'type: "topic"' in tags
    assert '? "新闻文章" : recommendationTopicLabel(item)' in tags
    assert 'type: "temporal"' in tags
    assert 'type: "evidence"' in tags
    assert "return tags.slice(0, 4);" in tags

    markup = _function_body("recommendationTagFingerprintHtml")
    assert 'class="tag-fingerprint"' in markup
    assert 'class="news-tag"' in markup
    assert 'data-tag-type="${escapeHtml(tag.type)}"' in markup
    assert "DISPLAY_TAG_TYPE_LABELS[tag.type]" in markup
    assert "item.title" not in markup
    assert "item.body_text" not in markup

    for selector in (
        ".cover.is-tag-card",
        ".tag-fingerprint-tags",
        ".news-tag",
        '.news-tag[data-tag-type="temporal"]',
        '.news-tag[data-tag-type="evidence"]',
    ):
        assert selector in APP_CSS


def test_topic_filter_is_primary_and_media_filter_is_secondary() -> None:
    assert 'id="filterRow" role="tablist" aria-label="推荐类别"' in INDEX_HTML
    assert 'id="mediaFilterSelect"' in INDEX_HTML
    assert 'aria-label="按媒体来源筛选"' in INDEX_HTML

    build = _function_body("buildFilters")
    assert "recommendationTopicCounts()" in build
    assert "recommendationMediaCounts()" not in build

    media = _function_body("renderMediaFilter")
    assert "recommendationMediaCounts()" in media
    assert "select.replaceChildren(...options);" in media
    assert "select.value = state.mediaFilter;" in media

    filtered = _function_body("filteredVideos")
    assert "topicOk" in filtered
    assert "mediaOk" in filtered
    assert "return topicOk && mediaOk && queryOk;" in filtered

    reset_start = APP_JS.index('safeBind("#resetFiltersBtn"')
    reset_end = APP_JS.index('safeBind("#mediaFilterSelect"', reset_start)
    reset = APP_JS[reset_start:reset_end]
    assert 'state.filter = "全部";' in reset
    assert "state.mediaFilter = ALL_MEDIA_FILTER;" in reset
    assert 'chip[data-filter="全部"]' in reset
    assert "focus({ preventScroll: true })" in reset


def test_saved_news_tag_board_is_not_treated_as_broken_image() -> None:
    fallback = _function_body("bindSavedCoverFallback")
    assert 'if (cover.querySelector(".tag-fingerprint")) return;' in fallback
    assert "showFallback();" in fallback
