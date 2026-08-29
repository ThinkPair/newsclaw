"""Isolated, deterministic demo server for the NewsClaw event workspace.

The server deliberately does not import the application database, load config.toml,
read environment credentials, or start any scheduler.  It serves the real desktop
assets against an in-memory event API so demos cannot contaminate a user's local
history.  Two event stories replay public historical sources; the conflict story is
labelled as a synthetic demo sample in every visible title and source.
"""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import re
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

if __package__:
    from scripts.chrome_webstore_demo import demo_payload as legacy_demo_payload
else:
    from chrome_webstore_demo import demo_payload as legacy_demo_payload

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8431
DEMO_STATUS_LABEL = "演示环境 · 正式库未连接"

LPR_EVENT_ID = "demo-event-lpr-2024"
RRR_EVENT_ID = "demo-event-rrr-2024"
CONFLICT_EVENT_ID = "demo-event-conflict-sample"


def _change(
    *,
    change_id: str,
    event_id: str,
    title: str,
    topic: str,
    previous_state: str,
    new_state: str,
    effective_at: str,
    detected_at: str,
    evidence_count: int,
    lifecycle: str = "active",
    is_state_applied: bool = True,
    is_read: bool = False,
    supersedes_change_id: str | None = None,
    objectivity_status: str = "confirmed",
    conflict_status: str = "none",
    source_scope: str = "single_source",
    evidence_status: str = "source_supported",
) -> dict[str, Any]:
    return {
        "change_id": change_id,
        "event_id": event_id,
        "canonical_title": title,
        "topic_group": topic,
        "story_key": f"demo:{event_id}",
        "change_type": "state_transition",
        "previous_state": previous_state,
        "new_state": new_state,
        "fact": f"{previous_state} → {new_state}",
        "summary": f"客观状态由“{previous_state}”变为“{new_state}”。",
        "evidence_quote": "",
        "role": "objective_change",
        "objectivity_status": objectivity_status,
        "conflict_status": conflict_status,
        "source_scope": source_scope,
        "evidence_status": evidence_status,
        "lifecycle": lifecycle,
        "is_state_applied": is_state_applied,
        "supersedes_change_id": supersedes_change_id,
        "supersedes_lifecycle": "superseded",
        "can_mark_read": lifecycle == "active" and is_state_applied,
        "is_read": is_read,
        "effective_at": effective_at,
        "effective_precision": "minute",
        "detected_at": detected_at,
        "evidence_count": evidence_count,
    }


def _evidence(
    *,
    membership_id: str,
    event_id: str,
    change_id: str,
    media: str,
    provider: str,
    title: str,
    url: str,
    published_at: str,
    body_text: str,
    relation_kind: str = "supports_change",
    content_scope: str = "full_text",
) -> dict[str, Any]:
    return {
        "membership_id": membership_id,
        "event_id": event_id,
        "change_id": change_id,
        "evidence_item_key": f"demo:{membership_id}",
        "relation_kind": relation_kind,
        "relation_basis": "",
        "relation_score": 1.0,
        "content_scope": content_scope,
        "media": media,
        "provider": provider,
        "title": title,
        "canonical_url": url,
        "published_at": published_at,
        "retrieved_at": published_at,
        "evidence_snapshot": {"body_text": body_text},
        "evidence_completeness": "complete",
        "first_seen_at": published_at,
        "last_seen_at": published_at,
    }


