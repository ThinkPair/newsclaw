/**
 * Tests for the Douyin content-script executor's pure helpers.
 *
 * Task 4 of the Douyin bootstrap import plan
 * (docs/plans/2026-05-06-douyin-bootstrap-import.md).
 *
 * Module isolation: zero imports from extension/src/content/xhs/.
 */

import test from "node:test";
import assert from "node:assert/strict";

import type { DouyinBootstrapItem } from "../src/main/dy-fetch-tap.ts";

import {
  BootstrapItemSink,
  buildBootstrapPartialPayload,
  buildHotResultPayload,
  buildSearchResultPayload,
  buildScopeUrl,
  dyShouldContinueScroll,
  ingestMainWorldFetchMessage,
  isValidDouyinBootstrapMessage,
  normalizeBootstrapScrollRounds,
} from "../src/content/dy/task-executor.ts";

function makeItem(
  scope: DouyinBootstrapItem["scope"],
  awemeId: string,
  overrides: Partial<DouyinBootstrapItem> = {},
): DouyinBootstrapItem {
  return {
    scope,
    aweme_id: awemeId,
    creator_sec_uid: "",
    url: `https://www.douyin.com/video/${awemeId}`,
    title: `t-${awemeId}`,
    author: "",
    author_sec_uid: "",
    cover_url: "",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// BootstrapItemSink
// ---------------------------------------------------------------------------

test("BootstrapItemSink.ingest dedups by (scope, aweme_id) and returns only NEW items", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 100 });
  const first = sink.ingest([makeItem("dy_post", "a"), makeItem("dy_post", "b")]);
  assert.deepEqual(
    first.map((i) => i.aweme_id),
    ["a", "b"],
  );
  // Second batch overlaps: only "c" is new.
  const second = sink.ingest([makeItem("dy_post", "a"), makeItem("dy_post", "c")]);
  assert.deepEqual(
    second.map((i) => i.aweme_id),
    ["c"],
  );
  // Same aweme_id under a DIFFERENT scope is legitimately new.
  const third = sink.ingest([makeItem("dy_collect", "a")]);
  assert.deepEqual(
    third.map((i) => i.scope),
    ["dy_collect"],
  );
});

test("BootstrapItemSink.ingest dedups follow scope by creator_sec_uid", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 100 });
  const item1: DouyinBootstrapItem = {
    scope: "dy_follow",
    aweme_id: "",
    creator_sec_uid: "uid1",
    url: "https://www.douyin.com/user/uid1",
    title: "n1",
    author: "n1",
    author_sec_uid: "uid1",
    cover_url: "",
  };
  const dup: DouyinBootstrapItem = { ...item1 };
  sink.ingest([item1]);
  const second = sink.ingest([dup]);
  assert.equal(second.length, 0);
});

test("BootstrapItemSink.ingest respects maxItemsPerScope cap", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 2 });
  const ingested = sink.ingest([
    makeItem("dy_collect", "a"),
    makeItem("dy_collect", "b"),
    makeItem("dy_collect", "c"),
    makeItem("dy_collect", "d"),
  ]);
  // Only the first 2 made it under the cap; "c" / "d" silently dropped.
  assert.deepEqual(
    ingested.map((i) => i.aweme_id),
    ["a", "b"],
  );
  assert.equal(sink.scopeCounts().dy_collect, 2);
});

test("BootstrapItemSink.scopeCounts reflects accumulated counts per scope", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 100 });
  sink.ingest([makeItem("dy_post", "p1"), makeItem("dy_post", "p2")]);
  sink.ingest([makeItem("dy_collect", "c1")]);
  const counts = sink.scopeCounts();
  assert.equal(counts.dy_post, 2);
  assert.equal(counts.dy_collect, 1);
  assert.equal(counts.dy_like, 0);
  assert.equal(counts.dy_follow, 0);
});

test("BootstrapItemSink.snapshot returns scope→items map", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 100 });
  sink.ingest([makeItem("dy_post", "p1")]);
  const snap = sink.snapshot();
  assert.equal(snap.dy_post.length, 1);
  assert.equal(snap.dy_post[0]!.aweme_id, "p1");
});

// ---------------------------------------------------------------------------
// MAIN-world message ingestion
// ---------------------------------------------------------------------------

