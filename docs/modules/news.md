# News domain (`openbiliclaw.news`)

## Purpose

The news package is the source-agnostic ingestion boundary for NewsClaw. It
normalizes provider data before it reaches discovery/recommendation code and
does not depend on Bilibili history, cookies, or a legacy user profile.

The package only reads contracted news APIs and site-published RSS/Atom feeds.
It does not fetch article pages or implement a crawler.

NewsClaw's optional Tavily Search/Extract lives in the separate
[`openbiliclaw.research`](research.md) module. It is an explicit user/Agent
research lane, not a `NewsSourceAdapter`, and therefore does not weaken this
package's scheduled-ingestion boundary or write research pages into the news
candidate pool.

## Implemented features

| Capability | Status | Notes |
| --- | --- | --- |
| Canonical `Article` model | Implemented | Stable ID, media, technical provider, title, body, canonical URL, publication/retrieval time, content scope, hash, freshness |
| Tushare Pro short news | Implemented | Injectable `news` endpoint client; optional documented `src` code |
| Tushare Pro major news | Implemented | Injectable `major_news` endpoint client |
| Tushare CCTV news | Implemented | Injectable day-scoped `cctv_news` endpoint client |
| RSS 2.0 / Atom | Implemented | Direct feed subscription only, bounded response size, no page crawling |
| Permission and health probes | Implemented | Separates available, empty, permission denied, rate limited, timeout, and unavailable |
| Durable source tasks | Implemented | SQLite lease, timeout, pacing, exponential backoff, immutable terminal state, result replay |
| Stable request/event IDs | Implemented | Caller IDs are preserved when valid; otherwise deterministic IDs are derived |
| Feedback/event idempotency | Implemented | SQLite first-write ledger rejects event-ID payload conflicts |
| Semantic dedupe | Implemented | Exact URL/hash/title plus offline headline similarity; local embedding similarity can be injected |
| From-zero onboarding | Implemented | Explicit manual interests/avoid terms, initial source fetch, cross-source dedupe, candidate callback, four-stage result |

## Canonical article contract

Every adapter returns `Article` values with exactly these public fields:

- `article_id`: stable local news identity;
- `media`: user-facing media name;
- `provider`: technical source, such as `Tushare Pro · news` or `RSS/Atom`;
- `title`, `body`, `canonical_url`;
- `published_at`, `retrieved_at`: seconds-precision UTC strings;
- `content_scope`: `title_only`, `summary`, or `full_text`;
- `hash`: SHA-256 content digest;
- `freshness`: `fresh`, `aging`, `stale`, `future`, or `unknown`.

Malformed publication times remain empty/`unknown`; the adapter does not invent
a time. Missing bodies become `title_only`; an adapter cannot mark an empty body
as `summary` or `full_text`.

## Public API

### Sources

- `TushareNewsAdapter(client, endpoint, media, source_code=...)`
- `RSSAdapter(url, media, http_client=...)`
- `SourceRegistry.register(adapter, enabled=True)`
- `SourceRegistry.enabled_adapters()`
- `NewsSourceAdapter.fetch(FetchWindow)`
- `NewsSourceAdapter.probe()`

The application creates the Tushare client after loading the local credential
and injects it into adapters. The news package never reads, stores, or logs the
token.

### Reliability and health

- `SourceTaskStore(path)`
- `SourceRunner(store, policy=RetryPolicy(...))`
- `SourceRunner.run(adapter, window, request_id=..., event_id=...)`
- `SourceTaskStore.get_task(source_id, request_id)`
- `SourceTaskStore.get_health(source_id)`
- `SourceTaskStore.record_probe(probe)`

Terminal states are `succeeded`, `empty`, `permission_denied`, `rate_limited`,
`timed_out`, and `failed`. A replay of the same request returns the persisted
normalized articles without calling the provider again. One source failure is
represented as a source-level degraded/unavailable state; it does not imply
that a multi-source refresh succeeded.

