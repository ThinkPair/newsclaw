/**
 * Douyin content-script executor — pure helpers + side-effecting
 * orchestrator separated for testability.
 *
 * Task 4 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md). Module
 * isolation: zero imports from extension/src/content/xhs/. The
 * Douyin executor is structurally simpler than its XHS sibling
 * because data arrives via the MAIN-world fetch-tap (not DOM
 * scrape), so we don't need MutationObservers or anchor pickers.
 */

import type {
  DouyinBootstrapItem,
  DouyinScope,
  DouyinSearchItem,
} from "../../main/dy-fetch-tap.ts";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const DOUYIN_BOOTSTRAP_AWEME_PAGE = "OPENBILICLAW_DOUYIN_AWEME_PAGE";

const MAX_BOOTSTRAP_SCROLL_ROUNDS = 30;

const KNOWN_SCOPES: readonly DouyinScope[] = [
  "dy_post",
  "dy_collect",
  "dy_like",
  "dy_follow",
] as const;

// ---------------------------------------------------------------------------
// MAIN-world message validation
// ---------------------------------------------------------------------------

interface MainWorldMessageData {
  type: string;
  scope: DouyinScope;
  items: DouyinBootstrapItem[];
}

/**
 * Filter postMessage events to those originating from our MAIN-world
 * fetch-tap. Uses a sentinel string so unrelated page chatter (and
 * `window.postMessage` calls from other extensions) doesn't leak in.
 */
export function isValidDouyinBootstrapMessage(
  event: MessageEvent,
): event is MessageEvent<MainWorldMessageData> {
  const data: unknown = event?.data;
  if (!data || typeof data !== "object") return false;
  const obj = data as Record<string, unknown>;
  if (obj.type !== DOUYIN_BOOTSTRAP_AWEME_PAGE) return false;
  if (!KNOWN_SCOPES.includes(obj.scope as DouyinScope)) return false;
  if (!Array.isArray(obj.items)) return false;
  return true;
}

// ---------------------------------------------------------------------------
// BootstrapItemSink — accumulates fetch-tap captures with dedup + cap
// ---------------------------------------------------------------------------

interface SinkOptions {
  maxItemsPerScope: number;
}

type ScopeMap<T> = Record<DouyinScope, T>;

function emptyScopeMap<T>(zero: () => T): ScopeMap<T> {
  return {
    dy_post: zero(),
    dy_collect: zero(),
    dy_like: zero(),
    dy_follow: zero(),
  };
}

function itemKey(item: DouyinBootstrapItem): string {
  // dy_follow uses creator_sec_uid as identity; everything else uses
  // aweme_id. Including the scope in the key lets the same aweme_id
  // legitimately appear under both dy_post and dy_collect (the user
  // may have collected something they posted).
  const id = item.scope === "dy_follow" ? item.creator_sec_uid : item.aweme_id;
  return `${item.scope}:${id}`;
}

export class BootstrapItemSink {
  private readonly maxItemsPerScope: number;
  private readonly seenKeys = new Set<string>();
  private readonly byScope: ScopeMap<DouyinBootstrapItem[]> = emptyScopeMap(() => []);

  constructor(opts: SinkOptions) {
    this.maxItemsPerScope = Math.max(0, Math.floor(opts.maxItemsPerScope));
  }

  /**
   * Ingest a batch and return the items that were genuinely new
   * (not duplicates, not over the cap). The caller forwards exactly
   * these to the backend so partial payloads carry only fresh data.
   */
  ingest(items: DouyinBootstrapItem[]): DouyinBootstrapItem[] {
    const newOnes: DouyinBootstrapItem[] = [];
    for (const item of items) {
      if (!item || !KNOWN_SCOPES.includes(item.scope)) continue;
      const key = itemKey(item);
      if (!key.includes(":") || key.endsWith(":")) continue; // empty id
      if (this.seenKeys.has(key)) continue;
      const bucket = this.byScope[item.scope];
      if (bucket.length >= this.maxItemsPerScope) continue;
      this.seenKeys.add(key);
      bucket.push(item);
      newOnes.push(item);
    }
    return newOnes;
  }

