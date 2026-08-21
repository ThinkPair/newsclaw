"""NewsClaw's source-agnostic news ingestion domain.

The package deliberately has no dependency on the web application or the
legacy video adapters.  It can therefore be exercised with contract fixtures
before it is wired into the shared recommendation pipeline.
"""

from openbiliclaw.news.bootstrap import (
    BootstrapResult,
    CandidateBatch,
    ManualPreferenceEvent,
    ManualProfileSeed,
    NewsBootstrapCoordinator,
    SourceRegistry,
)
from openbiliclaw.news.dedupe import DedupeResult, SemanticDeduplicator
from openbiliclaw.news.errors import (
    NewsSourceError,
    SourceLeaseBusy,
    SourcePermissionDenied,
    SourceRateLimited,
    SourceSchemaError,
    SourceTimedOut,
    SourceTransientError,
)
from openbiliclaw.news.identity import (
    IdempotencyConflict,
    SQLiteIdempotencyLedger,
    stable_event_id,
    stable_request_id,
)
from openbiliclaw.news.models import Article, ContentScope, Freshness, build_article
from openbiliclaw.news.protocol import FetchWindow, NewsSourceAdapter, SourceProbe
from openbiliclaw.news.reliability import (
    RetryPolicy,
    SourceRunner,
    SourceRunResult,
    SourceTaskStore,
)
from openbiliclaw.news.rss import RSSAdapter
from openbiliclaw.news.tushare import TushareNewsAdapter

__all__ = [
    "Article",
    "BootstrapResult",
    "CandidateBatch",
    "ContentScope",
    "DedupeResult",
    "FetchWindow",
    "Freshness",
    "IdempotencyConflict",
    "NewsSourceAdapter",
    "NewsBootstrapCoordinator",
    "NewsSourceError",
    "RSSAdapter",
    "RetryPolicy",
    "SQLiteIdempotencyLedger",
    "SemanticDeduplicator",
    "SourcePermissionDenied",
    "SourceLeaseBusy",
    "SourceProbe",
    "SourceRateLimited",
    "SourceRunResult",
    "SourceRegistry",
    "SourceRunner",
    "SourceSchemaError",
    "SourceTaskStore",
    "SourceTimedOut",
    "SourceTransientError",
    "TushareNewsAdapter",
    "ManualPreferenceEvent",
    "ManualProfileSeed",
    "build_article",
    "stable_event_id",
    "stable_request_id",
]
