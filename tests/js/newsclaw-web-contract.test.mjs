import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");

test("desktop keeps the OpenBiliClaw shell while exposing news semantics", async () => {
  const html = await read("src/openbiliclaw/web/desktop/index.html");
  assert.match(html, /NewsClaw/);
  assert.match(html, /你的本地新闻理解入口/);
  assert.match(html, /阅读原文/);
  assert.match(html, /稍后读/);
  assert.match(html, /data-news-source="tushare_news"/);
  assert.match(html, /data-news-source="tushare_major_news"/);
  assert.match(html, /data-news-source="tushare_cctv_news"/);
  assert.match(html, /id="newsRssSourceItems"/);
  assert.match(html, /添加或移除自定义 RSS/);
  assert.doesNotMatch(html, /id="newsOfficialRssEnabled"|id="newsCustomRssEnabled"/);
  assert.doesNotMatch(html, /阿B|你的本地全网推荐入口|Bangumi|B\s*站|bilibili\.com/);
});

test("desktop exposes write-only Tavily research credentials with an explicit privacy boundary", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  for (const id of [
    "newsTavilyApiKey",
    "newsTavilyApiKeyStatus",
    "newsSaveTavilyApiKeyBtn",
    "newsDeleteTavilyApiKeyBtn",
    "newsProbeTavilyBtn",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /Tavily 按需研究（不是新闻源）/);
  assert.match(html, /查询和公开 URL 会发送给 Tavily/);
  assert.match(html, /研究查询或 URL 中不得包含私人资料或任何凭据/);
  assert.match(html, /API Key 仅保存在本机后端，调用 Tavily 时只用于鉴权/);
  assert.match(html, /不会混入研究查询正文，也不会在页面展示或回显/);
  assert.match(html, /验证连接（不消耗搜索额度）/);
  assert.match(js, /"\/research\/providers\/tavily\/credential"/);
  assert.match(js, /"\/research\/providers\/tavily\/probe"/);
  assert.match(js, /JSON\.stringify\(\{ api_key: apiKey \}\)/);
  assert.doesNotMatch(js, /localStorage[^\n]*Tavily|storageSet[^\n]*apiKey/i);
});

