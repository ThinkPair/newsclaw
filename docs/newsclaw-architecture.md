# NewsClaw architecture

## Scope

NewsClaw is a single-user, local-only change-tracking information system. It
uses news articles as evidence, but its durable product unit is a verified
real-world event and the objective A→B changes that remain reachable over time.
Its four
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
                                |
     due tracking -> verified A→B event projection -> desktop event workspace
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

The recommendation API adds a non-canonical presentation layer without
rewriting that evidence: `display_title` removes only a bounded, substantial
`【title】lead`/`[title]lead` provider shape (short section-tag prefixes remain
intact), while `source_link_status` distinguishes an
exact provider URL from a title-search fallback or a completely missing
navigation path. Tushare `news` and `major_news` do not promise URL fields, so
their empty `canonical_url` remains empty. A generated `source_lookup_url` is a
user-initiated media-plus-title search route, not evidence and not an asserted
original; missing media or title yields no ambiguous fallback.
Before the 20-card NewsClaw view limit is applied, a conservative presentation
dedupe folds only rows from the same media and publication day whose compact
headline, dateline-normalized lead, complete normalized body and exact URL match
after removing a calendar-year formatting variant. Empty-body rows, changed
lead/body facts, different URLs, media or dates remain visible, and the durable
rows are never deleted.

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

### Today Pick freshness and replacement contract

“Today” means the Beijing calendar day, not the latest twenty rows and not a visual label.
All Today Pick exits—single pending, batch, proactive WebSocket, and manual refresh—share
`source_platform=news` plus the half-open publication window
`[published_after, published_before)`. Missing or invalid scope fails closed.

The refresh pipeline enqueues only articles in that window for recommendation evaluation;
the continuous-tracking lane still receives the complete normalized and deduplicated fetch
batch. At full or near-full capacity, a durable lane/day budget equal to the current pool
target bounds model evaluation. Reservation and claim are atomic, use the actual claimed
count, are not refunded after failure, and roll to a new key on the next Beijing day.

There is no pre-eviction. A newly admitted row must finish expression copy and become
servable before scoped reconciliation may remove a lower-priority row. Repairing an already
cached row never evicts. Liked, already recommended, and token-owned rows are protected;
when protected visible rows exceed the configured target, they define the effective floor.
An empty or still-processing day remains explicit and older articles are never substituted.

The ordinary expression-copy watermark remains unchanged, but NewsClaw has one explicit
escape hatch for this full-pool sequence: the coordinator counts and copies only `news` rows
inside the same Beijing-day half-open window, with a maximum batch of sixty. It may bypass the
historical inventory watermark only when all three scope fields are present. After a copy makes
progress, reconciliation runs once the visible pool is at or above target. Runtime keeps a
small reconciliation debt across coordinator retries (and starts with one repair check after a
process restart); debt is repaid before any new copy work, never while the pool is below target.
Missing maintenance, database failure, cancellation, or provider failure cannot silently clear
that debt. Reconciliation reloads the canonical visible set after every demotion because removing
one topic-window row can reveal another previously folded row; it stops only at the effective
target or with an explicit no-progress error. Ordinary product mode continues to use the existing
global watermark path.

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

### Phase 3 durable event projection (implemented truth contract)

Phase 3 adds a source-independent truth layer. An article becomes an immutable
evidence membership, a tracker remains the user's monitoring instruction, and
a completed check remains an execution receipt. The durable product unit is one
real-world event and its verified state transitions. The desktop event dossier
consumes this contract without changing its ownership; it is the presentation
surface of this same Phase 3 contract, not a separate roadmap phase.

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
one event's current objective timeline, a separate contested-change ledger,
evidence memberships, and idempotent read state for both objective and
contested rows. The projection stores `is_state_applied` separately from the
broader fact gate: only the safe, non-conflicting applied change set owns the
current unread count and latest change, while scalar `current_state` is computed
along one reachable matching chain. Confirmed changes on an
obsolete or losing branch remain auditable but cannot re-enter the current
stream merely because they are individually objective. Event details expose
current, audit and conflict counts without leaking model-private fields into
the feed. Cursors are opaque, keyset-based and bound to their filter/event
scope. Backfill and the separate origin repair run in bounded
background batches at startup and resume after interruption. Fact-gate repair
rechecks the saved text itself; pre-release change identities migrate to v3 and
old IDs resolve through aliases. Historical `changed` rows whose old schema
cannot prove the new fact gate remain `analysis / unconfirmed / provisional /
partial_legacy`; they are auditable but never promoted into the objective feed.
If revalidation later downgrades an accepted successor, the stale supersede
pointer loses authority, its predecessor is restored, and the rejected lineage
claim remains visible. A shallow or subjective contradiction remains an
auditable membership but cannot create the durable conflict state by itself.
These reads do not refresh sources, call Tavily, publish a recommendation,
enqueue work or mutate the profile.