  scopeCounts(): ScopeMap<number> {
    return {
      dy_post: this.byScope.dy_post.length,
      dy_collect: this.byScope.dy_collect.length,
      dy_like: this.byScope.dy_like.length,
      dy_follow: this.byScope.dy_follow.length,
    };
  }

  snapshot(): ScopeMap<DouyinBootstrapItem[]> {
    return {
      dy_post: [...this.byScope.dy_post],
      dy_collect: [...this.byScope.dy_collect],
      dy_like: [...this.byScope.dy_like],
      dy_follow: [...this.byScope.dy_follow],
    };
  }
}

/**
 * Adapter: take a raw `MessageEvent`, validate it as a fetch-tap
 * message, and forward the items into the sink. Returns the
 * newly-ingested items so the caller can immediately POST a partial
 * payload to the backend (and update its scrolling state).
 */
export function ingestMainWorldFetchMessage(
  event: MessageEvent,
  sink: BootstrapItemSink,
): DouyinBootstrapItem[] {
  if (!isValidDouyinBootstrapMessage(event)) return [];
  return sink.ingest(event.data.items);
}

// ---------------------------------------------------------------------------
// Scope URL routing
// ---------------------------------------------------------------------------

/**
 * Map a scope to the correct profile-tab URL.
 *
 * - `dy_post` / `dy_like` / `dy_follow`: `/user/<sec_uid>` plus an
 *   appropriate `showTab=...` query.
 * - `dy_collect`: `/user/self?showTab=favorite_collection`. This is
 *   the path jiji262/douyin-downloader confirmed is self-cookie-only;
 *   substituting an explicit sec_uid here would yield a public view
 *   that does NOT include the collections tab, so we always use
 *   `/user/self` for the collection scope.
 *
 * If `secUid` is empty we fall back to `/user/self` for the
 * non-collection scopes too — Douyin's idiom for "the logged-in user".
 */
export function buildScopeUrl(scope: DouyinScope, secUid: string): string {
  const base = secUid ? `https://www.douyin.com/user/${secUid}` : "https://www.douyin.com/user/self";
  switch (scope) {
    case "dy_post":
      return base;
    case "dy_collect":
      return "https://www.douyin.com/user/self?showTab=favorite_collection";
    case "dy_like":
      return `${base}?showTab=like`;
    case "dy_follow":
      return `${base}?showTab=following`;
  }
}

// ---------------------------------------------------------------------------
// Scroll-loop stopping condition (Douyin-native)
// ---------------------------------------------------------------------------

interface ScrollContinueOptions {
  currentCount: number;
  maxItemsPerScope: number;
  round: number;
  maxScrollRounds: number;
  stagnantRounds: number;
  maxStagnantScrollRounds: number;
}

/**
 * Decide whether the scope's scroll loop should make another pass.
 *
 * Douyin-native: stagnation is measured at the **fetch-tap level** —
 * `stagnantRounds` is the consecutive count of scrolls that produced
 * zero new aweme JSON pages. This is strictly more reliable than
 * XHS's DOM-card-count delta (which can't tell virtual-list churn
 * from a true dead end), so we deliberately do not reuse
 * `bootstrapScrollShouldContinue` from xhs/.
 */
export function dyShouldContinueScroll(opts: ScrollContinueOptions): boolean {
  if (opts.currentCount >= opts.maxItemsPerScope) return false;
  if (opts.round >= opts.maxScrollRounds) return false;
  if (opts.stagnantRounds >= opts.maxStagnantScrollRounds) return false;
  return true;
}

// ---------------------------------------------------------------------------
// Partial-payload construction
// ---------------------------------------------------------------------------