test("isValidDouyinBootstrapMessage filters by sentinel type", () => {
  assert.equal(
    isValidDouyinBootstrapMessage({
      data: { type: "OPENBILICLAW_DOUYIN_AWEME_PAGE", scope: "dy_post", items: [] },
    } as MessageEvent),
    true,
  );
  assert.equal(isValidDouyinBootstrapMessage({ data: { type: "OTHER" } } as MessageEvent), false);
  assert.equal(isValidDouyinBootstrapMessage({ data: null } as MessageEvent), false);
  assert.equal(isValidDouyinBootstrapMessage({ data: "string" } as MessageEvent), false);
});

test("ingestMainWorldFetchMessage routes valid messages into the sink", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 100 });
  const newItems = ingestMainWorldFetchMessage(
    {
      data: {
        type: "OPENBILICLAW_DOUYIN_AWEME_PAGE",
        scope: "dy_post",
        items: [makeItem("dy_post", "x")],
      },
    } as MessageEvent,
    sink,
  );
  assert.equal(newItems.length, 1);
  assert.equal(sink.scopeCounts().dy_post, 1);
});

test("ingestMainWorldFetchMessage drops non-bootstrap messages", () => {
  const sink = new BootstrapItemSink({ maxItemsPerScope: 100 });
  const newItems = ingestMainWorldFetchMessage(
    { data: { type: "OTHER", items: [makeItem("dy_post", "x")] } } as MessageEvent,
    sink,
  );
  assert.equal(newItems.length, 0);
  assert.equal(sink.scopeCounts().dy_post, 0);
});

// ---------------------------------------------------------------------------
// Scope URL building
// ---------------------------------------------------------------------------

test("buildScopeUrl points at the right tab for each scope", () => {
  // Posts + likes + follows route through /user/<sec_uid>; collection
  // is the special /user/self?showTab=favorite_collection path that
  // jiji262/douyin-downloader confirmed is self-cookie-only.
  assert.equal(buildScopeUrl("dy_post", "ABC"), "https://www.douyin.com/user/ABC");
  assert.equal(
    buildScopeUrl("dy_collect", "ABC"),
    "https://www.douyin.com/user/self?showTab=favorite_collection",
  );
  assert.equal(
    buildScopeUrl("dy_like", "ABC"),
    "https://www.douyin.com/user/ABC?showTab=like",
  );
  assert.equal(
    buildScopeUrl("dy_follow", "ABC"),
    "https://www.douyin.com/user/ABC?showTab=following",
  );
});

test("buildScopeUrl falls back to /user/self for empty sec_uid (post / like / follow)", () => {
  // Without a sec_uid we still want to land on the user's own profile;
  // /user/self is Douyin's idiom for "the logged-in user".
  assert.equal(buildScopeUrl("dy_post", ""), "https://www.douyin.com/user/self");
  assert.equal(buildScopeUrl("dy_like", ""), "https://www.douyin.com/user/self?showTab=like");
});

// ---------------------------------------------------------------------------
// Scroll-loop stopping condition (Douyin-native)
// ---------------------------------------------------------------------------

test("dyShouldContinueScroll stops when current count hits cap", () => {
  assert.equal(
    dyShouldContinueScroll({
      currentCount: 300,
      maxItemsPerScope: 300,
      round: 5,
      maxScrollRounds: 15,
      stagnantRounds: 0,
      maxStagnantScrollRounds: 5,
    }),
    false,
  );
});

test("dyShouldContinueScroll stops when round budget is exhausted", () => {
  assert.equal(
    dyShouldContinueScroll({
      currentCount: 50,
      maxItemsPerScope: 300,
      round: 15,
      maxScrollRounds: 15,
      stagnantRounds: 0,
      maxStagnantScrollRounds: 5,
    }),
    false,
  );
});

test("dyShouldContinueScroll stops when stagnant-rounds threshold hit", () => {
  // Even with budget remaining, if we've gone 5 rounds without new
  // pages from the fetch-tap, the scope is exhausted.
  assert.equal(
    dyShouldContinueScroll({
      currentCount: 60,
      maxItemsPerScope: 300,
      round: 8,
      maxScrollRounds: 15,
      stagnantRounds: 5,
      maxStagnantScrollRounds: 5,
    }),
    false,
  );
});

