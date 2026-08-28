import re
from pathlib import Path

APP_JS = Path("src/openbiliclaw/web/desktop/assets/js/app.js")
INDEX_HTML = Path("src/openbiliclaw/web/desktop/index.html")
APP_CSS = Path("src/openbiliclaw/web/desktop/assets/css/app.css")


def _function_body(js: str, name: str) -> str:
    match = re.search(rf"function {name}\([^)]*\) \{{(?P<body>.*?)\n    \}}", js, flags=re.S)
    assert match is not None, f"{name} function not found"
    return match.group("body")


def test_index_declares_autoload_sentinel_and_frontend_toggle() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert '<div id="loadMoreSentinel" aria-hidden="true"></div>' in html
    assert html.index('id="loadMoreSentinel"') < html.index('class="load-row"')
    assert 'id="autoLoadOnScrollSetting" type="checkbox" checked' in html
    assert "滚动到底自动加载推荐" in html


def test_autoload_setting_uses_frontend_storage_pattern() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    restore_frontend = _function_body(js, "restoreFrontendSettings")
    persist_frontend = _function_body(js, "persistFrontendSettings")

    assert 'const AUTO_LOAD_ON_SCROLL_KEY = "openbiliclaw.webui.autoLoadOnScroll";' in js
    assert 'state.autoLoadOnScroll = storageGet(AUTO_LOAD_ON_SCROLL_KEY) !== "0";' in js
    assert "renderAutoLoadOnScrollToggle();" in restore_frontend
    assert "syncAutoLoadBehavior();" in restore_frontend
    assert (
        'storageSet(AUTO_LOAD_ON_SCROLL_KEY, state.autoLoadOnScroll ? "1" : "0");'
        in persist_frontend
    )


def test_autoload_is_wired_to_the_inner_queue_scroll_container() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    sync_behavior = _function_body(js, "syncAutoLoadBehavior")

    assert "recommendationGridBottomReady = false;" in sync_behavior
    assert "if (!grid) return;" in sync_behavior
    assert 'grid?.addEventListener("scroll"' in js
    assert 'window.addEventListener("scroll", scheduleAutoLoadCheck' not in js


def test_legacy_autoload_sentinel_is_not_a_viewport_trigger() -> None:
    css = APP_CSS.read_text(encoding="utf-8")

    assert "#loadMoreSentinel" in css
    assert ".judgment-workbench #loadMoreSentinel { display: none; }" in css


def test_autoload_guards_cooldown_pool_page_grid_and_button_state() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    cooldown = re.search(r"const AUTO_LOAD_COOLDOWN_MS = (?P<value>\d+);", js)
    assert cooldown is not None, "autoload cooldown constant not found"
    assert int(cooldown.group("value")) >= 8000

    # 判定逻辑集中在 autoLoadBlockReason；shouldAutoLoadMore 只是它的布尔包装。
    guard = _function_body(js, "autoLoadBlockReason")
    assert "state.autoLoadOnScroll" in guard
    assert "appendMoreInFlight" in guard
    assert "recommendationLocalFiltersActive()" in guard
    assert 'state.workbenchView === "recommendations"' in guard
    assert "now - lastAutoLoadAt >= AUTO_LOAD_COOLDOWN_MS" in guard
    assert "state.runtimeStatus?.pool_available_count > 0" in guard
    assert '$("#homePage")' in guard
    assert "homePage.hidden" in guard
    # 骨架占位卡不算真实内容，不能触发自动加载（issue #81 skeleton cards）。
    assert 'grid.querySelector(".video-card:not(.is-skeleton)")' in guard
    assert "loadMore.hidden" in guard
    context_guard = _function_body(js, "recommendationAutoLoadContextReason")
    for reason in ("local-filter", "not-queue-view", "pool-empty", "in-flight"):
        assert f'return "{reason}";' in context_guard
    # shouldAutoLoadMore 仍是对外布尔入口，委托给 autoLoadBlockReason。
    boolean_wrapper = _function_body(js, "shouldAutoLoadMore")
    assert 'autoLoadBlockReason(now) === ""' in boolean_wrapper


def test_autoload_requires_leaving_and_reentering_the_bottom_after_each_append() -> None:
    """一次触发会消耗 bottom gate，不能停在底部连续灌入。"""
    js = APP_JS.read_text(encoding="utf-8")
    auto_load = _function_body(js, "autoLoadMoreIfNeeded")
    assert "recommendationGridBottomArmed = false;" in auto_load
    assert "recommendationGridBottomArmed = true;" in js
    gate = _function_body(js, "nextRecommendationGridScrollGate")
    assert "intent && !atBottom ? true" in gate


def test_autoload_rechecks_after_scroll_render_and_runtime_status() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    render_all = _function_body(js, "renderAll")
    pool_refill = _function_body(js, "maybeAutoLoadAfterPoolRefill")

    assert 'grid?.addEventListener("scroll"' in js
    assert 'window.addEventListener("resize", scheduleAutoLoadCheck);' in js
    assert "scheduleAutoLoadCheck();" in render_all
    assert "scheduleAutoLoadCheck();" in pool_refill


def test_autoload_geometry_reads_the_inner_grid_not_the_viewport() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    in_view = _function_body(js, "isRecommendationGridBottomReady")
    refresh = _function_body(js, "refreshRecommendationGridBottomReady")
    schedule = _function_body(js, "scheduleAutoLoadCheck")

    grid_bottom = _function_body(js, "isRecommendationGridAtBottom")
    assert "recommendationGridBottomArmed" in in_view
    assert "isRecommendationGridAtBottom(grid)" in in_view
    assert "target.scrollHeight" in grid_bottom
    assert "target.clientHeight" in grid_bottom
    assert "target.scrollTop" in grid_bottom
    assert "scrollHeight - 32" in grid_bottom
    assert "recommendationGridBottomReady = isRecommendationGridBottomReady();" in refresh
    assert "requestAnimationFrame" in schedule
    assert "autoLoadCheckFallbackTimer = setTimeout(run, 120);" in schedule
    assert "refreshRecommendationGridBottomReady()" in schedule


def test_autoload_uses_single_flight_append_and_keeps_manual_button_bound() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    append_more = _function_body(js, "appendMore")
    auto_load = _function_body(js, "autoLoadMoreIfNeeded")

    assert "if (appendMoreInFlight || reshuffleInFlight)" in append_more
    assert "appendMoreInFlight = true;" in append_more
    assert "appendMoreInFlight = false;" in append_more
    assert 'button.textContent = "正在自动加载…";' in auto_load
    assert "button.disabled = true;" in auto_load
    assert "button.disabled = false;" in auto_load
    assert 'safeBind("#loadMoreBtn", "click", appendMore);' in js
