"""Build the first structured display-tag contract for NewsClaw cards.

The values already exist in the recommendation/cache pipeline.  This module
only projects them into a small, deterministic API shape; it never calls an
LLM or invents a category from article text.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any, Literal, TypedDict

DisplayTagType = Literal["topic", "story", "temporal", "evidence"]
PriorityLevel = Literal["high", "medium", "normal", "low"]

# Decision-priority calibration v2.  These weights deliberately make user fit
# the largest signal without allowing a high-fit, stale, title-only item to
# outrank a well-grounded current item.  V2 raises the display bands after a
# real inventory check showed that full-text current news otherwise made every
# visible row "high", erasing the label's decision value.  Keep the version in
# the persisted snapshot when changing any value below.
DECISION_PRIORITY_VERSION = "decision-priority-v2"
_DECISION_PRIORITY_WEIGHTS = {
    "match": 0.45,
    "evidence": 0.20,
    "timeliness": 0.20,
    "impact": 0.15,
}
_DECISION_PRIORITY_THRESHOLDS: tuple[tuple[float, PriorityLevel, str], ...] = (
    (0.90, "high", "优先关注"),
    (0.88, "medium", "值得关注"),
    (0.78, "normal", "常规参考"),
)

_CONTENT_SCOPES = frozenset({"title_only", "summary", "full_text"})
_FRESHNESS_VALUES = frozenset({"fresh", "aging", "stale", "future", "unknown"})
_TEMPORAL_CLASSES = frozenset(
    {"breaking", "current", "versioned", "evergreen", "historical", "unknown"}
)
_TEMPORAL_STATES = frozenset({"unknown", "active", "expired", "superseded"})

_FRESHNESS_LABELS = {
    "fresh": "新鲜资讯",
    "aging": "近期资讯",
    "stale": "旧闻",
    "future": "发布时间待核",
}
_TEMPORAL_CLASS_LABELS = {
    "breaking": "突发",
    "current": "近期进展",
    "versioned": "版本相关",
    "evergreen": "长期有效",
    "historical": "历史回顾",
}
_TEMPORAL_STATE_LABELS = {
    "active": "仍有效",
    "expired": "已过期",
    "superseded": "已替代",
}
_EVIDENCE_LABELS = {
    "title_only": "标题级",
    "summary": "摘要级",
    "full_text": "全文级",
}
_ANALYSIS_SECTION_PATTERN = re.compile(r"(?P<section>为什么适合你|可能影响链|后续观察点)\s*[：:]")
_ANALYSIS_FIELD_NAMES = {
    "为什么适合你": "why_recommended",
    "可能影响链": "impact_chain",
    "后续观察点": "observation_points",
}
_ANALYSIS_SECTION_ORDER = tuple(_ANALYSIS_FIELD_NAMES)


class DisplayTag(TypedDict):
    """One compact display tag consumed by the desktop and mobile clients."""

    type: DisplayTagType
    label: str
    origin: str


def _value(content: object, name: str) -> object:
    if isinstance(content, Mapping):
        return content.get(name)
    return getattr(content, name, None)


def _clean_text(value: object, *, maximum: int = 128) -> str:
    if value is None or isinstance(value, Mapping):
        return ""
    if isinstance(value, Sequence) and not isinstance(value, str):
        return ""
    return " ".join(str(value).split()).strip()[:maximum]


def _mapping(value: object) -> Mapping[str, Any]:
    decoded = value
    if isinstance(value, str):
        with suppress(TypeError, ValueError):
            decoded = json.loads(value)
    return decoded if isinstance(decoded, Mapping) else {}


def _raw_payload(content: object) -> Mapping[str, Any]:
    return _mapping(_value(content, "raw_payload"))


def _tags(content: object, payload: Mapping[str, Any]) -> list[str]:
    raw_tags = _value(content, "tags")
    if raw_tags in (None, ""):
        raw_tags = payload.get("tags", [])
    decoded = raw_tags
    if isinstance(raw_tags, str):
        with suppress(TypeError, ValueError):
            decoded = json.loads(raw_tags)
    if not isinstance(decoded, Sequence) or isinstance(decoded, str | bytes | bytearray):
        return []
    return [text for value in decoded if (text := _clean_text(value))]


def _field(content: object, payload: Mapping[str, Any], name: str) -> str:
    return _clean_text(_value(content, name)) or _clean_text(payload.get(name))


def _enum_field(
    content: object,
    payload: Mapping[str, Any],
    tags: Sequence[str],
    name: str,
    allowed: frozenset[str],
    *,
    default: str = "",
) -> str:
    for value in (_value(content, name), payload.get(name)):
        normalized = _clean_text(value).casefold()
        if normalized in allowed and normalized != default:
            return normalized
    for tag in tags:
        normalized = tag.casefold()
        if normalized in allowed and normalized != default:
            return normalized
    return default


def _is_news(content: object, payload: Mapping[str, Any]) -> bool:
    source_platform = _clean_text(_value(content, "source_platform")).casefold()
    payload_platform = _clean_text(payload.get("source_platform")).casefold()
    explicit_platform = source_platform or payload_platform
    if explicit_platform:
        # A canonical platform is authoritative.  Legacy article IDs and
        # source-strategy strings must never turn an explicit Reddit, Zhihu,
        # Weibo, or other social row into NewsClaw on the wire.
        return explicit_platform == "news"

    source_strategy = (
        _clean_text(_value(content, "source_strategy"))
        or _clean_text(_value(content, "source"))
        or _clean_text(payload.get("source_strategy"))
    ).casefold()
    return bool(
        source_strategy.startswith("news-")
        or _clean_text(_value(content, "article_id"))
        or _clean_text(payload.get("article_id"))
    )


def is_news_content(content: object) -> bool:
    """Return whether ``content`` belongs to the canonical NewsClaw source.

    An explicit canonical platform always wins over legacy article IDs and
    source-strategy strings.  API alias, tag, analysis, and priority projection
    all share this predicate so a Reddit/Zhihu/Weibo row cannot accidentally
    acquire NewsClaw-only fields.
    """

    return _is_news(content, _raw_payload(content))


def parse_news_expression(expression: object) -> dict[str, str]:
    """Parse the three fixed NewsClaw analysis sections into API fields.

    Older and non-conforming recommendation copy remains valid: when all three
    headings are not present exactly once and in the product-defined order, no
    structured projection is emitted and clients can keep using ``expression``.
    """

    if expression is None or isinstance(expression, Mapping):
        return {}
    text = str(expression).strip()
    if not text:
        return {}
    matches = list(_ANALYSIS_SECTION_PATTERN.finditer(text))
    if [match.group("section") for match in matches] != list(_ANALYSIS_SECTION_ORDER):
        return {}

    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        value_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = " ".join(text[match.end() : value_end].split()).strip()
        if value:
            fields[_ANALYSIS_FIELD_NAMES[match.group("section")]] = value
    return fields


def _optional_bounded_score(value: object | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(str(value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score):
        return None
    return max(0.0, min(1.0, score))


def _first_explicit_score(
    content: object,
    payload: Mapping[str, Any],
    names: Sequence[str],
) -> float | None:
    for name in names:
        for value in (_value(content, name), payload.get(name)):
            if value is None or isinstance(value, bool):
                continue
            try:
                score = float(str(value))
            except (TypeError, ValueError):
                continue
            if math.isfinite(score):
                return max(0.0, min(1.0, score))
    return None


def _decision_priority_fields(
    content: object,
    *,
    confidence: object | None,
    analysis: Mapping[str, str],
    tags: Mapping[str, object],
) -> dict[str, object]:
    """Build an explainable decision priority rather than a fit-score alias."""

    payload = _raw_payload(content)
    match_score = _optional_bounded_score(confidence)
    if match_score is None:
        # Preserve the older optional-field contract: rows without a valid
        # personalization score do not pretend to have a decision priority.
        return {}

    content_scope = str(tags.get("content_scope") or "")
    evidence_score = {
        "full_text": 1.0,
        "summary": 0.70,
        "title_only": 0.30,
    }.get(content_scope, 0.25)

    freshness = str(tags.get("freshness") or "unknown")
    temporal_class = str(tags.get("temporal_class") or "unknown")
    temporal_state = str(tags.get("temporal_state") or "unknown")
    freshness_score = {
        "fresh": 1.0,
        "aging": 0.72,
        "stale": 0.18,
        "future": 0.10,
        "unknown": 0.50,
    }.get(freshness, 0.50)
    class_score = {
        "breaking": 1.0,
        "current": 0.85,
        "versioned": 0.70,
        "evergreen": 0.62,
        "historical": 0.35,
        "unknown": 0.50,
    }.get(temporal_class, 0.50)
    timeliness_score = 0.65 * freshness_score + 0.35 * class_score
    if temporal_state in {"expired", "superseded"}:
        timeliness_score = 0.0
    elif temporal_state == "active" and (
        freshness in {"fresh", "aging"} or temporal_class in {"breaking", "current", "versioned"}
    ):
        timeliness_score = max(timeliness_score, 0.80)

    explicit_impact = _first_explicit_score(
        content,
        payload,
        ("importance_score", "impact_score", "urgency_score", "significance_score"),
    )
    if explicit_impact is not None:
        impact_score = explicit_impact
    else:
        # A structured impact chain is evidence that impact was considered, not
        # proof that the event is important; keep the fallback deliberately
        # neutral and let explicit upstream signals carry stronger weight.
        impact_score = 0.55 if analysis.get("impact_chain") else 0.40
        if temporal_class == "breaking":
            impact_score = max(impact_score, 0.75)
        elif temporal_class == "current":
            impact_score = max(impact_score, 0.60)

    signals = {
        "match": round(match_score, 4),
        "evidence": round(evidence_score, 4),
        "timeliness": round(timeliness_score, 4),
        "impact": round(impact_score, 4),
    }
    score = round(
        sum(signals[name] * weight for name, weight in _DECISION_PRIORITY_WEIGHTS.items()),
        4,
    )

    level: PriorityLevel = "low"
    label = "探索观察"
    for threshold, candidate_level, candidate_label in _DECISION_PRIORITY_THRESHOLDS:
        if score >= threshold:
            level = candidate_level
            label = candidate_label
            break

    evidence_label = _EVIDENCE_LABELS.get(content_scope, "证据待补")
    temporal_label = (
        _TEMPORAL_STATE_LABELS.get(temporal_state)
        or _TEMPORAL_CLASS_LABELS.get(temporal_class)
        or _FRESHNESS_LABELS.get(freshness)
        or "时效待核"
    )
    return {
        "priority_level": level,
        "priority_label": label,
        "priority_score": score,
        "priority_signals": signals,
        "priority_reason": (
            f"匹配{match_score:.0%} · {evidence_label} · {temporal_label} · "
            f"影响信号{impact_score:.0%}"
        ),
        "priority_version": DECISION_PRIORITY_VERSION,
    }


def _temporal_tag(
    *,
    freshness: str,
    temporal_class: str,
    temporal_state: str,
) -> DisplayTag | None:
    # A terminal state is more important than the ingestion-time freshness
    # label.  Normal recommendation serving filters these rows, but preserving
    # the precedence keeps the projection truthful for compatibility callers.
    if temporal_state in {"expired", "superseded"}:
        return {
            "type": "temporal",
            "label": _TEMPORAL_STATE_LABELS[temporal_state],
            "origin": "model",
        }
    if temporal_class in _TEMPORAL_CLASS_LABELS:
        return {
            "type": "temporal",
            "label": _TEMPORAL_CLASS_LABELS[temporal_class],
            "origin": "model",
        }
    if freshness in _FRESHNESS_LABELS:
        return {
            "type": "temporal",
            "label": _FRESHNESS_LABELS[freshness],
            "origin": "rule",
        }
    if temporal_state == "active":
        return {
            "type": "temporal",
            "label": _TEMPORAL_STATE_LABELS[temporal_state],
            "origin": "model",
        }
    return None


def _dedupe_display_tags(tags: Sequence[DisplayTag]) -> list[DisplayTag]:
    result: list[DisplayTag] = []
    seen: set[str] = set()
    for tag in tags:
        identity = "".join(tag["label"].casefold().split())
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(tag)
        if len(result) == 4:
            break
    return result


def build_recommendation_tag_fields(content: object) -> dict[str, object]:
    """Return additive NewsClaw tag fields for one recommendation source.

    Non-news content intentionally returns an empty mapping, so existing video
    and social clients retain their previous on-wire payload byte shape.
    """

    payload = _raw_payload(content)
    if not _is_news(content, payload):
        return {}

    tags = _tags(content, payload)
    topic_group = _field(content, payload, "topic_group")
    franchise_key = _field(content, payload, "franchise_key")
    style_key = _field(content, payload, "style_key")
    content_scope = _enum_field(
        content,
        payload,
        tags,
        "content_scope",
        _CONTENT_SCOPES,
    )
    freshness = _enum_field(
        content,
        payload,
        tags,
        "freshness",
        _FRESHNESS_VALUES,
        default="unknown",
    )
    temporal_class = _enum_field(
        content,
        payload,
        (),
        "temporal_class",
        _TEMPORAL_CLASSES,
        default="unknown",
    )
    temporal_state = _enum_field(
        content,
        payload,
        (),
        "temporal_state",
        _TEMPORAL_STATES,
        default="unknown",
    )

    display_tags: list[DisplayTag] = []
    if topic_group:
        display_tags.append({"type": "topic", "label": topic_group, "origin": "model"})
    if franchise_key:
        display_tags.append({"type": "story", "label": franchise_key, "origin": "model"})
    temporal = _temporal_tag(
        freshness=freshness,
        temporal_class=temporal_class,
        temporal_state=temporal_state,
    )
    if temporal is not None:
        display_tags.append(temporal)
    if content_scope:
        display_tags.append(
            {
                "type": "evidence",
                "label": _EVIDENCE_LABELS[content_scope],
                "origin": "source",
            }
        )

    return {
        "topic_group": topic_group,
        "franchise_key": franchise_key,
        "style_key": style_key,
        "content_scope": content_scope,
        "freshness": freshness,
        "temporal_class": temporal_class,
        "temporal_state": temporal_state,
        "display_tags": _dedupe_display_tags(display_tags),
    }


def build_recommendation_presentation_fields(
    content: object,
    *,
    expression: object = "",
    confidence: object | None = None,
) -> dict[str, object]:
    """Build the complete additive NewsClaw recommendation projection.

    Tags, structured analysis, and priority deliberately share one news gate.
    This gives recommendation history, live batches, and proactive delights the
    same wire contract while leaving explicit non-news payloads unchanged.
    """

    if not is_news_content(content):
        return {}
    tag_fields = build_recommendation_tag_fields(content)
    analysis_fields = parse_news_expression(expression)
    return {
        **tag_fields,
        **analysis_fields,
        **_decision_priority_fields(
            content,
            confidence=confidence,
            analysis=analysis_fields,
            tags=tag_fields,
        ),
    }
