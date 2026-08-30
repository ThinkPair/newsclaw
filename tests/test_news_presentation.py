from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from openbiliclaw.api.models import PendingDelightOut, PendingNotificationOut, RecommendationOut
from openbiliclaw.news.presentation import (
    collapse_news_display_duplicates,
    compact_news_headline,
    news_display_duplicate_key,
    news_source_link_fields,
    news_source_lookup_url,
)


def test_compact_news_headline_removes_only_explicit_provider_lead() -> None:
    raw = "【国家发展改革委召开六网协同会议】2026年8月25日，会议在北京召开。"

    assert compact_news_headline(raw) == "国家发展改革委召开六网协同会议"
    assert compact_news_headline("普通标题：保留其中的解释") == "普通标题：保留其中的解释"
    assert compact_news_headline("【没有闭合括号") == "【没有闭合括号"
    assert compact_news_headline("【官方MV】歌曲名") == "【官方MV】歌曲名"
    assert compact_news_headline("[Official] Real headline") == "[Official] Real headline"


def test_source_link_fields_distinguish_exact_search_and_missing() -> None:
    exact = news_source_link_fields(
        title="有原文的新闻",
        media="示例媒体",
        content_url="https://example.com/article",
    )
    search = news_source_link_fields(
        title="【来源没有直接原文链接但标题信息完整】正文开头不应进入查询",
        media="示例媒体",
        content_url="",
    )
    missing = news_source_link_fields(
        title="",
        media="",
        content_url="javascript:alert(1)",
    )
    ambiguous = news_source_link_fields(
        title="宏观政策更新",
        media="",
        content_url="",
    )

    assert exact == {"source_link_status": "exact", "source_lookup_url": None}
    assert search["source_link_status"] == "search"
    assert str(search["source_lookup_url"]).startswith("https://www.bing.com/search?")
    assert missing == {"source_link_status": "missing", "source_lookup_url": None}
    assert ambiguous == {"source_link_status": "missing", "source_lookup_url": None}


def test_source_lookup_query_contains_only_the_compact_public_headline() -> None:
    url = news_source_lookup_url(
        "【国家宏观政策出现一项确定变化】政策标题后重复的正文和个人资料都不应进入查询",
        "权威媒体",
    )
    query = parse_qs(urlsplit(url).query)["q"][0]

    assert query == '"国家宏观政策出现一项确定变化" "权威媒体"'
    assert "重复的正文" not in query


def test_recommendation_contract_exposes_truthful_source_navigation_state() -> None:
    payload = RecommendationOut(
        id=1,
        bvid="news_1",
        display_title="干净标题",
        source_link_status="search",
        source_lookup_url="https://www.bing.com/search?q=test",
    ).model_dump()

    assert payload["display_title"] == "干净标题"
    assert payload["source_link_status"] == "search"
    assert payload["source_lookup_url"].startswith("https://www.bing.com/")


def test_pending_delight_contract_keeps_source_navigation_fields() -> None:
    payload = PendingDelightOut(
        bvid="news_1",
        display_title="干净标题",
        source_link_status="search",
        source_lookup_url="https://www.bing.com/search?q=test",
    ).model_dump()

    assert payload["display_title"] == "干净标题"
    assert payload["source_link_status"] == "search"
    assert payload["source_lookup_url"].startswith("https://www.bing.com/")


def test_pending_notification_contract_keeps_source_navigation_fields() -> None:
    payload = PendingNotificationOut(
        recommendation_id=1,
        bvid="news_1",
        display_title="干净标题",
        source_link_status="search",
        source_lookup_url="https://www.bing.com/search?q=test",
    ).model_dump()

    assert payload["display_title"] == "干净标题"
    assert payload["source_link_status"] == "search"
    assert payload["source_lookup_url"].startswith("https://www.bing.com/")


