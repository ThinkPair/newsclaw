from __future__ import annotations

import asyncio
import socket
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from typing import TYPE_CHECKING

import httpx
import pytest
from pypdf import PdfWriter

from openbiliclaw.news.web_monitoring import (
    FetchedDocument,
    MonitorFetchError,
    PublicPinnedTransport,
    WebDocumentFetcher,
    WebMonitorService,
    WebMonitorStore,
    _validate_response_peer,
    canonicalize_monitor_url,
    compare_documents,
    normalize_html,
    normalize_pdf,
    redact_url,
    validate_public_url,
)
from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class _AsyncPayload(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.data


def _document(
    text: str,
    *,
    raw: str,
    semantic: str,
    title: str = "政策页",
) -> FetchedDocument:
    return FetchedDocument(
        final_url="https://example.com/policy",
        kind="html",
        mime_type="text/html",
        title=title,
        normalized_text=text,
        raw_hash=raw * 64,
        semantic_hash=semantic * 64,
        byte_count=len(text.encode()),
        etag="",
        last_modified="",
        http_status=200,
    )


def _store(tmp_path: Path) -> tuple[Database, WebMonitorStore, dict[str, object]]:
    database = Database(tmp_path / "monitor.db")
    database.initialize()
    store = WebMonitorStore(database)
    monitor, created = store.create_monitor(
        canonical_url="https://example.com/policy",
        kind="auto",
        label="政策页",
        focus="",
        interval_minutes=60,
        request_id="create-monitor-request",
    )
    assert created is True
    return database, store, monitor


@pytest.mark.asyncio
async def test_url_boundary_rejects_local_private_and_credentials() -> None:
    for value in (
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
        "http://printer/",
        "https://user:password@example.com/",
        "https://example.com:8443/",
        "https://example.com/report?access_token=secret",
        "https://example.com/report?access-token=secret",
        "https://example.com/report?signature=secret",
        "https://example.com/report?X-Amz-Signature=secret&X-Amz-Credential=credential",
        "https://example.com/report?sig=secret",
        "https://example.com/report?jwt=secret",
    ):
        with pytest.raises(ValueError):
            await validate_public_url(value)

    assert canonicalize_monitor_url("HTTPS://Example.com/a#fragment") == "https://example.com/a"
    assert redact_url("https://example.com/?token=secret&section=1") == (
        "https://example.com/?token=%E2%80%A2%E2%80%A2%E2%80%A2%E2%80%A2&section=1"
    )
    assert "SECRET" not in redact_url("https://example.com/?X-Amz-Signature=SECRET&section=1")


def test_connected_peer_must_still_be_public_after_dns_resolution() -> None:
    class Stream:
        @staticmethod
        def get_extra_info(_name: str) -> tuple[str, int]:
            return ("127.0.0.1", 443)

    response = httpx.Response(200, extensions={"network_stream": Stream()})
    with pytest.raises(MonitorFetchError, match="公网地址") as error:
        _validate_response_peer(response)
    assert error.value.kind == "unsafe_peer"


@pytest.mark.asyncio
async def test_transport_pins_the_validated_ip_before_sending_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dns_calls = 0
    seen: list[httpx.Request] = []

    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        nonlocal dns_calls
        dns_calls += 1
        if dns_calls > 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    class Stream:
        @staticmethod
        def get_extra_info(_name: str) -> tuple[str, int]:
            return ("93.184.216.34", 443)

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=b"ok",
            extensions={"network_stream": Stream()},
        )

    transport = PublicPinnedTransport(transport_factory=lambda: httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        response = await client.get("https://example.com/policy")

    assert response.status_code == 200
    assert dns_calls == 1
    assert len(seen) == 1
    assert seen[0].url.host == "93.184.216.34"
    assert seen[0].headers["host"] == "example.com"
    assert seen[0].extensions["sni_hostname"] == "example.com"


@pytest.mark.asyncio
async def test_cross_origin_redirect_drops_validators_and_response_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    class Stream:
        @staticmethod
        def get_extra_info(_name: str) -> tuple[str, int]:
            return ("93.184.216.34", 443)

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "example.com":
            return httpx.Response(
                302,
                headers={
                    "location": "https://example.org/report",
                    "set-cookie": "source_session=private; Secure",
                },
                extensions={"network_stream": Stream()},
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "etag": '"destination-etag"'},
            stream=_AsyncPayload(
                b"<main><h1>Public report</h1><p>This is enough public document "
                b"text for extraction.</p></main>"
            ),
            extensions={"network_stream": Stream()},
        )

    monkeypatch.setattr(
        "openbiliclaw.news.web_monitoring.PublicPinnedTransport",
        lambda: httpx.MockTransport(handler),
    )
    result = await WebDocumentFetcher().fetch(
        url="https://example.com/report",
        kind="html",
        etag='"private-etag"',
        last_modified="Sat, 29 Aug 2026 00:00:00 GMT",
    )

    assert result.final_url == "https://example.org/report"
    assert seen[0].headers["if-none-match"] == '"private-etag"'
    assert "if-none-match" not in seen[1].headers
    assert "if-modified-since" not in seen[1].headers
    assert "cookie" not in seen[1].headers
    assert result.etag == ""

    await WebDocumentFetcher().fetch(
        url="https://example.com/report",
        kind="html",
        etag=result.etag,
        last_modified=result.last_modified,
    )
    assert "if-none-match" not in seen[2].headers
    assert "if-modified-since" not in seen[2].headers


