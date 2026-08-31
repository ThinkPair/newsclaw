"""Regression gates for the mobile/PWA and thin extension closure audit."""

from pathlib import Path


ROOT = Path(__file__).parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mobile_message_drawer_merges_durable_monitor_notifications() -> None:
    api = read("src/openbiliclaw/web/js/api.js")
    chat = read("src/openbiliclaw/web/js/views/chat.js")

    assert "/news/monitor-notifications?limit=" in api
    assert "request_id: requestId" in api
    assert "fetchMonitorNotifications().catch(() => null)" in chat
    assert "monitorNotifications" in chat
    assert '"delivered"' in chat and '"read"' in chat and '"dismiss"' in chat
    assert "monitorNotificationRequestIds.get(key)" in chat
    assert "操作失败，消息已保留，请重试。" in chat
    assert "/web/#library/monitoring" in chat


def test_mobile_notification_ack_uses_article_identity_and_stable_retry_key() -> None:
    api = read("src/openbiliclaw/web/js/api.js")
    chat = read("src/openbiliclaw/web/js/views/chat.js")

    assert 'requestJson("/notifications/ack"' in api
    assert 'requestJson("/notifications/sent"' not in api
    assert "article_id: articleId" in api
    assert "newsNotificationRequestIds.get(articleId)" in chat
    assert "newsNotificationRequestIds.set(articleId, stableRequestId)" in chat
    assert "await ackNotification(articleId, stableRequestId)" in chat


def test_mobile_failed_mutations_remain_visible_and_recoverable() -> None:
    recommend = read("src/openbiliclaw/web/js/views/recommend.js")
    profile = read("src/openbiliclaw/web/js/views/profile.js")

    assert "换一批失败，当前内容已保留" in recommend
    assert "reconcileRecommendationReplacement" in recommend
    assert "保存失败，改动没有确认生效" in profile
    assert "data-edit-undo" in profile
    assert "撤销删除" in profile
    assert "8000" in profile
    assert "specifics.map(editSpecificName)" in profile
    assert "for (const payload of pending.payloads)" in profile


def test_mobile_dialog_focus_density_and_source_status_contract() -> None:
    app = read("src/openbiliclaw/web/js/app.js")
    chat = read("src/openbiliclaw/web/js/views/chat.js")
    recommend = read("src/openbiliclaw/web/js/views/recommend.js")
    css = read("src/openbiliclaw/web/css/app.css")

    assert "toggleMessages(bell)" in app
    assert 'aria-label", "查看新闻源状态"' in app
    assert "createDialogFocusController" in chat
    assert "createDialogFocusController" in recommend
    assert '<details class="card-explanation">' in recommend
    assert "<summary>查看解读</summary>" in recommend
    assert ".card-explanation" in css
    assert "background: var(--bg-base)" in css


def test_extension_keeps_monitor_entry_in_degraded_state_and_traps_focus() -> None:
    html = read("extension/popup/popup.html")
    popup = read("extension/popup/popup.js")

    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="connection-settings-title"' in html
    assert 'monitorButton.classList.add("degraded")' in popup
    assert "网页监控状态暂不可用" in popup
    assert 'event.key === "Escape"' in popup
    assert 'event.key !== "Tab"' in popup
    assert "settingsOpener?.focus?.()" in popup
