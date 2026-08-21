# NewsClaw

**Understand you first, then proactively find real news.**

NewsClaw is an independent local-only personalized news agent for one person.
It owns its news sources, product semantics, data, and configuration, and does
not require another product at runtime.

> The local production cutover completed on 2026-08-17. The formal service
> runs at `http://127.0.0.1:8430` with an independent database, a dedicated
> `NewsClaw` Windows startup entry, and the DSH 2.0.0 plugin. Development keeps
> using port 8431 and does not overwrite the production runtime.

## Product contract

- Collect from documented Tushare news endpoints and official/custom RSS or
  Atom feeds only. No webpage crawler, page injection, or cookie access.
- Preserve the five-layer Event → Preference → Awareness → Insight →
  Soul memory loop, evidence, confidence, probes, and manual overrides.
- Learn from opening recommendations, likes, dislikes, ignores, comments, and chat. Saved and read-later items remain local library actions and do not automatically alter the profile.
- Explain why an item fits, its possible impact chain, and what to watch next;
  never emit buy/sell advice.
- Respect title-, summary-, and full-text evidence levels.
- Treat one failed source as degraded operation, not a successful global
  refresh.

## Sources

The first release supports Tushare `news`, `major_news`, and `cctv_news`, plus
publisher-provided or custom RSS/Atom feeds. Every article retains publisher,
technical provider, canonical URL, publish/retrieve times, content level,
content hash, and freshness state.

## Four clients

Desktop Web, mobile Web/PWA, a display-only browser side panel, and a DeepSeek
Harness trailing grid column all share one backend, SQLite database, profile,
recommendation pool, saved items, and chat history. The extension never reads
news pages or cookies. The DSH panel defaults to 380px, supports 280–560px, and
keeps at least 400px for the conversation area.

## Local preview

```powershell
& .\scripts\run-newsclaw-dev.ps1 -Port 8431 `
  -RuntimeRoot "<private-runtime-directory>" `
  -SourceConfig "<existing-local-config.toml>" `
  -PythonExecutable "<python-with-project-dependencies>"
```

Open `http://127.0.0.1:8431/web`. Credentials remain in the private runtime
directory or documented environment variables and are never written to source,
logs, or Agent output.

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