### Idempotency and dedupe

- `stable_request_id(namespace, *parts, supplied="")`
- `stable_event_id(namespace, *parts, supplied="")`
- `SQLiteIdempotencyLedger.claim(namespace, event_id, payload)`
- `SemanticDeduplicator(similarity=...).deduplicate(articles)`

The idempotency ledger returns `True` only for the first write, `False` for an
identical retry, and raises `IdempotencyConflict` if an event ID is reused for a
different payload.

### From-zero onboarding

`NewsBootstrapCoordinator.bootstrap(...)` accepts 3–10 explicit interest
directions plus up to 10 optional avoid terms. It has no legacy-profile input
and emits only `manual_interest` / `manual_avoid` events with confidence `1.0`.

The result always describes these four stages:

1. `validate_preferences`
2. `seed_manual_memory`
3. `fetch_initial_news`
4. `publish_candidate_pool`

The coordinator receives injectable profile-initializer, event, and candidate
callbacks. `ManualProfileSeed` is deliberately framework-neutral: it contains
only the normalized manual terms and stable events. The future
`POST /api/news/bootstrap` handler should use the initializer to create an empty
profile plus durable manual overrides, pass a dedicated manual-event writer and
candidate-pool ingester to the other callbacks, then return `BootstrapResult`.

For the current OBC profile implementation, the integration callback should
create an empty `OnionProfile` when the soul layer does not exist, then persist
interests/avoid terms through `overrides.apply_edit`: add each term to `likes`
or `dislikes` with weight `1.0`, then pin that domain weight to `1.0`. Save the
result with `MemoryManager.save_profile_overrides`. This preserves the rule that
user-authored values override later AI regeneration. Do not pass
`manual_interest` or `manual_avoid` directly to
`MemoryManager.propagate_event`: those are not supported OBC behavior event
types. The event callback may persist the evidence directly through
`Database.insert_event` with `ingest_key=event.event_id` (or a dedicated
news-preference ledger), while the profile initializer owns the effective
profile state. Both callbacks must be idempotent on the stable seed/event IDs.

## Data flow

```text
Tushare client / RSS URL
        |
        v
NewsSourceAdapter -> SourceRunner -> Article[] -> SemanticDeduplicator
                         |                            |
                         v                            v
                 source task + health          candidate callback
                    (SQLite)
```

From-zero initialization adds one explicit step before this flow:

```text
3-10 manual interests + optional avoid terms
        |
        v
stable manual events -> memory callback -> initial source run -> candidate callback
```

## Security and failure rules

- Tushare credentials stay in the application's local credential layer.
- RSS uses `trust_env=False` when it owns the HTTP client and never reads site
  cookies.
- Persisted/displayed upstream errors are bounded and redact common token,
  authorization, API-key, secret, and password forms.
- Retryable failures are bounded by `RetryPolicy`; permission/schema failures
  are terminal immediately.
- Full task/config wiring must be completed in the API and config modules; this
  module intentionally does not modify either integration surface.

## Client projection contract

- Shared desktop/mobile URL projection treats `news` and generic `web` items as
  articles: an explicit `content_url` or `canonical_url` opens as supplied;
  otherwise the item is non-actionable and no video-site fallback is invented.
- Legacy video rows remain compatible: a Bilibili item with only `bvid` still
  resolves to its canonical video page.
- The mobile profile keeps the compatibility wire field
  `interest.favorite_up_users`, but presents it to users as “关注的媒体/记者”.

# 首次设置与 RSS 重放合同（2026-08-17）

新闻首次设置先规范化兴趣/避雷词，再绑定 `request_id + input_digest`；同命令可安全回放，
同 ID 改输入或与另一命令并发会返回冲突。重启遗留的 `running` 状态会持久化为可重试
`failed`，不会让客户端永久轮询。自定义 RSS 以规范 URL 生成稳定来源 ID：相同参数重试
返回 `duplicate=true`，同 URL 不同参数返回冲突。
