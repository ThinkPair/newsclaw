import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");

test("desktop source settings persist only explicit per-source changes", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  const persistStart = js.indexOf("async function persistNewsSourceSettings()");
  const persistEnd = js.indexOf("function newsSourceGroup", persistStart);
  const persist = persistStart >= 0 && persistEnd > persistStart
    ? js.slice(persistStart, persistEnd)
    : "";
  assert.ok(persist, "source persistence function should remain inspectable");
  assert.match(html, /id="newsRssSourceItems"/);
  assert.match(js, /function renderNewsRssSourceCards\(items\)/);
  assert.match(js, /body: JSON\.stringify\(\{ enabled \}\)/);
  assert.match(js, /enabled === newsSourceOriginalEnabled\.get\(sourceId\)/);
  assert.doesNotMatch(persist, /JSON\.stringify\(values\)/);
  assert.doesNotMatch(persist, /newsSourceControlValues\("official_rss"\)/);
  assert.doesNotMatch(persist, /newsSourceControlValues\("custom_rss"\)/);
  assert.doesNotMatch(html, /id="newsOfficialRssEnabled"|id="newsCustomRssEnabled"/);
  assert.match(js, /refreshNewsSourceContracts\(\{ throwOnError: true \}\)/);
  assert.match(js, /settingsDirtyFields\.delete\(input\.id\)/);
});

test("desktop does not expose the video-era destructive reinitialize action", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  assert.doesNotMatch(html, /id="reinitBtn"|id="reinitResetCognition"/);
  assert.doesNotMatch(js, /handleDesktopReinitClick/);
  assert.doesNotMatch(js, /safeBind\("#reinitBtn"/);
  assert.match(html, /一键清空并重建画像”尚未开放/);
  assert.match(html, /收藏、稍后读和当前列表筛选只改变本机内容状态/);
});

test("first-run setup probes the selected AI draft and has no legacy init fallback", async () => {
  const html = await read("src/openbiliclaw/web/setup/index.html");
  assert.match(html, /const\s+probeAndSaveLlm\s*=\s*async/);
  assert.match(html, /request\("\/config\/probe-service"/);
  assert.match(html, /kind: "llm", config: \{ llm \}/);
  assert.match(html, /await waitForConfigApply\(result\)/);
  assert.match(html, /state\.bootstrapIdentity !== identity/);
  assert.match(html, /return request\("\/news\/bootstrap"/);
  assert.doesNotMatch(html, /request\("\/init"/);
  assert.doesNotMatch(html, /request\("\/ping"/);
});

test("mobile save feedback is local-only and cannot revive legacy sync tasks", async () => {
  const [recommend, saved, app, desktop] = await Promise.all([
    read("src/openbiliclaw/web/js/views/recommend.js"),
    read("src/openbiliclaw/web/js/views/saved.js"),
    read("src/openbiliclaw/web/js/app.js"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  assert.doesNotMatch(recommend, /平台记录不变|平台同步状态可在/);
  assert.match(saved, /sync_task_id: ""/);
  assert.match(saved, /error_code: "local_only_source"/);
  assert.match(saved, /仅保存在本机新闻爪，不会修改新闻网站账号/);
  assert.doesNotMatch(app, /openLegacyMobileSettings|保存时自动同步到对应平台/);
  assert.match(desktop, /const localOnly = String\(canonical\.source_platform \|\| ""\)/);
  assert.match(desktop, /error_code: localOnly \? "local_only_source"/);
  assert.doesNotMatch(desktop, /已保存到本地，平台同步状态可在/);
});

test("migration and mobile QR surfaces fail closed under NewsClaw identity", async () => {
  const [html, js, api] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
    read("src/openbiliclaw/api/app.py"),
  ]);
  assert.match(html, /\.newsclaw-backup/);
  assert.match(js, /application\/vnd\.newsclaw\.backup\+zip/);
  assert.match(js, /qrInfo\?\.lan_accessible === false/);
  assert.match(js, /copyButton\.disabled = true/);
  assert.match(api, /application\/vnd\.newsclaw\.backup\+zip/);
  assert.match(api, /body\["lan_accessible"\]/);
});

test("browser extension visible controls all have concrete handlers", async () => {
  const [html, js] = await Promise.all([
    read("extension/popup/popup.html"),
    read("extension/popup/popup.js"),
  ]);
  for (const id of [
    "retryButton", "settingsButton", "stateSettingsButton",
    "cancelSettingsButton", "openFullButton", "settingsForm",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
    assert.match(js, new RegExp(`#${id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"\\)\\.addEventListener`));
  }
  assert.match(js, /normalizeEndpoint\(endpointInput\.value\)/);
  assert.match(js, /only|只允许 127\.0\.0\.1 或 localhost/);
});