def test_display_duplicate_collapse_is_same_provider_same_day_and_year_tolerant() -> None:
    rows = [
        {
            "id": 1,
            "title": "【普华永道:上半年中国并购市场强势回暖】普华永道发布年度报告",
            "body_text": (
                "【普华永道:上半年中国并购市场强势回暖】普华永道发布年度报告。报告显示市场回暖。"
            ),
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25T04:15:04+08:00",
        },
        {
            "id": 2,
            "title": "【普华永道:2026年上半年中国并购市场强势回暖】8月25日，普华永道发布年度报告",
            "body_text": (
                "【普华永道:2026年上半年中国并购市场强势回暖】"
                "8月25日，普华永道发布年度报告。报告显示市场回暖。"
            ),
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026/8/25 05:31:44",
        },
        {
            "id": 3,
            "title": "【普华永道:2026年上半年中国并购市场强势回暖】普华永道发布年度报告",
            "body_text": (
                "【普华永道:2026年上半年中国并购市场强势回暖】"
                "普华永道发布年度报告。报告显示市场回暖。"
            ),
            "up_name": "另一媒体",
            "source_platform": "news",
            "published_at": "2026-08-25 06:00:00",
        },
        {
            "id": 4,
            "title": "【普华永道:2026年上半年中国并购市场强势回暖】普华永道发布年度报告",
            "body_text": (
                "【普华永道:2026年上半年中国并购市场强势回暖】"
                "普华永道发布年度报告。报告显示市场回暖。"
            ),
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-26 06:00:00",
        },
    ]

    collapsed = collapse_news_display_duplicates(rows)

    assert [row["id"] for row in collapsed] == [1, 3, 4]


