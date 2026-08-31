"""Contracts for the current mobile SPA and thin-client browser extension.

The extension no longer owns a parallel saved-items UI. It embeds the mobile
SPA so saved-state behavior has one implementation and one backend truth.
"""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mobile_saved_surface_keeps_durable_manual_controls() -> None:
    api = _read("src/openbiliclaw/web/js/api.js")
    saved = _read("src/openbiliclaw/web/js/views/saved.js")
    runtime = _read("src/openbiliclaw/web/js/saved-sync-runtime.js")

    for helper in (
        "saveItem",
        "removeSavedItem",
        "fetchSavedItems",
        "savedItemStatus",
        "syncSavedItems",
        "pollSavedSyncTask",
    ):
        assert f"function {helper}" in api
    assert "createDurableTaskTracker" in saved
    assert "createRetainedSavedListState" in saved
    assert "同步未同步内容" in saved
    assert all(label in saved for label in ("待同步", "同步中", "已同步", "需要登录", "同步失败"))
    assert "重试同步" in saved
    assert "aria-live" in saved
    assert "createDialogFocusController" in runtime


def test_extension_is_a_thin_mobile_spa_host_not_a_second_saved_ui() -> None:
    html = _read("extension/popup/popup.html")
    js = _read("extension/popup/popup.js")

    assert 'id="clientFrame"' in html
    assert "/m/?surface=extension" in js
    assert 'sandbox="allow-scripts allow-forms allow-popups allow-same-origin allow-downloads"' in html
    assert 'id="cfgSavedAutoSync"' not in html
    assert 'id="watchLaterSyncAll"' not in html
    assert 'id="favoritesSyncAll"' not in html
    assert "popup-saved-sync" not in html
    assert "partitionSavedQueueResults" not in js


def test_extension_connection_dialog_and_monitor_degradation_are_accessible() -> None:
    html = _read("extension/popup/popup.html")
    js = _read("extension/popup/popup.js")

    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="connection-settings-title"' in html
    assert 'event.key === "Escape"' in js
    assert 'event.key !== "Tab"' in js
    assert "settingsOpener?.focus?.()" in js
    assert 'monitorButton.hidden = false' in js
    assert 'monitorButton.classList.add("degraded")' in js
    assert "监控状态暂不可用" in js


def test_mobile_dialogs_share_the_focus_controller_and_settings_are_opaque() -> None:
    app = _read("src/openbiliclaw/web/js/app.js")
    chat = _read("src/openbiliclaw/web/js/views/chat.js")
    recommend = _read("src/openbiliclaw/web/js/views/recommend.js")
    css = _read("src/openbiliclaw/web/css/app.css")

    assert "createDialogFocusController" in app
    assert "createDialogFocusController" in chat
    assert "createDialogFocusController" in recommend
    assert 'setAttribute("role", "dialog")' in chat
    assert 'setAttribute("aria-modal", "true")' in chat
    assert 'setAttribute("role", "dialog")' in recommend
    assert 'setAttribute("aria-modal", "true")' in recommend
    assert "background: var(--bg-base)" in css
    assert "background: var(--bg);" not in css
