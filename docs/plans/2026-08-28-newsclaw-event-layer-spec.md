# NewsClaw Phase 3: durable event layer

## Goal

Phase 3 turns the Phase 2 tracker ledger into a durable, source-independent event model. The unit the product remembers is no longer one article or one tracking run. It is one real-world event, the evidence attached to it, and the verified state changes that occurred over time.

This phase changes storage and API contracts only. It does not redesign the current desktop surface, add a new collection loop, or invoke Tavily automatically.

## Product truth

- An article is evidence, not the event itself.
- A tracker is a user's monitoring instruction, not the event itself.
- A check is an execution receipt, not a user-visible change.
- A change is unread only when it records a new, durable event state transition.
- A forecast, market expectation, sentiment, or AI inference must never be exposed as an objective change.

## Durable objects

### Event

One stable real-world subject with a canonical title, first/last seen times, current state, monotonic state version, status, and optional merge lineage. Event identity is the exact normalized `story_key`; wording from a user's observation is not identity. If no story key exists, the seed remains tracker-local rather than being heuristically merged into another user's subject.

### Membership

One immutable evidence revision connected to an event. Its relation is explicit: origin evidence, supports a change, related but no change, or contradicts a change. Source identity, URL, publication/retrieval time, content scope, snapshot, and legacy completeness remain auditable.

### Event change

One event-scoped previous-state to new-state transition. It owns unread state. The record separates fact from inference and carries evidence scope, evidence status, lifecycle, conflict status, effective/detected times, explicit `effective_precision` (`date`, `minute`, or `unknown`), and objectivity classification.

Only an already occurred, time-scoped, evidence-supported `objective_change` with confirmed objectivity may enter the default event-change feed. Expectations and AI interpretation remain available as secondary context, never as fact.

### Lineage claim

One auditable candidate edge from a successor change to the predecessor it claims to supersede or invalidate. It retains canonical endpoints, requested lifecycle, immutable source event/change IDs, and `active`, `contested`, `resolved`, or `rejected` status. Canonical IDs may be remapped during deduplication; origin IDs and rejected candidates are never rewritten away.

## Projection rules

The Phase 2 completion transaction is the single write boundary:

1. `changed` creates or reuses an event, attaches the evidence revision, and creates one event-scoped change when its transition identity is new.
2. A second source supporting the same transition attaches another membership and upgrades evidence scope/status without creating another unread change.
3. A revised item keeps its immutable evidence revision and may support the same change.
4. `related_no_change` creates only a membership.
5. `unchanged`, `valid_empty`, `retry_wait`, and `failed` create no event change.
6. Two independently durable objective transitions with the same event, effective slot, and previous state but different new states quarantine both as `contested`, remove them from the objective feed, freeze the event's current state, and expose a separate conflict ledger. A title-only, future, expectation, sentiment, analysis, or otherwise provisional contradiction remains secondary evidence and cannot freeze the event by declaration alone.
7. A superseding transition changes lifecycle only after the new row independently passes the durable fact gate. An explicit stronger resolution may supersede the disputed rows and resume current-state projection.
8. If one canonical successor receives different durable predecessor claims, only the successor is quarantined. Both candidate edges remain in the lineage ledger and neither predecessor is mislabeled as a factual conflict. One explicit durable resolution atomically marks the chosen edge `resolved`, every other candidate `rejected`, applies only the winner's lifecycle, and closes the event conflict.
9. Any projection failure rolls back the original check completion as one transaction.

Identity has two layers. Event identity is the exact normalized story key. Change identity v3 is derived inside that event from a stable change-type family, normalized previous/new state, and the effective time plus its explicit precision. It must not contain publisher, technical provider, URL, item key, tracker wording, or another source-specific value. Labels such as progress, confirmation, and new fact normalize to a stable family so presentation wording cannot duplicate the same transition. If a producer used the article publication timestamp only as a fallback, identity uses that source-local calendar date rather than inventing an event minute. Controlled paraphrase folding is fail-closed: both states must have the same explicit action family, the same positive/pending/negative/stopped polarity, and exactly the same residual subject after adjacent state phrases are removed. Mixed polarity, different numbers, or any other residual difference remains separate for audit instead of being fuzzily merged.

## Migration

Migration is additive, restart-safe, and idempotent:

- existing `events` behavior data remains untouched;
- existing tracking/check/evidence rows remain the audit source;
- old trackers receive conservative event identities;
- origin and related evidence become memberships;
- only historical `changed` evidence becomes a secondary event change, classified as `analysis / unconfirmed / provisional / partial_legacy`;
- legacy evidence is revalidated from actually retained content rather than trusted metadata; title repetition, HTML boilerplate, or a false `full_text` label cannot pass the fact gate;
- if revalidation downgrades a once-accepted successor, its stale supersede authority is revoked, the predecessor lifecycle is restored, and the rejected lineage claim remains auditable;
- legacy evidence that cannot reconstruct every corroborating source is marked partial rather than invented, cannot enter the objective feed, and does not gain objective-feed unread status;
- pre-release change identities are rebuilt to v3, semantic duplicates are folded, and aliases keep old event/change reads valid;
- existing canonical supersede pointers seed the lineage-claim ledger on upgrade; change aliases and repeated event merges remap candidate endpoints without changing immutable source IDs;
- a separate resumable origin repair restores per-tracker origin lineage even after the original backfill marker is complete;
- rerunning backfill creates no duplicate events, memberships, changes, or unread counts.

## Read contract

Phase 3 exposes read-only event APIs plus an idempotent mark-read mutation:

- `GET /api/news/changes`: keyset-paginated objective event-change feed with all/unread filtering;
- `GET /api/news/events/{event_id}`: event detail and objective counts;
- `GET /api/news/events/{event_id}/changes`: one event's keyset-paginated objective timeline;
- `GET /api/news/events/{event_id}/evidence`: evidence memberships, optionally scoped to one change;
- `POST /api/news/changes/{change_id}/read`: mark one objective event change read;
- `GET /api/news/events/{event_id}/conflicts`: list contested transitions independently from the objective feed, including structured predecessor candidate claims and their origin/status when the conflict is about lineage;
- `POST /api/news/conflicts/{change_id}/read`: idempotently mark one contested transition read.

Invalid cursors return a validation error. Missing event/change IDs return not found. Reading a change twice is successful and reports no second update.

## Acceptance gates

- same real-world transition from two trackers or two sources yields one event change and one unread unit;
- publication-time fallback and controlled state paraphrases can corroborate one transition, while mixed polarity, distinct numeric values, and distinct residual subjects never merge;
- different evidence revisions remain inspectable;
- mutually exclusive durable outcomes are visible as conflicts, excluded from the objective feed, and cannot advance current state until explicitly resolved;
- two durable predecessor candidates survive merge/restart/alias folding; one explicit resolution chooses a winner, retains the rejected loser for audit, and reduces the event conflict count to zero;
- no-change and failed checks never leak into the change feed;
- legacy backfill is lossless within recorded evidence and safe to rerun;
- event projection and Phase 2 completion roll back together on failure;
- keyset pagination is stable under concurrent inserts;
- expectations, sentiment, and AI inference cannot pass the confirmed-objective default-feed gate;
- no new collection, profile mutation, Tavily request, notification loop, or UI behavior is introduced.
