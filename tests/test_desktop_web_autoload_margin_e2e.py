"""Desktop web 内层推荐队列见底自动加载的真实浏览器契约。

用真实 Chromium 驱动 /web/，验证只有 #videoGrid 本身产生滚动意图，
且距自身底部不超过 32px 时才追加。未滚动、外层页面滚动、距内层底部
150px，或一次追加后仍停在底部，都不得自行连续灌入。
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect
sync_playwright = playwright_api.sync_playwright

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
CARD_COUNT = 20


def _recommendations(prefix: str, count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}{index}",
            "bvid": f"BV1MARGIN{prefix}{index}",
            "content_id": f"BV1MARGIN{prefix}{index}",
            "content_url": f"https://www.bilibili.com/video/BV1MARGIN{prefix}{index}",
            "source_platform": "bilibili",
            "title": f"边距卡片 {prefix}-{index}",
            "up_name": f"UP {index}",
            "topic_label": "自动加载",
            "expression": f"第 {prefix}-{index} 张卡片的推荐理由，占位文案撑起卡片高度。",
        }
        for index in range(1, count + 1)
    ]


class MarginStub:
    def __init__(self) -> None:
        self.append_posts: list[dict[str, Any]] = []
        self.append_received = threading.Event()
        self.append_in_flight = 0
        self.max_append_in_flight = 0


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    with suppress(BrokenPipeError):
        handler.wfile.write(body)


@pytest.fixture()
def margin_server() -> tuple[str, MarginStub]:
    state = MarginStub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path.startswith("/shared/"):
                # The desktop page and the setup wizard both load the shared
                # source-status module from the backend's /shared mount. Without
                # this route it 404s, app.js dies on the missing global, and the
                # failure surfaces as an unrelated test timing out.
                rel = path.removeprefix("/shared/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/shared" / rel)
            if path in {"/web", "/web/", "/web/index.html"}:
                return self._serve_file(
                    ROOT / "src/openbiliclaw/web/desktop/index.html", "text/html"
                )
            if path.startswith("/web/assets/"):
                rel = path.removeprefix("/web/assets/")
                return self._serve_file(ROOT / "src/openbiliclaw/web/desktop/assets" / rel)
            if path == "/api/ping":
                return _json_response(self, {"ok": True})
            if path == "/api/health":
                return _json_response(self, {"ok": True, "embedding_ready": True})
            if path == "/api/auth/status":
                return _json_response(self, {"enabled": False, "authenticated": True})
            if path == "/api/recommendations":
                return _json_response(self, {"items": _recommendations("A", CARD_COUNT)})
            if path == "/api/runtime-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "pool_available_count": 30,
                        "pool_size": 30,
                        "pool_refresh_state": "idle",
                        "pool_source_shares": {"bilibili": 1.0},
                        "configured_sources": {"bilibili": {"enabled": True}},
                        "unread_count": 0,
                    },
                )
            if path == "/api/init-status":
                return _json_response(
                    self,
                    {
                        "initialized": True,
                        "running": False,
                        "can_start": False,
                        "reason": "already_initialized",
                        "stages": [],
                        "prerequisites": {
                            "bilibili_logged_in": True,
                            "llm_ready": True,
                            "embedding_ready": True,
                            "enabled_platforms": ["bilibili"],
                        },
                    },
                )
            if path == "/api/config":
                return _json_response(
                    self,
                    {
                        "config": {
                            "sources": {"bilibili": {"enabled": True}},
                            "scheduler": {},
                            "llm": {"default_provider": "ollama", "ollama": {}},
                        }
                    },
                )
            if path == "/api/profile-summary":
                return _json_response(self, {"initialized": True})
            if path == "/api/activity-feed":
                return _json_response(self, {"items": [], "has_more": False, "next_cursor": ""})
            if path in {"/api/delight/pending-batch", "/api/notifications/pending"}:
                return _json_response(self, {"items": []})
            if path == "/api/chat/turns":
                return _json_response(self, {"items": []})
            if path == "/api/qr-info":
                return _json_response(self, {"lan_ip": "127.0.0.1"})
            return _json_response(self, {}, 404)

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if path == "/api/recommendations/append":
                state.append_in_flight += 1
                state.max_append_in_flight = max(state.max_append_in_flight, state.append_in_flight)
                state.append_posts.append(payload)
                state.append_received.set()
                time.sleep(0.15)
                try:
                    return _json_response(
                        self,
                        {"status": "ok", "items": _recommendations("B", 10)},
                    )
                finally:
                    state.append_in_flight -= 1
            return _json_response(self, {"ok": True})

        def _serve_file(self, path: Path, content_type: str | None = None) -> None:
            if not path.exists():
                return _json_response(self, {"error": "not_found"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header(
                "Content-Type",
                content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture()
def chromium_page() -> Page:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script(
            """
            window.WebSocket = class FakeWebSocket {
              static OPEN = 1;
              constructor() {
                this.readyState = FakeWebSocket.OPEN;
                setTimeout(() => {
                  if (typeof this.onopen === 'function') this.onopen({type:'open'});
                }, 0);
              }
              addEventListener() {}
              removeEventListener() {}
              close() { this.readyState = 3; }
            };
            """
        )
        yield page
        browser.close()


def _grid_bottom_gap(page: Page) -> float:
    """队列当前位置距自身滚动底部的像素。"""
    return page.evaluate(
        """() => {
          const grid = document.getElementById('videoGrid');
          return grid.scrollHeight - grid.clientHeight - grid.scrollTop;
        }"""
    )


def _scroll_grid_to_gap(page: Page, gap: float) -> float:
    """只滚动 #videoGrid，使其距内部底部约 gap px。"""
    return page.evaluate(
        """(gap) => {
          const grid = document.getElementById('videoGrid');
          grid.scrollTop = Math.max(0, grid.scrollHeight - grid.clientHeight - gap);
          grid.dispatchEvent(new Event('scroll'));
          return grid.scrollHeight - grid.clientHeight - grid.scrollTop;
        }""",
        gap,
    )


