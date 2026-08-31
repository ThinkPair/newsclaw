"""Reliable local monitoring for user-added public webpages and PDFs.

P5 deliberately keeps document monitoring out of the article discovery pool.
The deterministic pipeline is: safe fetch -> immutable snapshot -> semantic
diff -> existing NewsClaw event projection -> durable local notification.
No LLM decides whether a change happened.
"""

from __future__ import annotations

import asyncio
import codecs
import hashlib
import ipaddress
import json
import logging
import re
import socket
import sys
import unicodedata
import uuid
from collections import Counter
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from io import BytesIO
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from pypdf import PdfReader

from openbiliclaw.proc import no_window_kwargs

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

MonitorKind = Literal["auto", "html", "pdf"]
FetchedKind = Literal["html", "pdf"]

NORMALIZER_VERSION = "newsclaw-document-normalizer-v2"
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 1_000
MAX_TEXT_CHARS = 10 * 1024 * 1024
MAX_DIFF_LINES = 50_000
MAX_REDIRECTS = 5
MAX_FETCH_SECONDS = 30.0

_HTML_MIME_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_GENERIC_BINARY_MIME_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_META_CHARSET_RE = re.compile(
    rb"<meta\b[^>]{0,2048}?\bcharset\s*=\s*['\"]?\s*([a-zA-Z0-9._:-]+)",
    re.IGNORECASE,
)

_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)
_SUPPRESSED_TAGS = frozenset({"script", "style", "svg", "canvas", "noscript", "form"})
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_SUPPRESSED_HINTS = re.compile(
    r"(?:^|[-_\s])(?:advert|ads?|cookie|consent|footer|navbar|navigation|promo|"
    r"recommend|related|share|social|subscribe|tracking)(?:$|[-_\s])",
    re.IGNORECASE,
)
_NOISE_LINES = (
    re.compile(r"^(?:更新于|最后更新|页面生成于)\s*\d{1,2}[:：]\d{2}(?::\d{2})?$"),
    re.compile(r"^\d+\s*(?:秒|分钟|小时)前$"),
    re.compile(r"^(?:阅读|浏览|观看)\s*[：:]?\s*\d+(?:\.\d+)?(?:万|亿)?$"),
    re.compile(r"^(?:接受|拒绝|管理)(?:全部)?\s*(?:Cookie|Cookies|隐私设置)$", re.I),
    re.compile(r"^(?:分享到|分享至|复制链接|返回顶部)$"),
    re.compile(r"^©\s*\d{4}(?:\s*-\s*\d{4})?.{0,120}$", re.I),
)
_SENSITIVE_QUERY_KEYS = re.compile(
    r"(?:^|_)(?:access_token|api_key|apikey|auth|authorization|credential|credentials|"
    r"jwt|key|password|secret|session|session_id|sessionid|sig|signature|signed|token)(?:$|_)",
    re.IGNORECASE,
)


