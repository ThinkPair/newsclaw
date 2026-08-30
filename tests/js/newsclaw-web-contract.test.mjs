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

test("desktop home is one responsive queue and evidence workbench", async () => {
  const [html, css] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
  ]);
  assert.match(html, /class="judgment-workbench"/);
  assert.match(html, /id="workbenchQueueTab"[^>]*role="tab"[^>]*aria-controls="recommendationWorkbenchPanel"/);
  assert.match(html, /id="workbenchDelightTab"[^>]*role="tab"[^>]*aria-controls="delightWorkbenchPanel"/);
  assert.match(html, /id="videoGrid" role="list"/);
  assert.match(html, /id="recommendationDetailPane"/);
  assert.match(css, /\.judgment-workbench-body\s*\{[\s\S]*?grid-template-columns:\s*minmax\(360px,[\s\S]*?minmax\(500px,/);
  assert.match(css, /\.delight-workbench-panel \.delight\s*\{[\s\S]*?height:\s*auto;[\s\S]*?max-height:\s*none;/);
  assert.match(css, /@media \(max-width: 900px\)[\s\S]*?\.recommendation-detail-pane\.is-sheet/);
  assert.match(css, /body\.recommendation-detail-sheet-open\s*\{\s*overflow:\s*hidden;/);
});

test("closing the narrow recommendation sheet also collapses its queue control", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  assert.match(
    js,
    /const expanded = active[\s\S]{0,140}?\(!isNarrowRecommendationWorkbench\(\) \|\| state\.recommendationDetailSheetOpen\)/,
  );

  const closeSource = js.match(
    /function closeRecommendationDetailSheet\([^)]*\) \{[\s\S]*?\n    \}/,
  )?.[0];
  assert.ok(closeSource, "narrow sheet close handler should remain executable");
  const state = { recommendationDetailSheetOpen: true };
  let selectionSyncs = 0;
  let presentationSyncs = 0;
  let focusRestores = 0;
  const close = new Function(
    "isNarrowRecommendationWorkbench",
    "state",
    "syncRecommendationSelection",
    "syncRecommendationDetailSheetPresentation",
    "recommendationDetailOpener",
    `${closeSource}; return closeRecommendationDetailSheet;`,
  )(
    () => true,
    state,
    () => { selectionSyncs += 1; },
    () => { presentationSyncs += 1; },
    { isConnected: true, focus() { focusRestores += 1; } },
  );

  close();
  assert.equal(state.recommendationDetailSheetOpen, false);
  assert.equal(selectionSyncs, 1);
  assert.equal(presentationSyncs, 1);
  assert.equal(focusRestores, 1);
});

test("desktop queue stays compact and only the evidence pane renders explanations", async () => {
  const [js, css] = await Promise.all([
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
  ]);
  const queueSource = js.match(/function recommendationCardHtml\(item, index\) \{[\s\S]*?\n    \}/)?.[0] || "";
  assert.ok(queueSource);
  assert.doesNotMatch(queueSource, /body_text|why_recommended|impact_chain|observation_points|item\.reason|class="reason"/);
  assert.match(queueSource, /queue-priority-tag/);
  assert.match(queueSource, /queue-topic-tag/);
  assert.match(js, /function recommendationDetailExplanationHtml[\s\S]*?why_recommended[\s\S]*?impact_chain[\s\S]*?observation_points/);
  assert.match(js, /structuredComplete[\s\S]*?is-expression-fallback/);
  assert.match(js, />\u53cd\u9988\u4e00\u53e5<\/button>/);
  assert.match(js, /button\.textContent = "\u804a\u4e00\u804a"/);
  assert.match(css, /\.judgment-workbench \.queue-card-main \.cover\s*\{\s*display:\s*none;/);
  assert.match(css, /\.judgment-workbench \.recommendation-queue \.card-actions\s*\{\s*display:\s*none;/);
  assert.match(css, /\.recommendation-detail-head h3\s*\{[\s\S]*?-webkit-line-clamp:\s*3;/);
  assert.match(js, /id="recommendationDetailTitle"[^>]*title="\$\{escapeHtml\(item\.title\)\}"/);
});

test("desktop observation points expose an executable local tracking contract", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const functionSource = (name) => js.match(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n    \\}`))?.[0] || "";
  const keySource = functionSource("trackedObservationKey");
  const syncSource = functionSource("syncObservationTrackingControls");
  const explanationSource = functionSource("recommendationDetailExplanationHtml");
  const mergeSource = functionSource("mergeTrackedObservationRefresh");
  assert.ok(keySource && syncSource && explanationSource && mergeSource);

  const renderExplanation = new Function(
    "asArray", "escapeHtml",
    `${explanationSource}; return recommendationDetailExplanationHtml;`,
  )((value) => Array.isArray(value) ? value : [], (value) => String(value ?? ""));
  const explanation = renderExplanation({
    why_recommended: "关系当前主题",
    impact_chain: "政策→供给→价格",
    observation_points: ["跟踪下周数据"],
  });
  assert.match(explanation, /data-detail-track-observation/);
  assert.match(explanation, /data-observation="跟踪下周数据"/);

  const item = { item_key: "news:article-1" };
  const button = {
    dataset: { observation: "跟踪下周数据" }, textContent: "", title: "", attrs: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
  };
  const records = new Map([["news:article-1\u0000跟踪下周数据", { tracking_id: "track-1" }]]);
  const sync = new Function(
    "trackedObservationRecords", "recommendationKey",
    `${keySource}\n${syncSource}; return syncObservationTrackingControls;`,
  )(records, (value) => value.item_key);
  sync({ querySelectorAll: () => [button] }, item);
  assert.equal(button.attrs["aria-pressed"], "true");
  assert.equal(button.textContent, "移出跟踪");

  const mergeRefresh = new Function(
    "asArray", "trackedObservationId",
    `${mergeSource}; return mergeTrackedObservationRefresh;`,
  )(
    (value) => Array.isArray(value) ? value : [],
    (value) => String(value?.tracking_id || ""),
  );
  const existing = Array.from({ length: 600 }, (_, index) => ({ tracking_id: `track-${index + 1}`, version: "old" }));
  const refreshed = Array.from({ length: 500 }, (_, index) => ({ tracking_id: `track-${index + 1}`, version: "new" }));
  const merged = mergeRefresh(existing, refreshed, 600, true);
  assert.equal(merged.length, 600);
  assert.equal(merged[0].version, "new");
  assert.equal(merged[599].tracking_id, "track-600");

  assert.match(js, /trackedObservations:\s*"\/news\/tracked-observations"/);
  assert.match(js, /const body = \{[\s\S]{0,180}?item_key:\s*itemKey[\s\S]{0,180}?observation:\s*cleanObservation/);
  assert.match(js, /requestJsonStrict\(ENDPOINTS\.trackedObservations,[\s\S]{0,120}?method:\s*"POST"/);
  assert.match(js, /`\$\{ENDPOINTS\.trackedObservations\}\/\$\{encodeURIComponent\(trackingId\)\}`[\s\S]{0,120}?method:\s*"DELETE"/);
  assert.match(js, /data-tracking-list-more/);
  assert.match(js, /data-tracking-action="timeline-more"/);
  assert.match(js, /class="tracking-check-error"/);
  assert.match(js, /有新变化/);
  assert.match(js, /timeoutMs:\s*300000/);
  assert.match(js, /mergeTrackedObservationRefresh\([\s\S]{0,220}?silent/);
  assert.doesNotMatch(js, /有待查看更新/);
});

test("desktop selection, local filters and mutations keep honest state", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const selectSource = js.match(/function selectRecommendationByKey\([\s\S]*?\n    \}/)?.[0] || "";
  assert.ok(selectSource);
  assert.doesNotMatch(selectSource, /trackRecommendationClick|openRecommendation/);
  assert.match(js, /originalLink\?\.addEventListener\("click", recordOriginalOpen\)/);
  assert.match(js, /originalLink\?\.addEventListener\("auxclick"/);
  assert.match(js, /selectedRecommendationKey/);
  assert.match(js, /new BroadcastChannel\("newsclaw-desktop-recommendations"\)/);
  assert.match(js, /type:\s*"saved-changed"/);
  assert.match(js, /function applySavedChangeAcrossSurfaces/);
  assert.match(js, /function publishSavedChange/);
  assert.match(js, /function reconcileSavedChangeAfterBusy[\s\S]*?fetchDesktopSaved\(listKind\)/);
  assert.match(js, /desktopSavedChangeEpochs/);
  assert.match(js, /if \(currentEpoch !== expectedEpoch\) return;/);
  assert.match(js, /desktopContentLibraryTab === "history"[\s\S]*?refreshContentHistory\(true\)/);
  assert.match(js, /await removeDesktopSavedItem\(listKind, item\.item_key\);[\s\S]{0,180}?publishSavedChange\(listKind, item\.item_key, false\);/);
  assert.match(js, /await saveDesktopItem\(context\.context, item\);[\s\S]{0,260}?publishSavedChange\(context\.context, desktopSavedItem\(item\)\.item_key, true\);/);
  assert.match(js, /function recommendationLocalFiltersActive\(\)/);
  assert.match(js, /function activePlatformSlug\(\)[\s\S]*?return "";/);
  assert.match(js, /if \(recommendationLocalFiltersActive\(\)\)[\s\S]*?\u8bf7先\u91cd置本地筛选/);
  assert.match(js, /if \(reshuffleInFlight \|\| appendMoreInFlight\)/);
  assert.match(js, /reconcileRecommendationSavedRegistry/);
  assert.match(js, /desktopSavedMutations\.setSaved/);
  assert.match(js, /const isCurrentDetail = \(\) => Boolean/);
  assert.match(js, /status && isCurrentDetail\(\)/);
});

test("desktop only exposes absolute HTTP(S) links to original content", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const helperSource = js.match(/function safeHttpContentUrl\(value\) \{[\s\S]*?\n    \}/)?.[0] || "";
  assert.ok(helperSource, "safe URL helper should remain executable");
  const safeHttpContentUrl = new Function(`${helperSource}; return safeHttpContentUrl;`)();
  assert.equal(safeHttpContentUrl("https://example.com/report?id=1"), "https://example.com/report?id=1");
  assert.equal(safeHttpContentUrl("http://example.com/news"), "http://example.com/news");
  assert.equal(safeHttpContentUrl("javascript:alert(1)"), "");
  assert.equal(safeHttpContentUrl("data:text/html,pwned"), "");
  assert.equal(safeHttpContentUrl("/relative/path"), "");
});

test("desktop title and body compaction is conservative at content boundaries", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const titleSource = js.match(/function displayRecommendationTitle\([^)]*\) \{[\s\S]*?\n    \}/)?.[0] || "";
  const bodySource = js.match(/function recommendationBodyForDisplay\([^)]*\) \{[\s\S]*?\n    \}/)?.[0] || "";
  assert.ok(titleSource && bodySource);
  const displayTitle = new Function(
    "ID_TITLE_RE", "BRACKETED_NEWS_TITLE_RE", "ASCII_BRACKETED_NEWS_TITLE_RE", "ZHIHU_TITLE_PLACEHOLDERS",
    `${titleSource}; return displayRecommendationTitle;`,
  )(
    /^(answer|article|question|zhihu)_\S+$/,
    /^【\s*([^】\r\n]{4,180}?)\s*】/,
    /^\[\s*([^\]\r\n]{4,180}?)\s*\]/,
    { answer: "来自知乎的回答", article: "来自知乎的文章", question: "来自知乎的提问" },
  );
  const displayBody = new Function(`${bodySource}; return recommendationBodyForDisplay;`)();

  assert.equal(displayTitle("【官方MV】歌曲名", "", "video", "bilibili"), "【官方MV】歌曲名");
  assert.equal(displayTitle("[Official] Real headline", "", "video", "youtube"), "[Official] Real headline");
  assert.equal(
    displayTitle("【国家发展改革委召开六网协同会议】会议后续", "", "article", "news"),
    "国家发展改革委召开六网协同会议",
  );
  assert.deepEqual(
    displayBody({ title: "苹果", original_title: "苹果", body_text: "苹果公司发布财报" }),
    { text: "苹果公司发布财报", duplicateOnly: false, trimmedPrefix: false },
  );
  assert.deepEqual(
    displayBody({ title: "政策更新", original_title: "政策更新", body_text: "政策更新：下调25个基点" }),
    { text: "下调25个基点", duplicateOnly: false, trimmedPrefix: true },
  );
});

test("desktop priority ordering consumes the decision contract and stays stable", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const start = js.indexOf("const RECOMMENDATION_PRIORITY_LEVEL_WEIGHT");
  const end = js.indexOf("\n    function normalizeRecommendation(item)", start);
  assert.ok(start >= 0 && end > start);
  const source = js.slice(start, end);
  const helpers = new Function(
    "decodeHtmlEntities",
    "asArray",
    `${source}; return { normalizeRecommendationPriority, sortRecommendationsByPriority };`,
  )((value) => String(value ?? ""), (value) => Array.isArray(value) ? value : []);

  const low = { id: "low", priority: { level: "low", score: 0.2 } };
  const highA = { id: "high-a", priority: { level: "high", score: 0.9 } };
  const highB = { id: "high-b", decision_priority: { level: "high", score: 0.9 } };
  assert.deepEqual(
    helpers.sortRecommendationsByPriority([low, highA, highB]).map((item) => item.id),
    ["high-a", "high-b", "low"],
  );
  assert.equal(helpers.normalizeRecommendationPriority(highB).score, 0.9);
});

test("desktop autoload geometry requires an actual inner-grid bottom gesture", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const source = js.match(/function isRecommendationGridAtBottom\([^)]*\) \{[\s\S]*?\n    \}/)?.[0];
  const gateSource = js.match(/function nextRecommendationGridScrollGate\([^)]*\) \{[\s\S]*?\n    \}/)?.[0];
  assert.ok(source);
  assert.ok(gateSource);
  const { atBottom, nextGate } = new Function(
    `${source}\n${gateSource}; return { atBottom: isRecommendationGridAtBottom, nextGate: nextRecommendationGridScrollGate };`,
  )();
  const gridAtTop = { scrollTop: 0, clientHeight: 600, scrollHeight: 1800 };
  const gridFar = { scrollTop: 1050, clientHeight: 600, scrollHeight: 1800 };
  const gridNear = { scrollTop: 1180, clientHeight: 600, scrollHeight: 1800 };
  assert.equal(atBottom(gridAtTop, false), false, "initial render is not a scroll intent");
  assert.equal(atBottom(gridAtTop, true), false, "top of an overflowing grid is not bottom");
  assert.equal(atBottom(gridFar, true), false, "150px from the bottom must not load");
  assert.equal(atBottom(gridNear, true), true, "20px from the inner bottom may load once");
  assert.equal(atBottom({ scrollTop: 0, clientHeight: 600, scrollHeight: 600 }, true), false);
  let gate = nextGate(gridNear, { intent: false, armed: true });
  assert.deepEqual(gate, { intent: true, armed: true, atBottom: true });
  gate = nextGate(gridNear, { intent: true, armed: false });
  assert.equal(gate.armed, false, "remaining at bottom cannot rearm after one append");
  gate = nextGate(gridFar, gate);
  assert.equal(gate.armed, true, "leaving the bottom rearms the next user gesture");
  gate = nextGate(gridNear, gate);
  assert.equal(gate.atBottom && gate.armed, true);

  const contextSource = js.match(/function recommendationAutoLoadContextReason\([^)]*\) \{[\s\S]*?\n    \}/)?.[0];
  assert.ok(contextSource);
  const contextReason = new Function(`${contextSource}; return recommendationAutoLoadContextReason;`)();
  const eligible = {
    enabled: true, inFlight: false, localFilters: false, queueView: true,
    hasStock: true, homeVisible: true, buttonVisible: true, hasCards: true,
    cooldownReady: true,
  };
  assert.equal(contextReason(eligible), "");
  assert.equal(contextReason({ ...eligible, localFilters: true }), "local-filter");
  assert.equal(contextReason({ ...eligible, queueView: false }), "not-queue-view");
  assert.equal(contextReason({ ...eligible, hasStock: false }), "pool-empty");
  assert.equal(contextReason({ ...eligible, inFlight: true }), "in-flight");
});

test("today pick uses the canonical safe URL and explicit failed presentation", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const functionSource = (name) => js.match(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n    \\}`))?.[0] || "";
  const safeSource = functionSource("safeHttpContentUrl");
  const contentSource = functionSource("contentUrl");
  const sourceLinkSource = functionSource("recommendationSourceLink");
  const delightSource = functionSource("delightContentUrl");
  const emptySource = functionSource("delightEmptyPresentation");
  assert.ok(safeSource && contentSource && sourceLinkSource && delightSource && emptySource);
  const helpers = new Function(
    `${safeSource}\n${contentSource}\n${sourceLinkSource}\n${delightSource}\n${emptySource}; return { recommendationSourceLink, delightContentUrl, delightEmptyPresentation };`,
  )();
  assert.equal(helpers.delightContentUrl({ content_url: "javascript:alert(1)", platform: "news" }), "");
  assert.equal(helpers.delightContentUrl({ content_url: "https://example.com/news", platform: "news" }), "https://example.com/news");
  assert.deepEqual(
    helpers.recommendationSourceLink({ source_lookup_url: "https://www.bing.com/search?q=article", platform: "news" }),
    {
      state: "search",
      url: "https://www.bing.com/search?q=article",
      label: "检索来源",
      statusLabel: "来源未提供直链",
      explanation: "将按标题检索公开网页；搜索结果不是新闻爪认定的原文。",
    },
  );
  assert.equal(
    helpers.recommendationSourceLink({ source_lookup_url: "javascript:alert(1)", platform: "news" }).label,
    "暂无原文",
  );
  const failed = helpers.delightEmptyPresentation("failed", "本地后端超时");
  assert.equal(failed.title, "今日首选暂时不可用");
  assert.equal(failed.reason, "本地后端超时");
  assert.equal(failed.retry, true);
  assert.equal(failed.role, "alert");
});

test("coverless news in today pick uses tags instead of duplicating article text", async () => {
  const [js, css] = await Promise.all([
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
  ]);
  assert.match(
    js,
    /function renderDelightTagMedia[\s\S]*?recommendationTagFingerprintHtml\(delight\)/,
  );
  assert.match(
    js,
    /function renderDelightFallbackMedia[\s\S]{0,180}?recommendationPlatformSlug\(delight\) === "news"[\s\S]{0,120}?renderDelightTagMedia\(thumb, delight\)/,
  );
  assert.match(css, /\.delight \.thumb\.is-tag-media/);
});

test("today pick DOM controls disable unsafe, single-item and repeated-like actions", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const source = js.match(/function syncDelightControlAvailability\([^)]*\) \{[\s\S]*?\n    \}/)?.[0];
  assert.ok(source);
  const button = (action) => ({
    dataset: { delight: action }, disabled: false, hidden: false, title: "", textContent: "", attrs: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
  });
  const controls = Object.fromEntries(
    ["prev", "next", "view", "like", "dislike", "dismiss", "chat", "watch-later", "favorite", "retry"]
      .map((action) => [action, button(action)]),
  );
  const fakeDocument = {
    querySelector(selector) {
      const action = selector.match(/data-delight="([^"]+)"/)?.[1];
      return action ? controls[action] : null;
    },
    querySelectorAll(selector) {
      return [...selector.matchAll(/data-delight="([^"]+)"/g)].map((match) => controls[match[1]]);
    },
  };
  const sourceLink = (item) => ({
    exact: { state: "exact", url: "https://example.com/article", label: "阅读原文", statusLabel: "原文可核验", explanation: "原文说明" },
    search: { state: "search", url: "https://www.bing.com/search?q=article", label: "检索来源", statusLabel: "来源未提供直链", explanation: "检索说明" },
    missing: { state: "missing", url: "", label: "暂无原文", statusLabel: "链接信息缺失", explanation: "缺失说明" },
  })[item?.sourceState || "missing"];
  const state = { delights: [{ state: "liked", sourceState: "missing" }], delight: { state: "liked", sourceState: "missing" }, delightLoadState: "ready" };
  const sync = new Function(
    "state", "document", "delightSourceLink", "$", "delightFeedbackInFlight",
    `${source}; return syncDelightControlAvailability;`,
  )(state, fakeDocument, sourceLink, (selector) => selector === "#delightRetryBtn" ? controls.retry : null, false);
  sync();
  assert.equal(controls.prev.disabled, true);
  assert.equal(controls.next.disabled, true);
  assert.equal(controls.view.disabled, true);
  assert.equal(controls.view.attrs["aria-disabled"], "true");
  assert.equal(controls.view.textContent, "暂无原文");
  assert.equal(controls.view.dataset.sourceLinkState, "missing");
  assert.equal(controls.like.disabled, true);
  assert.equal(controls.retry.hidden, true);

  state.delights = [{ state: "pending", sourceState: "search" }];
  state.delight = state.delights[0];
  sync();
  assert.equal(controls.view.disabled, false);
  assert.equal(controls.view.textContent, "检索来源");
  assert.equal(controls.view.dataset.sourceLinkState, "search");
  assert.equal(controls.view.attrs["aria-label"], "来源未提供直链，检索来源");

  state.delights = [{ state: "pending", sourceState: "exact" }];
  state.delight = state.delights[0];
  sync();
  assert.equal(controls.view.disabled, false);
  assert.equal(controls.view.textContent, "阅读原文");
  assert.equal(controls.view.dataset.sourceLinkState, "exact");

  const committedSource = js.match(/function recommendationFeedbackAlreadyCommitted\([^)]*\) \{[\s\S]*?\n    \}/)?.[0];
  assert.ok(committedSource);
  const alreadyCommitted = new Function(
    `${committedSource}; return recommendationFeedbackAlreadyCommitted;`,
  )();
  assert.equal(alreadyCommitted({ feedback_type: "like" }, "like"), true);
  assert.equal(alreadyCommitted({ feedback_type: "comment" }, "like"), false);
  assert.equal(alreadyCommitted({ feedback_type: "like" }, "dislike"), false);
});

