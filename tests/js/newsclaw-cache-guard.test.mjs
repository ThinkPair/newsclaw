import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");
const importSource = async (source, tag) => import(
  `data:text/javascript;base64,${Buffer.from(`${source}\n//# sourceURL=${tag}`).toString("base64")}`
);

class MemoryStorage {
  constructor(entries = {}) { this.values = new Map(Object.entries(entries)); }
  get length() { return this.values.size; }
  key(index) { return [...this.values.keys()][index] ?? null; }
  getItem(key) { return this.values.has(key) ? this.values.get(key) : null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

test("mobile accepts only explicit news delight candidates from batch and stream", async () => {
  const source = await read("src/openbiliclaw/web/js/view-models.js");
  const models = await importSource(source, "newsclaw-view-models.mjs");
  const news = { source_platform: "news", article_id: "n-1", title: "news" };
  const video = { source_platform: "bilibili", bvid: "BV1legacy", title: "video" };

  assert.equal(models.isNewsDelightCandidate(news), true);
  assert.equal(models.isNewsDelightCandidate(video), false);
  assert.equal(models.isNewsDelightCandidate({ bvid: "BV1missing" }), false);
  assert.deepEqual(
    models.normalizeNewsDelightCandidates([video, news]).map((item) => item.source_platform),
    ["news"],
  );

  const recommend = await read("src/openbiliclaw/web/js/views/recommend.js");
  const chat = await read("src/openbiliclaw/web/js/views/chat.js");
  assert.match(recommend, /activeDelights:\s*normalizeNewsDelightCandidates\(delights\)/);
  assert.match(recommend, /item\?\.title\s*&&\s*isNewsDelightCandidate\(item\)/);
  assert.match(recommend, /normalizeNewsDelightCandidates\(state\.activeDelights\)/);
  assert.match(chat, /delightMsgs\s*=\s*normalizeNewsDelightCandidates\(delightData\)/);
});

test("mobile startup purges only stale delight queues and preserves settings", async () => {
  const source = await read("src/openbiliclaw/web/js/legacy-delight-cache.js");
  const guard = await importSource(source, "legacy-delight-cache.mjs");
  const storage = new MemoryStorage({
    "openbiliclaw.delight_queue": JSON.stringify([
      { source_platform: "bilibili", bvid: "BV1legacy" },
      { source_platform: "news", article_id: "n-1" },
    ]),
    "openbiliclaw.delight_queue_limit": "20",
    "unrelated.state": JSON.stringify({ activeDelights: [
      { source_platform: "bilibili", bvid: "BV2legacy" },
      { source_platform: "news", article_id: "n-2" },
    ], theme: "dark" }),
    "unrelated.value": "keep-me",
  });

  const result = guard.purgeLegacyDelightLocalStorage(storage);
  assert.equal(result.updated, 2);
  assert.deepEqual(JSON.parse(storage.getItem("openbiliclaw.delight_queue")), [
    { source_platform: "news", article_id: "n-1" },
  ]);
  assert.deepEqual(JSON.parse(storage.getItem("unrelated.state")), {
    activeDelights: [{ source_platform: "news", article_id: "n-2" }],
    theme: "dark",
  });
  assert.equal(storage.getItem("openbiliclaw.delight_queue_limit"), "20");
  assert.equal(storage.getItem("unrelated.value"), "keep-me");

  const app = await read("src/openbiliclaw/web/js/app.js");
  assert.match(app, /purgeLegacyDelightLocalStorage\(\)/);
  assert.match(app, /purgeLegacyDelightIndexedDb\(\)/);
  assert.match(source, /objectStore\(storeName\)\.openCursor\(\)/);
});

test("probe retries reuse a persisted request_id and submit it in the body", async () => {
  const apiSource = await read("src/openbiliclaw/web/js/api.js");
  const storage = new MemoryStorage();
  const original = {
    fetch: globalThis.fetch,
    location: Object.getOwnPropertyDescriptor(globalThis, "location"),
    localStorage: Object.getOwnPropertyDescriptor(globalThis, "localStorage"),
  };
  const calls = [];
  let failuresRemaining = 1;
  Object.defineProperty(globalThis, "location", {
    configurable: true,
    value: { protocol: "http:", host: "127.0.0.1:8431" },
  });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), body: JSON.parse(options.body) });
    if (failuresRemaining > 0) {
      failuresRemaining -= 1;
      return new Response(JSON.stringify({ detail: "retry" }), {
        status: 503,
        headers: { "content-type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  try {
    const firstPage = await importSource(apiSource, "newsclaw-api-first.mjs");
    await assert.rejects(firstPage.respondToProbe("AI", "like", { surface: "profile" }));
    const secondPage = await importSource(apiSource, "newsclaw-api-reload.mjs");
    await secondPage.respondToProbe("AI", "like", { surface: "profile" });
    assert.equal(calls[0].body.request_id, calls[1].body.request_id);
    assert.ok(calls[0].body.request_id);
  } finally {
    globalThis.fetch = original.fetch;
    if (original.location) Object.defineProperty(globalThis, "location", original.location);
    else delete globalThis.location;
    if (original.localStorage) Object.defineProperty(globalThis, "localStorage", original.localStorage);
    else delete globalThis.localStorage;
  }

  assert.match(apiSource, /respondToAvoidanceProbe[\s\S]*?avoidance-probe-response/);
  assert.match(apiSource, /avoidance-probes\/respond[\s\S]*?request_id:\s*pending\.requestId/);

  const worker = await read("extension/src/background/service-worker.ts");
  assert.match(worker, /chrome\.storage\.local\.get\(null\)/);
  assert.match(worker, /chrome\.storage\.local\.remove\(removeKeys\)/);
  assert.match(worker, /source_platform[\s\S]*?NEWS_DELIGHT_PLATFORM/);
  assert.doesNotMatch(worker, /chrome\.cookies|executeScript|chrome\.tabs\.query/);
});