def utc_now_text(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def stable_uuid(namespace: str, *parts: str) -> str:
    seed = "\x1f".join((namespace, *parts))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sensitive_query_key(key: str) -> bool:
    normalized = re.sub(r"[-.]+", "_", key.strip())
    return bool(_SENSITIVE_QUERY_KEYS.search(normalized))


def redact_url(url: str) -> str:
    """Return a display-safe URL without credentials or secret-like values."""

    parsed = urlsplit(url)
    query = [
        (key, "••••" if _is_sensitive_query_key(key) else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


class MonitorFetchError(RuntimeError):
    """Bounded, redaction-safe fetch failure."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        retryable: bool,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind[:80]
        self.safe_message = " ".join(message.split())[:500]
        self.retryable = retryable
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


def canonicalize_monitor_url(value: str) -> str:
    raw = value.strip()
    if len(raw) > 4_096:
        raise ValueError("网址过长")
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("只支持公开的 HTTP 或 HTTPS 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("网址不能包含用户名或密码")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("网址缺少主机名")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("不能监控本机或内网地址")
    if "." not in host and not _is_ip_literal(host):
        raise ValueError("不能监控单标签内网主机")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("网址端口无效") from error
    expected_port = 443 if scheme == "https" else 80
    if port not in {None, expected_port}:
        raise ValueError("首版仅允许标准的 80/443 端口")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("网址主机名无效") from error
    if any(
        _is_sensitive_query_key(key) and bool(value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ):
        raise ValueError("网址不能包含访问令牌、签名或密码参数")
    netloc = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(address.is_global)


async def validate_public_url(url: str) -> str:
    """Canonicalize and resolve a URL, rejecting every non-public address."""

    canonical, _addresses = await _resolve_public_url(url)
    return canonical


async def _resolve_public_url(url: str) -> tuple[str, tuple[str, ...]]:
    """Resolve one canonical URL to a stable, public-only address set."""

    canonical = canonicalize_monitor_url(url)
    parsed = urlsplit(canonical)
    host = parsed.hostname or ""
    if _is_ip_literal(host):
        if not _public_ip(host):
            raise ValueError("不能监控本机、内网或保留地址")
        return canonical, (host,)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM),
            timeout=5.0,
        )
    except TimeoutError as error:
        raise MonitorFetchError("dns_timeout", "域名解析超时", retryable=True) from error
    except OSError as error:
        raise MonitorFetchError("dns_failed", "域名暂时无法解析", retryable=True) from error
    addresses = {str(row[4][0]).split("%", 1)[0] for row in rows if row[4]}
    if not addresses or any(not _public_ip(address) for address in addresses):
        raise ValueError("不能监控解析到本机、内网或保留地址的网页")
    ordered = tuple(
        sorted(
            addresses,
            key=lambda value: (ipaddress.ip_address(value).version != 4, value),
        )
    )
    return canonical, ordered


def _pinned_url(canonical_url: str, address: str) -> tuple[str, str, str]:
    parsed = urlsplit(canonical_url)
    original_host = parsed.hostname or ""
    pinned_host = f"[{address}]" if ":" in address else address
    if parsed.port is not None:
        pinned_host = f"{pinned_host}:{parsed.port}"
    host_header = f"[{original_host}]" if ":" in original_host else original_host
    if parsed.port is not None:
        host_header = f"{host_header}:{parsed.port}"
    return (
        urlunsplit((parsed.scheme, pinned_host, parsed.path, parsed.query, "")),
        host_header,
        original_host,
    )


class PublicPinnedTransport(httpx.AsyncBaseTransport):
    """Resolve, validate, then connect to that exact IP before sending HTTP."""

    def __init__(self, *, transport_factory: Any = None) -> None:
        self._transport_factory = transport_factory or (lambda: httpx.AsyncHTTPTransport(retries=0))
        self._transports: dict[tuple[str, str, str], httpx.AsyncBaseTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        canonical, addresses = await _resolve_public_url(str(request.url))
        parsed = urlsplit(canonical)
        last_error: httpx.TransportError | None = None
        for address in addresses:
            pinned, host_header, sni_hostname = _pinned_url(canonical, address)
            key = (parsed.scheme, sni_hostname, address)
            transport = self._transports.setdefault(key, self._transport_factory())
            headers = [
                (name, value) for name, value in request.headers.raw if name.lower() != b"host"
            ]
            headers.append((b"host", host_header.encode("ascii")))
            extensions = dict(request.extensions)
            if parsed.scheme == "https":
                extensions["sni_hostname"] = sni_hostname
            pinned_request = httpx.Request(
                method=request.method,
                url=pinned,
                headers=headers,
                content=b"",
                extensions=extensions,
            )
            try:
                response = await transport.handle_async_request(pinned_request)
            except httpx.TransportError as error:
                last_error = error
                continue
            try:
                _validate_response_peer(response, expected_address=address)
            except Exception:
                await response.aclose()
                raise
            return response
        if last_error is not None:
            raise last_error
        raise MonitorFetchError("dns_failed", "域名没有可用的公网地址", retryable=True)

    async def aclose(self) -> None:
        if self._transports:
            await asyncio.gather(
                *(transport.aclose() for transport in self._transports.values()),
                return_exceptions=True,
            )
        self._transports.clear()


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._suppressed_tags: list[str] = []
        self._primary_depth = 0
        self._title_depth = 0
        self._all: list[str] = []
        self._primary: list[str] = []
        self._title: list[str] = []

    @staticmethod
    def _is_suppressed(tag: str, attrs: Sequence[tuple[str, str | None]]) -> bool:
        if tag in _SUPPRESSED_TAGS or tag in {"nav", "footer", "aside"}:
            return True
        values = " ".join(value or "" for key, value in attrs if key in {"id", "class", "role"})
        return bool(_SUPPRESSED_HINTS.search(values))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if self._suppressed_tags:
            if normalized not in _VOID_TAGS:
                self._suppressed_tags.append(normalized)
            return
        if self._is_suppressed(normalized, attrs):
            if normalized not in _VOID_TAGS:
                self._suppressed_tags.append(normalized)
            return
        if normalized in {"main", "article"}:
            self._primary_depth += 1
        if normalized == "title":
            self._title_depth += 1
        if normalized in _BLOCK_TAGS:
            self._append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._suppressed_tags and tag.lower() in _BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if self._suppressed_tags:
            if normalized in self._suppressed_tags:
                index = (
                    len(self._suppressed_tags) - 1 - self._suppressed_tags[::-1].index(normalized)
                )
                del self._suppressed_tags[index:]
            return
        if normalized in _BLOCK_TAGS:
            self._append("\n")
        if normalized == "title" and self._title_depth:
            self._title_depth -= 1
        if normalized in {"main", "article"} and self._primary_depth:
            self._primary_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._suppressed_tags:
            return
        if self._title_depth:
            self._title.append(data)
        self._append(data)

    def _append(self, value: str) -> None:
        self._all.append(value)
        if self._primary_depth:
            self._primary.append(value)

    @property
    def title(self) -> str:
        return " ".join("".join(self._title).split())[:1_000]

    @property
    def text(self) -> str:
        primary = "".join(self._primary)
        return primary if len(primary.strip()) >= 80 else "".join(self._all)


def _normalize_lines(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("\u00a0", " ")
    result: list[str] = []
    for raw_line in normalized.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line or any(pattern.fullmatch(line) for pattern in _NOISE_LINES):
            continue
        if result and result[-1] == line:
            continue
        result.append(line)
    output = "\n".join(result).strip()
    if len(output) > MAX_TEXT_CHARS:
        raise MonitorFetchError("text_too_large", "可读正文超过 10MB 上限", retryable=False)
    return output


def _normalize_encoding_label(value: str) -> str:
    label = value.strip().strip("\"'").lower()
    aliases = {
        "gb2312": "gb18030",
        "gb_2312-80": "gb18030",
        "gbk": "gb18030",
        "x-gbk": "gb18030",
        "utf8": "utf-8",
    }
    label = aliases.get(label, label)
    if not label or label in {"utf-7", "unicode-1-1-utf-7"}:
        return "utf-8"
    try:
        codecs.lookup(label)
    except LookupError:
        return "utf-8"
    return label


def _html_encoding(data: bytes, content_type: str) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    header = re.search(r"charset\s*=\s*['\"]?\s*([^;\s\"']+)", content_type, re.I)
    if header is not None:
        return _normalize_encoding_label(header.group(1))
    meta = _META_CHARSET_RE.search(data[:8_192])
    if meta is not None:
        return _normalize_encoding_label(meta.group(1).decode("ascii", errors="ignore"))
    return "utf-8"


def _looks_like_html(data: bytes) -> bool:
    sample = data[:8_192].lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    return sample.startswith((b"<!doctype html", b"<html", b"<?xml")) or any(
        marker in sample for marker in (b"<head", b"<body", b"<main", b"<article")
    )


def normalize_html(data: bytes, *, encoding: str = "utf-8") -> tuple[str, str]:
    try:
        decoded = data.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        decoded = data.decode("utf-8", errors="replace")
    parser = _VisibleTextParser()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception as error:
        raise MonitorFetchError("html_parse_failed", "网页正文解析失败", retryable=False) from error
    text = _normalize_lines(parser.text)
    if len(text) < 20:
        raise MonitorFetchError("empty_text", "没有提取到足够的网页正文", retryable=False)
    return parser.title, text


def _extract_pdf_sync(data: bytes) -> tuple[str, str]:
    try:
        reader = PdfReader(BytesIO(data), strict=False)
    except Exception as error:
        raise MonitorFetchError("pdf_invalid", "PDF 文件损坏或无法解析", retryable=False) from error
    if reader.is_encrypted:
        raise MonitorFetchError("pdf_encrypted", "暂不支持加密 PDF", retryable=False)
    if len(reader.pages) > MAX_PDF_PAGES:
        raise MonitorFetchError("pdf_too_many_pages", "PDF 超过 1000 页上限", retryable=False)
    chunks: list[str] = []
    total = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            extracted = page.extract_text() or ""
        except Exception as error:
            raise MonitorFetchError(
                "pdf_parse_failed", "PDF 正文解析失败", retryable=False
            ) from error
        cleaned = _normalize_lines(extracted)
        if not cleaned:
            continue
        chunk = f"【第 {index} 页】\n{cleaned}"
        total += len(chunk)
        if total > MAX_TEXT_CHARS:
            raise MonitorFetchError("text_too_large", "PDF 可读正文超过 10MB 上限", retryable=False)
        chunks.append(chunk)
    if not chunks:
        raise MonitorFetchError(
            "pdf_ocr_required",
            "这个 PDF 没有可提取文字，首版暂不支持扫描件 OCR",
            retryable=False,
        )
    title = ""
    metadata = reader.metadata
    if metadata is not None:
        title = " ".join(str(getattr(metadata, "title", "") or "").split())[:1_000]
    return title, "\n".join(chunks)


async def normalize_pdf(data: bytes) -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        raise MonitorFetchError(
            "pdf_worker_unavailable",
            "当前安装形态尚未启用安全的 PDF 解析进程",
            retryable=False,
        )
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "openbiliclaw.news.pdf_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(data), timeout=20.0)
    except TimeoutError as error:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        raise MonitorFetchError("pdf_timeout", "PDF 解析超过 20 秒上限", retryable=False) from error
    except BaseException:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode != 0 or len(stdout) > (MAX_TEXT_CHARS * 2 + 100_000):
        raise MonitorFetchError("pdf_worker_failed", "PDF 解析进程异常退出", retryable=False)
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MonitorFetchError(
            "pdf_worker_failed", "PDF 解析进程返回了无效结果", retryable=False
        ) from error
    if not isinstance(result, dict):
        raise MonitorFetchError("pdf_worker_failed", "PDF 解析进程返回了无效结果", retryable=False)
    if result.get("ok") is True:
        return str(result.get("title") or "")[:1_000], str(result.get("text") or "")
    raise MonitorFetchError(
        str(result.get("kind") or "pdf_parse_failed"),
        str(result.get("message") or "PDF 正文解析失败"),
        retryable=bool(result.get("retryable", False)),
    )


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    final_url: str
    kind: FetchedKind
    mime_type: str
    title: str
    normalized_text: str
    raw_hash: str
    semantic_hash: str
    byte_count: int
    etag: str
    last_modified: str
    http_status: int
    not_modified: bool = False


class WebDocumentFetcher:
    """Fetch public documents with bounded I/O and redirect-by-redirect checks."""

    async def fetch(
        self,
        *,
        url: str,
        kind: MonitorKind,
        etag: str = "",
        last_modified: str = "",
    ) -> FetchedDocument:
        try:
            async with asyncio.timeout(MAX_FETCH_SECONDS):
                return await self._fetch_bounded(
                    url=url,
                    kind=kind,
                    etag=etag,
                    last_modified=last_modified,
                )
        except TimeoutError as error:
            raise MonitorFetchError(
                "fetch_timeout",
                "整次网页检查超过 30 秒上限",
                retryable=True,
            ) from error

    async def _fetch_bounded(
        self,
        *,
        url: str,
        kind: MonitorKind,
        etag: str = "",
        last_modified: str = "",
    ) -> FetchedDocument:
        current = canonicalize_monitor_url(url)
        initial = urlsplit(current)
        initial_origin = (
            initial.scheme,
            initial.hostname or "",
            initial.port or (443 if initial.scheme == "https" else 80),
        )
        base_headers = {
            "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
            "User-Agent": "NewsClaw-Local-Monitor/1.0",
        }
        timeout = httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            headers=base_headers,
            transport=PublicPinnedTransport(),
        ) as client:
            for redirect_count in range(MAX_REDIRECTS + 1):
                try:
                    parsed_current = urlsplit(current)
                    current_origin = (
                        parsed_current.scheme,
                        parsed_current.hostname or "",
                        parsed_current.port or (443 if parsed_current.scheme == "https" else 80),
                    )
                    conditional_headers: dict[str, str] = {}
                    if current_origin == initial_origin:
                        if etag:
                            conditional_headers["If-None-Match"] = etag[:1_000]
                        if last_modified:
                            conditional_headers["If-Modified-Since"] = last_modified[:1_000]
                    # A public page can redirect to another owner. Do not let
                    # response cookies or origin-specific validators follow it.
                    client.cookies.clear()
                    async with client.stream(
                        "GET", current, headers=conditional_headers
                    ) as response:
                        _validate_response_peer(response)
                        status = response.status_code
                        if status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location", "").strip()
                            if not location or redirect_count >= MAX_REDIRECTS:
                                raise MonitorFetchError(
                                    "redirect_limit", "网页重定向次数过多", retryable=False
                                )
                            target = urljoin(current, location)
                            target = canonicalize_monitor_url(target)
                            if (
                                urlsplit(current).scheme == "https"
                                and urlsplit(target).scheme != "https"
                            ):
                                raise MonitorFetchError(
                                    "https_downgrade",
                                    "安全连接不能重定向到非加密地址",
                                    retryable=False,
                                )
                            current = target
                            continue
                        if status == 304:
                            if not conditional_headers:
                                raise MonitorFetchError(
                                    "unexpected_not_modified",
                                    "网页在没有条件请求时返回了 304",
                                    retryable=True,
                                )
                            return FetchedDocument(
                                final_url=current,
                                kind="pdf" if kind == "pdf" else "html",
                                mime_type=response.headers.get("content-type", "")[:200],
                                title="",
                                normalized_text="",
                                raw_hash="",
                                semantic_hash="",
                                byte_count=0,
                                etag=response.headers.get("etag", etag)[:1_000],
                                last_modified=response.headers.get("last-modified", last_modified)[
                                    :1_000
                                ],
                                http_status=304,
                                not_modified=True,
                            )
                        if status >= 400:
                            retryable = status in {408, 425, 429} or status >= 500
                            retry_after = _retry_after_seconds(response.headers.get("retry-after"))
                            raise MonitorFetchError(
                                "http_error",
                                f"网页返回 HTTP {status}",
                                retryable=retryable,
                                http_status=status,
                                retry_after_seconds=retry_after,
                            )
                        content_encoding = (
                            response.headers.get("content-encoding", "").strip().lower()
                        )
                        if content_encoding not in {"", "identity"}:
                            raise MonitorFetchError(
                                "encoded_response_unsupported",
                                "目标网页强制返回压缩内容，首版为保护内存不自动解压",
                                retryable=False,
                            )
                        declared = _content_length(response.headers.get("content-length"))
                        content_type = response.headers.get("content-type", "").lower()
                        mime_type = content_type.split(";", 1)[0].strip()
                        expects_pdf = (
                            kind == "pdf"
                            or mime_type == "application/pdf"
                            or urlsplit(current).path.lower().endswith(".pdf")
                        )
                        declared_limit = MAX_PDF_BYTES if expects_pdf else MAX_HTML_BYTES
                        if declared is not None and declared > declared_limit:
                            raise MonitorFetchError(
                                "document_too_large", "文档超过首版大小上限", retryable=False
                            )
                        chunks: list[bytes] = []
                        received = 0
                        # Encoded responses were rejected above, so raw stream
                        # accounting is also the parser's hard allocation bound.
                        async for chunk in response.aiter_raw():
                            received += len(chunk)
                            if received > declared_limit:
                                raise MonitorFetchError(
                                    "document_too_large",
                                    "文档超过首版大小上限",
                                    retryable=False,
                                )
                            chunks.append(chunk)
                        data = b"".join(chunks)
                        if data.startswith(b"%PDF-"):
                            detected: FetchedKind = "pdf"
                        elif mime_type == "application/pdf":
                            raise MonitorFetchError(
                                "pdf_mime_mismatch",
                                "服务器声称返回 PDF，但文件内容不是 PDF",
                                retryable=False,
                            )
                        elif mime_type in _HTML_MIME_TYPES or (
                            mime_type in _GENERIC_BINARY_MIME_TYPES and _looks_like_html(data)
                        ):
                            detected = "html"
                        else:
                            raise MonitorFetchError(
                                "unsupported_content_type",
                                "返回内容不是可监控的 HTML 网页或 PDF",
                                retryable=False,
                            )
                        if kind != "auto" and detected != kind:
                            raise MonitorFetchError(
                                "content_kind_mismatch",
                                f"返回内容不是设置的 {kind.upper()} 类型",
                                retryable=False,
                            )
                        if detected == "html" and received > MAX_HTML_BYTES:
                            raise MonitorFetchError(
                                "document_too_large", "网页正文超过 10MB 上限", retryable=False
                            )
                        if detected == "pdf":
                            title, normalized = await normalize_pdf(data)
                        else:
                            title, normalized = normalize_html(
                                data,
                                encoding=_html_encoding(data, content_type),
                            )
                        same_origin = current_origin == initial_origin
                        return FetchedDocument(
                            final_url=current,
                            kind=detected,
                            mime_type=content_type[:200],
                            title=title,
                            normalized_text=normalized,
                            raw_hash=hashlib.sha256(data).hexdigest(),
                            semantic_hash=_text_hash(normalized),
                            byte_count=received,
                            etag=(response.headers.get("etag", "")[:1_000] if same_origin else ""),
                            last_modified=(
                                response.headers.get("last-modified", "")[:1_000]
                                if same_origin
                                else ""
                            ),
                            http_status=status,
                        )
                except MonitorFetchError:
                    raise
                except httpx.TimeoutException as error:
                    raise MonitorFetchError(
                        "network_timeout", "连接或读取网页超时", retryable=True
                    ) from error
                except httpx.HTTPError as error:
                    raise MonitorFetchError(
                        "network_error", "暂时无法连接这个网页", retryable=True
                    ) from error
        raise MonitorFetchError("redirect_limit", "网页重定向次数过多", retryable=False)


def _content_length(value: str | None) -> int | None:
    try:
        number = int(value or "")
    except ValueError:
        return None
    return number if number >= 0 else None


def _validate_response_peer(
    response: httpx.Response,
    *,
    expected_address: str | None = None,
) -> None:
    stream = response.extensions.get("network_stream")
    getter = getattr(stream, "get_extra_info", None)
    peer = getter("server_addr") if callable(getter) else None
    address = str(peer[0]).split("%", 1)[0] if isinstance(peer, tuple) and peer else ""
    if not address or not _public_ip(address):
        raise MonitorFetchError(
            "unsafe_peer",
            "连接目标不是可验证的公网地址",
            retryable=False,
        )
    if expected_address is not None:
        actual = ipaddress.ip_address(address)
        expected = ipaddress.ip_address(expected_address)
        if isinstance(actual, ipaddress.IPv6Address) and actual.ipv4_mapped is not None:
            actual = actual.ipv4_mapped
        if isinstance(expected, ipaddress.IPv6Address) and expected.ipv4_mapped is not None:
            expected = expected.ipv4_mapped
        if actual != expected:
            raise MonitorFetchError(
                "unsafe_peer",
                "实际连接地址与已验证的公网地址不一致",
                retryable=False,
            )


def _retry_after_seconds(value: str | None) -> int | None:
    try:
        seconds = int(value or "")
    except ValueError:
        return None
    return max(0, min(21_600, seconds))


@dataclass(frozen=True, slots=True)
class DocumentDiff:
    classification: Literal["meaningful", "noise_only"]
    summary: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    evidence_quote: str


def compare_documents(previous: str, current: str) -> DocumentDiff:
    if previous == current:
        return DocumentDiff("noise_only", "正文没有变化", (), (), "")
    if previous.count("\n") + 1 > MAX_DIFF_LINES or current.count("\n") + 1 > MAX_DIFF_LINES:
        return _compare_oversized_line_sets(previous, current)
    before = previous.splitlines()
    after = current.splitlines()
    # A line multiset comparison is deterministic and linear in the bounded
    # document size. SequenceMatcher is intentionally avoided: adversarial
    # repeated lines can make it near-quadratic while a SQLite write lock is held.
    after_remaining = Counter(after)
    removed_count = 0
    removed: list[str] = []
    for line in before:
        if after_remaining[line] > 0:
            after_remaining[line] -= 1
        else:
            removed_count += 1
            if len(removed) < 20:
                removed.append(line[:600])
    before_remaining = Counter(before)
    added_count = 0
    added: list[str] = []
    for line in after:
        if before_remaining[line] > 0:
            before_remaining[line] -= 1
        else:
            added_count += 1
            if len(added) < 20:
                added.append(line[:600])
    if added_count == 0 and removed_count == 0:
        first_mismatch = next(
            (
                index
                for index, pair in enumerate(zip(before, after, strict=True))
                if pair[0] != pair[1]
            ),
            0,
        )
        removed = [before[first_mismatch][:600]]
        added = [after[first_mismatch][:600]]
        summary = "正文段落顺序发生变化"
    else:
        summary = f"新增 {added_count} 段，删除或替换 {removed_count} 段"
    bounded_added = tuple(added)
    bounded_removed = tuple(removed)
    quote_parts: list[str] = []
    if bounded_removed:
        quote_parts.append("原文：" + " / ".join(bounded_removed[:3]))
    if bounded_added:
        quote_parts.append("现文：" + " / ".join(bounded_added[:3]))
    return DocumentDiff(
        "meaningful",
        summary,
        bounded_added,
        bounded_removed,
        "\n".join(quote_parts)[:4_000],
    )


def _compare_oversized_line_sets(previous: str, current: str) -> DocumentDiff:
    """Bound a pathological millions-of-lines document to a linear text scan."""

    common_prefix = 0
    shared = min(len(previous), len(current))
    while common_prefix < shared and previous[common_prefix] == current[common_prefix]:
        common_prefix += 1
    old_end = len(previous)
    new_end = len(current)
    while (
        old_end > common_prefix
        and new_end > common_prefix
        and previous[old_end - 1] == current[new_end - 1]
    ):
        old_end -= 1
        new_end -= 1
    old_start = previous.rfind("\n", 0, common_prefix) + 1
    new_start = current.rfind("\n", 0, common_prefix) + 1
    old_line_end = previous.find("\n", old_end)
    new_line_end = current.find("\n", new_end)
    if old_line_end < 0:
        old_line_end = len(previous)
    if new_line_end < 0:
        new_line_end = len(current)
    removed = tuple(
        line[:600] for line in previous[old_start:old_line_end].split("\n", 20)[:20] if line.strip()
    )
    added = tuple(
        line[:600] for line in current[new_start:new_line_end].split("\n", 20)[:20] if line.strip()
    )
    if not removed:
        removed = ("（原文在此位置没有内容）",)
    if not added:
        added = ("（现文在此位置没有内容）",)
    quote = f"原文：{' / '.join(removed[:3])}\n现文：{' / '.join(added[:3])}"[:4_000]
    return DocumentDiff(
        "meaningful",
        "正文发生变化（文档段落过多，差异证据已截取）",
        added,
        removed,
        quote,
    )


@dataclass(frozen=True, slots=True)
class ClaimedCheck:
    monitor: dict[str, Any]
    check_id: str
    lease_token: str
    request_id: str
    joined: bool = False


class WebMonitorStore:
    """SQLite DAO whose changed commit shares the Phase-3 transaction."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def _conn(self) -> sqlite3.Connection:
        return cast("sqlite3.Connection", self.database.open_connection())

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create_monitor(
        self,
        *,
        canonical_url: str,
        kind: MonitorKind,
        label: str,
        focus: str,
        interval_minutes: int,
        request_id: str,
    ) -> tuple[dict[str, Any], bool]:
        payload_hash = _text_hash(
            json.dumps(
                {
                    "url": canonical_url,
                    "kind": kind,
                    "label": label,
                    "focus": focus,
                    "interval": interval_minutes,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        existing_command = self.command_result(request_id, "create", payload_hash)
        if existing_command is not None:
            monitor = self.get_monitor(str(existing_command.get("monitor_id") or ""), True)
            if monitor is None:
                raise RuntimeError("monitor command points to a missing target")
            return monitor, False
        monitor_id = stable_uuid("newsclaw-web-monitor", canonical_url, kind)
        event_id = stable_uuid("newsclaw-web-monitor-event", canonical_url, kind)
        now = utc_now_text()
        title = label or _display_host(canonical_url)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            concurrent_command = conn.execute(
                "SELECT * FROM web_monitor_commands WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if concurrent_command is not None:
                if (
                    str(concurrent_command["operation"]) != "create"
                    or str(concurrent_command["payload_hash"]) != payload_hash
                ):
                    raise ValueError("request_id 已用于另一条不同的操作")
                monitor_id = str(concurrent_command["monitor_id"] or "")
                conn.commit()
                monitor = self.get_monitor(monitor_id, True)
                if monitor is None:
                    raise RuntimeError("monitor command points to a missing target")
                return monitor, False
            create_event = getattr(self.database, "_create_news_event_on", None)
            if not callable(create_event):  # pragma: no cover - current Database contract
                raise RuntimeError("news event creation is unavailable")
            # The target and its event archive are one aggregate: either both
            # become visible or neither does. Deterministic identities keep
            # retries/re-adding a soft-deleted target safe.
            create_event(
                conn,
                event_id=event_id,
                event_key=f"document-monitor:{_text_hash(canonical_url + chr(31) + kind)}",
                canonical_title=title,
                event_type="document_monitor",
                topic_group="网页与PDF监控",
                story_key=f"document:{_text_hash(canonical_url)}",
                current_state="等待建立首个正文基线",
                first_seen_at=now,
                last_seen_at=now,
            )
            existing = conn.execute(
                "SELECT * FROM web_monitor_targets WHERE canonical_url = ? "
                "AND content_kind = ? AND status != 'deleted'",
                (canonical_url, kind),
            ).fetchone()
            created = existing is None
            if existing is None:
                deleted = conn.execute(
                    "SELECT monitor_id FROM web_monitor_targets WHERE monitor_id = ?",
                    (monitor_id,),
                ).fetchone()
                if deleted is None:
                    conn.execute(
                        """
                        INSERT INTO web_monitor_targets (
                            monitor_id, canonical_url, content_kind, label, focus,
                            interval_minutes, event_id, next_check_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            monitor_id,
                            canonical_url,
                            kind,
                            label[:500],
                            focus[:1_000],
                            interval_minutes,
                            event_id,
                            now,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE web_monitor_targets
                        SET status = 'active', label = ?, focus = ?, interval_minutes = ?,
                            next_check_at = ?, last_error_kind = '', last_error = '',
                            consecutive_failures = 0, state_version = state_version + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE monitor_id = ?
                        """,
                        (label[:500], focus[:1_000], interval_minutes, now, monitor_id),
                    )
            else:
                monitor_id = str(existing["monitor_id"])
            result = {"monitor_id": monitor_id}
            conn.execute(
                "INSERT INTO web_monitor_commands "
                "(request_id, operation, payload_hash, monitor_id, result_json) "
                "VALUES (?, 'create', ?, ?, ?)",
                (request_id, payload_hash, monitor_id, json.dumps(result)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        monitor = self.get_monitor(monitor_id, True)
        if monitor is None:  # pragma: no cover
            raise RuntimeError("monitor disappeared after create")
        return monitor, created

    def command_result(
        self, request_id: str, operation: str, payload_hash: str
    ) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM web_monitor_commands WHERE request_id = ?", (request_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        if str(row["operation"]) != operation or str(row["payload_hash"]) != payload_hash:
            raise ValueError("request_id 已用于另一条不同的操作")
        try:
            value = json.loads(str(row["result_json"] or "{}"))
        except json.JSONDecodeError as error:  # pragma: no cover
            raise RuntimeError("stored monitor command is invalid") from error
        return cast("dict[str, Any]", value)

    def list_monitors(self, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        clause = "" if include_deleted else "WHERE t.status != 'deleted'"
        conn = self._conn()
        try:
            rows = conn.execute(
                f"""
                SELECT t.*,
                       s.title AS snapshot_title,
                       s.semantic_hash AS current_semantic_hash,
                       s.normalizer_version AS current_normalizer_version,
                       s.ordinal AS current_version,
                       (SELECT COUNT(*) FROM web_monitor_diffs d
                        WHERE d.monitor_id = t.monitor_id
                          AND d.classification = 'meaningful') AS change_count,
                       (SELECT COUNT(*) FROM web_monitor_notifications n
                        WHERE n.monitor_id = t.monitor_id AND n.read_at = ''
                          AND n.dismissed_at = '') AS unread_count
                FROM web_monitor_targets t
                LEFT JOIN web_monitor_snapshots s
                  ON s.snapshot_id = t.current_snapshot_id
                {clause}
                ORDER BY t.updated_at DESC, t.monitor_id DESC
                """
            ).fetchall()
        finally:
            conn.close()
        return [self._monitor_out(dict(row)) for row in rows]

    def get_monitor(self, monitor_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
        clause = "" if include_deleted else "AND t.status != 'deleted'"
        conn = self._conn()
        try:
            row = conn.execute(
                f"""
                SELECT t.*, s.title AS snapshot_title,
                       s.semantic_hash AS current_semantic_hash,
                       s.normalizer_version AS current_normalizer_version,
                       s.ordinal AS current_version,
                       (SELECT COUNT(*) FROM web_monitor_diffs d
                        WHERE d.monitor_id = t.monitor_id
                          AND d.classification = 'meaningful') AS change_count,
                       (SELECT COUNT(*) FROM web_monitor_notifications n
                        WHERE n.monitor_id = t.monitor_id AND n.read_at = ''
                          AND n.dismissed_at = '') AS unread_count
                FROM web_monitor_targets t
                LEFT JOIN web_monitor_snapshots s
                  ON s.snapshot_id = t.current_snapshot_id
                WHERE t.monitor_id = ? {clause}
                """,
                (monitor_id,),
            ).fetchone()
        finally:
            conn.close()
        return self._monitor_out(dict(row)) if row is not None else None

    @staticmethod
    def _monitor_out(item: dict[str, Any]) -> dict[str, Any]:
        item["display_url"] = redact_url(str(item.get("canonical_url") or ""))
        item["has_baseline"] = bool(item.get("current_snapshot_id"))
        item["is_running"] = bool(item.get("lease_until") and item.get("lease_token"))
        return item

    def update_monitor(
        self,
        monitor_id: str,
        *,
        label: str | None = None,
        focus: str | None = None,
        interval_minutes: int | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any] | None:
        updates: list[str] = []
        params: list[object] = []
        if label is not None:
            updates.append("label = ?")
            params.append(label[:500])
        if focus is not None:
            updates.append("focus = ?")
            params.append(focus[:1_000])
        if interval_minutes is not None:
            updates.append("interval_minutes = ?")
            params.append(interval_minutes)
        if not updates:
            return self.get_monitor(monitor_id)
        updates.extend(("state_version = state_version + 1", "updated_at = CURRENT_TIMESTAMP"))
        where = "monitor_id = ? AND status != 'deleted'"
        params.append(monitor_id)
        if expected_version is not None:
            where += " AND state_version = ?"
            params.append(expected_version)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                f"UPDATE web_monitor_targets SET {', '.join(updates)} WHERE {where}", params
            ).rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if (
            not changed
            and expected_version is not None
            and self.get_monitor(monitor_id) is not None
        ):
            raise RuntimeError("monitor_version_conflict")
        return self.get_monitor(monitor_id)

    def set_status(self, monitor_id: str, status: str) -> dict[str, Any] | None:
        if status not in {"active", "paused", "deleted"}:
            raise ValueError("unsupported monitor status")
        now = utc_now_text()
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM web_monitor_targets WHERE monitor_id = ? AND status != 'deleted'",
                (monitor_id,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            if status in {"paused", "deleted"}:
                conn.execute(
                    "UPDATE web_monitor_checks SET status = 'cancelled', finished_at = ? "
                    "WHERE monitor_id = ? AND status = 'running'",
                    (now, monitor_id),
                )
            conn.execute(
                """
                UPDATE web_monitor_targets
                SET status = ?, state_version = state_version + 1,
                    next_check_at = CASE WHEN ? = 'active' THEN ? ELSE next_check_at END,
                    lease_owner = '', lease_token = '', lease_until = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE monitor_id = ?
                """,
                (status, status, now, monitor_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.get_monitor(monitor_id, include_deleted=status == "deleted")

    @staticmethod
    def _check_payload_hash(monitor_id: str) -> str:
        return _text_hash(
            json.dumps({"monitor_id": monitor_id}, ensure_ascii=False, sort_keys=True)
        )

    @staticmethod
    def _check_command_result(row: sqlite3.Row) -> tuple[str, str]:
        try:
            result = json.loads(str(row["result_json"] or "{}"))
        except json.JSONDecodeError as error:
            raise RuntimeError("stored check command is invalid") from error
        if not isinstance(result, dict):
            raise RuntimeError("stored check command is invalid")
        return (
            str(result.get("primary_request_id") or row["request_id"]),
            str(result.get("check_id") or ""),
        )

    @classmethod
    def _ensure_check_command_on(
        cls,
        conn: sqlite3.Connection,
        *,
        request_id: str,
        monitor_id: str,
        primary_request_id: str,
        check_id: str = "",
    ) -> str:
        payload_hash = cls._check_payload_hash(monitor_id)
        row = conn.execute(
            "SELECT * FROM web_monitor_commands WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is not None:
            if str(row["operation"]) != "check" or str(row["payload_hash"]) != payload_hash:
                raise ValueError("request_id 已用于另一条不同的操作")
            _stored_primary, stored_check_id = cls._check_command_result(row)
            if stored_check_id:
                return stored_check_id
        result = json.dumps(
            {"primary_request_id": primary_request_id, "check_id": check_id},
            ensure_ascii=False,
        )
        if row is None:
            conn.execute(
                "INSERT INTO web_monitor_commands "
                "(request_id, operation, payload_hash, monitor_id, result_json) "
                "VALUES (?, 'check', ?, ?, ?)",
                (request_id, payload_hash, monitor_id, result),
            )
        elif check_id:
            conn.execute(
                "UPDATE web_monitor_commands SET result_json = ? WHERE request_id = ?",
                (result, request_id),
            )
        return check_id

    def reserve_check_request(
        self,
        monitor_id: str,
        *,
        request_id: str,
        primary_request_id: str,
    ) -> str:
        """Persist a retry/join receipt before an in-memory task can disappear."""

        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._ensure_check_command_on(
                conn,
                request_id=request_id,
                monitor_id=monitor_id,
                primary_request_id=primary_request_id,
            )
            if existing:
                conn.commit()
                return existing
            primary_check_id = self._ensure_check_command_on(
                conn,
                request_id=primary_request_id,
                monitor_id=monitor_id,
                primary_request_id=primary_request_id,
            )
            if request_id != primary_request_id and primary_check_id:
                self._ensure_check_command_on(
                    conn,
                    request_id=request_id,
                    monitor_id=monitor_id,
                    primary_request_id=primary_request_id,
                    check_id=primary_check_id,
                )
            conn.commit()
            return primary_check_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_check(
        self,
        monitor_id: str,
        *,
        request_id: str,
        owner: str,
        manual: bool,
    ) -> ClaimedCheck | None:
        now = datetime.now(UTC)
        now_text = utc_now_text(now)
        lease_until = utc_now_text(now + timedelta(minutes=2))
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            payload_hash = self._check_payload_hash(monitor_id)
            command = conn.execute(
                "SELECT * FROM web_monitor_commands WHERE request_id = ?", (request_id,)
            ).fetchone()
            primary_request_id = request_id
            command_check_id = ""
            if command is not None:
                if (
                    str(command["operation"]) != "check"
                    or str(command["payload_hash"]) != payload_hash
                ):
                    raise ValueError("request_id 已用于另一条不同的操作")
                primary_request_id, command_check_id = self._check_command_result(command)
            else:
                self._ensure_check_command_on(
                    conn,
                    request_id=request_id,
                    monitor_id=monitor_id,
                    primary_request_id=request_id,
                )
            if command_check_id:
                replay = conn.execute(
                    "SELECT * FROM web_monitor_checks WHERE check_id = ? AND monitor_id = ?",
                    (command_check_id, monitor_id),
                ).fetchone()
                monitor = conn.execute(
                    "SELECT * FROM web_monitor_targets WHERE monitor_id = ?", (monitor_id,)
                ).fetchone()
                conn.commit()
                if replay is None or monitor is None:
                    raise RuntimeError("check command points to a missing result")
                return ClaimedCheck(
                    monitor=dict(monitor),
                    check_id=str(replay["check_id"]),
                    lease_token=str(replay["lease_token"] or ""),
                    request_id=request_id,
                    joined=True,
                )
            replay = conn.execute(
                "SELECT * FROM web_monitor_checks WHERE monitor_id = ? AND request_id = ?",
                (monitor_id, primary_request_id),
            ).fetchone()
            if replay is not None:
                self._ensure_check_command_on(
                    conn,
                    request_id=primary_request_id,
                    monitor_id=monitor_id,
                    primary_request_id=primary_request_id,
                    check_id=str(replay["check_id"]),
                )
                self._ensure_check_command_on(
                    conn,
                    request_id=request_id,
                    monitor_id=monitor_id,
                    primary_request_id=primary_request_id,
                    check_id=str(replay["check_id"]),
                )
                monitor = conn.execute(
                    "SELECT * FROM web_monitor_targets WHERE monitor_id = ?", (monitor_id,)
                ).fetchone()
                conn.commit()
                if monitor is None:
                    return None
                return ClaimedCheck(
                    monitor=dict(monitor),
                    check_id=str(replay["check_id"]),
                    lease_token=str(replay["lease_token"] or ""),
                    request_id=request_id,
                    joined=True,
                )
            target = conn.execute(
                "SELECT * FROM web_monitor_targets WHERE monitor_id = ? AND status != 'deleted'",
                (monitor_id,),
            ).fetchone()
            if target is None or str(target["status"]) == "paused":
                conn.commit()
                return None
            if not manual and (
                str(target["status"]) not in {"active", "retry_wait"}
                or str(target["next_check_at"] or "") > now_text
            ):
                conn.commit()
                return None
            running = conn.execute(
                "SELECT * FROM web_monitor_checks WHERE monitor_id = ? AND status = 'running'",
                (monitor_id,),
            ).fetchone()
            if running is not None:
                if str(target["lease_until"] or "") > now_text:
                    self._ensure_check_command_on(
                        conn,
                        request_id=request_id,
                        monitor_id=monitor_id,
                        primary_request_id=str(running["request_id"]),
                        check_id=str(running["check_id"]),
                    )
                    conn.commit()
                    return ClaimedCheck(
                        monitor=dict(target),
                        check_id=str(running["check_id"]),
                        lease_token=str(running["lease_token"] or ""),
                        request_id=str(running["request_id"]),
                        joined=True,
                    )
                conn.execute(
                    "UPDATE web_monitor_checks SET status = 'cancelled', finished_at = ? "
                    "WHERE check_id = ? AND status = 'running'",
                    (now_text, str(running["check_id"])),
                )
            check_id = str(uuid.uuid4())
            lease_token = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO web_monitor_checks "
                "(check_id, monitor_id, request_id, lease_token, status, started_at) "
                "VALUES (?, ?, ?, ?, 'running', ?)",
                (check_id, monitor_id, primary_request_id, lease_token, now_text),
            )
            self._ensure_check_command_on(
                conn,
                request_id=primary_request_id,
                monitor_id=monitor_id,
                primary_request_id=primary_request_id,
                check_id=check_id,
            )
            self._ensure_check_command_on(
                conn,
                request_id=request_id,
                monitor_id=monitor_id,
                primary_request_id=primary_request_id,
                check_id=check_id,
            )
            conn.execute(
                "UPDATE web_monitor_targets SET last_attempt_at = ?, lease_owner = ?, "
                "lease_token = ?, lease_until = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE monitor_id = ?",
                (now_text, owner, lease_token, lease_until, monitor_id),
            )
            conn.commit()
            monitor = self.get_monitor(monitor_id)
            if monitor is None:  # pragma: no cover
                return None
            return ClaimedCheck(monitor, check_id, lease_token, request_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish_success(
        self,
        claim: ClaimedCheck,
        fetched: FetchedDocument,
    ) -> dict[str, Any]:
        monitor_id = str(claim.monitor["monitor_id"])
        finished_at = utc_now_text()
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            target = self._fenced_target(conn, monitor_id, claim.lease_token)
            current = (
                conn.execute(
                    "SELECT * FROM web_monitor_snapshots WHERE snapshot_id = ?",
                    (str(target["current_snapshot_id"]),),
                ).fetchone()
                if target["current_snapshot_id"]
                else None
            )
            interval = int(target["interval_minutes"])
            next_at = utc_now_text(datetime.now(UTC) + timedelta(minutes=interval))
            recovered = str(target["status"] or "") == "needs_attention"
            if fetched.not_modified:
                status = "unchanged"
                snapshot_id = str(target["current_snapshot_id"] or "")
                change_id = ""
            elif current is None:
                snapshot_id = self._insert_snapshot(
                    conn,
                    monitor_id,
                    claim.check_id,
                    fetched,
                    classification="baseline",
                    suppression_reason="",
                )
                status = "baseline"
                change_id = ""
                conn.execute(
                    """
                    UPDATE news_events
                    SET current_state = '已建立正文基线，等待后续变化',
                        last_seen_at = ?, state_version = state_version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE event_id = ? AND status != 'merged'
                    """,
                    (finished_at, str(target["event_id"])),
                )
            elif str(current["normalizer_version"]) != NORMALIZER_VERSION:
                snapshot_id = self._insert_snapshot(
                    conn,
                    monitor_id,
                    claim.check_id,
                    fetched,
                    classification="noise_only",
                    suppression_reason=(
                        "规范化器由旧版升级，当前正文作为新基线；跨版本差异不作为网页真实变化"
                    ),
                )
                status = "noise_only"
                change_id = ""
                self._insert_noise_diff(
                    conn,
                    monitor_id=monitor_id,
                    check_id=claim.check_id,
                    from_snapshot_id=str(current["snapshot_id"]),
                    to_snapshot_id=snapshot_id,
                )
            elif fetched.raw_hash == str(current["raw_hash"]):
                if str(current["semantic_hash"]) == fetched.semantic_hash:
                    snapshot_id = str(current["snapshot_id"])
                    status = "unchanged"
                else:
                    snapshot_id = self._insert_snapshot(
                        conn,
                        monitor_id,
                        claim.check_id,
                        fetched,
                        classification="noise_only",
                        suppression_reason=(
                            "原始文档字节未变，仅规范化输出发生校准；不作为网页真实变化"
                        ),
                    )
                    status = "noise_only"
                    self._insert_noise_diff(
                        conn,
                        monitor_id=monitor_id,
                        check_id=claim.check_id,
                        from_snapshot_id=str(current["snapshot_id"]),
                        to_snapshot_id=snapshot_id,
                    )
                change_id = ""
            elif str(current["semantic_hash"]) == fetched.semantic_hash:
                snapshot_id = self._insert_snapshot(
                    conn,
                    monitor_id,
                    claim.check_id,
                    fetched,
                    classification="noise_only",
                    suppression_reason="原始页面变了，但规范化正文没有变化",
                )
                status = "noise_only"
                change_id = ""
                self._insert_noise_diff(
                    conn,
                    monitor_id=monitor_id,
                    check_id=claim.check_id,
                    from_snapshot_id=str(current["snapshot_id"]),
                    to_snapshot_id=snapshot_id,
                )
            else:
                snapshot_id = self._insert_snapshot(
                    conn,
                    monitor_id,
                    claim.check_id,
                    fetched,
                    classification="meaningful",
                    suppression_reason="",
                )
                diff = compare_documents(str(current["normalized_text"]), fetched.normalized_text)
                change_id = self._project_change(
                    conn,
                    target=dict(target),
                    claim=claim,
                    current=dict(current),
                    snapshot_id=snapshot_id,
                    fetched=fetched,
                    diff=diff,
                    detected_at=finished_at,
                )
                self._insert_meaningful_diff(
                    conn,
                    monitor_id=monitor_id,
                    check_id=claim.check_id,
                    from_snapshot_id=str(current["snapshot_id"]),
                    to_snapshot_id=snapshot_id,
                    change_id=change_id,
                    diff=diff,
                )
                notification_subject = str(
                    target["label"] or fetched.title or _display_host(str(target["canonical_url"]))
                )
                self._insert_notification(
                    conn,
                    notification_key=f"change:{change_id}:in_app",
                    monitor_id=monitor_id,
                    event_id=str(target["event_id"]),
                    change_id=change_id,
                    check_id=claim.check_id,
                    kind="change",
                    title=f"{notification_subject} 有新变化",
                    body=diff.summary,
                    created_at=finished_at,
                )
                status = "changed"
            conn.execute(
                """
                UPDATE web_monitor_checks
                SET status = ?, from_snapshot_id = ?, to_snapshot_id = ?,
                    http_status = ?, finished_at = ?
                WHERE check_id = ? AND status = 'running' AND lease_token = ?
                """,
                (
                    status,
                    str(current["snapshot_id"]) if current is not None else None,
                    snapshot_id or None,
                    fetched.http_status,
                    finished_at,
                    claim.check_id,
                    claim.lease_token,
                ),
            )
            conn.execute(
                """
                UPDATE web_monitor_targets
                SET status = 'active',
                    current_snapshot_id = COALESCE(NULLIF(?, ''), current_snapshot_id),
                    etag = ?, last_modified = ?, last_success_at = ?,
                    last_change_at = CASE WHEN ? = 'changed' THEN ? ELSE last_change_at END,
                    next_check_at = ?, consecutive_failures = 0,
                    last_error_kind = '', last_error = '',
                    lease_owner = '', lease_token = '', lease_until = '',
                    state_version = state_version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE monitor_id = ? AND lease_token = ?
                """,
                (
                    snapshot_id,
                    fetched.etag,
                    fetched.last_modified,
                    finished_at,
                    status,
                    finished_at,
                    next_at,
                    monitor_id,
                    claim.lease_token,
                ),
            )
            if recovered:
                self._insert_notification(
                    conn,
                    notification_key=f"recovered:{monitor_id}:{claim.check_id}",
                    monitor_id=monitor_id,
                    event_id=str(target["event_id"]),
                    change_id="",
                    check_id=claim.check_id,
                    kind="recovered",
                    title="网页监控已经恢复",
                    body=(
                        f"{str(target['label'] or _display_host(str(target['canonical_url'])))}"
                        " 已重新检查成功"
                    ),
                    created_at=finished_at,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.check_result_envelope(monitor_id, claim.check_id)

    def _fenced_target(
        self, conn: sqlite3.Connection, monitor_id: str, lease_token: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM web_monitor_targets WHERE monitor_id = ? AND lease_token = ? "
            "AND status != 'deleted'",
            (monitor_id, lease_token),
        ).fetchone()
        if row is None:
            raise RuntimeError("monitor lease is no longer valid")
        return cast("sqlite3.Row", row)

    def _insert_snapshot(
        self,
        conn: sqlite3.Connection,
        monitor_id: str,
        check_id: str,
        fetched: FetchedDocument,
        *,
        classification: str,
        suppression_reason: str,
    ) -> str:
        ordinal_row = conn.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 AS ordinal "
            "FROM web_monitor_snapshots WHERE monitor_id = ?",
            (monitor_id,),
        ).fetchone()
        ordinal = int(ordinal_row["ordinal"] if ordinal_row is not None else 1)
        snapshot_id = stable_uuid(
            "newsclaw-web-monitor-snapshot",
            monitor_id,
            check_id,
            fetched.raw_hash,
            fetched.semantic_hash,
            NORMALIZER_VERSION,
        )
        conn.execute(
            """
            INSERT INTO web_monitor_snapshots (
                snapshot_id, monitor_id, ordinal, raw_hash, semantic_hash,
                normalizer_version, content_kind, mime_type, title,
                normalized_text, byte_count, final_url, etag, last_modified,
                classification, suppression_reason, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                monitor_id,
                ordinal,
                fetched.raw_hash,
                fetched.semantic_hash,
                NORMALIZER_VERSION,
                fetched.kind,
                fetched.mime_type,
                fetched.title,
                fetched.normalized_text,
                fetched.byte_count,
                fetched.final_url,
                fetched.etag,
                fetched.last_modified,
                classification,
                suppression_reason,
                utc_now_text(),
            ),
        )
        return snapshot_id

    def _project_change(
        self,
        conn: sqlite3.Connection,
        *,
        target: dict[str, Any],
        claim: ClaimedCheck,
        current: dict[str, Any],
        snapshot_id: str,
        fetched: FetchedDocument,
        diff: DocumentDiff,
        detected_at: str,
    ) -> str:
        previous_state = "\n".join(diff.removed)[:8_000] or "正文旧版本"
        new_state = "\n".join(diff.added)[:8_000] or "正文新版本"
        item_key = f"web-monitor:{target['monitor_id']}:{snapshot_id}"
        snapshot = {
            "item_key": item_key,
            "evidence_fingerprint": fetched.semantic_hash,
            "title": (
                fetched.title or target["label"] or _display_host(str(target["canonical_url"]))
            ),
            "body_text": fetched.normalized_text[:30_000],
            "content_url": fetched.final_url,
            "canonical_url": fetched.final_url,
            "content_scope": "full_text",
            "media": "用户添加的网页/PDF",
            "provider": _display_host(fetched.final_url),
            "retrieved_at": detected_at,
        }
        change = {
            **snapshot,
            "change_id": stable_uuid(
                "newsclaw-web-monitor-change",
                str(target["monitor_id"]),
                str(current["snapshot_id"]),
                snapshot_id,
                NORMALIZER_VERSION,
            ),
            "change_type": "document_text_updated",
            "occurrence_id": claim.check_id,
            "previous_state": previous_state,
            "new_state": new_state,
            "fact": f"监控文档正文已发生可核验变化：{diff.summary}",
            "inference": "",
            "summary": diff.summary,
            "evidence_quote": diff.evidence_quote,
            "confidence": 1.0,
            "role": "objective_change",
            "objectivity_status": "confirmed",
            "conflict_status": "none",
            "source_scope": "single_source",
            "evidence_status": "source_supported",
            "lifecycle": "active",
            "origin_evidence_id": snapshot_id,
            "effective_at": detected_at,
            "effective_precision": "minute",
            "detected_at": detected_at,
            "evidence_snapshot": snapshot,
        }
        upsert = getattr(self.database, "_upsert_news_event_change_on", None)
        if not callable(upsert):  # pragma: no cover - current Database contract
            raise RuntimeError("news event projection is unavailable")
        row, _created, _membership_count, _became_default = upsert(
            conn,
            event_id=str(target["event_id"]),
            change=change,
            check_id=claim.check_id,
            detected_at=detected_at,
            evidence_completeness="complete",
        )
        return str(row["change_id"])

    @staticmethod
    def _insert_noise_diff(
        conn: sqlite3.Connection,
        *,
        monitor_id: str,
        check_id: str,
        from_snapshot_id: str,
        to_snapshot_id: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO web_monitor_diffs (
                diff_id, monitor_id, check_id, from_snapshot_id, to_snapshot_id,
                change_id, classification, summary, added_json, removed_json
            ) VALUES (?, ?, ?, ?, ?, NULL, 'noise_only', ?, '[]', '[]')
            """,
            (
                stable_uuid(
                    "newsclaw-web-monitor-diff",
                    monitor_id,
                    check_id,
                    from_snapshot_id,
                    to_snapshot_id,
                ),
                monitor_id,
                check_id,
                from_snapshot_id,
                to_snapshot_id,
                "页面结构或元数据变化，规范化正文未变化",
            ),
        )

    @staticmethod
    def _insert_meaningful_diff(
        conn: sqlite3.Connection,
        *,
        monitor_id: str,
        check_id: str,
        from_snapshot_id: str,
        to_snapshot_id: str,
        change_id: str,
        diff: DocumentDiff,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO web_monitor_diffs (
                diff_id, monitor_id, check_id, from_snapshot_id, to_snapshot_id,
                change_id, classification, summary, added_json, removed_json,
                evidence_quote
            ) VALUES (?, ?, ?, ?, ?, ?, 'meaningful', ?, ?, ?, ?)
            """,
            (
                stable_uuid(
                    "newsclaw-web-monitor-diff",
                    monitor_id,
                    check_id,
                    from_snapshot_id,
                    to_snapshot_id,
                ),
                monitor_id,
                check_id,
                from_snapshot_id,
                to_snapshot_id,
                change_id,
                diff.summary,
                json.dumps(diff.added, ensure_ascii=False),
                json.dumps(diff.removed, ensure_ascii=False),
                diff.evidence_quote,
            ),
        )

    @staticmethod
    def _insert_notification(
        conn: sqlite3.Connection,
        *,
        notification_key: str,
        monitor_id: str,
        event_id: str,
        change_id: str,
        check_id: str,
        kind: str,
        title: str,
        body: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO web_monitor_notifications (
                delivery_id, notification_key, monitor_id, event_id, change_id,
                check_id, kind, title, body, next_attempt_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stable_uuid("newsclaw-web-monitor-notification", notification_key),
                notification_key,
                monitor_id,
                event_id,
                change_id or None,
                check_id,
                kind,
                title[:1_000],
                body[:4_000],
                created_at,
                created_at,
            ),
        )

    def finish_failure(
        self,
        claim: ClaimedCheck,
        error: MonitorFetchError,
    ) -> dict[str, Any]:
        monitor_id = str(claim.monitor["monitor_id"])
        finished_at = utc_now_text()
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            target = self._fenced_target(conn, monitor_id, claim.lease_token)
            failures = int(target["consecutive_failures"] or 0) + 1
            retryable = error.retryable and failures < 6
            target_status = "retry_wait" if retryable else "needs_attention"
            delay = error.retry_after_seconds or _retry_delay_minutes(failures) * 60
            next_at = utc_now_text(datetime.now(UTC) + timedelta(seconds=delay))
            check_status = "retry_wait" if retryable else "failed"
            conn.execute(
                """
                UPDATE web_monitor_checks
                SET status = ?, http_status = ?, error_kind = ?, error_message = ?,
                    retryable = ?, finished_at = ?
                WHERE check_id = ? AND status = 'running' AND lease_token = ?
                """,
                (
                    check_status,
                    error.http_status,
                    error.kind,
                    error.safe_message,
                    int(retryable),
                    finished_at,
                    claim.check_id,
                    claim.lease_token,
                ),
            )
            conn.execute(
                """
                UPDATE web_monitor_targets
                SET status = ?, consecutive_failures = ?, last_error_kind = ?,
                    last_error = ?, next_check_at = ?, lease_owner = '',
                    lease_token = '', lease_until = '', state_version = state_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE monitor_id = ? AND lease_token = ?
                """,
                (
                    target_status,
                    failures,
                    error.kind,
                    error.safe_message,
                    next_at,
                    monitor_id,
                    claim.lease_token,
                ),
            )
            if not retryable and str(target["status"] or "") != "needs_attention":
                self._insert_notification(
                    conn,
                    notification_key=f"failure:{monitor_id}:{claim.check_id}",
                    monitor_id=monitor_id,
                    event_id=str(target["event_id"]),
                    change_id="",
                    check_id=claim.check_id,
                    kind="failure",
                    title="网页监控需要处理",
                    body=error.safe_message,
                    created_at=finished_at,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return self.check_result_envelope(monitor_id, claim.check_id)

    def cancel_claim(self, claim: ClaimedCheck) -> bool:
        """Fence and release one owned check without recording a fetch failure."""

        monitor_id = str(claim.monitor["monitor_id"])
        finished_at = utc_now_text()
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute(
                "SELECT monitor_id FROM web_monitor_targets "
                "WHERE monitor_id = ? AND lease_token = ? AND status != 'deleted'",
                (monitor_id, claim.lease_token),
            ).fetchone()
            if target is None:
                conn.commit()
                return False
            changed = conn.execute(
                "UPDATE web_monitor_checks SET status = 'cancelled', finished_at = ? "
                "WHERE check_id = ? AND monitor_id = ? AND status = 'running' "
                "AND lease_token = ?",
                (finished_at, claim.check_id, monitor_id, claim.lease_token),
            ).rowcount
            if changed:
                conn.execute(
                    "UPDATE web_monitor_targets SET lease_owner = '', lease_token = '', "
                    "lease_until = '', state_version = state_version + 1, "
                    "updated_at = CURRENT_TIMESTAMP "
                    "WHERE monitor_id = ? AND lease_token = ?",
                    (monitor_id, claim.lease_token),
                )
            conn.commit()
            return bool(changed)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def check_result(self, check_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM web_monitor_checks WHERE check_id = ?", (check_id,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def check_result_envelope(self, monitor_id: str, check_id: str) -> dict[str, Any]:
        """Return the same durable result shape for first answers and replays."""

        check = self.check_result(check_id)
        if check is None:
            raise RuntimeError("check result is missing")
        conn = self._conn()
        try:
            changed = conn.execute(
                "SELECT change_id FROM web_monitor_diffs "
                "WHERE check_id = ? AND change_id IS NOT NULL AND change_id != '' "
                "ORDER BY created_at DESC, diff_id DESC LIMIT 1",
                (check_id,),
            ).fetchone()
        finally:
            conn.close()
        safe_check = dict(check)
        safe_check.pop("lease_token", None)
        change_id = str(changed["change_id"] or "") if changed is not None else ""
        return {
            "check_id": str(check.get("check_id") or check_id),
            "status": str(check.get("status") or ""),
            "change_id": change_id,
            "snapshot_id": str(check.get("to_snapshot_id") or ""),
            "error_kind": str(check.get("error_kind") or ""),
            "error": str(check.get("error_message") or ""),
            "retryable": bool(check.get("retryable")),
            "check": safe_check,
            "monitor": self.get_monitor(monitor_id),
        }

    def history(self, monitor_id: str, *, limit: int = 50) -> dict[str, Any] | None:
        monitor = self.get_monitor(monitor_id)
        if monitor is None:
            return None
        conn = self._conn()
        try:
            checks = conn.execute(
                "SELECT * FROM web_monitor_checks WHERE monitor_id = ? "
                "ORDER BY started_at DESC, check_id DESC LIMIT ?",
                (monitor_id, max(1, min(200, limit))),
            ).fetchall()
            versions = conn.execute(
                "SELECT snapshot_id, ordinal, title, content_kind, raw_hash, semantic_hash, "
                "classification, suppression_reason, byte_count, fetched_at "
                "FROM web_monitor_snapshots WHERE monitor_id = ? "
                "ORDER BY ordinal DESC LIMIT ?",
                (monitor_id, max(1, min(200, limit))),
            ).fetchall()
            diffs = conn.execute(
                "SELECT * FROM web_monitor_diffs WHERE monitor_id = ? "
                "ORDER BY created_at DESC, diff_id DESC LIMIT ?",
                (monitor_id, max(1, min(200, limit))),
            ).fetchall()
        finally:
            conn.close()
        return {
            "monitor": monitor,
            "checks": [dict(row) for row in checks],
            "versions": [dict(row) for row in versions],
            "diffs": [_decode_diff(dict(row)) for row in diffs],
        }

    def notifications(self, *, include_read: bool = False, limit: int = 100) -> dict[str, Any]:
        clause = "" if include_read else "AND n.read_at = '' AND n.dismissed_at = ''"
        conn = self._conn()
        try:
            rows = conn.execute(
                f"""
                SELECT n.*, t.label, t.canonical_url
                FROM web_monitor_notifications n
                JOIN web_monitor_targets t ON t.monitor_id = n.monitor_id
                WHERE n.dismissed_at = '' {clause}
                ORDER BY n.created_at DESC, n.delivery_id DESC
                LIMIT ?
                """,
                (max(1, min(200, limit)),),
            ).fetchall()
            unread = conn.execute(
                "SELECT COUNT(*) AS count FROM web_monitor_notifications "
                "WHERE read_at = '' AND dismissed_at = ''"
            ).fetchone()
        finally:
            conn.close()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["display_url"] = redact_url(str(item.pop("canonical_url", "")))
            items.append(item)
        return {"items": items, "unread_count": int(unread["count"] if unread else 0)}

    def mark_notification(
        self,
        delivery_id: str,
        *,
        action: Literal["delivered", "read", "dismiss"],
        request_id: str,
    ) -> dict[str, Any] | None:
        payload_hash = _text_hash(
            json.dumps(
                {"delivery_id": delivery_id, "action": action},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        now = utc_now_text()
        if action == "delivered":
            sql = (
                "UPDATE web_monitor_notifications SET delivery_state = 'delivered', "
                "attempt_count = CASE WHEN delivery_state = 'pending' "
                "THEN attempt_count + 1 ELSE attempt_count END, "
                "delivered_at = CASE WHEN delivered_at = '' "
                "THEN ? ELSE delivered_at END, updated_at = CURRENT_TIMESTAMP "
                "WHERE delivery_id = ?"
            )
        elif action == "read":
            sql = (
                "UPDATE web_monitor_notifications SET delivery_state = 'delivered', "
                "delivered_at = CASE WHEN delivered_at = '' THEN ? ELSE delivered_at END, "
                "read_at = CASE WHEN read_at = '' THEN ? ELSE read_at END, "
                "updated_at = CURRENT_TIMESTAMP WHERE delivery_id = ?"
            )
        else:
            sql = (
                "UPDATE web_monitor_notifications SET dismissed_at = CASE WHEN dismissed_at = '' "
                "THEN ? ELSE dismissed_at END, updated_at = CURRENT_TIMESTAMP "
                "WHERE delivery_id = ?"
            )
        params: tuple[object, ...] = (
            (now, now, delivery_id) if action == "read" else (now, delivery_id)
        )
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            command = conn.execute(
                "SELECT * FROM web_monitor_commands WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if command is not None:
                if (
                    str(command["operation"]) != "notification_action"
                    or str(command["payload_hash"]) != payload_hash
                ):
                    raise ValueError("request_id 已用于另一条不同的操作")
                replay = conn.execute(
                    "SELECT * FROM web_monitor_notifications WHERE delivery_id = ?",
                    (delivery_id,),
                ).fetchone()
                conn.commit()
                return dict(replay) if replay is not None else None
            exists = conn.execute(
                "SELECT monitor_id FROM web_monitor_notifications WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if exists is None:
                conn.rollback()
                return None
            changed = conn.execute(sql, params).rowcount
            row = conn.execute(
                "SELECT * FROM web_monitor_notifications WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            conn.execute(
                "INSERT INTO web_monitor_commands "
                "(request_id, operation, payload_hash, monitor_id, result_json) "
                "VALUES (?, 'notification_action', ?, ?, ?)",
                (
                    request_id,
                    payload_hash,
                    str(exists["monitor_id"]),
                    json.dumps(
                        {"delivery_id": delivery_id, "action": action},
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return dict(row) if changed and row is not None else None

    def due_monitor_ids(self, *, limit: int) -> list[str]:
        now = utc_now_text()
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT monitor_id FROM web_monitor_targets
                WHERE status IN ('active', 'retry_wait')
                  AND next_check_at <= ?
                  AND (lease_until = '' OR lease_until <= ?)
                ORDER BY next_check_at, monitor_id LIMIT ?
                """,
                (now, now, max(1, min(50, limit))),
            ).fetchall()
        finally:
            conn.close()
        return [str(row["monitor_id"]) for row in rows]

    def runtime_state(self) -> dict[str, Any]:
        now = utc_now_text()
        conn = self._conn()
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                       SUM(CASE WHEN status = 'retry_wait' THEN 1 ELSE 0 END) AS retry_wait,
                       SUM(CASE WHEN status = 'needs_attention' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN lease_until > ? THEN 1 ELSE 0 END) AS running,
                       SUM(CASE WHEN status IN ('active', 'retry_wait')
                                 AND next_check_at <= ? THEN 1 ELSE 0 END) AS due
                FROM web_monitor_targets WHERE status != 'deleted'
                """,
                (now, now),
            ).fetchone()
            notices = conn.execute(
                "SELECT COUNT(*) AS unread FROM web_monitor_notifications "
                "WHERE read_at = '' AND dismissed_at = ''"
            ).fetchone()
        finally:
            conn.close()
        return {
            "total": int(row["total"] or 0) if row else 0,
            "active": int(row["active"] or 0) if row else 0,
            "retry_wait": int(row["retry_wait"] or 0) if row else 0,
            "failed": int(row["failed"] or 0) if row else 0,
            "running": int(row["running"] or 0) if row else 0,
            "due": int(row["due"] or 0) if row else 0,
            "unread_notifications": int(notices["unread"] or 0) if notices else 0,
        }


def _retry_delay_minutes(failures: int) -> int:
    schedule = (1, 5, 30, 120, 360)
    return schedule[min(max(failures - 1, 0), len(schedule) - 1)]


def _display_host(url: str) -> str:
    return (urlsplit(url).hostname or "网页监控")[:200]


def _decode_diff(item: dict[str, Any]) -> dict[str, Any]:
    for field in ("added_json", "removed_json"):
        try:
            item[field.removesuffix("_json")] = json.loads(str(item.pop(field, "[]")))
        except json.JSONDecodeError:
            item[field.removesuffix("_json")] = []
    return item


class WebMonitorService:
    """Independent scheduler and single-flight check executor."""

    def __init__(
        self,
        *,
        database: Any,
        fetcher: WebDocumentFetcher | None = None,
        poll_seconds: float = 15.0,
        max_concurrency: int = 3,
        max_per_host: int = 1,
    ) -> None:
        self.store = WebMonitorStore(database)
        self.fetcher = fetcher or WebDocumentFetcher()
        self.poll_seconds = max(1.0, poll_seconds)
        self.owner = str(uuid.uuid4())
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._max_per_host = max(1, max_per_host)
        self._host_semaphores: dict[str, asyncio.Semaphore] = {}
        self._stop = asyncio.Event()
        self._scheduler: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self._single_flight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._single_flight_requests: dict[str, str] = {}

    async def start(self) -> None:
        self._stop.clear()
        if self._scheduler is None or self._scheduler.done():
            self._scheduler = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._scheduler is not None and not self._scheduler.done():
            self._scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await self._scheduler
        if self._tasks:
            for task in tuple(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._single_flight.clear()
        self._single_flight_requests.clear()
        self._host_semaphores.clear()

    async def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                due = await asyncio.to_thread(self.store.due_monitor_ids, limit=10)
                for monitor_id in due:
                    self.schedule_check(
                        monitor_id,
                        request_id=f"scheduled:{monitor_id}:{utc_now_text()[:16]}",
                        manual=False,
                    )
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(min(self.poll_seconds, 5.0))

    def schedule_check(
        self, monitor_id: str, *, request_id: str, manual: bool
    ) -> asyncio.Task[dict[str, Any]]:
        existing = self._single_flight.get(monitor_id)
        if existing is not None and existing.done():
            existing = None
        primary_request_id = (
            self._single_flight_requests.get(monitor_id, request_id)
            if existing is not None
            else request_id
        )
        if existing is not None and not existing.done():
            joined = asyncio.create_task(
                self._reserved_join(
                    monitor_id,
                    request_id=request_id,
                    primary_request_id=primary_request_id,
                    existing=existing,
                )
            )
            self._tasks.add(joined)
            joined.add_done_callback(self._tasks.discard)
            return joined
        task = asyncio.create_task(
            self._reserved_primary(
                monitor_id,
                request_id=request_id,
                manual=manual,
            )
        )
        self._single_flight[monitor_id] = task
        self._single_flight_requests[monitor_id] = request_id
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        def _clear(completed: asyncio.Task[dict[str, Any]]) -> None:
            if self._single_flight.get(monitor_id) is completed:
                self._single_flight.pop(monitor_id, None)
                self._single_flight_requests.pop(monitor_id, None)

        task.add_done_callback(_clear)
        return task

    async def _reserved_primary(
        self, monitor_id: str, *, request_id: str, manual: bool
    ) -> dict[str, Any]:
        reserved_check_id = await asyncio.to_thread(
            self.store.reserve_check_request,
            monitor_id,
            request_id=request_id,
            primary_request_id=request_id,
        )
        if reserved_check_id:
            return await self._wait_for_check_result(monitor_id, reserved_check_id)
        return await self.check_monitor(monitor_id, request_id=request_id, manual=manual)

    async def _reserved_join(
        self,
        monitor_id: str,
        *,
        request_id: str,
        primary_request_id: str,
        existing: asyncio.Task[dict[str, Any]],
    ) -> dict[str, Any]:
        reserved_check_id = await asyncio.to_thread(
            self.store.reserve_check_request,
            monitor_id,
            request_id=request_id,
            primary_request_id=primary_request_id,
        )
        if reserved_check_id:
            return await self._wait_for_check_result(monitor_id, reserved_check_id)
        return await asyncio.shield(existing)

    async def _wait_for_check_result(self, monitor_id: str, check_id: str) -> dict[str, Any]:
        for _ in range(240):
            result = await asyncio.to_thread(self.store.check_result, check_id)
            if result is not None and str(result.get("status")) != "running":
                return await asyncio.to_thread(
                    self.store.check_result_envelope, monitor_id, check_id
                )
            await asyncio.sleep(0.25)
        return await asyncio.to_thread(self.store.check_result_envelope, monitor_id, check_id)

    async def check_monitor(
        self, monitor_id: str, *, request_id: str, manual: bool = True
    ) -> dict[str, Any]:
        monitor = await asyncio.to_thread(self.store.get_monitor, monitor_id)
        if monitor is None:
            raise KeyError(monitor_id)
        host = (urlsplit(str(monitor["canonical_url"])).hostname or monitor_id).lower()
        host_semaphore = self._host_semaphores.setdefault(
            host, asyncio.Semaphore(self._max_per_host)
        )
        async with host_semaphore, self._semaphore:
            claim_task = asyncio.create_task(
                asyncio.to_thread(
                    self.store.claim_check,
                    monitor_id,
                    request_id=request_id,
                    owner=self.owner,
                    manual=manual,
                )
            )
            try:
                claim = await asyncio.shield(claim_task)
            except asyncio.CancelledError:
                claim = None
                with suppress(Exception):
                    claim = await claim_task
                if claim is not None and not claim.joined:
                    await self._cancel_claim(claim)
                raise
            if claim is None:
                raise KeyError(monitor_id)
            if claim.joined:
                return await self._wait_for_check_result(monitor_id, claim.check_id)
            monitor = claim.monitor
            current_normalizer = str(monitor.get("current_normalizer_version") or "")
            validators_are_current = (
                not bool(monitor.get("has_baseline")) or current_normalizer == NORMALIZER_VERSION
            )
            try:
                try:
                    fetched = await self.fetcher.fetch(
                        url=str(monitor["canonical_url"]),
                        kind=cast("MonitorKind", str(monitor["content_kind"])),
                        etag=str(monitor["etag"] or "") if validators_are_current else "",
                        last_modified=(
                            str(monitor["last_modified"] or "") if validators_are_current else ""
                        ),
                    )
                except MonitorFetchError as error:
                    return await self._finish_claim(self.store.finish_failure, claim, error)
                except ValueError as error:
                    safe = MonitorFetchError("unsafe_url", str(error), retryable=False)
                    return await self._finish_claim(self.store.finish_failure, claim, safe)
                if fetched.not_modified and (
                    not bool(monitor.get("has_baseline")) or not validators_are_current
                ):
                    safe = MonitorFetchError(
                        "unexpected_not_modified",
                        ("当前正文基线需要重新建立，不能接受服务器返回的 304 未变化响应"),
                        retryable=False,
                    )
                    return await self._finish_claim(self.store.finish_failure, claim, safe)
                return await self._finish_claim(self.store.finish_success, claim, fetched)
            except asyncio.CancelledError:
                await self._cancel_claim(claim)
                raise
            except Exception as error:
                # A provider/parser bug must not strand a durable check in
                # ``running``.  Persist only a bounded public message; the
                # concrete exception type remains available in local logs for
                # diagnosis without leaking credentials or response bodies to
                # the UI/notification channel.
                logger.error(
                    "Unexpected web monitor failure (%s)",
                    type(error).__name__,
                )
                safe = MonitorFetchError(
                    "internal_monitor_error",
                    "网页或 PDF 检查暂时失败",
                    retryable=True,
                )
                return await self._finish_claim(self.store.finish_failure, claim, safe)

    async def _finish_claim(self, function: Any, *args: Any) -> dict[str, Any]:
        terminal = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(terminal)
        except asyncio.CancelledError:
            with suppress(Exception):
                await terminal
            raise
        except RuntimeError as error:
            claim = args[0] if args and isinstance(args[0], ClaimedCheck) else None
            if str(error) != "monitor lease is no longer valid" or claim is None:
                raise
            return await asyncio.to_thread(
                self.store.check_result_envelope,
                str(claim.monitor["monitor_id"]),
                claim.check_id,
            )

    async def _cancel_claim(self, claim: ClaimedCheck) -> None:
        cancelled = asyncio.create_task(asyncio.to_thread(self.store.cancel_claim, claim))
        with suppress(Exception):
            await asyncio.shield(cancelled)

    def runtime_state(self) -> dict[str, Any]:
        state = self.store.runtime_state()
        state.update(
            {
                "scheduler_running": bool(self._scheduler and not self._scheduler.done()),
                "owner": self.owner,
                "normalizer_version": NORMALIZER_VERSION,
            }
        )
        return state
