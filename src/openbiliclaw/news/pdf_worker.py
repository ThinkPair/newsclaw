"""Bounded child-process entrypoint for untrusted PDF text extraction."""

from __future__ import annotations

import json
import sys
from typing import Any

from openbiliclaw.news.web_monitoring import (
    MAX_PDF_BYTES,
    MAX_TEXT_CHARS,
    MonitorFetchError,
    _extract_pdf_sync,
)


def _result(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_PDF_BYTES:
        return {
            "ok": False,
            "kind": "document_too_large",
            "message": "PDF 超过首版大小上限",
            "retryable": False,
        }
    try:
        title, text = _extract_pdf_sync(data)
    except MonitorFetchError as error:
        return {
            "ok": False,
            "kind": error.kind,
            "message": error.safe_message,
            "retryable": error.retryable,
        }
    except Exception:
        return {
            "ok": False,
            "kind": "pdf_parse_failed",
            "message": "PDF 正文解析失败",
            "retryable": False,
        }
    if len(text) > MAX_TEXT_CHARS:
        return {
            "ok": False,
            "kind": "text_too_large",
            "message": "PDF 可读正文超过 10MB 上限",
            "retryable": False,
        }
    return {"ok": True, "title": title[:1_000], "text": text}


def main() -> int:
    data = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    payload = json.dumps(_result(data), ensure_ascii=False, separators=(",", ":"))
    sys.stdout.buffer.write(payload.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