test("dyShouldContinueScroll keeps going while everything is fine", () => {
  assert.equal(
    dyShouldContinueScroll({
      currentCount: 60,
      maxItemsPerScope: 300,
      round: 3,
      maxScrollRounds: 15,
      stagnantRounds: 1,
      maxStagnantScrollRounds: 5,
    }),
    true,
  );
});

// ---------------------------------------------------------------------------
// Partial payload construction
// ---------------------------------------------------------------------------

test("buildBootstrapPartialPayload shapes the POST body for /api/sources/dy/task-result", () => {
  const payload = buildBootstrapPartialPayload({
    taskId: "t1",
    scope: "dy_collect",
    newItems: [makeItem("dy_collect", "x", { title: "demo" })],
    scopeCounts: { dy_post: 0, dy_collect: 5, dy_like: 0, dy_follow: 0 },
    round: 2,
  });
  assert.equal(payload.task_id, "t1");
  assert.equal(payload.status, "partial");
  assert.equal(payload.videos.length, 1);
  assert.equal(payload.videos[0]!.aweme_id, "x");
  assert.equal(payload.videos[0]!.title, "demo");
  assert.equal(payload.scope_counts.dy_collect, 5);
  assert.equal(payload.debug?.round, 2);
});

test("buildBootstrapPartialPayload omits videos when newItems is empty (heartbeat-style)", () => {
  // A round that scrolled but produced no new items still wants to
  // refresh scope_counts so the backend has up-to-date progress for
  // the daemon to read; the videos[] just stays empty.
  const payload = buildBootstrapPartialPayload({
    taskId: "t1",
    scope: "dy_collect",
    newItems: [],
    scopeCounts: { dy_post: 0, dy_collect: 5, dy_like: 0, dy_follow: 0 },
    round: 4,
  });
  assert.equal(payload.videos.length, 0);
  assert.equal(payload.scope_counts.dy_collect, 5);
});

test("buildSearchResultPayload shapes the final search task result", () => {
  const payload = buildSearchResultPayload({
    taskId: "search-task",
    keyword: "猫",
    items: [
      {
        scope: "dy_search",
        aweme_id: "7788",
        url: "https://www.douyin.com/video/7788",
        title: "搜索结果",
        author: "作者",
        author_sec_uid: "MS4wAuthor",
        cover_url: "",
      },
    ],
    apiPages: 1,
    domItems: 0,
  });
  assert.equal(payload.task_id, "search-task");
  assert.equal(payload.status, "ok");
  assert.equal(payload.videos.length, 1);
  assert.equal(payload.videos[0]!.scope, "dy_search");
  assert.equal(payload.scope_counts.dy_search, 1);
  assert.equal(payload.debug?.keyword, "猫");
  assert.equal(payload.debug?.api_pages_fetched, 1);
});

test("buildHotResultPayload shapes the final hot task result", () => {
  const payload = buildHotResultPayload({
    taskId: "hot-task",
    sentenceId: "2495363",
    word: "热点词",
    items: [
      {
        scope: "dy_hot",
        aweme_id: "8899",
        url: "https://www.douyin.com/video/8899",
        title: "热点相关结果",
        author: "作者",
        author_sec_uid: "MS4wAuthor",
        cover_url: "",
        hot_word: "热点词",
        sentence_id: "2495363",
        seed_aweme_id: "seed-1",
      },
    ],
    apiPages: 1,
    seedAwemeId: "seed-1",
  });

  assert.equal(payload.task_id, "hot-task");
  assert.equal(payload.status, "ok");
  assert.equal(payload.videos.length, 1);
  assert.equal(payload.videos[0]!.scope, "dy_hot");
  assert.equal(payload.scope_counts.dy_hot, 1);
  assert.equal(payload.debug?.sentence_id, "2495363");
  assert.equal(payload.debug?.seed_aweme_id, "seed-1");
});

// ---------------------------------------------------------------------------
// Round normalization
// ---------------------------------------------------------------------------

test("normalizeBootstrapScrollRounds clamps to the safe 0..30 range", () => {
  assert.equal(normalizeBootstrapScrollRounds(15), 15);
  assert.equal(normalizeBootstrapScrollRounds(-3), 0);
  assert.equal(normalizeBootstrapScrollRounds(99), 30); // hard ceiling
  assert.equal(normalizeBootstrapScrollRounds(undefined), 0);
  assert.equal(normalizeBootstrapScrollRounds(NaN), 0);
});
