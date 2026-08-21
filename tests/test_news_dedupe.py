from __future__ import annotations

from datetime import UTC, datetime

from openbiliclaw.news.dedupe import SemanticDeduplicator
from openbiliclaw.news.models import Article, build_article


def _article(
    title: str,
    *,
    media: str,
    body: str = "",
    url: str = "",
    scope: str = "title_only",
) -> Article:
    return build_article(
        media=media,
        provider="fixture",
        title=title,
        body=body,
        canonical_url=url,
        published_at="2026-08-17T08:00:00Z",
        retrieved_at=datetime(2026, 8, 17, 9, 0, tzinfo=UTC),
        content_scope=scope,  # type: ignore[arg-type]
        source_identifier=f"{media}:{title}",
    )


def test_exact_and_semantic_duplicates_cluster_and_prefer_richer_evidence() -> None:
    title_only = _article(
        "央行宣布下调存款准备金率0.5个百分点 - 财联社",
        media="财联社",
        url="https://example.com/shared",
    )
    full_text = _article(
        "央行宣布下调存款准备金率0.5个百分点",
        media="新华社",
        body="这是完整正文证据。",
        url="https://example.com/shared?utm_source=x",
        scope="full_text",
    )
    unique = _article("科技企业发布新模型", media="第一财经")

    result = SemanticDeduplicator().deduplicate([title_only, full_text, unique])

    assert result.articles == (full_text, unique)
    assert result.clusters[0].duplicates == (title_only,)


def test_embedding_similarity_can_be_injected_without_network_or_llm() -> None:
    left = _article("A媒体标题", media="A")
    right = _article("完全不同的B媒体表述", media="B")
    deduplicator = SemanticDeduplicator(similarity=lambda _left, _right: 0.99)

    result = deduplicator.deduplicate([left, right])

    assert len(result.articles) == 1
    assert len(result.clusters[0].duplicates) == 1