### Phase 3 event dossier (implemented desktop contract)

The event dossier makes the Phase 3 truth usable without creating a second client-side
ledger. The desktop home adds an event workspace with three progressive layers:

```text
event inbox
    ├→ unread / conflict / topic filters
    └→ selected event
           ├→ current state + objective change pulse
           ├→ explicit audit view for superseded/invalidated/side-branch changes
           ├→ on-demand evidence drawer
           └→ tracking status / next check
```

`GET /api/news/events` returns an event-inbox page with the latest applied
objective change, event counts, topic counts and tracking metadata. Its v3
opaque keyset cursor binds `status`, `attention`, `topic_group`, the last sort
position and a monotonic global `revision`. Inserts, updates or deletes in
events, event changes, evidence memberships or tracked observations advance
`news_event_inbox_revision`. `load_news_event_inbox_snapshot()` reads the
revision, page, summary and topic counts from one WAL snapshot. A continuation
whose revision is stale returns HTTP 409; the Web client discards that
continuation and reloads the first page instead of risking OFFSET-style gaps or
duplicates.

`GET /api/news/events/summary` supplies the global active-event badge without
being narrowed by the currently selected attention/topic filters. It returns
event, unread-event, conflict-event, unread-change and unread-conflict counts.
Event details then use `GET /api/news/events/{event_id}/changes?view=current` for
the safe applied change set or `view=audit` for retained objective history. Current rows
expose `can_mark_read`; obsolete audit rows cannot own current unread. Conflicts
and evidence keep their existing bounded pagination and independent idempotent
read actions.

The desktop visual hierarchy is intentionally not another article wall. The
inbox is scan-first, the selected event owns the A→B story, and full evidence is
opened only when the user asks to verify it. At narrow widths the layout becomes
a true master/detail switch: the inbox and detail are not stacked into two
simultaneously scrolling panes. The event workspace uses locally bundled
Phosphor Icons; it does not add a remote runtime dependency.

The current view must never include expectations, market sentiment, AI analysis,
unresolved conflicting outcomes or objective rows that are no longer reachable
from the event's accepted state chain. Those records remain available only in
their explicit audit/conflict surfaces.

### Phase 4 personal memory (implemented user-control contract)

Phase 4 does not replace the five-layer Soul pipeline. It adds a separate,
local-only layer for claims the user has explicitly chosen to keep:

```text
behaviour / chat / legacy Soul
        └→ system inference ─→ sourced pending candidate
                                      ├→ user rejects ─→ durable rejected record
                                      └→ user accepts or edits+accepts
                                                        └→ active personal memory

manual user entry ─────────────────────────────────────→ active personal memory
active personal memory ─→ edit / soft delete / restore ─→ append-only revision history
```

The authoritative stores are `personal_memory_items`,
`personal_memory_candidates`, `personal_memory_revisions`, and the minimal
`personal_memory_requests` idempotency receipt table. Active items have one
stable identity, a monotonic version, a `confirmed_fact` or
`research_direction` kind, and an explicit provenance. Deletion is soft; a
deleted identity must be restored instead of silently recreated. Candidate
accept/reject and item create/edit/delete/restore write the state change,
revision, and request receipt in one SQLite transaction. Every new mutation
checks `expected_version`; reusing a request ID for a different target or
payload returns a conflict instead of replaying the wrong result.

Inference producers can call the internal candidate method but there is no
public candidate-creation endpoint. Evidence accepts only the bounded
`type/ref/excerpt/hash/observed_at` structure; unsafe local paths, credential
fields, and token-bearing URL query/fragment data are refused or removed.
Public DTOs omit internal claim/content fingerprints and request data. The
personal-memory bodies stay local and are not sent to an external model merely
because the management page is opened.

Consumption is explicitly split by purpose. Active `confirmed_fact` rows are
loaded only by dialogue and tool-enabled dialogue calls. Active
`research_direction` rows are loaded only by recommendation evaluation and copy
generation. Generic structured/multimodal tasks, probes, profile work, and Soul
rebuilds receive neither. The rows are read at call time, so an edit or deletion
applies to the next relevant model call without restarting the service.

