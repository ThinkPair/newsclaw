"""NewsClaw must not borrow OpenBiliClaw project stats or release channels."""

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from openbiliclaw.api.app import create_app
from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder


class _ExplodingProjectStats:
    async def get_snapshot(self) -> dict[str, object]:
        raise AssertionError("NewsClaw mode must not query upstream project stats")


class _ExplodingUpdater:
    async def check_now(self) -> dict[str, object]:
        raise AssertionError("NewsClaw mode must not query an upstream release channel")

    async def request_apply(self, *, tag: str = "") -> tuple[int, dict[str, object]]:
        raise AssertionError(f"NewsClaw mode must not apply an upstream release: {tag}")


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    app = create_app(
        memory_manager=object(),
        database=object(),
        soul_engine=object(),
        project_stats_service=_ExplodingProjectStats(),
        auto_update_service=_ExplodingUpdater(),
    )
    return TestClient(app)


def test_newsclaw_project_stats_do_not_expose_upstream_stars(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/project-stats")

    assert response.status_code == 200
    assert response.json() == {"stale": False, "source": "unavailable"}


def test_newsclaw_update_check_is_local_managed(monkeypatch) -> None:
    response = _client(monkeypatch).post("/api/update/check", json={})

    assert response.status_code == 200
    assert response.json()["backend"] == {
        "state": "managed_local",
        "auto_update_enabled": False,
        "install_mode": "newsclaw-local",
        "current_version": "",
        "latest_version": "",
        "latest_tag": "",
        "last_check_at": "",
        "last_error": "",
        "reason": "independent_local_product",
    }


def test_newsclaw_update_apply_is_rejected(monkeypatch) -> None:
    response = _client(monkeypatch).post(
        "/api/update/apply",
        json={"target": "backend", "tag": "backend-v999"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "target": "backend",
        "state": "managed_local",
        "reason": "independent_local_product",
        "accepted": False,
        "observe_via": "local_plugin_workshop",
    }


def test_setup_stores_tushare_token_through_newsclaw_credential_api() -> None:
    setup_html = Path("src/openbiliclaw/web/setup/index.html").read_text(encoding="utf-8")

    assert 'request("/news/credentials/tushare"' in setup_html
    assert "payload.sources = { tushare: { token } }" not in setup_html
    assert "稍后读、收藏、搜索和对话会继续修正画像" not in setup_html


class _ScopedSoul:
    async def get_profile(self) -> dict[str, object]:
        return {"profile": "news-only"}


class _ScopedRuntime:
    event_hub = None

    def get_runtime_status(self) -> dict[str, object]:
        return {"initialized": True, "pool_available_count": 5, "pool_pending_count": 0}

    async def request_replenishment(
        self,
        *,
        reason: str,
        force: bool = False,
    ) -> dict[str, object]:
        return {"accepted": True, "state": "running", "reason": reason, "force": force}


class _ScopedEngine:
    def __init__(self) -> None:
        self.reshuffle_calls: list[dict[str, object]] = []
        self.append_calls: list[dict[str, object]] = []

    @staticmethod
    def _result() -> SimpleNamespace:
        return SimpleNamespace(
            items=[],
            pool_counts_after={"available": 5, "raw": 5, "pending": 0},
            timings=None,
        )

    async def reshuffle_recommendations_with_result(self, **kwargs: object) -> SimpleNamespace:
        self.reshuffle_calls.append(dict(kwargs))
        return self._result()

    async def append_recommendations_with_result(self, **kwargs: object) -> SimpleNamespace:
        self.append_calls.append(dict(kwargs))
        return self._result()


def test_newsclaw_reshuffle_and_append_force_news_scope(monkeypatch) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    engine = _ScopedEngine()
    app = create_app(
        memory_manager=object(),
        database=object(),
        soul_engine=_ScopedSoul(),
        recommendation_engine=engine,
        runtime_controller=_ScopedRuntime(),
    )
    client = TestClient(app)

    reshuffle = client.post(
        "/api/recommendations/reshuffle",
        json={"source_platform": "weibo", "excluded_article_ids": ["news-old"]},
    )
    append = client.post(
        "/api/recommendations/append",
        json={"source_platform": "bilibili", "excluded_article_ids": ["news-old"]},
    )

    assert reshuffle.status_code == 200
    assert append.status_code == 200
    assert engine.reshuffle_calls[0]["source_platform"] == "news"
    assert engine.append_calls[0]["source_platform"] == "news"
    assert engine.reshuffle_calls[0]["excluded_bvids"] == ["news-old"]
    assert engine.append_calls[0]["excluded_bvids"] == ["news-old"]


class _RecommendationDatabase:
    def get_recommendations(
        self,
        *,
        limit: int,
        exclude_processed: bool,
    ) -> list[dict[str, object]]:
        assert limit == 1000
        assert exclude_processed is True
        legacy = {
            "id": 900,
            "bvid": "BV1LEGACY",
            "title": "legacy video",
            "source_platform": "bilibili",
        }
        news = [
            {
                "id": index,
                "bvid": f"news-{index}",
                "content_id": f"news-{index}",
                "title": f"news article {index}",
                "source_platform": "news",
                "content_type": "article",
            }
            for index in range(1, 11)
        ]
        return [legacy, *news]


def test_newsclaw_initial_recommendations_drop_legacy_rows(monkeypatch) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    app = create_app(
        memory_manager=object(),
        database=_RecommendationDatabase(),
        soul_engine=object(),
    )

    response = TestClient(app).get("/api/recommendations")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 10
    assert {item["source_platform"] for item in response.json()["items"]} == {"news"}
    assert "BV1LEGACY" not in {item["bvid"] for item in response.json()["items"]}


def test_newsclaw_qr_info_reports_loopback_only_by_default(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/qr-info")

    assert response.status_code == 200
    assert response.json()["lan_accessible"] is False


class _BrandBoundaryDatabase:
    def get_recommendations(self, limit: int = 20) -> list[dict[str, object]]:
        return []


class _BrandBoundaryMemory:
    def load_cognition_updates(self) -> list[dict[str, object]]:
        return [
            {
                "id": "legacy-cognition-1",
                "kind": "interest_added",
                "summary": "阿B 现在更确定你关注宏观政策。",
                "created_at": "2026-08-17T12:00:00+08:00",
                "notified": False,
            }
        ]


class _BrandBoundaryRuntime:
    event_hub = None

    def get_runtime_status(self) -> dict[str, object]:
        return {
            "initialized": True,
            "pending_signal_events": 5,
            "pool_available_count": 300,
        }


def test_newsclaw_activity_and_cognition_copy_never_expose_legacy_assistant_name(
    monkeypatch,
) -> None:
    monkeypatch.setenv("NEWSCLAW_MODE", "1")
    app = create_app(
        memory_manager=_BrandBoundaryMemory(),
        database=_BrandBoundaryDatabase(),
        soul_engine=object(),
        runtime_controller=_BrandBoundaryRuntime(),
    )
    client = TestClient(app)

    activity = client.get("/api/activity-feed")
    pending = client.get("/api/cognition-updates/pending")

    assert activity.status_code == 200
    assert pending.status_code == 200
    assert "阿B" not in activity.text
    assert "阿B" not in pending.text
    assert activity.json()["live_summary"] == ("新闻爪已记下 5 个新动作，下一轮新闻推荐会参考。")
    assert activity.json()["headline"] == "新闻爪现在更确定你关注宏观政策。"
    assert pending.json()["item"]["summary"] == "新闻爪现在更确定你关注宏观政策。"


class _MixedActivityDatabase:
    def get_recommendations(self, limit: int = 20) -> list[dict[str, object]]:
        rows = [
            {
                "id": index,
                "item_key": f"bilibili:BV{index}",
                "source_platform": "bilibili",
                "title": f"legacy-{index}",
                "created_at": f"2026-08-17T12:{index:02d}:00+08:00",
            }
            for index in range(12)
        ]
        rows.append(
            {
                "id": 99,
                "item_key": "news:article-99",
                "source_platform": "news",
                "title": "news-99",
                "created_at": "2026-08-17T13:00:00+08:00",
            }
        )
        return rows[:limit]


def test_newsclaw_activity_feed_overscans_and_filters_legacy_recommendations() -> None:
    payload = ActivityFeedBuilder(
        database=_MixedActivityDatabase(),
        newsclaw_mode=True,
    ).build(
        runtime_status={"initialized": True, "pool_available_count": 1},
        cognition_updates=[],
    )

    recommendations = [item for item in payload["items"] if item.get("kind") == "recommendation"]
    assert [item["summary"] for item in recommendations] == ["这批先给你翻出来了：news-99"]
    assert "legacy" not in str(payload)


def test_newsclaw_new_cognition_uses_newsclaw_identity(monkeypatch) -> None:
    from openbiliclaw.soul.engine import _assistant_display_name

    monkeypatch.setenv("NEWSCLAW_MODE", "1")

    assert _assistant_display_name() == "新闻爪"


def test_newsclaw_hides_legacy_video_speculations_without_deleting_them() -> None:
    from openbiliclaw.api.app import _newsclaw_speculation_is_compatible

    old_video_probe = SimpleNamespace(
        domain="乐队现场",
        reason="乐队现场视频会带来真实的情绪冲击",
        source_signal="",
        specifics=[SimpleNamespace(name="livehouse 现场实录")],
    )
    news_probe = SimpleNamespace(
        domain="A股盘面复盘与板块轮动",
        reason="跟踪每天资金和板块的变化",
        source_signal="财经新闻反馈",
        specifics=[SimpleNamespace(name="两市成交额变化")],
    )
    documentary_probe = SimpleNamespace(
        domain="制造业隐形冠军与企业家访谈",
        reason="用纪录片把工厂现场直接拍给你看",
        source_signal="",
        specifics=[SimpleNamespace(name="企业家访谈剪辑")],
    )

    assert _newsclaw_speculation_is_compatible(old_video_probe) is False
    assert _newsclaw_speculation_is_compatible(documentary_probe) is False
    assert _newsclaw_speculation_is_compatible(news_probe) is True


def test_newsclaw_library_and_profile_copy_are_news_native() -> None:
    desktop_html = Path("src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    desktop_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text(encoding="utf-8")
    mobile_library = Path("src/openbiliclaw/web/js/views/library.js").read_text(encoding="utf-8")

    assert "都只保存在这台电脑上" in desktop_html
    assert "都只保存在这台电脑上" in mobile_library
    assert '["资讯使用场景", context.session_type]' in desktop_js
    assert '["观看会话", context.session_type]' not in desktop_js
    assert "data/openbiliclaw.db" not in desktop_html
    assert "logs/openbiliclaw.log" not in desktop_html
    assert 'filename || "newsclaw.log"' in desktop_js
    assert "不会调用旧的视频初始化流程" not in desktop_html