def _demo_data() -> tuple[
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    lpr_title = "贷款市场报价利率（LPR）调整（真实公开材料回放）"
    lpr_topic = "货币政策"
    lpr_feb = _change(
        change_id="demo-change-lpr-2024-02",
        event_id=LPR_EVENT_ID,
        title=lpr_title,
        topic=lpr_topic,
        previous_state="1年期 3.45%；5年期以上 4.20%",
        new_state="1年期 3.45%；5年期以上 3.95%",
        effective_at="2024-02-20T09:15:00+08:00",
        detected_at="2024-02-20T09:16:00+08:00",
        evidence_count=1,
        lifecycle="superseded",
        is_state_applied=False,
        is_read=True,
    )
    lpr_july = _change(
        change_id="demo-change-lpr-2024-07",
        event_id=LPR_EVENT_ID,
        title=lpr_title,
        topic=lpr_topic,
        previous_state="1年期 3.45%；5年期以上 3.95%",
        new_state="1年期 3.35%；5年期以上 3.85%",
        effective_at="2024-07-22T09:15:00+08:00",
        detected_at="2024-07-22T09:16:00+08:00",
        evidence_count=1,
        lifecycle="superseded",
        is_state_applied=False,
        is_read=True,
        supersedes_change_id=lpr_feb["change_id"],
    )
    lpr_october = _change(
        change_id="demo-change-lpr-2024-10",
        event_id=LPR_EVENT_ID,
        title=lpr_title,
        topic=lpr_topic,
        previous_state="1年期 3.35%；5年期以上 3.85%",
        new_state="1年期 3.10%；5年期以上 3.60%",
        effective_at="2024-10-21T09:00:00+08:00",
        detected_at="2024-10-21T09:01:00+08:00",
        evidence_count=1,
        supersedes_change_id=lpr_july["change_id"],
    )

    rrr_title = "金融机构加权平均存款准备金率变化（真实公开材料回放）"
    rrr_topic = "货币政策"
    rrr_change = _change(
        change_id="demo-change-rrr-2024-09",
        event_id=RRR_EVENT_ID,
        title=rrr_title,
        topic=rrr_topic,
        previous_state="加权平均存款准备金率 7.0%",
        new_state="加权平均存款准备金率 6.6%",
        effective_at="2024-09-27T00:00:00+08:00",
        detected_at="2024-11-08T15:41:00+08:00",
        evidence_count=2,
        source_scope="multi_source",
        evidence_status="cross_source_supported",
    )

    conflict_title = "【演示冲突样本】先进制造业补贴比例"
    conflict_topic = "产业政策 · 演示"
    conflict_a = _change(
        change_id="demo-change-conflict-15",
        event_id=CONFLICT_EVENT_ID,
        title=conflict_title,
        topic=conflict_topic,
        previous_state="补贴比例待正式核验",
        new_state="补贴比例 15%",
        effective_at="2026-08-29T09:00:00+08:00",
        detected_at="2026-08-29T09:05:00+08:00",
        evidence_count=1,
        is_state_applied=False,
        objectivity_status="disputed",
        conflict_status="contested",
        evidence_status="contested",
    )
    conflict_b = _change(
        change_id="demo-change-conflict-20",
        event_id=CONFLICT_EVENT_ID,
        title=conflict_title,
        topic=conflict_topic,
        previous_state="补贴比例待正式核验",
        new_state="补贴比例 20%",
        effective_at="2026-08-29T09:00:00+08:00",
        detected_at="2026-08-29T09:06:00+08:00",
        evidence_count=1,
        is_state_applied=False,
        objectivity_status="disputed",
        conflict_status="contested",
        evidence_status="contested",
    )
    conflict_a["can_mark_read"] = False
    conflict_b["can_mark_read"] = False
    for change in (conflict_a, conflict_b):
        change["lineage_claims"] = [
            {
                "claim_id": f"claim-{change['change_id']}",
                "event_id": CONFLICT_EVENT_ID,
                "successor_change_id": change["change_id"],
                "predecessor_change_id": "demo-predecessor-conflict",
                "supersedes_lifecycle": "superseded",
                "source_event_id": CONFLICT_EVENT_ID,
                "source_change_id": change["change_id"],
                "status": "contested",
                "created_at": change["detected_at"],
                "updated_at": change["detected_at"],
            }
        ]

    changes = {
        LPR_EVENT_ID: [lpr_october, lpr_july, lpr_feb],
        RRR_EVENT_ID: [rrr_change],
        CONFLICT_EVENT_ID: [conflict_b, conflict_a],
    }
    evidence = {
        lpr_feb["change_id"]: [
            _evidence(
                membership_id="demo-evidence-lpr-feb",
                event_id=LPR_EVENT_ID,
                change_id=lpr_feb["change_id"],
                media="中国货币网",
                provider="全国银行间同业拆借中心",
                title="2024年2月20日贷款市场报价利率（LPR）公告",
                url="https://www.chinamoney.com.cn/chinese/rdgz/20240220/2806938.html",
                published_at="2024-02-20T09:15:00+08:00",
                body_text="1年期LPR为3.45%，5年期以上LPR为3.95%。",
            )
        ],
        lpr_july["change_id"]: [
            _evidence(
                membership_id="demo-evidence-lpr-july",
                event_id=LPR_EVENT_ID,
                change_id=lpr_july["change_id"],
                media="中国货币网",
                provider="全国银行间同业拆借中心",
                title="2024年7月22日贷款市场报价利率（LPR）公告",
                url="https://www.chinamoney.org.cn/chinese/rdgz/20240722/2921782.html",
                published_at="2024-07-22T09:15:00+08:00",
                body_text="1年期LPR为3.35%，5年期以上LPR为3.85%。",
            )
        ],
        lpr_october["change_id"]: [
            _evidence(
                membership_id="demo-evidence-lpr-october",
                event_id=LPR_EVENT_ID,
                change_id=lpr_october["change_id"],
                media="中国人民银行",
                provider="官方公开页面",
                title="2024年10月21日贷款市场报价利率（LPR）公告",
                url="https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/3876551/5482244/index.html",
                published_at="2024-10-21T09:00:00+08:00",
                body_text="1年期LPR为3.1%，5年期以上LPR为3.6%。",
            )
        ],
        rrr_change["change_id"]: [
            _evidence(
                membership_id="demo-evidence-rrr-baseline",
                event_id=RRR_EVENT_ID,
                change_id=rrr_change["change_id"],
                media="中国人民银行",
                provider="2024年第二季度货币政策执行报告",
                title="报告确认2月降准后加权平均存款准备金率为7.0%",
                url="https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/5347949/2025100917195573922/2025081217013923839.pdf",
                published_at="2024-08-09T17:00:00+08:00",
                body_text="2月5日降准后，金融机构加权平均存款准备金率为7.0%。",
            ),
            _evidence(
                membership_id="demo-evidence-rrr-current",
                event_id=RRR_EVENT_ID,
                change_id=rrr_change["change_id"],
                media="中国人民银行",
                provider="2024年第三季度货币政策执行报告",
                title="报告确认9月27日降准后加权平均存款准备金率为6.6%",
                url="https://www.pbc.gov.cn/zhengcehuobisi/125207/125227/125957/5347949/afbfa5df25ee45889d916a2819b60a43/2024110815410752868.pdf",
                published_at="2024-11-08T15:41:00+08:00",
                body_text="9月27日下调0.5个百分点后，加权平均存款准备金率为6.6%。",
            ),
        ],
        conflict_a["change_id"]: [
            _evidence(
                membership_id="demo-evidence-conflict-a",
                event_id=CONFLICT_EVENT_ID,
                change_id=conflict_a["change_id"],
                media="演示机构 A（非真实来源）",
                provider="合成冲突样本",
                title="演示材料 A：补贴比例写为15%",
                url="https://example.invalid/newsclaw-demo/conflict-a",
                published_at="2026-08-29T09:00:00+08:00",
                body_text="此材料仅用于演示冲突冻结逻辑，不代表真实政策。",
                relation_kind="contradicts_change",
            )
        ],
        conflict_b["change_id"]: [
            _evidence(
                membership_id="demo-evidence-conflict-b",
                event_id=CONFLICT_EVENT_ID,
                change_id=conflict_b["change_id"],
                media="演示机构 B（非真实来源）",
                provider="合成冲突样本",
                title="演示材料 B：补贴比例写为20%",
                url="https://example.invalid/newsclaw-demo/conflict-b",
                published_at="2026-08-29T09:01:00+08:00",
                body_text="此材料仅用于演示冲突冻结逻辑，不代表真实政策。",
                relation_kind="contradicts_change",
            )
        ],
    }

    events = [
        {
            "event_id": LPR_EVENT_ID,
            "canonical_title": lpr_title,
            "event_type": "policy_change",
            "topic_group": lpr_topic,
            "story_key": f"demo:{LPR_EVENT_ID}",
            "entities": ["中国人民银行", "全国银行间同业拆借中心", "LPR"],
            "current_state": lpr_october["new_state"],
            "state_version": 3,
            "status": "active",
            "merged_into_event_id": None,
            "first_seen_at": lpr_feb["detected_at"],
            "last_seen_at": lpr_october["detected_at"],
            "last_change_at": lpr_october["detected_at"],
            "audit_change_count": 3,
            "change_count": 1,
            "conflict_count": 0,
            "evidence_count": 3,
            "inbox_sort_at": "2026-08-29T15:00:00+08:00",
            "latest_change_id": lpr_october["change_id"],
            "active_tracking_count": 1,
            "next_check_at": "",
        },
        {
            "event_id": RRR_EVENT_ID,
            "canonical_title": rrr_title,
            "event_type": "policy_change",
            "topic_group": rrr_topic,
            "story_key": f"demo:{RRR_EVENT_ID}",
            "entities": ["中国人民银行", "存款准备金率"],
            "current_state": rrr_change["new_state"],
            "state_version": 1,
            "status": "active",
            "merged_into_event_id": None,
            "first_seen_at": "2024-08-09T17:00:00+08:00",
            "last_seen_at": rrr_change["detected_at"],
            "last_change_at": rrr_change["detected_at"],
            "audit_change_count": 1,
            "change_count": 1,
            "conflict_count": 0,
            "evidence_count": 2,
            "inbox_sort_at": "2026-08-29T14:59:00+08:00",
            "latest_change_id": rrr_change["change_id"],
            "active_tracking_count": 1,
            "next_check_at": "",
        },
        {
            "event_id": CONFLICT_EVENT_ID,
            "canonical_title": conflict_title,
            "event_type": "policy_change",
            "topic_group": conflict_topic,
            "story_key": f"demo:{CONFLICT_EVENT_ID}",
            "entities": ["演示机构 A", "演示机构 B"],
            "current_state": "补贴比例待核验",
            "state_version": 0,
            "status": "active",
            "merged_into_event_id": None,
            "first_seen_at": conflict_a["detected_at"],
            "last_seen_at": conflict_b["detected_at"],
            "last_change_at": conflict_b["detected_at"],
            "audit_change_count": 2,
            "change_count": 0,
            "conflict_count": 2,
            "evidence_count": 2,
            "inbox_sort_at": "2026-08-29T14:58:00+08:00",
            "latest_change_id": conflict_b["change_id"],
            "active_tracking_count": 0,
            "next_check_at": "",
        },
    ]
    return events, changes, evidence


class EventDemoState:
    """Thread-safe, reset-on-restart state for the isolated demo API."""

    def __init__(self) -> None:
        self.events, self.changes, self.evidence = _demo_data()
        self.lock = threading.RLock()

    def _event(self, event_id: str) -> dict[str, Any] | None:
        return next((item for item in self.events if item["event_id"] == event_id), None)

    def _current_changes(self, event_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.changes.get(event_id, [])
            if item["lifecycle"] == "active" and item["is_state_applied"]
        ]

    def _conflicts(self, event_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.changes.get(event_id, [])
            if item["objectivity_status"] == "disputed"
            and item["conflict_status"] == "contested"
        ]

    def _decorate_event(self, event: dict[str, Any]) -> dict[str, Any]:
        item = copy.deepcopy(event)
        current = self._current_changes(event["event_id"])
        conflicts = self._conflicts(event["event_id"])
        item["unread_change_count"] = sum(not row["is_read"] for row in current)
        item["unread_conflict_count"] = sum(not row["is_read"] for row in conflicts)
        all_changes = self.changes.get(event["event_id"], [])
        latest_id = item.get("latest_change_id")
        item["latest_change"] = copy.deepcopy(
            next((row for row in all_changes if row["change_id"] == latest_id), None)
        )
        return item

    def _summary(self, events: list[dict[str, Any]] | None = None) -> dict[str, int]:
        source = self.events if events is None else events
        decorated = [self._decorate_event(item) for item in source]
        return {
            "event_count": len(decorated),
            "unread_event_count": sum(
                bool(item["unread_change_count"] or item["unread_conflict_count"])
                for item in decorated
            ),
            "conflict_event_count": sum(bool(item["conflict_count"]) for item in decorated),
            "unread_change_count": sum(item["unread_change_count"] for item in decorated),
            "unread_conflict_count": sum(item["unread_conflict_count"] for item in decorated),
        }

    @staticmethod
    def _page(items: list[dict[str, Any]], query: dict[str, list[str]]) -> tuple[list[dict[str, Any]], int | None, bool]:
        offset = max(0, int(query.get("offset", ["0"])[0] or 0))
        limit = max(1, min(200, int(query.get("limit", ["30"])[0] or 30)))
        page = items[offset : offset + limit]
        has_more = offset + len(page) < len(items)
        return page, offset + len(page) if has_more else None, has_more

    def get(self, raw_path: str) -> tuple[int, Any]:
        parsed = urlsplit(raw_path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        with self.lock:
            if path == "/api/news/events/summary":
                return 200, copy.deepcopy(self._summary())
            if path == "/api/news/events":
                topic = str(query.get("topic_group", [""])[0] or "")
                attention = str(query.get("attention", ["all"])[0] or "all")
                candidates = [self._decorate_event(item) for item in self.events]
                if topic:
                    candidates = [item for item in candidates if item["topic_group"] == topic]
                if attention == "unread":
                    candidates = [
                        item
                        for item in candidates
                        if item["unread_change_count"] or item["unread_conflict_count"]
                    ]
                elif attention == "conflict":
                    candidates = [item for item in candidates if item["unread_conflict_count"]]
                page, next_offset, has_more = self._page(candidates, query)
                topics = [
                    {
                        "topic_group": name,
                        "event_count": sum(item["topic_group"] == name for item in self.events),
                    }
                    for name in dict.fromkeys(item["topic_group"] for item in self.events)
                ]
                return 200, {
                    "items": copy.deepcopy(page),
                    "summary": self._summary(
                        [item for item in self.events if not topic or item["topic_group"] == topic]
                    ),
                    "topics": topics,
                    "next_cursor": str(next_offset) if next_offset is not None else None,
                    "has_more": has_more,
                }

            evidence_match = re.fullmatch(r"/api/news/events/([^/]+)/evidence", path)
            if evidence_match:
                event_id = evidence_match.group(1)
                if self._event(event_id) is None:
                    return 404, {"detail": "demo event not found"}
                change_id = str(query.get("change_id", [""])[0] or "")
                rows = self.evidence.get(change_id, [])
                page, next_offset, has_more = self._page(rows, query)
                return 200, {
                    "event_id": event_id,
                    "change_id": change_id,
                    "resolved_from_change_id": "",
                    "items": copy.deepcopy(page),
                    "next_offset": next_offset,
                    "has_more": has_more,
                }

            conflict_match = re.fullmatch(r"/api/news/events/([^/]+)/conflicts", path)
            if conflict_match:
                event_id = conflict_match.group(1)
                event = self._event(event_id)
                if event is None:
                    return 404, {"detail": "demo event not found"}
                rows = self._conflicts(event_id)
                page, next_offset, has_more = self._page(rows, query)
                return 200, {
                    "event": self._decorate_event(event),
                    "items": copy.deepcopy(page),
                    "next_offset": next_offset,
                    "has_more": has_more,
                }

            change_match = re.fullmatch(r"/api/news/events/([^/]+)/changes", path)
            if change_match:
                event_id = change_match.group(1)
                event = self._event(event_id)
                if event is None:
                    return 404, {"detail": "demo event not found"}
                view = str(query.get("view", ["current"])[0] or "current")
                rows = (
                    self._current_changes(event_id)
                    if view == "current"
                    else self.changes.get(event_id, [])
                )
                page, next_offset, has_more = self._page(rows, query)
                return 200, {
                    "event": self._decorate_event(event),
                    "items": copy.deepcopy(page),
                    "view": "audit" if view == "audit" else "current",
                    "next_cursor": str(next_offset) if next_offset is not None else None,
                    "has_more": has_more,
                }

            detail_match = re.fullmatch(r"/api/news/events/([^/]+)", path)
            if detail_match:
                event = self._event(detail_match.group(1))
                if event is None:
                    return 404, {"detail": "demo event not found"}
                item = self._decorate_event(event)
                item.pop("inbox_sort_at", None)
                item.pop("latest_change", None)
                return 200, {"item": item, "resolved_from_event_id": ""}

        return legacy_demo_payload(raw_path)

    def post(self, raw_path: str) -> tuple[int, Any]:
        path = urlsplit(raw_path).path.rstrip("/")
        match = re.fullmatch(r"/api/news/(changes|conflicts)/([^/]+)/read", path)
        if match is None:
            return 200, {"ok": True}
        expected_conflict = match.group(1) == "conflicts"
        change_id = match.group(2)
        with self.lock:
            for rows in self.changes.values():
                for item in rows:
                    if item["change_id"] != change_id:
                        continue
                    is_conflict = item["conflict_status"] == "contested"
                    if is_conflict != expected_conflict:
                        return 409, {"detail": "demo read route does not match change kind"}
                    item["is_read"] = True
                    return 200, {"item": copy.deepcopy(item), "ok": True}
        return 404, {"detail": "demo change not found"}


def demo_payload(
    path: str,
    *,
    method: str = "GET",
    state: EventDemoState | None = None,
) -> tuple[int, Any]:
    """Return one isolated demo response without touching application state."""

    owner = state or EventDemoState()
    if method.upper() == "GET":
        return owner.get(path)
    if method.upper() == "POST":
        return owner.post(path)
    return 405, {"detail": "demo method not allowed"}


def inject_demo_disclosure(html: str) -> str:
    """Mark a served desktop page as isolated without changing production assets."""

    html = html.replace(
        "<title>NewsClaw 事件脉冲台</title>",
        "<title>NewsClaw · 隔离演示环境</title>",
        1,
    )
    disclosure = f"""
  <script>
    (() => {{
      const disclosure = {json.dumps(DEMO_STATUS_LABEL, ensure_ascii=False)};
      const keepDisclosure = () => {{
        const label = document.getElementById("statusLabel");
        if (label && label.textContent !== disclosure) label.textContent = disclosure;
      }};
      window.addEventListener("DOMContentLoaded", () => {{
        keepDisclosure();
        const label = document.getElementById("statusLabel");
        if (label) new MutationObserver(keepDisclosure).observe(label, {{ childList: true, subtree: true }});
      }});
    }})();
  </script>
"""
    return html.replace("</body>", f"{disclosure}</body>", 1)


def _demo_index() -> bytes:
    html = (ROOT / "src/openbiliclaw/web/desktop/index.html").read_text(encoding="utf-8")
    return inject_demo_disclosure(html).encode("utf-8")


class EventDemoServer:
    """Serve current NewsClaw assets and the in-memory event API on loopback."""

    def __init__(self, *, port: int = 0) -> None:
        self.port = int(port)
        self.state = EventDemoState()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.requests: list[str] = []

    def __enter__(self) -> str:
        owner = self

        class DemoHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def end_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-OBC-Auth")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_OPTIONS(self) -> None:  # noqa: N802
                self.send_response(204)
                self.end_headers()

            def do_GET(self) -> None:  # noqa: N802
                owner.requests.append(self.path)
                path = urlsplit(self.path).path
                if path in {"/web", "/web/", "/web/index.html"}:
                    return self._bytes(_demo_index(), "text/html; charset=utf-8")
                if path.startswith("/web/assets/"):
                    return self._serve_file(
                        ROOT
                        / "src/openbiliclaw/web/desktop/assets"
                        / path.removeprefix("/web/assets/")
                    )
                if path.startswith("/shared/"):
                    return self._serve_file(
                        ROOT / "src/openbiliclaw/web/shared" / path.removeprefix("/shared/")
                    )
                if path in {"/m", "/m/", "/m/index.html"}:
                    return self._serve_file(ROOT / "src/openbiliclaw/web/index.html")
                if path.startswith("/m/"):
                    return self._serve_file(
                        ROOT / "src/openbiliclaw/web" / path.removeprefix("/m/")
                    )
                status, payload = owner.state.get(self.path)
                return self._json(payload, status)

            def do_POST(self) -> None:  # noqa: N802
                owner.requests.append(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                if length:
                    self.rfile.read(length)
                status, payload = owner.state.post(self.path)
                self._json(payload, status)

            def _json(self, payload: Any, status: int = 200) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self._bytes(body, "application/json; charset=utf-8", status)

            def _bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

            def _serve_file(self, path: Path) -> None:
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(ROOT)
                except (FileNotFoundError, ValueError):
                    return self._json({"error": "not_found"}, 404)
                self._bytes(
                    resolved.read_bytes(),
                    mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
                )

        self.server = ThreadingHTTPServer((LOOPBACK_HOST, self.port), DemoHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://{LOOPBACK_HOST}:{self.server.server_port}"

    def __exit__(self, *_: object) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated NewsClaw event demo")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    with EventDemoServer(port=args.port) as origin:
        print(f"NewsClaw isolated demo: {origin}/web/")
        print("No config, credentials, scheduler, or production database is loaded.")
        with suppress(KeyboardInterrupt):
            threading.Event().wait()


if __name__ == "__main__":
    main()
