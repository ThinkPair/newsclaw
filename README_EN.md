# NewsClaw

<p align="center">
  <img src="assets/brand/newsclaw-icon.png" width="144" alt="NewsClaw friendly claw news assistant logo">
</p>

**NewsClaw does not merely collect news; it tracks what objectively changed.**

📌 Latest: **v0.1.0**

> Release boundary: NewsClaw does not use the upstream `openbiliclaw-v*` aggregate Release as its own update or download channel; this GitHub repository is private source hosting only.

NewsClaw is an independent local-only personalized news agent for one person.
Articles remain evidence; the product unit is the objective A→B change of a
real-world event over time. It owns its news sources, product semantics, data,
and configuration, and does not require another product at runtime.

> The local production cutover completed on 2026-08-17. The formal service
> runs at `http://127.0.0.1:8430` with an independent database, a dedicated
> `NewsClaw` Windows startup entry, and the DSH 2.0.0 plugin. Development keeps
> using port 8431 and does not overwrite the production runtime.

## Product contract

- Collect scheduled news from documented Tushare endpoints and official/custom
  RSS or Atom feeds. News ingestion does not crawl pages, inject scripts, or
  access cookies.
- On an explicit request submitted through a DSH Agent tool or the backend
  REST API, use Tavily Search or extract evidence from 1–3 explicitly supplied
  public URLs. Research never updates the profile or recommendation pool automatically.
- Preserve the five-layer Event → Preference → Awareness → Insight → Soul
  pipeline as a **system-inferred profile**, never as user-confirmed facts.
- Keep personal memory in a separate user-controlled layer: only an explicit
  manual entry or acceptance of a sourced candidate can become active memory;
  every edit, delete, and restore remains locally versioned.
- Learn from opening recommendations, likes, dislikes, ignores, comments, and chat. Saved and read-later items remain local library actions and do not automatically alter the profile.
- Use a priority queue → single structured judgment → action/memory workbench;
  rank by user fit, evidence, timeliness, and event impact instead of repeating article text across the screen.
- Explain why an item fits, its possible impact chain, and what to watch next;
  never emit buy/sell advice.
- Respect title-, summary-, and full-text evidence levels.
- Treat one failed source as degraded operation, not a successful global
  refresh.
- The Today Pick lane only admits news published during the current Beijing
  calendar day and never backfills the surface with older articles. It keeps a
  durable, bounded daily evaluation budget even when the pool is full. A new
  item may replace a lower-priority item only after its copy is ready and it is
  actually servable; liked, already recommended, and in-flight rows remain
  protected. A genuinely empty day stays an honest empty or processing state.
- **Phase 2 continuous tracking is implemented against an explicit acceptance
  contract.** A completed normal refresh reuses the same normalized,
  deduplicated local article batch for due observations, while model comparison
  runs outside the refresh critical path. Deterministic local rules bound the
  candidate set, then a structured model distinguishes merely related news from
  a material change. Only a material change creates an unread timeline update;
  failures stay read, expose a safe reason, and retry with backoff. “Check now”
  waits for the same refresh/comparison path and joins an already-running check
  for that tracker instead of issuing a second model call. Stopping a tracker preserves its history, and a
  restart creates a new cycle without rewriting the archived one. This lane
  never calls Tavily automatically and never updates the user profile.
- **Phase 3 durable event projection is implemented.** An article is evidence,
  not the event itself. Exact normalized `story_key` identity first determines
  the real-world event; an event-scoped before/after state plus explicit time
  precision then identifies the transition. Corroboration from another tracker
  or source strengthens that transition without creating another unread item.
  The default feed admits only confirmed, already-effective objective changes
  with enough locally retained summary or body evidence. Mutually exclusive,
  durable outcomes for the same event state slot are quarantined as contested:
  current state is frozen, both evidence trails remain auditable, and only a
  stronger explicit resolution may restore projection. When an event merge
  produces two predecessor candidates for one successor, both edges and their
  immutable origins are retained in a lineage-claim ledger. One explicit
  resolution selects the winner, rejects but preserves the loser, and closes
  the conflict atomically. Expectations, market
  sentiment, and AI analysis remain secondary records and cannot masquerade as
  facts. Restart-safe repair revalidates legacy evidence depth, origin lineage,
  and pre-release change identities. This truth layer never calls Tavily
  automatically and never updates the profile.
- **The Phase 3 event-dossier desktop surface is implemented.** Desktop Web now presents an event
  inbox → current change pulse → evidence/tracking master-detail workspace.
  Unread, conflict, and topic filters consume one backend truth. The default
  timeline exposes the safe `is_state_applied` objective A→B set; the scalar
  `current_state` alone is calculated along one reachable state chain. Unresolved
  conflicts freeze current state, while obsolete and side-branch changes remain
  available through an explicit audit view. Evidence opens on demand instead of
  flooding the main surface. Narrow layouts switch between inbox and detail.
  The global unread badge uses a separate summary endpoint, and event-inbox
  cursor v3 carries a global revision fence and binds long topic filters by a
  fixed-length digest: a mutation during continuation
  returns HTTP 409 and requires a first-page refresh rather than risking skips
  or duplicates.