test("today pick source search opens without recording a view", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  assert.match(
    js,
    /if \(response === "view"\) \{\s*const sourceLink = delightSourceLink\(delight\);\s*if \(sourceLink\.state === "search"\) \{[\s\S]{0,700}?return;\s*\}\s*if \(sourceLink\.state !== "exact"/,
  );
  const searchBranch = js.match(
    /if \(sourceLink\.state === "search"\) \{[\s\S]{0,700}?return;\s*\}/,
  )?.[0] || "";
  assert.ok(searchBranch);
  assert.match(searchBranch, /window\.open\(sourceLink\.url, "_blank", "noopener,noreferrer"\)/);
  assert.doesNotMatch(searchBranch, /trackRecommendationClick|delightRespond|response: "view"/);
});

test("desktop recommendation source search is navigation-only", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const start = js.indexOf("const recordSourceSearch = () => {");
  const end = js.indexOf('sourceSearchLink?.addEventListener("click"', start);
  const searchHandler = js.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.match(searchHandler, /本次检索不记为阅读或原文点击/);
  assert.doesNotMatch(searchHandler, /trackRecommendationClick|ENDPOINTS\.click|recommendation-click/);
});

test("desktop pending notifications keep exact, search and missing source actions distinct", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  assert.match(js, /type: "notification",[\s\S]{0,800}?original_url:[\s\S]{0,400}?source_link_status:[\s\S]{0,250}?source_lookup_url:/);
  assert.match(js, /data-notification-msg="view" data-source-link-state="exact"/);
  assert.match(js, /data-notification-msg="search" data-source-link-state="search"/);
  assert.match(js, /data-notification-msg="missing" data-source-link-state="missing"[\s\S]{0,120}?disabled/);

  const start = js.indexOf("async function respondNotification");
  const end = js.indexOf("async function refreshWebMonitorNotifications", start);
  const responder = js.slice(start, end);
  assert.ok(start >= 0 && end > start);
  assert.match(responder, /if \(response === "search"\) \{[\s\S]{0,220}?return;/);
  assert.match(responder, /if \(response === "missing"\) return;/);
  assert.match(responder, /if \(response === "view"\) \{\s*if \(sourceLink\.state !== "exact" \|\| !sourceLink\.url\) return;\s*trackRecommendationClick\(msg\);/);
  assert.ok(responder.indexOf('if (response === "search")') < responder.indexOf("ENDPOINTS.notificationSent"));
  assert.match(responder, /await requestJsonStrict\(ENDPOINTS\.notificationSent/);
  assert.doesNotMatch(responder, /await requestJson\(ENDPOINTS\.notificationSent/);
  assert.match(responder, /state\.messageListSnapshot = state\.messageListSnapshot\.filter/);
  assert.match(responder, /catch \(error\) \{[\s\S]{0,500}?标记通知失败/);
});

test("desktop initial recommendation state is loading, never a fabricated empty pool", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  assert.match(js, /let desktopRecommendationLoadState = "loading";/);
  assert.match(js, /const initialLoading = state\.videos\.length === 0/);
  assert.match(js, /summary\.textContent = "\u6b63在读取推荐…"/);
  assert.match(js, /loadMore\.disabled = true/);
  assert.match(js, /renderRecommendationDetail\(null, \[\]\)/);
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
  assert.match(js, /topicCounts\.get\(name\)/);
  assert.match(js, /function renderMediaFilter/);
  assert.match(html, /id="mediaFilterSelect"/);
  assert.doesNotMatch(js, /\balert\s*\(/);
});

test("desktop coverless news renders a typed tag fingerprint", async () => {
  const [html, js, css] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
  ]);
  assert.match(js, /const DISPLAY_TAG_TYPES = new Set\(\["topic", "story", "temporal", "evidence"\]\)/);
  assert.match(js, /function recommendationUsesTagFingerprint/);
  assert.match(js, /if \(recommendationHasVisualCover\(item\)\) return false;/);
  assert.match(js, /function recommendationTagFingerprintHtml/);
  assert.match(js, /return tags\.slice\(0, 4\);/);
  assert.match(js, /class="news-tag" data-tag-type=/);
  assert.match(css, /\.cover\.is-tag-card/);
  assert.match(css, /\.news-tag\[data-tag-type="evidence"\]/);
  assert.match(html, /id="filterRow" role="tablist" aria-label="推荐类别"/);
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
  assert.match(recommendations, /getRecommendationSourceLink/);
  assert.match(recommendations, /sourceLink\.label/);
  assert.match(recommendations, /sourceLink\.state === "exact"/);
  assert.match(models, /label: "阅读原文"/);
  assert.match(models, /label: "检索来源"/);
  assert.match(models, /label: "暂无原文"/);
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
