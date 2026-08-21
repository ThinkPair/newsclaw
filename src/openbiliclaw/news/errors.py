"""Stable provider error taxonomy used by health and retry policies."""

from __future__ import annotations

import re

_SECRET_RE = re.compile(
    r"(?i)(authorization|bearer|api[_-]?key|token|secret|password)\s*[:=]?\s*[^\s,;]+"
)


def safe_error_message(value: object) -> str:
    """Bound and redact an upstream message before persistence or display."""

    text = " ".join(str(value or "").split())
    return _SECRET_RE.sub(r"\1=[REDACTED]", text)[:240]


class NewsSourceError(RuntimeError):
    """Base class for UI-safe news source failures."""

    code = "source_error"
    retryable = False

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(safe_error_message(message))
        self.retry_after_seconds = retry_after_seconds


class SourcePermissionDenied(NewsSourceError):  # noqa: N818 - public domain contract
    """The configured account has no access to this endpoint."""

    code = "permission_denied"


class SourceRateLimited(NewsSourceError):  # noqa: N818 - public domain contract
    """The provider temporarily rejected the request frequency."""

    code = "rate_limited"
    retryable = True


class SourceTimedOut(NewsSourceError):  # noqa: N818 - public domain contract
    """The provider did not finish within the bounded request time."""

    code = "timed_out"
    retryable = True


class SourceTransientError(NewsSourceError):
    """A temporary upstream/network condition that may be retried."""

    code = "transient_error"
    retryable = True


class SourceSchemaError(NewsSourceError):
    """The provider returned an unsupported or malformed envelope."""

    code = "schema_error"


class SourceLeaseBusy(NewsSourceError):  # noqa: N818 - public domain contract
    """Another worker still owns the durable source-task lease."""

    code = "lease_busy"
    retryable = True


def classify_provider_exception(error: Exception) -> NewsSourceError:
    """Translate common Tushare/HTTP exception wording without leaking secrets."""

    if isinstance(error, NewsSourceError):
        return error
    message = safe_error_message(error) or type(error).__name__
    folded = message.casefold()
    if isinstance(error, TimeoutError) or any(word in folded for word in ("timeout", "timed out")):
        return SourceTimedOut("News source request timed out")
    if any(
        word in folded
        for word in ("permission", "unauthorized", "forbidden", "no access", "权限", "积分不足")
    ):
        return SourcePermissionDenied(message)
    if any(word in folded for word in ("429", "rate limit", "too many", "频率", "限流")):
        return SourceRateLimited(message)
    if any(word in folded for word in ("connection", "temporarily", "502", "503", "504")):
        return SourceTransientError(message)
    return SourceSchemaError(message)
