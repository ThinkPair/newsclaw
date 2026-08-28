# NewsClaw

**Understand you first, then proactively find real news.**

📌 Latest: **v0.1.0**

> Release boundary: NewsClaw does not use the upstream `openbiliclaw-v*` aggregate Release as its own update or download channel; this GitHub repository is private source hosting only.

NewsClaw is an independent local-only personalized news agent for one person.
It owns its news sources, product semantics, data, and configuration, and does
not require another product at runtime.

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
- Preserve the five-layer Event → Preference → Awareness → Insight →
  Soul memory loop, evidence, confidence, probes, and manual overrides.
- Learn from opening recommendations, likes, dislikes, ignores, comments, and chat. Saved and read-later items remain local library actions and do not automatically alter the profile.
- Use a priority queue → single structured judgment → action/memory workbench;
  rank by user fit, evidence, timeliness, and event impact instead of repeating article text across the screen.
- Explain why an item fits, its possible impact chain, and what to watch next;
  never emit buy/sell advice.
- Respect title-, summary-, and full-text evidence levels.
- Treat one failed source as degraded operation, not a successful global
  refresh.
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
Tushare / RSS → news evidence → normalized, deduplicated local batch → evaluation/ranking → workbench
                                      │
                                      └→ [Phase 2] due observation → local-rule shortlist
                                                                         → structured model check
                                                                         ├→ material change → timeline + unread
                                                                         └→ no change → check history only

Workbench → explicit feedback → profile
          └→ save/click/feedback → immutable judgment snapshot → local history

DSH Agent / REST research request → fresh local cache or Tavily Search/Extract
                                  → cited evidence + separate research history
                                  └→ no automatic profile, candidate, or pool write
```

## Four clients

Desktop Web, mobile Web/PWA, a display-only browser side panel, and a DeepSeek
Harness trailing grid column all share one backend, primary SQLite database,
profile, recommendation pool, saved items, and chat history. On-demand research
evidence stays in a separate SQLite ledger under the same private data
directory, so it is not mixed with profile or pool state. The extension never
reads news pages or cookies. The DSH panel defaults to 380px, supports 280–560px,
and keeps at least 400px for the conversation area.

The complete Phase 2 tracking surface is desktop-only. Mobile/PWA, the browser
extension, and DSH keep their existing news capabilities in this phase, but do
not expose a tracking center, change timeline, or manual check-now action. A
future client must consume the same backend truth rather than create a second
tracking state.

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