@pytest.mark.asyncio
async def test_encoded_response_is_rejected_before_automatic_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stream:
        @staticmethod
        def get_extra_info(_name: str) -> tuple[str, int]:
            return ("93.184.216.34", 443)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-type": "text/html", "content-encoding": "gzip"},
            stream=_AsyncPayload(b"compressed-payload-is-never-decoded"),
            extensions={"network_stream": Stream()},
        )

    monkeypatch.setattr(
        "openbiliclaw.news.web_monitoring.PublicPinnedTransport",
        lambda: httpx.MockTransport(handler),
    )
    with pytest.raises(MonitorFetchError) as error:
        await WebDocumentFetcher().fetch(url="https://example.com/report", kind="html")
    assert error.value.kind == "encoded_response_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["image/png", "application/octet-stream"])
async def test_non_document_payload_is_not_accepted_as_html(
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
) -> None:
    class Stream:
        @staticmethod
        def get_extra_info(_name: str) -> tuple[str, int]:
            return ("93.184.216.34", 443)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": content_type},
            stream=_AsyncPayload(
                b"this is arbitrary binary-looking content with enough characters"
            ),
            extensions={"network_stream": Stream()},
        )

    monkeypatch.setattr(
        "openbiliclaw.news.web_monitoring.PublicPinnedTransport",
        lambda: httpx.MockTransport(handler),
    )
    with pytest.raises(MonitorFetchError) as error:
        await WebDocumentFetcher().fetch(url="https://example.com/report", kind="auto")
    assert error.value.kind == "unsupported_content_type"


@pytest.mark.asyncio
async def test_html_meta_charset_preserves_chinese_policy_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stream:
        @staticmethod
        def get_extra_info(_name: str) -> tuple[str, int]:
            return ("93.184.216.34", 443)

    payload = (
        '<html><head><meta charset="gbk"><title>政策更新</title></head>'
        "<body><main><h1>政策更新</h1><p>存款准备金率从百分之十调整为百分之九，"
        "本通知自发布之日起执行。</p></main></body></html>"
    ).encode("gbk")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=_AsyncPayload(payload),
            extensions={"network_stream": Stream()},
        )

    monkeypatch.setattr(
        "openbiliclaw.news.web_monitoring.PublicPinnedTransport",
        lambda: httpx.MockTransport(handler),
    )
    result = await WebDocumentFetcher().fetch(
        url="https://example.com/policy",
        kind="html",
    )
    assert result.title == "政策更新"
    assert "存款准备金率" in result.normalized_text
    assert "�" not in result.normalized_text


@pytest.mark.asyncio
async def test_scanned_pdf_without_text_is_an_explicit_failure() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    with pytest.raises(MonitorFetchError, match="OCR") as error:
        await normalize_pdf(output.getvalue())
    assert error.value.kind == "pdf_ocr_required"


