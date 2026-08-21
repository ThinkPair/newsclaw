# NewsClaw architecture

## Scope

NewsClaw is a single-user, local-only news recommendation system. Its four
clients share one FastAPI process, one SQLite database, one five-layer profile,
one recommendation pool, and one feedback ledger. No client can place trades,
read browser cookies, scrape a news page, or publish the service to the public
Internet by default.

## Runtime flow

```text
Tushare news / major_news / cctv_news     Official or custom RSS/Atom
                    \                       /
                     source adapters + contract probes
                                |
             normalize + provenance + content-level guard
                                |
              stable identity + hash + semantic de-duplication
                                |
               durable source task / lease / retry state
                                |
                       discovery candidate queue
                                |
           five-layer profile -> deterministic relevance gates
                                |
                  LLM evaluation and explanation
                                |
         freshness + diversity + exploration + negative filters
                                |
                       recommendation pool
                                |
       Desktop Web / Mobile PWA / browser side panel / DSH
                                |
 open / like / dislike / ignore / read-later / save / search / chat
                                |
        idempotent events -> profile ledger -> next recommendation
```

## Article contract

| Field | Meaning |
| --- | --- |
| `article_id` | Stable NewsClaw identity derived from provider identity or canonical content |
| `media` | User-facing publisher or media source |
| `provider` | Technical API/feed provider such as `tushare` or `rss` |
| `title` | Publisher title, never generated as source evidence |
| `body` | Acquired body or summary; may be empty |
| `canonical_url` | Original publisher URL when supplied by the contract |
| `published_at` | Source publication clock, normalized with timezone evidence |
| `retrieved_at` | Local acquisition clock |
| `content_scope` | `title`, `summary`, or `full_text` |
| `content_hash` | Hash of the canonical evidence payload |
| `freshness` | `fresh`, `aging`, `stale`, `unknown`, or terminal supersession state |

Compatibility projection into the inherited recommendation pipeline is
lossless: `bvid=content_id=article_id`, `source_platform=news`,
`author_name/up_name=media`, `source_strategy=provider`,
`content_url=canonical_url`, `body_text=body`, and `content_type=article`.
The inherited column names are private schema aliases, not video-domain
semantics. The action-level persistence and replay contract is recorded in
[the backend closure matrix](newsclaw-backend-closure-matrix.md).

## Source contracts

Each source has a durable status record containing enablement, configured
weight/cadence, entitlement/health state, last attempt, last success, next
retry, error code, and a redacted human-readable explanation. In v0.1,
enable/disable, credentials, RSS addresses, health and retry state are active
runtime controls. Per-source weight and cadence are stored for the next
scheduler revision but do not yet change ranking or launch separate source
timers; the settings UI and user guide label them accordingly. Fetch jobs use:

Tushare credentials are written through `/api/news/credentials/tushare` into
the private runtime data directory and are never returned by the API. A local
credential takes precedence over the legacy `TUSHARE_TOKEN` environment
fallback, so onboarding and the desktop settings page use the same token as
the next probe or refresh without restarting the service.

- a single active lease per source;
- bounded request timeout and item budget;
- provider rate limits and exponential backoff with jitter;
- stable `request_id` for retries;
- a terminal outcome (`success`, `valid_empty`, `unauthorized`, `rate_limited`,
  `timeout`, `invalid_payload`, or `failed`);
- atomic last-good configuration fallback.

The global refresh result reports every source outcome. It is `degraded` when
one or more enabled sources fail, even when other sources succeed.

## AI and evidence boundary

LLM prompts receive an explicit evidence level. Title-only input must be
described as title-level information; the model cannot claim it read the full
story. Explanations contain three named sections: why this fits the user,
possible impact chain, and follow-up observation points. They are informational
and must not contain buy/sell advice.

Manual profile edits are an override layer above model inference. Inferred
interests, avoidance hypotheses, awareness, and insights retain evidence,
confidence, lifecycle state, and deletion history.

## Client contract

- Desktop Web retains the licensed interaction geometry for its top bar, left
  navigation, runtime and activity cards, spotlight hero, source filters,
  content library, profile, dialogue, messages, and seven settings categories.
- Mobile Web/PWA uses the same API and adds pull-to-refresh and explicit
  online/degraded/offline state.
- The browser extension is a display-only side-panel client. Its manifest has
  no news-site host permissions, content scripts, cookie access, or page hooks.
- DSH is a trailing grid column. Its plugin failure is isolated so Harness can
  continue loading even if NewsClaw is unavailable.

All feedback writes carry stable `request_id`/`event_id` values. Duplicate
clicks or retries return the original receipt and cannot teach twice.

## Runtime separation and cutover

The formal local service runs on port 8430; development stays on port 8431.
The fresh NewsClaw database started without imported behavior, profile, saved
items, or dialogue history. Existing local model and Tushare credentials were
reused through the private runtime only.

NewsClaw has no public repository or release channel yet. In NewsClaw mode the
upstream project-statistics service and updater are disabled, the Web client
shows no external Star/download action, and update application is rejected by
the API. Local upgrades are backed up, verified and applied through the DSH
plugin workshop. Open-source attribution remains in LICENSE, NOTICE and the
bundled license files, where it belongs.
