# NewsClaw architecture

## Scope

NewsClaw is a single-user, local-only news recommendation system. Its four
clients share one FastAPI process, one primary application SQLite database, one
five-layer profile, one recommendation pool, and one feedback ledger. The
optional Research lane keeps a separate `newsclaw_research.db` evidence ledger.
No client can place trades,
read browser cookies, directly scrape a news page, or publish the service to the
public Internet by default. An explicit research request may ask Tavily to
search the public web or extract evidence from 1–3 caller-supplied public URLs;
that is a separate, bounded research lane rather than scheduled news ingestion.

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
 open / like / dislike / ignore / read-later / save / track / search / chat
                                |
 idempotent events -> profile ledger; judgment snapshots -> local memory
```

The on-demand research lane is deliberately separate:

```text
User or Agent question
        |
        v
REST /api/research/* or Agent Bridge research-*
        |
        v
ResearchService -> fresh local cache, or Tavily Search / Extract
        |                                   |
        +---------- bounded evidence + public source URLs
                            |
                            v
                  newsclaw_research.db
                            |
                            +-- no automatic profile, discovery, or pool write
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

## On-demand research contract

Tavily is a `ResearchProvider`, not a `NewsSourceAdapter`. It is never placed in
the Tushare/RSS refresh schedule and does not create news candidates by itself.

- Search uses Tavily `basic` with `chunks_per_source=1`, defaults to five results and caps at eight.
  Answer, raw-content, image and automatic-parameter fields are disabled.
- Extract requires a research question and 1–3 explicit public HTTP(S) URLs.
  It uses `basic`, defaults to two relevant chunks per source and caps at three.
- Queries reject obvious email addresses, mobile numbers, local paths and
  secrets before any network call. Extract rejects credentials in URLs,
  localhost, private/loopback IPs and local/internal host suffixes.
- Credentials live in the private NewsClaw credential file, with
  `TAVILY_API_KEY` as the environment fallback. Status responses expose only
  `configured`, `stored_locally`, `source` and a masked value. The key is used
  only as an authorization header; it is never mixed into query/URL/evidence
  text or shown back to any client.
- Results are stored in `newsclaw_research.db` using `research_runs`,
  `research_evidence` and `research_run_evidence`. Fresh-cache windows are 24
  hours for general, 30 minutes for news and 15 minutes for finance; Extract
  uses the general window. `refresh=true` bypasses freshness without rewriting
  old evidence.

Research evidence is external-world memory, not user memory. It cannot update
the five-layer profile unless a later, separate user-confirmation workflow is
explicitly designed. It also cannot flow into `discovery_candidates`,
`content_cache`, the recommendation pool, saved items or feedback events.

## AI and evidence boundary

LLM prompts receive an explicit evidence level. Title-only input must be
described as title-level information; the model cannot claim it read the full
story. Explanations contain three named sections: why this fits the user,
possible impact chain, and follow-up observation points. They are informational
and must not contain buy/sell advice.

Tavily results retain their source URL, provider request ID and local run ID.
They are evidence fragments, not proof that every statement on the source page
is true. A later model analysis must cite the returned URLs and distinguish
quoted/source facts from its own inference. Provider answers are disabled so
NewsClaw remains the owner of reasoning and provenance.

Manual profile edits are an override layer above model inference. Inferred
interests, avoidance hypotheses, awareness, and insights retain evidence,
confidence, lifecycle state, and deletion history.

## Decision workbench and local memory

NewsClaw's desktop home is a decision workbench rather than a dense reading
wall. The queue carries only priority, topic, title, source, time and evidence
level; the selected-item pane owns the explanation and actions. Selection is
local UI state. Only opening the original URL or submitting an explicit action
creates an event.

Decision priority is a versioned composite, not an alias of personalization
confidence. `decision-priority-v2` combines match (45%), evidence completeness
(20%), timeliness (20%) and event impact (15%), with display bands calibrated
against the real local inventory so full-text current news does not collapse
into one all-high bucket. Recommendation history,
reshuffle, append and delight surfaces share the same projection and stable
descending order. The API also preserves every signal and a human-readable
reason so a displayed rank remains inspectable.

When a recommendation is saved, opened or removed, NewsClaw can retain an
immutable `newsclaw-judgment-v1` snapshot containing the identity, publication
context, tags, priority factors and three-part analysis. A later ranking change
must not rewrite what the system believed at that moment. Legacy rows without
a snapshot remain readable and explicitly lack that historical judgment.

An observation point can be promoted into `tracked_observations`. Creation is
idempotent by request ID and by the active semantic observation key; stopping
tracking archives the row instead of erasing it. This creates a local memory
anchor with its original judgment. Phase 1 only establishes this anchor; it does
not claim that the point has been verified, schedule automatic Tavily research,
or train the user profile.

### Phase 2 continuous tracking closure (implemented contract)

Phase 2 implements the following end-to-end contract. The live local desktop
service has verified the new API schema, content-library route and empty state;
populated tracker cards, state transitions and timeline actions are covered by
automated backend and desktop contracts. The first naturally occurring external
news change remains an operational observation rather than a fabricated demo.

```text
normal Tushare/RSS refresh
        |
        v
normalized + cross-source deduplicated Article batch
        |                              |
        |                              └→ normal candidate/recommendation path
        v
due active observations → deterministic local shortlist
        |                         |
        |                         └→ no related candidates → valid empty check
        v
bounded structured model comparison (previous state vs new evidence)
        |                         |
        |                         ├→ merely related / no material change → history only
        |                         └→ provider/model failure → retry/degraded, never “unchanged”
        v
material change → evidence ledger → tracker timeline → unread update
```

The normal `NewsRuntime.refresh()` cycle owns source collection. Tracking reuses
that completed local batch and source-health result in an app-owned background
task, so scheduled model comparison does not hold the refresh lock; it must not create a second
scheduled crawler or silently call Tavily. Deterministic rules may conclude that
there are no related candidates and avoid a model call. Once plausible related
evidence exists, a bounded structured model comparison is required to separate
“related” from “material change”; a missing, malformed or failed model result is
a retry/degraded outcome, not evidence that nothing changed.

A changed check atomically records the check, its source evidence, the fact vs
inference split, the new tracker state and an unread timeline entry. A verified
unchanged or valid-empty check remains in history but creates no change
notification. A tracker-level database single-flight makes a manual request join
an already-running background check instead of launching a second model call.
Operational failures remain read, surface a safe concrete reason,
and keep an automatic backoff schedule; they cannot be presented as change
updates. “Check now” waits for the same refresh, dedupe,
shortlist, comparison and idempotency path with a forced due decision; it is not
a Tavily research shortcut. Archiving a tracker stops future checks and cancels
an in-flight claim while retaining the original judgment, all completed checks,
evidence and read state.

Tracking outcomes do not generate profile events or alter recommendation
weights. The complete Phase 2 management surface belongs to desktop Web in this
phase. Mobile/PWA, the browser extension and DSH have no tracking-center,
timeline or check-now contract yet; they must later consume the same database
truth rather than introduce a parallel ledger.

### Phase 3 durable event projection (implemented backend contract)

Phase 3 adds a source-independent truth layer without changing the current
desktop surface. An article becomes an immutable evidence membership, a tracker
remains the user's monitoring instruction, and a completed check remains an
execution receipt. The durable product unit is one real-world event and its
verified state transitions.

```text
Phase 2 changed check + evidence
        |
        └→ same SQLite transaction
              ├→ create or reuse exact story-key news event
              ├→ attach immutable evidence revision
              ├→ new confirmed, effective A→B transition → one change + unread
              ├→ same transition from another source → evidence upgrade only
              ├→ mutually exclusive durable outcome → contested + freeze state
              └→ expectation / sentiment / AI analysis → secondary record only

legacy tracker/check/evidence ledger
        └→ restart-safe bounded backfill → conservative partial-legacy projection
              └→ analysis + unconfirmed + provisional; not the objective feed
        └→ restart-safe origin/fact/identity repairs
              └→ revalidate content depth + rebuild aliases and lineage
```

`objective_change` is allowed into the default change feed only when it is
confirmed, not contested, has an effective time with explicit date/minute
precision, preserves a previous and new state, and has enough actually retained
summary or body evidence. The shared evidence verifier removes markup and
boilerplate and rejects content that merely repeats the title; a metadata label
cannot promote itself to a confirmed fact. A publisher repeated through another
technical provider is not counted as an independent source. Expectations,
sentiment and inference remain auditable but are excluded from the objective
feed and cannot own its unread state.

The check completion and event projection share one transaction: if event,
membership or change persistence fails, the Phase 2 completion rolls back too.
Identity is deliberately split in two: exact normalized `story_key` selects the
event, then change identity v3 uses the event-scoped stable change family,
normalized A/B states, effective value and its precision. Publisher, provider,
URL, tracker wording and item key are excluded, so equivalent transitions do
not duplicate unread while distinct evidence revisions remain inspectable. A
publication timestamp used only as fallback is folded to its source-local date;
it does not invent an intraday event time. Controlled paraphrase matching is
deliberately conservative: action family and positive/pending/negative/stopped
polarity must match, and the residual subject must be exact after adjacent state
phrases are removed. Mixed polarity, different numbers and any other residual
difference fail closed instead of using fuzzy similarity. Two independently
durable objective outcomes for the same event/effective/previous-state slot are not
silently merged: both become contested, the event current state is frozen, and
resolution requires a stronger explicit superseding change. Event merge
preserves event and change aliases, rebinds trackers and evidence, deduplicates
equivalent changes, and applies the same conflict rule. When equivalent
successors arrive with different predecessor edges, `news_event_lineage_claims`
retains both candidates, their requested lifecycle and immutable source IDs.
One explicit resolution selects a winner, keeps every loser as `rejected`,
restores any predecessor no longer suppressed, and resumes state projection in
the same transaction. Alias folding and later event merges remap only canonical
edge endpoints, so the claim origin remains auditable. Existing behavior
`events` are untouched.

The NewsClaw-only read contract exposes the objective change feed, event detail,
one event's objective timeline, a separate contested-change ledger, evidence
memberships, and idempotent read state for both objective and contested rows.
Event details expose objective and conflict counts without leaking model-private
fields into the feed. Cursors are opaque, keyset-based and bound to their
filter/event scope. Backfill and the separate origin repair run in bounded
background batches at startup and resume after interruption. Fact-gate repair
rechecks the saved text itself; pre-release change identities migrate to v3 and
old IDs resolve through aliases. Historical `changed` rows whose old schema
cannot prove the new fact gate remain `analysis / unconfirmed / provisional /
partial_legacy`; they are auditable but never promoted into the objective feed.
If revalidation later downgrades an accepted successor, the stale supersede
pointer loses authority, its predecessor is restored, and the rejected lineage
claim remains visible. A shallow or subjective contradiction remains an
auditable membership but cannot create the durable conflict state by itself.
These reads do not refresh sources, call Tavily, publish a
recommendation, enqueue work or mutate the profile. Phase 4 will decide the UI;
Phase 3 deliberately adds none.

## Client contract

- Desktop Web retains the licensed interaction geometry for its top bar, left
  navigation, runtime and activity cards, spotlight hero, source filters,
  content library, profile, dialogue, messages, and seven settings categories.
  Its Tavily card reads masked status and can save/delete the local key; its
  “验证连接（不消耗搜索额度）” button posts to the local probe endpoint, whose
  backend only calls Tavily's official `GET /usage`. It sends no Search query or
  URL, creates no research run, consumes no search credit, and returns only the
  redaction-safe `key_usage`, `key_limit`, `account_plan`, `plan_usage` and
  `plan_limit` fields.
  Phase 1 does not add a desktop research-question, result-list or history UI.
- Mobile Web/PWA uses the same API and adds pull-to-refresh and explicit
  online/degraded/offline state. Phase 1 adds no Research controls and never
  sends a Tavily credential to this client.
- The browser extension is a display-only side-panel client. Its manifest has
  no news-site host permissions, content scripts, cookie access, or page hooks.
  Phase 1 adds no Research control and never stores the Tavily credential.
- DSH is a trailing grid column. Its plugin failure is isolated so Harness can
  continue loading even if NewsClaw is unavailable. Its research tools call the
  local Agent Bridge and never hold the Tavily credential.

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
