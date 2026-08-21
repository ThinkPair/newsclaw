"""NewsClaw desktop version-maintenance and config-apply contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_newsclaw_desktop_uses_local_version_maintenance() -> None:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'class="settings-local-update-card"' in html
    assert "版本维护" in html
    assert "不会从其他项目自动拉取或安装更新" in html
    assert 'id="autoUpdate" type="hidden" value="off"' in html
    assert 'id="updateCheckBtn"' not in html
    assert 'id="updateApplyBtn"' not in html
    assert 'id="updateDownloadLink"' not in html
    assert 'updateCheck: "/update/check"' not in js
    assert 'updateApply: "/update/apply"' not in js
    assert "github.com/whiteguo233/OpenBiliClaw/releases" not in js


def test_desktop_web_settings_understands_background_config_apply() -> None:
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert 'configApplyStatus: "/config/apply-status"' in js
    assert 'let settingsSavePhase = "idle";' in js
    assert "let settingsPendingApplyRevision = 0;" in js
    assert "正在保存配置…" in js
    assert "配置已保存，正在后台应用…" in js
    assert 'bar.toggleAttribute("aria-busy",' in js
    assert "function applyConfigApplyStatus" in js
    assert "function refreshConfigApplyStatus" in js
    assert "void refreshConfigApplyStatus();" in js
    assert 'result?.apply_state === "queued"' in js
    assert "配置已进入后台应用队列，连续保存会自动合并为最新版本" in js
    assert 'event.type === "config_reloaded"' in js
    assert 'event.type === "config_reload_failed"' in js
    assert "后台应用配置失败，已恢复上一次生效配置" in js


def test_desktop_web_settings_persists_delight_queue_limit_to_backend_config() -> None:
    """The shared delight queue size must be saved through /api/config."""
    js = (ROOT / "src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")

    assert "delight_queue_limit: getDelightQueueLimit()" in js
    assert "config.scheduler?.delight_queue_limit" in js
