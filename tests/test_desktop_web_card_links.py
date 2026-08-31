import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")
SAVED_SYNC_CORE = Path("src/openbiliclaw/web/desktop/assets/js/saved-sync-core.js")


def test_main_recommendation_detail_distinguishes_exact_and_search_links() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    # The compact queue is a selector, not a wall of miniature detail cards.
    # Safe navigation lives in the selected-item pane. Provider URLs and
    # title-search fallbacks are different controls and the latter is never
    # presented as an already verified original.
    assert '<div class="cover${recommendationCoverClass(item)}"' in app_js
    assert 'class="video-title queue-card-select"' in app_js
    assert "data-detail-open-original" in app_js
    assert "data-detail-search-source" in app_js
    assert 'rel="noopener noreferrer"' in app_js
    assert 'statusLabel: "来源未提供直链"' in app_js
    assert 'label: "检索来源"' in app_js
    assert 'label: "暂无原文"' in app_js
    assert "搜索结果不是新闻爪认定的原文" in app_js


def test_detail_original_link_tracks_click_and_auxclick_without_window_open() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    open_match = re.search(
        r"function openRecommendation\(item, card\) \{(?P<body>.*?)\n    \}",
        app_js,
        flags=re.S,
    )
    assert open_match is not None, "openRecommendation function not found"
    open_body = open_match.group("body")

    # Recommendation card opening is anchor-native: openRecommendation only
    # tracks, never window.open()s. (The delight 去看看 button IS a plain button
    # and legitimately window.open()s — see the delight test below — so this
    # guard is scoped to openRecommendation, not the whole file.)
    assert "window.open" not in open_body
    assert "preventDefault" not in open_body
    assert "trackRecommendationClick(item);" in open_body
    assert 'originalLink?.addEventListener("click", recordOriginalOpen);' in app_js
    assert re.search(
        r'originalLink\?\.addEventListener\("auxclick", \(event\) => \{\s*'
        r"if \(event\.button === 1\) recordOriginalOpen\(\);",
        app_js,
    )
    assert 'sourceSearchLink?.addEventListener("click", recordSourceSearch);' in app_js
    search_start = app_js.index("const recordSourceSearch = () => {")
    search_end = app_js.index(
        'sourceSearchLink?.addEventListener("click", recordSourceSearch);',
        search_start,
    )
    search_handler = app_js[search_start:search_end]
    assert "本次检索不记为阅读或原文点击" in search_handler
    assert "trackRecommendationClick" not in search_handler


def test_recommendation_title_and_body_remove_provider_prefix_duplication() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert "BRACKETED_NEWS_TITLE_RE" in app_js
    assert "function recommendationBodyForDisplay(item)" in app_js
    assert "compactBody.startsWith(candidate)" in app_js
    assert "查看正文（已去除重复标题）" in app_js
    assert "来源正文与标题完全相同，没有更多内容可展开" in app_js


def test_saved_item_normalization_accepts_newsclaw_original_url_alias() -> None:
    core = SAVED_SYNC_CORE.read_text(encoding="utf-8")

    assert "item.original_url || item.content_url || item.canonical_url || item.url" in core


def test_saved_cards_expose_exact_search_and_missing_without_fake_reads() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    start = app_js.index("function renderSavedList")
    end = app_js.index("async function refreshTrackedObservations", start)
    render = app_js[start:end]

    assert 'data-saved-source="exact"' in render
    assert 'data-saved-source="search"' in render
    assert 'data-saved-source="missing"' in render
    assert 'disabled aria-disabled="true" aria-label="暂无原文' in render
    assert "recommendationSourceLink(item)" in render
    assert "contentUrl(item)" not in render
    assert 'if (sourceLink.state === "exact") {' in render
    assert "trackRecommendationClick(item)" in render
    assert "搜索结果不会被当作原文或已读记录" in render


def test_saved_message_and_delight_content_opens_use_anchor_semantics() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")

    assert '<a class="cover${savedCoverClass}"' in app_js
    saved_anchor = (
        '<a class="cover${savedCoverClass}" data-platform='
        '"${escapeHtml(item.source_platform || item.platform || "news")}"'
    )
    assert saved_anchor in app_js
    assert 'data-notification-msg="view" data-source-link-state="exact"' in app_js
    assert 'data-notification-msg="search" data-source-link-state="search"' in app_js
    assert 'data-notification-msg="missing" data-source-link-state="missing"' in app_js
    assert "function ensureDelightThumbAnchor()" in app_js
    assert 'document.createElement("a")' in app_js
    assert "function syncDelightThumbLink(delight)" in app_js
    thumb_start = app_js.index("function syncDelightThumbLink")
    thumb_end = app_js.index("function applyTurnToDelight", thumb_start)
    thumb_source = app_js[thumb_start:thumb_end]
    assert 'sourceLink.state === "exact" && sourceLink.url' in thumb_source
    assert "contentUrl(" not in thumb_source
    assert 'window.open(msg.content_url, "_blank", "noopener,noreferrer")' not in app_js


