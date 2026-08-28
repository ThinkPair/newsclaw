"""Deterministic evidence-depth checks shared by tracking and storage."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Mapping
from difflib import SequenceMatcher

_SCOPES = frozenset({"unknown", "title_only", "summary", "full_text"})
_HTML_RE = re.compile(r"<[^>]+>")
_BOILERPLATE = (
    "点击查看全文",
    "点击阅读全文",
    "查看全文",
    "阅读全文",
    "查看原文",
    "来源",
    "转载",
    "免责声明",
    "暂无正文",
)


def _plain_text(value: object) -> str:
    text = html.unescape(_HTML_RE.sub(" ", str(value or "")))
    return " ".join(unicodedata.normalize("NFKC", text).split()).strip().casefold()


def _compact(value: object) -> str:
    return "".join(character for character in _plain_text(value) if character.isalnum())


def _substantive(value: object, *, title: object, minimum: int) -> bool:
    compact = _compact(value)
    for marker in _BOILERPLATE:
        compact = compact.replace(_compact(marker), "")
    if len(compact) < minimum:
        return False
    compact_title = _compact(title)
    if not compact_title:
        return True
    # Feed extracts sometimes repeat the headline two or more times and label
    # the result as body text.  Removing every exact normalized headline copy
    # makes that periodic repetition auditable as title-only while preserving
    # any genuinely substantive remainder.
    if compact_title in compact:
        without_title_repetitions = compact.replace(compact_title, "")
        if len(without_title_repetitions) < minimum:
            return False
    similarity = SequenceMatcher(None, compact, compact_title, autojunk=False).ratio()
    return not (
        compact == compact_title
        or (similarity >= 0.90 and len(compact) <= len(compact_title) + 6)
    )


def verified_content_scope(
    declared_scope: object,
    snapshot: object,
    *,
    title: object = "",
    derive_when_unknown: bool = False,
) -> str:
    """Return declared depth capped by locally retained source text."""

    declared = str(declared_scope or "unknown").strip().lower()
    if declared not in _SCOPES:
        raise ValueError("unsupported news event content scope")
    if isinstance(snapshot, Mapping):
        payload = dict(snapshot)
    else:
        try:
            decoded = json.loads(str(snapshot or "{}"))
        except (TypeError, ValueError):
            decoded = {}
        payload = dict(decoded) if isinstance(decoded, Mapping) else {}
    evidence_title = payload.get("title") or title
    body = payload.get("body_text") or payload.get("body") or payload.get("content")
    description = payload.get("description")
    if _substantive(body, title=evidence_title, minimum=24):
        retained = "full_text"
    elif _substantive(description, title=evidence_title, minimum=10) or _substantive(
        body,
        title=evidence_title,
        minimum=10,
    ):
        retained = "summary"
    else:
        retained = "title_only"
    if declared == "unknown":
        return retained if derive_when_unknown else "unknown"
    depth = {"unknown": 0, "title_only": 1, "summary": 2, "full_text": 3}
    return min((declared, retained), key=depth.__getitem__)
