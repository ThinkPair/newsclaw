import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");

test("today pick has its own real refresh action and never aliases reshuffle", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);

  assert.match(html, /id="reshuffleBtn"[^>]*aria-label="换一批"/);
  assert.match(html, /id="todayRefreshBtn"[^>]*aria-label="刷新今天新闻"/);
  assert.match(html, /新材料判断工作台/);
  assert.doesNotMatch(html, /今日判断工作台/);
  assert.match(js, /safeBind\("#reshuffleBtn", "click", reshuffle\)/);
  assert.match(js, /safeBind\("#todayRefreshBtn", "click", \(\) => \{ void refreshTodaySelection\(\); \}\)/);
  assert.match(js, /requestJsonStrict\("\/news\/refresh"/);
  assert.match(js, /todayRefreshInFlight/);
  assert.match(js, /todayRefreshAwaitingResults/);
  assert.match(js, /aria-busy/);
  assert.match(js, /不会用旧闻补位/);
  assert.match(js, /refresh\.pool_updated" && state\.newsclawMode === true[\s\S]{0,160}?scheduleDelightQueueRefresh/);
  assert.match(js, /后台仍在分析，已停止自动等待/);
  assert.match(js, /function scheduleTodayMidnightRollover\(/);
});

test("today pick enforces China calendar time on batch, stream, and retained state", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const functionSource = (name) => {
    const start = js.indexOf(`function ${name}(`);
    if (start < 0) return "";
    const end = js.indexOf("\n    function ", start + 10);
    return js.slice(start, end < 0 ? js.length : end);
  };
  const newsSource = functionSource("isNewsDelightItem");
  const dateSource = functionSource("chinaDateKey");
  const todaySource = functionSource("isTodayNewsDelightItem");
  assert.ok(newsSource && dateSource && todaySource);

  const isToday = new Function(
    `const NEWS_DELIGHT_PLATFORM = "news";\n${newsSource}\n${dateSource}\n${todaySource}; return isTodayNewsDelightItem;`,
  )();
  const now = new Date("2026-08-29T16:00:00Z");
  assert.equal(isToday({ source_platform: "news", published_at: "2026-08-29T16:00:00Z" }, now), true);
  assert.equal(isToday({ source_platform: "news", published_at: "2026-08-29T15:59:59Z" }, now), false);
  assert.equal(isToday({ source_platform: "news", published_at: "bad-date" }, now), false);
  assert.equal(isToday({ source_platform: "news", published_at: "2026-08-29T16:06:00Z" }, now), false);
  assert.equal(isToday({ source_platform: "bilibili", published_at: "2026-08-29T16:00:00Z" }, now), false);

  assert.match(js, /items[\s\S]{0,240}?filter\(\(item\) => state\.newsclawMode !== true \|\| isTodayNewsDelightItem\(item\)\)/);
  assert.match(js, /event\.type === "delight\.candidate"[\s\S]{0,260}?isTodayNewsDelightItem\(delight\)/);
  assert.match(js, /state\.delights = state\.delights\.filter\(isTodayNewsDelightItem\)/);
  assert.match(js, /cache:\s*"no-store"/);
  assert.match(js, /delightQueueFetchPromise/);
  assert.match(js, /while \(delightQueueFetchQueued\)/);
});

test("today queue keeps processing honest and rolls over at China midnight", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const functionSource = (name) => {
    const start = js.indexOf(`function ${name}(`);
    if (start < 0) return "";
    const end = js.indexOf("\n    function ", start + 10);
    return js.slice(start, end < 0 ? js.length : end);
  };
  const midnightSource = functionSource("millisecondsUntilNextChinaMidnight");
  const loadStateSource = functionSource("todayQueueLoadState");
  const preserveStatusSource = functionSource("shouldPreserveTodayQueueStatus");
  const renderCurrentSource = functionSource("delightRenderIsCurrent");
  assert.ok(midnightSource && loadStateSource && preserveStatusSource && renderCurrentSource);

  const untilMidnight = new Function(`${midnightSource}; return millisecondsUntilNextChinaMidnight;`)();
  const loadState = new Function(`${loadStateSource}; return todayQueueLoadState;`)();
  const preserveStatus = new Function(`${preserveStatusSource}; return shouldPreserveTodayQueueStatus;`)();
  const renderIsCurrent = new Function(`${renderCurrentSource}; return delightRenderIsCurrent;`)();
  const oneMinuteBeforeChinaMidnight = new Date("2026-08-30T15:59:00Z");

  assert.equal(untilMidnight(oneMinuteBeforeChinaMidnight), 60_250);
  assert.equal(loadState(1, "processing"), "processing");
  assert.equal(loadState(1, "ready"), "ready");
  assert.equal(loadState(0, "empty"), "empty");
  assert.equal(preserveStatus(true, false, false, "processing", 1, 1), true);
  assert.equal(preserveStatus(true, false, false, "failed", 1, 1), true);
  assert.equal(preserveStatus(true, false, false, "ready", 1, 2), true);
  assert.equal(preserveStatus(true, false, false, "ready", 2, 2), false);
  assert.equal(preserveStatus(false, true, true, "processing", 1, 2), false);
  const card = {};
  assert.equal(renderIsCurrent(1, 1, card, card), true);
  assert.equal(renderIsCurrent(1, 2, card, card), false);
  assert.equal(renderIsCurrent(1, 1, card, null), false);
  assert.equal(renderIsCurrent(1, 1, null, null), false);
  assert.match(js, /window\.clearTimeout\(delightContentApplyTimer\)/);
  assert.match(js, /const delightAtRender = state\.delight/);
});

test("today queue serializes overlapping reads and every caller receives the newest payload", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const start = js.indexOf("function fetchDelightQueue()");
  const end = js.indexOf("\n    function ", start + 10);
  const fetchSource = js.slice(start, end);
  assert.ok(fetchSource);

  const buildHarness = new Function(`
    let delightQueueFetchPromise = null;
    let delightQueueFetchQueued = false;
    const releases = [];
    let calls = 0;
    function fetchDelightQueueOnce() {
      calls += 1;
      return new Promise((resolve) => releases.push(resolve));
    }
    ${fetchSource}
    return { fetchDelightQueue, releases, calls: () => calls };
  `);
  const harness = buildHarness();
  const first = harness.fetchDelightQueue();
  const second = harness.fetchDelightQueue();
  assert.equal(first, second);
  assert.equal(harness.calls(), 1);

  harness.releases.shift()({ state: "processing", marker: 1 });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.calls(), 2);
  harness.releases.shift()({ state: "ready", marker: 2 });

  assert.deepEqual(await first, { state: "ready", marker: 2 });
  assert.deepEqual(await second, { state: "ready", marker: 2 });
});