def test_pending_notification_ack_is_strict_and_updates_the_open_snapshot() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    start = app_js.index("async function respondNotification")
    end = app_js.index("async function refreshWebMonitorNotifications", start)
    responder = app_js[start:end]

    assert 'rememberPendingRequestId("notification-ack", articleId)' in responder
    assert "await requestJsonStrict(ENDPOINTS.notificationAck" in responder
    assert "article_id: articleId, request_id: pending.requestId" in responder
    assert "notificationSent" not in responder
    assert "state.messageListSnapshot = state.messageListSnapshot.filter" in responder
    assert "catch (error)" in responder
    assert "标记通知失败" in responder


def test_delight_view_button_actually_opens_the_content() -> None:
    """The 去看看 button on the delight card is a plain <button> (only the cover
    thumbnail is an <a>), so its click must open the URL from JS. It previously
    only tracked + toasted 「已打开」 without opening anything (field report
    2026-07-07). The cover anchor keeps opening natively, so respondDelight
    only window.open()s when the caller asks (openUrl), avoiding a double-open.
    """
    app_js = APP_JS.read_text(encoding="utf-8")

    # respondDelight takes an openUrl flag and opens in the view branch.
    assert "async function respondDelight(delight, response, el = null, openUrl = false)" in app_js
    respond_start = app_js.index("async function respondDelight")
    respond_end = app_js.index("function openMessageChat", respond_start)
    respond_source = app_js[respond_start:respond_end]
    view_match = re.search(
        r'if \(response === "view"\) \{(?P<body>.*?)\n        return;\n      \}',
        respond_source,
        flags=re.S,
    )
    assert view_match is not None, "delight view branch not found"
    view_body = view_match.group("body")
    assert 'if (sourceLink.state === "search") {' in view_body
    assert 'if (sourceLink.state !== "exact" || !sourceLink.url) {' in view_body
    assert 'window.open(url, "_blank", "noopener,noreferrer")' in view_body
    assert "if (opened) opened.opener = null;" in view_body

    # The 去看看 button ([data-delight] handler) passes openUrl=true for view.
    assert 'await respondDelight(state.delight, response, null, response === "view");' in app_js

    # The cover thumbnail triggers only exact links and do not pass openUrl
    # (they navigate via the native <a href>), so there's no double-open.
    assert 'void respondDelight(state.delight, "view");' in app_js
    assert 'if (delightFeedbackInFlight || sourceLink.state !== "exact") {' in app_js
    assert "event.preventDefault();\n        return;" in app_js


def test_delight_close_button_is_labeled_as_permanent_seen_action() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    app_css = APP_CSS.read_text(encoding="utf-8")
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert (
        'class="delight-dismiss-btn" data-delight="dismiss" type="button" '
        'aria-label="看过了，不再推荐" title="看过了，不再推荐"'
    ) in index_html
    delight_meta = index_html.index('<div class="delight-meta">')
    delight_copy = index_html.index('<div class="delight-copy">', delight_meta)
    delight_dismiss = index_html.index('class="delight-dismiss-btn"', delight_meta)
    feedback_actions = index_html.index('<div class="delight-feedback-actions card-feedback-icons"')
    status_line = index_html.index('<p class="status-line"', feedback_actions)
    assert delight_meta < delight_dismiss < delight_copy
    assert 'data-delight="dismiss"' not in index_html[feedback_actions:status_line]
    assert ".delight-dismiss-btn {" in app_css
    assert ".delight-dismiss-btn:focus-visible" in app_css
    assert ".delight-dismiss-btn:disabled" in app_css
    assert "flex-basis: 44px; width: 44px; height: 44px; min-height: 44px;" in app_css
    assert "已标为看过，不再推荐" in app_js
    assert "if (feedbackResult == null)" in app_js
    assert "这次还没记上，请再试一次" in app_js
    assert app_js.index("if (feedbackResult == null)") < app_js.index(
        'if (response === "dislike" || response === "dismiss")'
    )


def test_cover_css_resets_anchor_defaults() -> None:
    app_css = APP_CSS.read_text(encoding="utf-8")

    cover_match = re.search(r"\.cover \{(?P<body>.*?)\}", app_css, flags=re.S)
    assert cover_match is not None, ".cover rule not found"
    cover_body = cover_match.group("body")

    assert "display: block;" in cover_body
    assert "text-decoration: none;" in cover_body
    assert "color: inherit;" in cover_body


def test_saved_pages_render_manual_sync_without_platform_routing() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    app_css = APP_CSS.read_text(encoding="utf-8")
    core = SAVED_SYNC_CORE.read_text(encoding="utf-8")

    assert "function runDesktopSavedSync" in app_js
    assert "function summarizeDesktopSavedTask" in app_js
    assert "请连接已安装 NewsClaw 插件的登录态浏览器后重试。" in core
    assert "removeDesktopSavedItem(listKind, item.item_key)" in app_js
    assert "switch (item.source_platform" not in app_js
    assert ".saved-sync-chip" in app_css
    assert "min-height: 44px" in app_css


def test_unsupported_saved_items_are_truthful_local_only_and_not_sync_eligible() -> None:
    app_js = APP_JS.read_text(encoding="utf-8")
    core = SAVED_SYNC_CORE.read_text(encoding="utf-8")

    assert 'unsupported: ["仅本地保存", "neutral", false]' in app_js
    assert 'errorCode === "unsupported_content_type"' in core
    assert 'errorCode === "unsupported_adapter_missing"' in core
    assert (
        "window.NewsClawSavedSync.isSavedSyncEligibleStatus(\n        item.sync_status,"
    ) in app_js