def test_autoload_fires_only_when_last_row_nearly_in_view(
    margin_server: tuple[str, MarginStub],
    chromium_page: Page,
) -> None:
    base_url, stub = margin_server
    chromium_page.goto(f"{base_url}/web/")

    grid = chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")
    expect(grid).to_have_count(CARD_COUNT, timeout=8000)

    # 自动加载默认开启，但未滚动内层队列时即使等待两个 cooldown 也不得追加。
    assert _grid_bottom_gap(chromium_page) > 300
    time.sleep(17.0)
    assert not stub.append_received.is_set(), "首屏未滚动就触发了自动加载"

    # 外层页面滚动不代表读完队列。
    chromium_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(0.4)
    assert not stub.append_received.is_set(), "外层页面滚动错误触发了内层队列续页"

    # 内层队列距自身底部约 150px：未进入 32px 见底区间，不得触发。
    gap_far = _scroll_grid_to_gap(chromium_page, 150)
    assert 110 < gap_far < 190, f"定位失败，实际 gap={gap_far}"
    assert not stub.append_received.wait(timeout=1.2), (
        f"内层队列距自身底部 {gap_far:.0f}px 时不应自动加载"
    )

    # 内层队列距自身底部约 20px：进入 32px 见底区间，必须触发一次追加。
    gap_near = _scroll_grid_to_gap(chromium_page, 20)
    assert gap_near < 32, f"定位失败，实际 gap={gap_near}"
    assert stub.append_received.wait(timeout=2.0), (
        f"内层队列已逼近自身底部 {gap_near:.0f}px 却没有自动加载"
    )

    # 追加成功后新卡片入列。
    expect(chromium_page.locator("#videoGrid .video-card:not(.is-skeleton)")).to_have_count(
        CARD_COUNT + 10, timeout=4000
    )
    assert len(stub.append_posts) == 1
    assert stub.max_append_in_flight == 1

    # 留在底部等待一个 cooldown 也不能连发；必须先离开底部再回来。
    time.sleep(8.5)
    assert len(stub.append_posts) == 1
    stub.append_received.clear()
    _scroll_grid_to_gap(chromium_page, 150)
    time.sleep(0.2)
    _scroll_grid_to_gap(chromium_page, 20)
    assert stub.append_received.wait(timeout=2.0)
    assert len(stub.append_posts) == 2
    assert stub.max_append_in_flight == 1