- **Phase 4 personal memory is implemented.** Desktop Web has four focused
  sections: confirmed facts, current research, pending confirmations, and
  revision history. Behavioural/AI inference may only propose a sourced pending
  candidate; it cannot write active memory. Manual create or explicit acceptance
  is required, while version checks, idempotent mutation receipts, and revisions
  make edit/delete/restore and accept/reject durable. Legacy profile data,
  `validated` flags, and source-less legacy verdicts are never migrated as
  user-confirmed facts.
  Active confirmed facts enter only explicitly scoped dialogue-model calls;
  current research enters only the next recommendation evaluation/copy context.
  Generic evaluation, probes, profile work, and Soul rebuilds receive neither.
  Merely viewing or managing memory makes no external model call, and a
  deletion takes effect on the next applicable call. If acceptance would update or restore
  an existing manual memory, the target is previewed first and an exact target ID
  plus version must be explicitly confirmed; concurrent changes return 409.

## Sources

The first release supports Tushare `news`, `major_news`, and `cctv_news`, plus
publisher-provided or custom RSS/Atom feeds. Every article retains publisher,
technical provider, canonical URL, publish/retrieve times, content level,
content hash, and freshness state.

Tavily is not a scheduled news source. It is an on-demand research provider:
Search stays on the low-cost basic mode and defaults to five results (hard cap
eight), while Extract only processes URLs explicitly supplied by the caller.
Evidence and source URLs are kept in a separate local research ledger with
bounded topic-aware caching. In Phase 1 the desktop Web surface manages the key
and connection probe only; mobile/PWA and the browser extension add no research
buttons and never hold the key.

## Data flow

```text
Tushare / RSS → news evidence → normalized local batch → evaluation/ranking → fact-identical display suppression → workbench
                                      └→ compact title + exact/search/missing source state; canonical evidence stays intact
                                      └→ Beijing-today news → scoped queue + bounded daily budget
                                                               → copy-ready → protected reconciliation → Today Pick
                                      │
                                      └→ [Phase 2] due observation → local-rule shortlist
                                                                         → structured model check
                                                                         ├→ material change → check + evidence ledger
                                                                         │                    └→ [Phase 3] transactional event projection
                                                                         │                         ├→ new objective transition → local event-change ledger + unread
                                                                         │                         ├→ corroboration → stronger evidence, no duplicate unread
                                                                         │                         ├→ conflicting outcome → contested, freeze state, await resolution
                                                                         │                         ├→ expectation/sentiment/analysis → secondary only
                                                                         │                         └→ [Phase 3 dossier] event inbox → safe current changes → evidence drawer
                                                                         │                                        ├→ backend unread/conflict/topic truth
                                                                         │                                        ├→ frozen conflict + explicit audit branches
                                                                         │                                        └→ narrow-screen master/detail; no parallel ledger
                                                                         └→ no change → check history only

Behaviour/chat → system-inferred profile → sourced pending candidate → user accepts → [Phase 4] personal memory
Manual user entry ─────────────────────────────────────────────────────────────→ [Phase 4] personal memory
                                                                                  └→ versioned edit/delete/restore/history

Workbench → explicit feedback → system-inferred profile
          └→ save/click/feedback → immutable judgment snapshot → local history

DSH Agent / REST research request → fresh local cache or Tavily Search/Extract
                                  → cited evidence + separate research history
                                  └→ no automatic profile, candidate, or pool write
```

## Four clients

Desktop Web (including the event workspace), mobile Web/PWA, a display-only
browser side panel, and a DeepSeek Harness trailing grid column all share one
backend, primary SQLite database,
system-inferred profile, personal-memory ledger, recommendation pool, saved items, and chat history. On-demand research
evidence stays in a separate SQLite ledger under the same private data
directory, so it is not mixed with profile or pool state. The extension never
reads news pages or cookies. Logged-in platform work reuses the seven-source task multiplex
instead of introducing a side queue. The DSH panel defaults to 380px, supports 280–560px,
and keeps at least 400px for the conversation area.

The complete Phase 2 tracking surface is desktop-only. Mobile/PWA, the browser
extension, and DSH keep their existing news capabilities in this phase, but do
not expose a tracking center, change timeline, or manual check-now action. A
future client must consume the same backend truth rather than create a second
tracking state.

The Phase 3 event dossier consumes the event truth in Desktop Web. Mobile/PWA, the browser
extension, and DSH still expose no independent event workspace and must not
create another event ledger. The full Phase 4 personal-memory manager is also
desktop-only for now; other clients may not promote a legacy profile or AI
inference into confirmed memory. No client may promote a forecast, sentiment signal,
or AI inference into an objective change, or silently choose a winner when
evidence conflicts.

## Local preview

```powershell
& .\scripts\run-newsclaw-dev.ps1 -Port 8431 `
  -RuntimeRoot "<private-runtime-directory>" `
  -SourceConfig "<existing-local-config.toml>" `
  -PythonExecutable "<python-with-project-dependencies>"
```

Open `http://127.0.0.1:8431/web`. Credentials remain in the private runtime
directory or documented environment variables and are never written to source,
logs, or Agent output. A Tavily key can be saved in NewsClaw's local settings,
with `TAVILY_API_KEY` available only as an environment fallback. The key is
used only for authentication, never mixed into a research query or URL, and
never displayed back by the page.

See [the NewsClaw architecture](docs/newsclaw-architecture.md) for data and
failure contracts.

## Boundaries, technology sources, and licensing

The default build is for the owner's personal, local, non-commercial use.
Tushare news entitlement is separate from ordinary membership. Public or paid
use requires separate institutional and content-display rights. This release
does not include quantitative market data, brokerage integration, automatic
trading, webpage crawling, or public deployment.

The main code remains under the [MIT License](LICENSE). Derivative and
third-party notices are in [NOTICE](NOTICE). Parts of the architecture,
interaction model, and code are adapted under OpenBiliClaw's open-source
license; it is a technology source, not NewsClaw's product homepage, support,
release channel, or news service.
