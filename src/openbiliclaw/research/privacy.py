"""Input guards that keep obvious private data away from external research."""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from openbiliclaw.research.errors import ResearchValidationError

_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?i)(?:^|\s)[A-Z]:[\\/]"),
    re.compile(r"(?:^|\s)(?:/Users/|/home/|/private/|~/|\\\\)"),
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:sk|tvly|ghp|github_pat)-[A-Z0-9_-]{10,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|secret)\s*[:=]\s*\S{8,}"),
    re.compile(r"(?i)\bBearer\s+[A-Z0-9._~-]{12,}\b"),
)
_HTTP_URL_PATTERN = re.compile(r"(?i)https?://[^\s<>'\"]+")
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "credential",
    "key",
    "passwd",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
    "x_amz_credential",
    "x_amz_signature",
    "x_goog_credential",
    "x_goog_signature",
}


def normalize_query(value: str) -> str:
    """Normalize and privacy-check a query before any network request."""

    query = " ".join(value.split()).strip()
    if not query:
        raise ResearchValidationError("研究问题不能为空")
    if len(query) > 1_000:
        raise ResearchValidationError("研究问题不能超过 1000 个字符")
    if _EMAIL_PATTERN.search(query):
        raise ResearchValidationError("研究问题疑似包含邮箱，请先删除私人资料")
    if _PHONE_PATTERN.search(query):
        raise ResearchValidationError("研究问题疑似包含手机号，请先删除私人资料")
    if any(pattern.search(query) for pattern in _LOCAL_PATH_PATTERNS):
        raise ResearchValidationError("研究问题疑似包含本机路径，请先删除私人资料")
    if any(pattern.search(query) for pattern in _SECRET_PATTERNS):
        raise ResearchValidationError("研究问题疑似包含密钥或令牌，请先删除敏感内容")
    for matched_url in _HTTP_URL_PATTERN.findall(query):
        normalize_public_url(matched_url.rstrip(".,;:!?，。；：！？)】"))
    return query


def normalize_public_url(value: str) -> str:
    """Accept only a public-looking HTTP(S) URL and remove its fragment."""

    candidate = value.strip()
    if len(candidate) > 4_096:
        raise ResearchValidationError("URL 过长")
    decoded_candidate = unquote(candidate)
    if _EMAIL_PATTERN.search(decoded_candidate) or _PHONE_PATTERN.search(decoded_candidate):
        raise ResearchValidationError("URL 疑似包含私人联系方式，请先删除私人资料")
    if any(pattern.search(decoded_candidate) for pattern in _SECRET_PATTERNS):
        raise ResearchValidationError("URL 疑似包含密钥或令牌，请先删除敏感内容")
    if re.search(
        r"(?i)(?:(?<![A-Z])[A-Z]:[\\/]|(?:^|[?&=])(?:/Users/|/home/|/private/|~/|\\\\))",
        decoded_candidate,
    ):
        raise ResearchValidationError("URL 疑似包含本机路径，请先删除私人资料")
    try:
        parsed = urlsplit(candidate)
        hostname_value = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ResearchValidationError("URL 格式无效") from error
    if parsed.scheme.casefold() not in {"http", "https"} or not hostname_value:
        raise ResearchValidationError("只允许公开的 http(s) URL")
    if parsed.username or parsed.password:
        raise ResearchValidationError("URL 不能包含账号或凭据")
    try:
        query_fields = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)
    except ValueError as error:
        raise ResearchValidationError("URL 查询参数格式无效") from error
    for name, _value in query_fields:
        normalized_name = name.strip().casefold().replace("-", "_")
        if normalized_name in _SENSITIVE_QUERY_KEYS:
            raise ResearchValidationError("URL 查询参数疑似包含凭据，请先删除敏感内容")

    hostname = hostname_value.rstrip(".").casefold()
    if (
        hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
        or "." not in hostname
    ):
        raise ResearchValidationError("不允许 localhost、内网或本机 URL")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ResearchValidationError("不允许 localhost、内网或本机 URL")

    host = hostname
    if ":" in hostname:
        host = f"[{hostname}]"
    if port is not None and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, parsed.query, ""))


def is_public_url(value: str) -> bool:
    """Return false rather than raising when filtering provider responses."""

    try:
        normalize_public_url(value)
    except (ResearchValidationError, ValueError):
        return False
    return True