interface PartialPayloadInput {
  taskId: string;
  scope: DouyinScope;
  newItems: DouyinBootstrapItem[];
  scopeCounts: ScopeMap<number>;
  round: number;
}

export interface BootstrapPartialPayload {
  task_id: string;
  status: "partial";
  videos: DouyinBootstrapItem[];
  scope_counts: ScopeMap<number>;
  debug?: { round: number; scope: DouyinScope };
}

interface SearchPayloadInput {
  taskId: string;
  keyword: string;
  items: DouyinSearchItem[];
  apiPages: number;
  domItems: number;
  error?: string;
}

interface HotPayloadInput {
  taskId: string;
  sentenceId: string;
  word: string;
  items: DouyinSearchItem[];
  apiPages: number;
  seedAwemeId: string;
  error?: string;
}

export interface SearchResultPayload {
  task_id: string;
  status: "ok" | "empty" | "failed";
  videos: DouyinSearchItem[];
  scope_counts: { dy_search: number };
  error?: string;
  debug?: {
    keyword: string;
    api_pages_fetched: number;
    dom_items_harvested: number;
  };
}

export interface HotResultPayload {
  task_id: string;
  status: "ok" | "empty" | "failed";
  videos: DouyinSearchItem[];
  scope_counts: { dy_hot: number };
  error?: string;
  debug?: {
    sentence_id: string;
    word: string;
    seed_aweme_id: string;
    api_pages_fetched: number;
  };
}

/**
 * Shape the body for a `POST /api/sources/dy/task-result` partial
 * update. The backend's `merge_result` handler is idempotent on
 * (scope, aweme_id) so re-sending an already-known item is harmless;
 * we still try not to do that to keep wire traffic minimal.
 *
 * Empty `newItems` are still legal — they act as a heartbeat that
 * surfaces the latest scope_counts to the daemon.
 */
export function buildBootstrapPartialPayload(
  input: PartialPayloadInput,
): BootstrapPartialPayload {
  return {
    task_id: input.taskId,
    status: "partial",
    videos: input.newItems,
    scope_counts: input.scopeCounts,
    debug: { round: input.round, scope: input.scope },
  };
}

export function buildSearchResultPayload(input: SearchPayloadInput): SearchResultPayload {
  const status = input.error ? "failed" : input.items.length > 0 ? "ok" : "empty";
  return {
    task_id: input.taskId,
    status,
    videos: input.items,
    scope_counts: { dy_search: input.items.length },
    error: input.error,
    debug: {
      keyword: input.keyword,
      api_pages_fetched: input.apiPages,
      dom_items_harvested: input.domItems,
    },
  };
}

export function buildHotResultPayload(input: HotPayloadInput): HotResultPayload {
  const status = input.error ? "failed" : input.items.length > 0 ? "ok" : "empty";
  return {
    task_id: input.taskId,
    status,
    videos: input.items,
    scope_counts: { dy_hot: input.items.length },
    error: input.error,
    debug: {
      sentence_id: input.sentenceId,
      word: input.word,
      seed_aweme_id: input.seedAwemeId,
      api_pages_fetched: input.apiPages,
    },
  };
}

// ---------------------------------------------------------------------------
// Misc helpers
// ---------------------------------------------------------------------------

/**
 * Clamp a `max_scroll_rounds` value to the safe range [0, 30].
 * Negative / NaN / undefined fall through to 0 (which means "no
 * scrolling — read whatever's already on the page"). 30 is the hard
 * ceiling because each round costs a network roundtrip and we want
 * a deterministic worst-case duration.
 */
export function normalizeBootstrapScrollRounds(rounds: number | undefined): number {
  if (rounds === undefined) return 0;
  if (!Number.isFinite(rounds)) return 0;
  const floored = Math.floor(rounds);
  if (floored <= 0) return 0;
  return Math.min(floored, MAX_BOOTSTRAP_SCROLL_ROUNDS);
}
