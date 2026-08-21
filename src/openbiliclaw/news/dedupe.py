"""Deterministic exact and semantic near-duplicate clustering."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from openbiliclaw.news.models import Article

SimilarityFunction = Callable[[Article, Article], float]

_WORD_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]")
_TRAILING_MEDIA_RE = re.compile(r"(?:[-_—|｜]\s*)?(?:财联社|新华社|央视新闻|新浪财经|第一财经)$")


def _normalized_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    normalized = _TRAILING_MEDIA_RE.sub("", normalized).strip()
    return "".join(_WORD_RE.findall(normalized))


def _title_tokens(title: str) -> set[str]:
    words = _WORD_RE.findall(unicodedata.normalize("NFKC", title).casefold())
    latin = {word for word in words if not (len(word) == 1 and "\u3400" <= word <= "\u9fff")}
    chinese = "".join(word for word in words if len(word) == 1 and "\u3400" <= word <= "\u9fff")
    grams = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    return latin | grams


def _lexical_similarity(left: Article, right: Article) -> float:
    left_title = _normalized_title(left.title)
    right_title = _normalized_title(right.title)
    if not left_title or not right_title:
        return 0.0
    sequence = SequenceMatcher(None, left_title, right_title, autojunk=False).ratio()
    left_tokens = _title_tokens(left.title)
    right_tokens = _title_tokens(right.title)
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return max(sequence, (sequence * 0.55) + (jaccard * 0.45))


def _quality(article: Article) -> tuple[int, int, str]:
    scope = {"title_only": 0, "summary": 1, "full_text": 2}[article.content_scope]
    return (scope, len(article.body), article.published_at)


@dataclass(frozen=True, slots=True)
class DuplicateCluster:
    """One retained canonical article and the rows folded into it."""

    canonical: Article
    duplicates: tuple[Article, ...]


@dataclass(frozen=True, slots=True)
class DedupeResult:
    """Unique rows plus inspectable duplicate evidence."""

    articles: tuple[Article, ...]
    clusters: tuple[DuplicateCluster, ...]


class SemanticDeduplicator:
    """Cluster exact and near-identical reports from multiple media.

    The optional similarity function is the seam for the application's local
    embedding service.  The deterministic title matcher remains the offline
    fallback and keeps ingestion useful when the LLM/embedding service is down.
    """

    # Calibrated against same-event Chinese headline variants in the adapter
    # contract fixtures.  Reopen calibration when the similarity provider or
    # title normalization rules change.
    DEFAULT_THRESHOLD = 0.86

    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        similarity: SimilarityFunction | None = None,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("dedupe threshold must be between zero and one")
        self._threshold = threshold
        self._similarity = similarity or _lexical_similarity

    def deduplicate(self, articles: Iterable[Article]) -> DedupeResult:
        """Return stable first-seen clusters while preferring richer evidence."""

        clusters: list[list[Article]] = []
        exact_index: dict[str, int] = {}
        for article in articles:
            exact_keys = tuple(
                key
                for key in (
                    f"hash:{article.hash}",
                    f"url:{article.canonical_url}" if article.canonical_url else "",
                    f"title:{_normalized_title(article.title)}",
                )
                if key
            )
            cluster_index = next(
                (exact_index[key] for key in exact_keys if key in exact_index),
                None,
            )
            if cluster_index is None:
                for index, cluster in enumerate(clusters):
                    if any(
                        self._similarity(existing, article) >= self._threshold
                        for existing in cluster
                    ):
                        cluster_index = index
                        break
            if cluster_index is None:
                cluster_index = len(clusters)
                clusters.append([article])
            else:
                clusters[cluster_index].append(article)
            for key in exact_keys:
                exact_index[key] = cluster_index

        output: list[DuplicateCluster] = []
        for cluster in clusters:
            canonical = max(cluster, key=_quality)
            duplicates = tuple(item for item in cluster if item is not canonical)
            output.append(DuplicateCluster(canonical=canonical, duplicates=duplicates))
        return DedupeResult(
            articles=tuple(cluster.canonical for cluster in output),
            clusters=tuple(output),
        )