def test_display_duplicate_collapse_preserves_changed_lead_or_body_facts() -> None:
    rows = [
        {
            "id": 1,
            "title": "【央行货币政策出现新的明确变化】宣布下调25个基点",
            "body_text": "【央行货币政策出现新的明确变化】宣布下调25个基点。执行时间为下周。",
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
        {
            "id": 2,
            "title": "【央行货币政策出现新的明确变化】改为下调50个基点",
            "body_text": "【央行货币政策出现新的明确变化】改为下调50个基点。执行时间为下周。",
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
        {
            "id": 3,
            "title": "同一政策标题",
            "body_text": "同一政策标题。执行规模为100亿元。",
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
        {
            "id": 4,
            "title": "同一政策标题",
            "body_text": "同一政策标题。执行规模为200亿元。",
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
    ]

    assert [row["id"] for row in collapse_news_display_duplicates(rows)] == [1, 2, 3, 4]


def test_display_duplicate_collapse_preserves_change_after_identical_first_sentence() -> None:
    common = {
        "title": "【央行货币政策出现新的明确变化】央行宣布政策调整",
        "up_name": "财经快讯",
        "source_platform": "news",
        "published_at": "2026-08-25",
    }
    rows = [
        {
            "id": 1,
            **common,
            "body_text": "【央行货币政策出现新的明确变化】央行宣布政策调整。下调25个基点。",
        },
        {
            "id": 2,
            **common,
            "body_text": "【央行货币政策出现新的明确变化】央行宣布政策调整。下调50个基点。",
        },
    ]

    assert [row["id"] for row in collapse_news_display_duplicates(rows)] == [1, 2]


def test_display_duplicate_collapse_accepts_serialized_recommendation_models() -> None:
    items = [
        RecommendationOut(
            id=1,
            bvid="one",
            title="【普华永道上半年中国并购市场强势回暖】普华永道发布年度报告",
            display_title="普华永道上半年中国并购市场强势回暖",
            body_text="【普华永道上半年中国并购市场强势回暖】普华永道发布年度报告。报告显示市场回暖。",
            media="财经快讯",
            source_platform="news",
            content_type="article",
            published_at="2026-08-25",
        ),
        RecommendationOut(
            id=2,
            bvid="two",
            title="【普华永道2026年上半年中国并购市场强势回暖】8月25日，普华永道发布年度报告",
            display_title="普华永道2026年上半年中国并购市场强势回暖",
            body_text="【普华永道2026年上半年中国并购市场强势回暖】8月25日，普华永道发布年度报告。报告显示市场回暖。",
            media="财经快讯",
            source_platform="news",
            content_type="article",
            published_at="2026-08-25",
        ),
    ]

    assert [item.id for item in collapse_news_display_duplicates(items)] == [1]


def test_display_duplicate_collapse_preserves_changed_numeric_facts() -> None:
    rows = [
        {
            "id": 1,
            "title": "公司利润增长20%",
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
        {
            "id": 2,
            "title": "公司利润增长21%",
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
        {
            "id": 3,
            "title": "2025年公司利润增长20%",
            "up_name": "财经快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
    ]

    assert [row["id"] for row in collapse_news_display_duplicates(rows)] == [1, 2, 3]


def test_display_duplicate_collapse_preserves_empty_body_and_different_urls() -> None:
    common = {
        "title": "【同一政策发布】同一段导语",
        "up_name": "财经快讯",
        "source_platform": "news",
        "published_at": "2026-08-25",
    }
    rows = [
        {"id": 1, **common, "body_text": ""},
        {"id": 2, **common, "body_text": ""},
        {
            "id": 3,
            **common,
            "body_text": "【同一政策发布】同一段导语。政策规模为100亿元。",
            "content_url": "https://example.com/a",
        },
        {
            "id": 4,
            **common,
            "body_text": "【同一政策发布】同一段导语。政策规模为100亿元。",
            "content_url": "https://example.com/b",
        },
    ]

    assert [row["id"] for row in collapse_news_display_duplicates(rows)] == [1, 2, 3, 4]


def test_display_duplicate_collapse_preserves_fact_bearing_symbols() -> None:
    common = {
        "up_name": "财经快讯",
        "source_platform": "news",
        "published_at": "2026-08-25",
    }
    decimal_rows = [
        {
            "id": 1,
            **common,
            "title": "【公司2026年利润率调整至3.5%】公告发布",
            "body_text": "【公司2026年利润率调整至3.5%】公告发布。执行时间相同。",
        },
        {
            "id": 2,
            **common,
            "title": "【公司2026年利润率调整至35%】公告发布",
            "body_text": "【公司2026年利润率调整至35%】公告发布。执行时间相同。",
        },
    ]
    sign_rows = [
        {
            "id": 3,
            **common,
            "title": "【宏观政策对市场利率作出明确调整】市场变化+3%",
            "body_text": "【宏观政策对市场利率作出明确调整】市场变化+3%。执行时间相同。",
        },
        {
            "id": 4,
            **common,
            "title": "【宏观政策对市场利率作出明确调整】市场变化-3%",
            "body_text": "【宏观政策对市场利率作出明确调整】市场变化-3%。执行时间相同。",
        },
    ]
    currency_rows = [
        {
            "id": 5,
            **common,
            "title": "【重大项目投资安排正式公布】部门发布公告",
            "body_text": "【重大项目投资安排正式公布】部门发布公告。项目规模为3亿元。",
        },
        {
            "id": 6,
            **common,
            "title": "【重大项目投资安排正式公布】部门发布公告",
            "body_text": "【重大项目投资安排正式公布】部门发布公告。项目规模为$3亿。",
        },
    ]

    assert [row["id"] for row in collapse_news_display_duplicates(decimal_rows)] == [1, 2]
    assert [row["id"] for row in collapse_news_display_duplicates(sign_rows)] == [3, 4]
    assert [row["id"] for row in collapse_news_display_duplicates(currency_rows)] == [5, 6]


def test_display_duplicate_collapse_normalizes_full_width_and_whitespace_only() -> None:
    rows = [
        {
            "id": 1,
            "title": "公司ＡＩ利润率和配套政策正式调整",
            "body_text": "公司ＡＩ利润率和配套政策正式调整。目标值为 ３．５％，执行时间相同。",
            "up_name": "财经　快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
        {
            "id": 2,
            "title": "公司AI利润率和配套政策正式调整",
            "body_text": "公司AI利润率和配套政策正式调整。目标值为   3.5%，执行时间相同。",
            "up_name": "财经 快讯",
            "source_platform": "news",
            "published_at": "2026-08-25",
        },
    ]

    assert [row["id"] for row in collapse_news_display_duplicates(rows)] == [1]


def test_display_duplicate_key_is_limited_to_news_family() -> None:
    row = {
        "title": "【同一政策发布】同一段导语",
        "body_text": "【同一政策发布】同一段导语。政策规模为100亿元。",
        "up_name": "财经快讯",
        "published_at": "2026-08-25",
    }

    assert news_display_duplicate_key({**row, "source_platform": "news"}) is not None
    assert news_display_duplicate_key({**row, "source_platform": "bilibili"}) is None
