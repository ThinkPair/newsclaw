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
  assert.match(js, /aria-busy/);
  assert.match(js, /不会用旧闻补位/);
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
  assert.match(js, /delightQueueRequestGeneration/);
});