@pytest.mark.asyncio
async def test_scheduler_limits_parallel_checks_per_host(tmp_path: Path) -> None:
    database = Database(tmp_path / "host-limit.db")
    database.initialize()
    store = WebMonitorStore(database)
    monitors = [
        store.create_monitor(
            canonical_url=f"https://example.com/policy-{index}",
            kind="auto",
            label=f"政策页 {index}",
            focus="",
            interval_minutes=60,
            request_id=f"create-host-limit-{index}",
        )[0]
        for index in range(2)
    ]

    class Fetcher:
        active = 0
        max_active = 0

        async def fetch(self, **_kwargs: object) -> FetchedDocument:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.05)
                return _document("政策正文", raw="a", semantic="b")
            finally:
                self.active -= 1

    fetcher = Fetcher()
    service = WebMonitorService(
        database=database,
        fetcher=fetcher,
        max_concurrency=3,
        max_per_host=1,
    )
    await asyncio.gather(
        *(
            service.check_monitor(str(monitor["monitor_id"]), request_id=f"host-check-{index}")
            for index, monitor in enumerate(monitors)
        )
    )
    assert fetcher.max_active == 1
    database.close()


@pytest.mark.asyncio
async def test_joined_manual_request_id_replays_same_durable_check(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    class Fetcher:
        calls = 0

        async def fetch(self, **_kwargs: object) -> FetchedDocument:
            self.calls += 1
            started.set()
            await release.wait()
            return _document("政策正文", raw="a", semantic="b")

    fetcher = Fetcher()
    service = WebMonitorService(database=database, fetcher=fetcher)
    monitor_id = str(monitor["monitor_id"])
    primary = service.schedule_check(monitor_id, request_id="manual-primary", manual=True)
    await started.wait()
    joined = service.schedule_check(monitor_id, request_id="manual-joined", manual=True)
    release.set()
    first, joined_result = await asyncio.gather(primary, joined)
    replay = await service.schedule_check(monitor_id, request_id="manual-joined", manual=True)

    assert fetcher.calls == 1
    assert first == joined_result == replay
    assert first["status"] == first["check"]["status"] == "baseline"
    assert replay["check"]["status"] == "baseline"
    assert database.conn.execute("SELECT COUNT(*) FROM web_monitor_checks").fetchone()[0] == 1
    assert (
        database.conn.execute(
            "SELECT COUNT(*) FROM web_monitor_commands WHERE operation = 'check'"
        ).fetchone()[0]
        == 2
    )
    database.close()


@pytest.mark.asyncio
async def test_schedule_reservation_never_blocks_the_event_loop(tmp_path: Path) -> None:
    database, _store_ref, monitor = _store(tmp_path)

    class Fetcher:
        async def fetch(self, **_kwargs: object) -> FetchedDocument:
            return _document("政策正文足够建立首个稳定基线。", raw="a", semantic="b")

    service = WebMonitorService(database=database, fetcher=Fetcher())
    blocker = sqlite3.connect(tmp_path / "monitor.db", check_same_thread=False)
    blocker.execute("PRAGMA busy_timeout = 30000")
    blocker.execute("BEGIN IMMEDIATE")
    with ThreadPoolExecutor(max_workers=1) as executor:
        release = executor.submit(lambda: (time.sleep(0.25), blocker.commit()))
        started_at = time.perf_counter()
        scheduled = service.schedule_check(
            str(monitor["monitor_id"]),
            request_id="non-blocking-reservation",
            manual=True,
        )
        elapsed = time.perf_counter() - started_at
        await asyncio.sleep(0.05)
        assert elapsed < 0.1
        await scheduled
        release.result(timeout=2)
    blocker.close()
    database.close()


@pytest.mark.asyncio
async def test_service_stop_cancels_owned_check_and_releases_lease(tmp_path: Path) -> None:
    database, _store_ref, monitor = _store(tmp_path)
    started = asyncio.Event()

    class Fetcher:
        calls = 0

        async def fetch(self, **_kwargs: object) -> FetchedDocument:
            self.calls += 1
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    fetcher = Fetcher()
    service = WebMonitorService(database=database, fetcher=fetcher)
    monitor_id = str(monitor["monitor_id"])
    service.schedule_check(monitor_id, request_id="shutdown-check", manual=True)
    await started.wait()
    await service.stop()

    check = database.conn.execute(
        "SELECT status, finished_at FROM web_monitor_checks WHERE monitor_id = ?",
        (monitor_id,),
    ).fetchone()
    target = database.conn.execute(
        "SELECT lease_owner, lease_token, lease_until FROM web_monitor_targets "
        "WHERE monitor_id = ?",
        (monitor_id,),
    ).fetchone()
    assert tuple(check) == ("cancelled", check["finished_at"])
    assert check["finished_at"] != ""
    assert tuple(target) == ("", "", "")
    replay = await service.schedule_check(monitor_id, request_id="shutdown-check", manual=True)
    assert replay["status"] == replay["check"]["status"] == "cancelled"
    assert fetcher.calls == 1
    database.close()


@pytest.mark.asyncio
async def test_pause_during_fetch_returns_cancelled_result_instead_of_500(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    class Fetcher:
        async def fetch(self, **_kwargs: object) -> FetchedDocument:
            started.set()
            await release.wait()
            return _document("暂停前已经开始读取的政策正文。", raw="a", semantic="b")

    service = WebMonitorService(database=database, fetcher=Fetcher())
    monitor_id = str(monitor["monitor_id"])
    checking = asyncio.create_task(
        service.check_monitor(monitor_id, request_id="pause-race", manual=True)
    )
    await started.wait()
    await asyncio.to_thread(store.set_status, monitor_id, "paused")
    release.set()
    result = await checking

    assert result["status"] == result["check"]["status"] == "cancelled"
    assert result["monitor"]["status"] == "paused"
    database.close()


@pytest.mark.asyncio
async def test_unexpected_fetch_failure_is_sanitized_and_durably_retried(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database, store, monitor = _store(tmp_path)

    class Fetcher:
        async def fetch(self, **_kwargs: object) -> FetchedDocument:
            raise RuntimeError("secret upstream response body")

    service = WebMonitorService(database=database, fetcher=Fetcher())
    monitor_id = str(monitor["monitor_id"])
    result = await service.check_monitor(
        monitor_id,
        request_id="unexpected-fetch-failure",
        manual=True,
    )

    assert result["status"] == result["check"]["status"] == "retry_wait"
    assert result["check"]["error_kind"] == "internal_monitor_error"
    assert "secret upstream" not in result["check"]["error_message"]
    assert "Unexpected web monitor failure (RuntimeError)" in caplog.text
    assert "secret upstream" not in caplog.text
    assert result["monitor"]["status"] == "retry_wait"
    assert result["monitor"]["lease_token"] == ""
    replay = await service.check_monitor(
        monitor_id,
        request_id="unexpected-fetch-failure",
        manual=True,
    )
    assert replay == result
    assert (
        database.conn.execute(
            "SELECT COUNT(*) FROM web_monitor_checks WHERE status = 'running'"
        ).fetchone()[0]
        == 0
    )
    assert store.notifications(include_read=True)["items"] == []
    database.close()


@pytest.mark.asyncio
async def test_304_before_baseline_is_a_failure_not_a_false_recovery(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)

    class Fetcher:
        async def fetch(self, **_kwargs: object) -> FetchedDocument:
            return FetchedDocument(
                final_url="https://example.com/policy",
                kind="html",
                mime_type="text/html",
                title="",
                normalized_text="",
                raw_hash="",
                semantic_hash="",
                byte_count=0,
                etag="",
                last_modified="",
                http_status=304,
                not_modified=True,
            )

    service = WebMonitorService(database=database, fetcher=Fetcher())
    result = await service.check_monitor(
        str(monitor["monitor_id"]), request_id="unexpected-304-before-baseline"
    )

    assert result["status"] == "failed"
    assert result["monitor"]["status"] == "needs_attention"
    assert database.conn.execute("SELECT COUNT(*) FROM web_monitor_snapshots").fetchone()[0] == 0
    kinds = [item["kind"] for item in store.notifications(include_read=True)["items"]]
    assert kinds == ["failure"]
    database.close()


def test_html_normalizer_drops_structure_noise_but_preserves_factual_tokens() -> None:
    title, text = normalize_html(
        """
        <html><head><title>政策更新</title><style>.x{}</style></head>
        <body><nav>首页 市场 登录</nav><main><h1>政策更新</h1>
        <p>政策利率从 3% 调整为 4%。</p>
        <p>本规定尚未生效，不得提前执行。</p></main>
        <div class="cookie-consent">接受全部 Cookie</div><footer>© 2026</footer></body></html>
        """.encode()
    )

    assert title == "政策更新"
    assert "首页" not in text
    assert "Cookie" not in text
    assert "3%" in text and "4%" in text
    assert "尚未生效" in text and "不得" in text


def test_suppressed_regions_with_void_elements_do_not_swallow_main_content() -> None:
    title, text = normalize_html(
        b"<html><head><title>Policy</title></head><body>"
        b"<nav><img src='logo.png'>Navigation</nav>"
        b"<form><input name='query'><button>Search</button></form>"
        b"<main><h1>Policy update</h1><p>The public rate changed from 3% to 4%. "
        b"This paragraph must remain visible after suppressed controls.</p></main>"
        b"</body></html>"
    )

    assert title == "Policy"
    assert "Navigation" not in text
    assert "Search" not in text
    assert "3%" in text and "4%" in text


def test_deterministic_diff_preserves_before_after_numbers_and_negation() -> None:
    diff = compare_documents(
        "政策利率为 3%\n本规定尚未生效，不得提前执行。",
        "政策利率为 4%\n本规定已经生效，可以执行。",
    )

    assert diff.classification == "meaningful"
    assert any("3%" in line for line in diff.removed)
    assert any("4%" in line for line in diff.added)
    assert "尚未生效" in diff.evidence_quote
    assert "已经生效" in diff.evidence_quote


def test_diff_bounds_pathological_repeated_line_documents() -> None:
    before = "\n".join(["重复段落"] * 60_000 + ["政策利率为 3%"])
    after = "\n".join(["重复段落"] * 60_000 + ["政策利率为 4%"])

    diff = compare_documents(before, after)

    assert diff.classification == "meaningful"
    assert "段落过多" in diff.summary
    assert "3%" in diff.evidence_quote
    assert "4%" in diff.evidence_quote


def test_baseline_noise_and_change_commit_to_one_durable_chain(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])

    first = store.claim_check(
        monitor_id, request_id="check-baseline", owner="worker-a", manual=True
    )
    assert first is not None and first.joined is False
    assert (
        store.finish_success(first, _document("政策利率为 3%", raw="a", semantic="b"))["status"]
        == "baseline"
    )
    event_state = database.conn.execute(
        "SELECT current_state FROM news_events WHERE event_id = ?",
        (str(monitor["event_id"]),),
    ).fetchone()[0]
    assert event_state == "已建立正文基线，等待后续变化"
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 0
    assert store.notifications()["unread_count"] == 0

    cosmetic = store.claim_check(
        monitor_id, request_id="check-cosmetic", owner="worker-a", manual=True
    )
    assert cosmetic is not None
    result = store.finish_success(
        cosmetic,
        _document("政策利率为 3%", raw="c", semantic="b"),
    )
    assert result["status"] == "noise_only"
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 0
    assert (
        database.conn.execute("SELECT classification FROM web_monitor_diffs").fetchone()[0]
        == "noise_only"
    )

    changed = store.claim_check(
        monitor_id, request_id="check-change", owner="worker-a", manual=True
    )
    assert changed is not None
    result = store.finish_success(
        changed,
        _document("政策利率为 4%", raw="d", semantic="e"),
    )
    assert result["status"] == "changed"
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 1
    notice = store.notifications()
    assert notice["unread_count"] == 1
    assert notice["items"][0]["change_id"] == result["change_id"]
    assert database.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert database.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


def test_same_raw_document_never_becomes_a_confirmed_change(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    baseline = store.claim_check(
        monitor_id, request_id="same-raw-baseline", owner="worker-a", manual=True
    )
    assert baseline is not None
    store.finish_success(
        baseline,
        _document("旧的规范化正文", raw="a", semantic="b"),
    )
    recalibrated = store.claim_check(
        monitor_id, request_id="same-raw-recalibrated", owner="worker-a", manual=True
    )
    assert recalibrated is not None

    result = store.finish_success(
        recalibrated,
        _document("新的规范化正文", raw="a", semantic="c"),
    )

    assert result["status"] == "noise_only"
    assert result["monitor"]["current_version"] == 2
    current = database.conn.execute(
        "SELECT normalized_text, classification, suppression_reason "
        "FROM web_monitor_snapshots WHERE snapshot_id = ?",
        (result["snapshot_id"],),
    ).fetchone()
    assert current["normalized_text"] == "新的规范化正文"
    assert current["classification"] == "noise_only"
    assert "原始文档字节未变" in current["suppression_reason"]
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 0
    assert (
        database.conn.execute("SELECT COUNT(*) FROM web_monitor_notifications").fetchone()[0] == 0
    )
    database.close()


def test_normalizer_upgrade_rebaselines_once_without_a_false_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    monkeypatch.setattr("openbiliclaw.news.web_monitoring.NORMALIZER_VERSION", "normalizer-v1")
    baseline = store.claim_check(
        monitor_id, request_id="normalizer-v1-baseline", owner="worker-a", manual=True
    )
    assert baseline is not None
    store.finish_success(baseline, _document("旧版解析正文", raw="a", semantic="a"))

    monkeypatch.setattr("openbiliclaw.news.web_monitoring.NORMALIZER_VERSION", "normalizer-v2")
    upgraded = store.claim_check(
        monitor_id, request_id="normalizer-v2-rebaseline", owner="worker-a", manual=True
    )
    assert upgraded is not None
    rebaseline = store.finish_success(
        upgraded,
        _document("新版解析后的正文", raw="b", semantic="b"),
    )

    assert rebaseline["status"] == "noise_only"
    snapshot = database.conn.execute(
        "SELECT normalizer_version, classification, suppression_reason "
        "FROM web_monitor_snapshots WHERE snapshot_id = ?",
        (rebaseline["snapshot_id"],),
    ).fetchone()
    assert tuple(snapshot) == (
        "normalizer-v2",
        "noise_only",
        "规范化器由旧版升级，当前正文作为新基线；跨版本差异不作为网页真实变化",
    )
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 0
    assert (
        database.conn.execute("SELECT COUNT(*) FROM web_monitor_notifications").fetchone()[0] == 0
    )

    changed = store.claim_check(
        monitor_id, request_id="normalizer-v2-change", owner="worker-a", manual=True
    )
    assert changed is not None
    changed_result = store.finish_success(
        changed,
        _document("网页随后真的发生变化", raw="c", semantic="c"),
    )
    assert changed_result["status"] == "changed"
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 1
    assert (
        database.conn.execute("SELECT COUNT(*) FROM web_monitor_notifications").fetchone()[0] == 1
    )
    assert database.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert database.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    database.close()


@pytest.mark.asyncio
async def test_normalizer_upgrade_forces_full_body_instead_of_old_validators(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    monkeypatch.setattr("openbiliclaw.news.web_monitoring.NORMALIZER_VERSION", "normalizer-v1")
    baseline = store.claim_check(
        monitor_id, request_id="validator-v1-baseline", owner="worker-a", manual=True
    )
    assert baseline is not None
    store.finish_success(baseline, _document("旧基线", raw="a", semantic="a"))
    database.conn.execute(
        "UPDATE web_monitor_targets SET etag = 'old-etag', last_modified = 'old-date' "
        "WHERE monitor_id = ?",
        (monitor_id,),
    )
    database.conn.commit()
    monkeypatch.setattr("openbiliclaw.news.web_monitoring.NORMALIZER_VERSION", "normalizer-v2")

    class Fetcher:
        seen: tuple[str, str] | None = None

        async def fetch(self, **kwargs: object) -> FetchedDocument:
            self.seen = (str(kwargs["etag"]), str(kwargs["last_modified"]))
            return FetchedDocument(
                final_url="https://example.com/policy",
                kind="html",
                mime_type="text/html",
                title="政策页",
                normalized_text="",
                raw_hash="",
                semantic_hash="",
                byte_count=0,
                etag="old-etag",
                last_modified="old-date",
                http_status=304,
                not_modified=True,
            )

    fetcher = Fetcher()
    service = WebMonitorService(database=database, fetcher=fetcher)
    result = await service.check_monitor(
        monitor_id,
        request_id="validator-v2-check",
        manual=True,
    )

    assert fetcher.seen == ("", "")
    assert result["status"] == "failed"
    assert result["error_kind"] == "unexpected_not_modified"
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 0
    database.close()


def test_repeated_a_b_state_transitions_each_create_a_new_occurrence(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    sequence = (
        ("state-a-1", "政策状态 A", "a", "a"),
        ("state-b-1", "政策状态 B", "b", "b"),
        ("state-a-2", "政策状态 A", "a", "a"),
        ("state-b-2", "政策状态 B", "b", "b"),
    )
    statuses: list[str] = []
    for request_id, text, raw, semantic in sequence:
        claim = store.claim_check(
            monitor_id,
            request_id=request_id,
            owner="worker-a",
            manual=True,
        )
        assert claim is not None
        statuses.append(
            store.finish_success(claim, _document(text, raw=raw, semantic=semantic))["status"]
        )

    assert statuses == ["baseline", "changed", "changed", "changed"]
    assert database.conn.execute("SELECT COUNT(*) FROM web_monitor_snapshots").fetchone()[0] == 4
    assert database.conn.execute("SELECT COUNT(*) FROM web_monitor_diffs").fetchone()[0] == 3
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 3
    assert (
        database.conn.execute("SELECT COUNT(*) FROM web_monitor_notifications").fetchone()[0] == 3
    )
    database.close()


def test_monitor_and_event_creation_roll_back_together(tmp_path: Path) -> None:
    database = Database(tmp_path / "atomic-create.db")
    database.initialize()
    database.conn.execute(
        "CREATE TRIGGER reject_monitor BEFORE INSERT ON web_monitor_targets "
        "BEGIN SELECT RAISE(ABORT, 'forced failure'); END"
    )
    database.conn.commit()
    store = WebMonitorStore(database)
    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        store.create_monitor(
            canonical_url="https://example.com/atomic",
            kind="auto",
            label="原子测试",
            focus="",
            interval_minutes=60,
            request_id="atomic-create-request",
        )
    assert database.conn.execute("SELECT COUNT(*) FROM news_events").fetchone()[0] == 0
    assert database.conn.execute("SELECT COUNT(*) FROM web_monitor_targets").fetchone()[0] == 0
    database.close()


def test_concurrent_create_request_replays_without_unique_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "concurrent-create.db")
    database.initialize()
    store = WebMonitorStore(database)
    barrier = Barrier(2)
    original_command_result = store.command_result

    def synchronized_command_result(*args: object, **kwargs: object) -> dict[str, object] | None:
        barrier.wait(timeout=5)
        return original_command_result(*args, **kwargs)

    store.command_result = synchronized_command_result  # type: ignore[method-assign]

    def create() -> tuple[dict[str, object], bool]:
        return store.create_monitor(
            canonical_url="https://example.com/concurrent",
            kind="auto",
            label="并发创建",
            focus="",
            interval_minutes=60,
            request_id="same-concurrent-create-request",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: create(), range(2)))

    assert sorted(created for _item, created in results) == [False, True]
    assert database.conn.execute("SELECT COUNT(*) FROM web_monitor_targets").fetchone()[0] == 1
    assert database.conn.execute("SELECT COUNT(*) FROM web_monitor_commands").fetchone()[0] == 1
    assert database.conn.execute("SELECT COUNT(*) FROM news_events").fetchone()[0] == 1
    database.close()


def test_concurrent_claim_joins_and_expired_owner_is_fenced(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    first = store.claim_check(monitor_id, request_id="first", owner="worker-a", manual=True)
    assert first is not None
    joined = store.claim_check(monitor_id, request_id="second", owner="worker-b", manual=True)
    assert joined is not None and joined.joined is True
    assert joined.check_id == first.check_id

    database.conn.execute(
        "UPDATE web_monitor_targets SET lease_until = '2000-01-01T00:00:00Z' WHERE monitor_id = ?",
        (monitor_id,),
    )
    database.conn.commit()
    replacement = store.claim_check(
        monitor_id, request_id="replacement", owner="worker-b", manual=True
    )
    assert replacement is not None and replacement.joined is False
    with pytest.raises(RuntimeError, match="lease"):
        store.finish_success(first, _document("旧 owner", raw="a", semantic="b"))
    assert (
        store.finish_success(replacement, _document("新 owner 基线", raw="c", semantic="d"))[
            "status"
        ]
        == "baseline"
    )
    database.close()


def test_failure_retry_state_and_notification_ack_are_durable(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    claim = store.claim_check(monitor_id, request_id="failed", owner="worker-a", manual=True)
    assert claim is not None
    failure = store.finish_failure(
        claim,
        MonitorFetchError("pdf_ocr_required", "扫描件需要 OCR", retryable=False),
    )
    assert failure["status"] == "failed"
    assert failure["monitor"]["status"] == "needs_attention"
    notices = store.notifications()
    assert notices["unread_count"] == 1
    delivery_id = notices["items"][0]["delivery_id"]
    delivered = store.mark_notification(
        delivery_id, action="delivered", request_id="notification-delivered-1"
    )
    delivered_again = store.mark_notification(
        delivery_id, action="delivered", request_id="notification-delivered-1"
    )
    assert delivered is not None and delivered_again is not None
    assert delivered_again["attempt_count"] == 1
    read = store.mark_notification(delivery_id, action="read", request_id="notification-read-1")
    assert read is not None and read["read_at"]
    replay = store.mark_notification(delivery_id, action="read", request_id="notification-read-1")
    assert replay is not None and replay["read_at"] == read["read_at"]
    with pytest.raises(ValueError, match="request_id"):
        store.mark_notification(delivery_id, action="dismiss", request_id="notification-read-1")
    assert store.notifications()["unread_count"] == 0
    database.close()


def test_recovery_after_established_baseline_creates_one_recovery_notice(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    baseline = store.claim_check(
        monitor_id, request_id="baseline-before-failure", owner="worker-a", manual=True
    )
    assert baseline is not None
    store.finish_success(baseline, _document("政策正文保持稳定", raw="a", semantic="b"))
    failed = store.claim_check(
        monitor_id, request_id="terminal-failure", owner="worker-a", manual=True
    )
    assert failed is not None
    store.finish_failure(
        failed,
        MonitorFetchError("pdf_invalid", "文档暂时无法解析", retryable=False),
    )
    recovered = store.claim_check(monitor_id, request_id="recovered", owner="worker-a", manual=True)
    assert recovered is not None
    store.finish_success(
        recovered,
        _document("政策正文保持稳定", raw="c", semantic="b"),
    )
    kinds = [item["kind"] for item in store.notifications(include_read=True)["items"]]
    assert kinds.count("failure") == 1
    assert kinds.count("recovered") == 1
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 0
    database.close()


def test_first_success_after_terminal_failure_creates_baseline_and_recovery(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    failed = store.claim_check(
        monitor_id, request_id="failure-before-baseline", owner="worker-a", manual=True
    )
    assert failed is not None
    store.finish_failure(
        failed,
        MonitorFetchError("pdf_invalid", "文档暂时无法解析", retryable=False),
    )
    recovered = store.claim_check(
        monitor_id, request_id="baseline-after-failure", owner="worker-a", manual=True
    )
    assert recovered is not None

    result = store.finish_success(
        recovered, _document("政策正文首次成功读取", raw="a", semantic="b")
    )

    assert result["status"] == "baseline"
    kinds = [item["kind"] for item in store.notifications(include_read=True)["items"]]
    assert kinds.count("failure") == 1
    assert kinds.count("recovered") == 1
    assert database.conn.execute("SELECT COUNT(*) FROM news_event_changes").fetchone()[0] == 0
    database.close()


def test_repeated_failure_recovery_cycles_each_create_a_notice(tmp_path: Path) -> None:
    database, store, monitor = _store(tmp_path)
    monitor_id = str(monitor["monitor_id"])
    baseline = store.claim_check(
        monitor_id, request_id="cycle-baseline", owner="worker-a", manual=True
    )
    assert baseline is not None
    store.finish_success(baseline, _document("政策正文", raw="a", semantic="a"))

    for index in range(2):
        failed = store.claim_check(
            monitor_id,
            request_id=f"cycle-failure-{index}",
            owner="worker-a",
            manual=True,
        )
        assert failed is not None
        store.finish_failure(
            failed,
            MonitorFetchError("pdf_invalid", "文档暂时无法解析", retryable=False),
        )
        recovered = store.claim_check(
            monitor_id,
            request_id=f"cycle-recovery-{index}",
            owner="worker-a",
            manual=True,
        )
        assert recovered is not None
        store.finish_success(recovered, _document("政策正文", raw="a", semantic="a"))

    kinds = [item["kind"] for item in store.notifications(include_read=True)["items"]]
    assert kinds.count("failure") == 2
    assert kinds.count("recovered") == 2
    database.close()


def test_schema_v9_is_additive_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "migration.db"
    database = Database(path)
    database.initialize()
    database.conn.execute(
        "INSERT INTO events (event_type, ingest_key) VALUES ('view', 'legacy-row')"
    )
    database.conn.commit()
    database.close()

    reopened = Database(path)
    reopened.initialize()
    assert reopened.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
    assert reopened.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 9
    tables = {
        row[0]
        for row in reopened.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'web_monitor_%'"
        )
    }
    assert {
        "web_monitor_targets",
        "web_monitor_checks",
        "web_monitor_snapshots",
        "web_monitor_diffs",
        "web_monitor_notifications",
        "web_monitor_commands",
    } <= tables
    reopened.close()