test("desktop surprise hero stays bounded for long news copy", async () => {
  const css = await read("src/openbiliclaw/web/desktop/assets/css/app.css");
  assert.match(css, /@media \(min-width: 821px\)[\s\S]*?\.delight\s*\{[\s\S]*?height:\s*380px;[\s\S]*?max-height:\s*380px;/);
  assert.match(css, /\.delight-copy h3\s*\{[\s\S]*?-webkit-line-clamp:\s*2;/);
  assert.match(css, /#delightReason\s*\{[\s\S]*?-webkit-line-clamp:\s*3;/);
  assert.match(css, /\.delight-excerpt\.is-expanded \.delight-excerpt-text\s*\{[\s\S]*?max-height:\s*112px;[\s\S]*?overflow:\s*auto;/);
  assert.match(css, /@media \(max-width: 820px\)[\s\S]*?\.delight\s*\{[^}]*height:\s*auto;[^}]*max-height:\s*none;/);
});

test("desktop rejects and purges legacy non-news delight queues", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  assert.match(js, /function isNewsDelightItem\(item\)[\s\S]*?source_platform[\s\S]*?=== NEWS_DELIGHT_PLATFORM/);
  assert.match(js, /function normalizeDelight\(item\)\s*\{[\s\S]*?if \(!isNewsDelightItem\(item\)\) return null;/);
  assert.match(js, /function setActiveDelight\([\s\S]*?state\.delights\.filter\(isNewsDelightItem\)/);
  assert.match(js, /function purgeLegacyDelightLocalStorage\(\)/);
  assert.match(js, /function purgeLegacyDelightIndexedDb\(\)/);
  assert.match(js, /storage\.removeItem\(key\)/);
  assert.match(js, /transaction\.objectStore\(storeName\)\.openCursor\(\)/);

  const guardSource = js.match(/function isNewsDelightItem\(item\) \{[\s\S]*?\n    \}/)?.[0];
  assert.ok(guardSource, "desktop delight guard should remain executable");
  const isNews = new Function(
    "item",
    `const NEWS_DELIGHT_PLATFORM = "news"; ${guardSource}; return isNewsDelightItem(item);`,
  );
  assert.equal(isNews({ source_platform: "news", article_id: "n1" }), true);
  assert.equal(isNews({ source_platform: "bilibili", bvid: "BV1legacy" }), false);
  assert.equal(isNews({ bvid: "BV1missingPlatform" }), false);
});

test("every hard desktop binding still has a DOM target", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]));
  const boundIds = [...js.matchAll(/safeBind\("#([^"]+)"/g)].map((match) => match[1]);
  const missing = [...new Set(boundIds.filter((id) => !ids.has(id)))];
  assert.deepEqual(missing, []);
  assert.doesNotMatch(js, /safeBind\("#savedAutoSync"/);
  assert.match(js, /"\/news\/sources\/probe"/);
  assert.match(js, /"\/news\/settings"/);
  assert.match(js, /typeof item\.health === "object"/);
  assert.match(js, /function recommendationMediaLabel/);
  assert.match(js, /const mediaCount = recommendationMediaCounts\(\)\.size/);
  assert.match(js, /mediaCounts\.get\(name\)/);
  assert.doesNotMatch(js, /\balert\s*\(/);
});

test("desktop settings cannot mutate inherited video-only discovery controls", async () => {
  const [html, js, css] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
  ]);
  for (const id of [
    "settingsTabAdvanced",
    "settingsPanelAdvanced",
    "keywordGenerationMode",
    "visualProfileEnabled",
    "keyframeEnabled",
    "keyframeMaxFrames",
    "keyframeFetchLimit",
    "danmakuEnabled",
    "danmakuFetchLimit",
    "danmakuMaxChars",
    "embeddingMultimodalEnabled",
    "multimodalEvaluationEnabled",
    "candidateEvalConcurrency",
    "multimodalBatchSize",
    "multimodalImageMaxPx",
    "multimodalImageQuality",
    "multimodalImageTimeout",
  ]) {
    assert.doesNotMatch(html, new RegExp(`id="${id}"`), id);
    assert.doesNotMatch(js, new RegExp(`#${id}\\b`), id);
  }
  assert.doesNotMatch(html, /正文证据加权|同事件聚类|搜索词生成模式|候选封面参与 LLM 评估/);
  assert.match(js, /discovery:\s*\{ \.\.\.\(state\.config\?\.discovery \|\| \{\}\) \}/);
  assert.match(js, /embedding:\s*\{ \.\.\.\(state\.config\?\.llm\?\.embedding \|\| \{\}\), \.\.\.embedding \}/);
  assert.doesNotMatch(js, /keyword_generation_mode:|keyframe_enabled:|danmaku_enabled:|multimodal_evaluation_enabled:/);
  assert.match(css, /\.settings-tabs[^}]*grid-template-columns:\s*repeat\(6,/);
});

test("mobile compatibility layer accepts canonical NewsClaw article fields", async () => {
  const [api, models, recommendations] = await Promise.all([
    read("src/openbiliclaw/web/js/api.js"),
    read("src/openbiliclaw/web/js/view-models.js"),
    read("src/openbiliclaw/web/js/views/recommend.js"),
  ]);
  for (const field of [
    "article_id", "media", "provider", "canonical_url", "published_at",
    "retrieved_at", "content_scope", "content_hash", "freshness",
  ]) {
    assert.match(models, new RegExp(field));
  }
  assert.match(api, /requestJson\("\/news\/sources"/);
  assert.match(api, /requestJson\("\/news\/refresh"/);
  assert.match(api, /requestJson\("\/news\/sources\/probe"/);
  assert.match(recommendations, /标题级信息/);
  assert.match(recommendations, /阅读原文/);
});

test("first setup submits a durable bootstrap and polls its terminal state", async () => {
  const html = await read("src/openbiliclaw/web/setup/index.html");
  assert.match(html, /连接 AI/);
  assert.match(html, /验证新闻源/);
  assert.match(html, /从零画像/);
  assert.match(html, /首批推荐/);
  assert.match(html, /request\("\/news\/bootstrap"/);
  assert.match(html, /request\("\/news\/bootstrap\/status"/);
  assert.match(html, /avoid_terms: avoid/);
  assert.match(html, /selectedTags\("interests"\)\.length < 3/);
  assert.match(html, /request\("\/news\/sources\/probe"/);
  assert.match(html, /request\("\/config\/probe-service"/);
  assert.match(html, /probe\?\.ok !== true/);
  assert.match(html, /id="setupRssSourceItems"/);
  assert.match(html, /stage === "degraded"/);
  assert.match(html, /stage === "failed"/);
  assert.match(html, /window\.location\.assign\("\/web"\)/);
  assert.doesNotMatch(html, /request\("\/init"/);
  assert.doesNotMatch(html, /Bangumi|B\s*站|bilibili\.com|平台登录/);
});

test("browser extension is a local side-panel client without page access", async () => {
  const manifestNames = [
    "extension/manifest.json",
    "extension/manifest.firefox.json",
    "extension/manifest.safari.json",
  ];
  for (const name of manifestNames) {
    const manifest = JSON.parse(await read(name));
    assert.match(manifest.name, /NewsClaw/);
    assert.equal("content_scripts" in manifest, false, name);
    assert.equal("web_accessible_resources" in manifest, false, name);
    assert.equal("optional_host_permissions" in manifest, false, name);
    const permissions = new Set(manifest.permissions || []);
    for (const forbidden of ["activeTab", "cookies", "scripting", "tabs"]) {
      assert.equal(permissions.has(forbidden), false, `${name}: ${forbidden}`);
    }
    for (const host of manifest.host_permissions || []) {
      assert.match(host, /^http:\/\/(127\.0\.0\.1|localhost)\//, `${name}: ${host}`);
    }
  }

  const [build, worker, popup] = await Promise.all([
    read("extension/scripts/build.mjs"),
    read("extension/src/background/service-worker.ts"),
    read("extension/popup/popup.js"),
  ]);
  assert.doesNotMatch(build, /src\/content|src\/main|safari-page-injector/);
  assert.doesNotMatch(worker, /chrome\.cookies|executeScript|chrome\.tabs\.query/);
  assert.match(popup, /\/m\/\?surface=extension/);
  assert.match(popup, /127\.0\.0\.1/);
  assert.match(popup, /localhost/);
});