Legacy Soul `validated` values are algorithmic state, not user confirmation.
An old verdict without a provable explicit-user source remains pending/unknown;
it is never migrated into `confirmed_facts`. Rejected and pending insights are
excluded from the user-confirmed prompt projection. The old Soul profile may
remain as a labelled system-inference compatibility surface, but it cannot
write or relabel Phase 4 memory.

Desktop Web exposes one panel at a time through
`#memory/confirmed`, `#memory/research`, `#memory/candidates`, and
`#memory/history`. Long-term facts and research directions can be manually
created, edited, or deleted; pending candidates can be accepted, edited before
acceptance, or rejected; a currently deleted item exposes a version-checked
restore action from history. Empty, loading, conflict, timeout, and retry states
all reconcile from the backend truth rather than optimistic client-only state.

### Phase 5 webpage/PDF monitoring (implemented deterministic contract)

Phase 5 is a separate document-watch ledger, not a `NewsSourceAdapter`, tracked
observation, recommendation source, or personal-memory producer:

```text
user-added public URL
  -> per-hop SSRF validation + bounded fetch
  -> immutable raw/text snapshot
  -> deterministic normalization and diff
       baseline / unchanged / noise_only / meaningful / failure
  -> meaningful only: existing event change + durable local notification
```

`web_monitor_targets`, `web_monitor_checks`, `web_monitor_snapshots`,
`web_monitor_diffs`, `web_monitor_notifications`, and
`web_monitor_commands` form the authoritative schema-v9 aggregate. A monitor
and its deterministic `document_monitor` event are created in one transaction.
A meaningful successor snapshot, Phase-3 change projection, exact diff, target
cursor, check terminal state, and notification are also committed in one
transaction. Snapshots and diffs are append-only; remove is a soft target state.

The first successful fetch is always a baseline and cannot notify. Raw bytes may
change while normalized text stays equal; that produces an auditable
`noise_only` version without an event or notification. The fact recorded in the
event says the monitored document text was observed to change at detection
time; it never invents the policy's legal effective date. User label/focus text
is presentation metadata and cannot override the objective diff.

The fetcher permits only public HTTP(S), standard ports, and a bounded redirect
chain. On every hop it resolves and validates a public address set, connects to
one of those exact IP literals while preserving the original Host/SNI identity,
and then requires the actual peer to match the selected IP. It rejects
credentials, localhost/private/reserved/link-local targets and HTTPS downgrade,
ignores environment proxies, sends no cookies or authorization, and never
forwards origin validators or response cookies to another host. Time,
decompressed-byte, MIME/magic and PDF page/text limits are all bounded. HTML
removes executable, navigation, form, footer and narrowly scoped
layout/cookie/share/time noise. PDF extraction uses `pypdf` in a separate child
process; a 20-second parse deadline kills that process. Frozen distributions
without a packaged safe worker fail closed instead of falling back to an
uninterruptible thread. Encrypted, damaged, scanned and oversized documents are
surfaced as explicit safe failures.

An independent scheduler claims due rows through persisted leases and a partial
unique running-check index. Concurrent manual/background calls join the same
check; expired owners are fenced. Retry delays are bounded at 1m, 5m, 30m, 2h
and 6h before `needs_attention`. Failure, recovery and change notifications are
durable rows; delivered/read/dismiss mutations use request receipts so an
ambiguous retry cannot silently become a different action. This workflow never
calls an LLM or Tavily and never reads/writes the Soul profile or Phase-4 memory.

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
  The event dossier reads only the shared Phase 3 event truth; it does not infer
  event state in JavaScript. Phase 4 adds the four-section personal-memory
  manager and uses only the versioned local personal-memory API. Phase 5 adds a
  scan-first monitor subpage: counts remain unknown until loaded, history stays
  folded, and event details open through the existing Phase-3 workspace.
- Mobile Web/PWA uses the same API and adds pull-to-refresh and explicit
  online/degraded/offline state. Phase 1 adds no Research controls and never
  sends a Tavily credential to this client. The responsive `/web` monitor view
  uses the same backend rows rather than maintaining a mobile copy.
- The browser extension is a display-only side-panel client. Its manifest has
  no news-site host permissions, content scripts, cookie access, or page hooks.
  Phase 1 adds no Research control and never stores the Tavily credential. P5
  only adds a bounded localhost inbox probe and a link to the formal monitor
  page; the extension itself never fetches the monitored document.
- DSH is a trailing grid column. Its plugin failure is isolated so Harness can
  continue loading even if NewsClaw is unavailable. Its research tools call the
  local Agent Bridge and never hold the Tavily credential. Its content library,
  durable inbox and seven P5 Agent operations consume the same local ledger.

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
