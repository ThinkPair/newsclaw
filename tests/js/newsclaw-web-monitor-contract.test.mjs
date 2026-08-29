import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");
const between = (source, start, end) => source.slice(source.indexOf(start), source.indexOf(end));

test("P5 exposes one responsive local monitor surface with honest unknown state", async () => {
  const [html, js, css] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
  ]);

  assert.match(html, /id="contentLibraryMonitoringTab"[^>]*hidden/);
  assert.match(html, /id="monitoringPage"[^>]*role="tabpanel"/);
  assert.match(html, /id="monitoringActiveCount">—</);
  assert.match(html, /id="monitoringUnreadCount">—</);
  assert.match(html, /id="monitoringFailedCount">—</);
  assert.match(html, /首次只建立基线，以后只提醒正文的真实变化/);
  assert.match(js, /webMonitors:\s*"\/news\/monitors"/);
  assert.match(js, /webMonitorNotifications:\s*"\/news\/monitor-notifications"/);
  assert.match(js, /state\.newsclawMode !== true/);
  assert.match(js, /monitoringTab\.hidden = !newsclawMode/);
  assert.match(js, /void refreshWebMonitorNotifications\(\)/);
  assert.match(js, /payload\?\.check\?\.status === "baseline"/);
  assert.match(js, /事件档案/);
  assert.match(js, /已有版本和事件档案会保留在本机/);
  assert.match(js, /historyWasOpen = action === "check"/);
  assert.match(js, /historyWasOpen[\s\S]+encodeURIComponent\(monitorId\)\}\/history/);
  assert.match(js, /检查已完成，但\$\{historyMessage\}/);
  assert.match(css, /@media \(max-width: 620px\)[\s\S]*\.monitoring-summary/);
});

test("P5 notifications use stable deliveries and open the existing event workspace", async () => {
  const [app, workspace] = await Promise.all([
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
    read("src/openbiliclaw/web/desktop/assets/js/event-workspace.js"),
  ]);
  assert.match(app, /type:\s*"web-monitor-notification"/);
  assert.match(app, /delivery_id/);
  assert.match(app, /action:\s*"delivered"/);
  assert.match(app, /request_id: `web-monitor:\$\{action\}:\$\{String\(msg\.delivery_id/);
  assert.doesNotMatch(
    app,
    /respondWebMonitorNotification[\s\S]{0,700}String\(item\.delivery_id/,
  );
  assert.match(app, /eventWorkspaceController\?\.openEvent\?\./);
  assert.match(workspace, /async function openEvent\(eventId, changeId = ""\)/);
  assert.match(workspace, /await selectEvent\(eventId/);
  assert.match(workspace, /if \(changeId\) await selectChange\(changeId\)/);
});

test("extension only exposes the formal NewsClaw monitor inbox and bounds every probe", async () => {
  const [html, js] = await Promise.all([
    read("extension/popup/popup.html"),
    read("extension/popup/popup.js"),
  ]);
  assert.match(html, /id="monitorButton"[^>]*hidden/);
  assert.match(html, /id="monitorBadge"[^>]*hidden/);
  assert.match(js, /snapshot\?\.service \|\| ""\) === "newsclaw-api"/);
  assert.match(js, /\/api\/news\/monitor-notifications/);
  assert.match(js, /#library\/monitoring/);
  assert.match(js, /refreshMonitorBadge\([\s\S]*AbortController[\s\S]*PROBE_TIMEOUT_MS/);
  assert.match(js, /setInterval\([\s\S]*refreshConnectionState\(\)/);
  assert.match(js, /const PROBE_TIMEOUT_MS = \d+;/);
  assert.doesNotMatch(js, /chrome\.notifications|contentScripts|document\.cookie/);
});

test("extension monitor badge executes after a successful local connection", async () => {
  const source = await read("extension/popup/popup.js");
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) {
      elements.set(selector, {
        hidden: false,
        textContent: "",
        className: "",
        value: "",
        src: "",
        addEventListener() {},
        setAttribute(name, value) {
          this[name] = value;
        },
        removeAttribute(name) {
          delete this[name];
        },
        focus() {},
        classList: { contains: () => false },
      });
    }
    return elements.get(selector);
  };
  element("#monitorButton").hidden = true;
  element("#monitorBadge").hidden = true;

  const response = (payload) => ({
    ok: true,
    status: 200,
    json: async () => payload,
  });
  const context = {
    AbortController,
    URL,
    chrome: {
      storage: { local: { get: () => new Promise(() => {}), set: async () => undefined } },
      tabs: { create() {} },
    },
    document: { querySelector: element },
    fetch: async (url) => {
      if (String(url).endsWith("/api/health")) return response({ service: "newsclaw-api" });
      if (String(url).endsWith("/api/news/monitor-notifications")) {
        return response({ unread_count: 3 });
      }
      return response({ ok: true });
    },
    localStorage: { getItem: () => "", setItem() {} },
    setTimeout,
    clearTimeout,
    setInterval: () => 0,
  };
  vm.runInNewContext(`${source}\nglobalThis.__refreshMonitorBadge = refreshMonitorBadge;`, context);

  await context.__refreshMonitorBadge();

  assert.equal(element("#monitorButton").hidden, false);
  assert.equal(element("#monitorBadge").hidden, false);
  assert.equal(element("#monitorBadge").textContent, "3");
  assert.equal(element("#monitorButton")["aria-label"], "网页监控，3 条未读变化");
});

test("extension clears and fences an old monitor badge when the endpoint goes offline", async () => {
  const source = await read("extension/popup/popup.js");
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) {
      elements.set(selector, {
        hidden: false, textContent: "", className: "", value: "", src: "",
        addEventListener() {}, setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; }, focus() {},
        classList: { contains: () => false },
      });
    }
    return elements.get(selector);
  };
  let resolveInbox;
  const inbox = new Promise((resolvePromise) => { resolveInbox = resolvePromise; });
  const unavailable = { ok: false, status: 503, json: async () => ({}) };
  const context = {
    AbortController, URL,
    chrome: {
      storage: { local: { get: () => new Promise(() => {}), set: async () => undefined } },
      tabs: { create() {} },
    },
    document: { querySelector: element },
    fetch: async (url) => String(url).includes("monitor-notifications") ? inbox : unavailable,
    localStorage: { getItem: () => "", setItem() {} },
    setTimeout, clearTimeout, setInterval: () => 0,
  };
  vm.runInNewContext(`${source}\nglobalThis.__test = { refreshMonitorBadge, connect, setEndpoint(value) { activeEndpoint = value; } };`, context);

  const stale = context.__test.refreshMonitorBadge();
  context.__test.setEndpoint("http://localhost:8430");
  await context.__test.connect({ allowDevelopmentFallback: false });
  resolveInbox({ ok: true, status: 200, json: async () => ({ unread_count: 5 }) });
  await stale;

  assert.equal(element("#monitorButton").hidden, true);
  assert.equal(element("#monitorBadge").hidden, true);
  assert.equal(element("#monitorBadge").textContent, "");
  assert.match(element("#connectionText").textContent, /离线/);
});

test("extension periodic health refresh replaces a stale online state with offline controls", async () => {
  const source = await read("extension/popup/popup.js");
  const elements = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) {
      const row = {
        hidden: false, textContent: "", className: "", value: "", src: "",
        addEventListener() {}, setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; }, focus() {},
      };
      row.classList = { contains: (name) => row.className.split(/\s+/).includes(name) };
      elements.set(selector, row);
    }
    return elements.get(selector);
  };
  element("#clientFrame").src = "http://127.0.0.1:8430/m/?surface=extension";
  element("#connectionDot").className = "status-dot online";
  element("#connectionText").textContent = "在线 · 本机服务";
  let backendAvailable = false;
  const response = (payload = {}) => ({ ok: true, status: 200, json: async () => payload });
  const context = {
    AbortController, URL,
    chrome: {
      storage: { local: { get: () => new Promise(() => {}), set: async () => undefined } },
      tabs: { create() {} },
    },
    document: { querySelector: element },
    fetch: async (url) => {
      if (!backendAvailable) return { ok: false, status: 503, json: async () => ({}) };
      if (String(url).endsWith("/api/health")) return response({ service: "newsclaw-api" });
      if (String(url).endsWith("/api/news/monitor-notifications")) return response({ unread_count: 0 });
      return response();
    },
    localStorage: { getItem: () => "", setItem() {} },
    setTimeout, clearTimeout, setInterval: () => 0,
  };
  vm.runInNewContext(`${source}\nglobalThis.__refreshConnectionState = refreshConnectionState;`, context);

  await context.__refreshConnectionState();

  assert.equal(Object.hasOwn(element("#clientFrame"), "src"), false);
  assert.equal(element("#stateCard").hidden, false);
  assert.match(element("#connectionText").textContent, /离线/);
  assert.match(element("#connectionDot").className, /degraded/);

  backendAvailable = true;
  await context.__refreshConnectionState();
  assert.equal(element("#clientFrame").src, "http://127.0.0.1:8430/m/?surface=extension");
  assert.equal(element("#stateCard").hidden, true);
  assert.match(element("#connectionText").textContent, /在线/);
});

test("saving a new extension endpoint invalidates an older connection probe", async () => {
  const source = await read("extension/popup/popup.js");
  const elements = new Map();
  const handlers = new Map();
  const element = (selector) => {
    if (!elements.has(selector)) {
      const row = {
        hidden: false, textContent: "", className: "", value: "", src: "",
        addEventListener(name, handler) { handlers.set(`${selector}:${name}`, handler); },
        setAttribute(name, value) { this[name] = value; },
        removeAttribute(name) { delete this[name]; }, focus() {},
      };
      row.classList = { contains: (name) => row.className.split(/\s+/).includes(name) };
      elements.set(selector, row);
    }
    return elements.get(selector);
  };
  let resolveOldProbe;
  const oldProbe = new Promise((resolvePromise) => { resolveOldProbe = resolvePromise; });
  let resolveStorage;
  const storageWrite = new Promise((resolvePromise) => { resolveStorage = resolvePromise; });
  const response = (payload = {}) => ({ ok: true, status: 200, json: async () => payload });
  const context = {
    AbortController, URL,
    chrome: {
      storage: {
        local: {
          get: () => new Promise(() => {}),
          set: async () => storageWrite,
        },
      },
      tabs: { create() {} },
    },
    document: { querySelector: element },
    fetch: async (url) => {
      const value = String(url);
      if (value === "http://127.0.0.1:8430/api/ping") return oldProbe;
      if (value.endsWith("/api/health")) return response({ service: "newsclaw-api" });
      if (value.endsWith("/api/news/monitor-notifications")) return response({ unread_count: 0 });
      return response();
    },
    localStorage: { getItem: () => "", setItem() {} },
    setTimeout, clearTimeout, setInterval: () => 0,
  };
  vm.runInNewContext(`${source}\nglobalThis.__connect = connect;`, context);

  const oldConnect = context.__connect({ allowDevelopmentFallback: false });
  await new Promise(setImmediate);
  element("#endpointInput").value = "http://127.0.0.1:8431";
  const submit = handlers.get("#settingsForm:submit")({ preventDefault() {} });
  resolveOldProbe(response());
  await oldConnect;
  resolveStorage();
  await submit;

  assert.equal(element("#clientFrame").src, "http://127.0.0.1:8431/m/?surface=extension");
  assert.match(element("#connectionText").textContent, /开发端口 8431/);
});

test("monitor mutation waits for a fresh list instead of letting an old GET win", async () => {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const refreshSource = between(source, "async function refreshWebMonitors", "async function createWebMonitor");
  const actionSource = between(source, "async function runWebMonitorAction", "const DESKTOP_CONTENT_LIBRARY_STORAGE_KEY");
  let resolveOldList;
  const oldList = new Promise((resolvePromise) => { resolveOldList = resolvePromise; });
  let getCalls = 0;
  const state = {
    newsclawMode: true,
    webMonitors: {
      items: [{ monitor_id: "m1", status: "active" }], runtime: {}, loading: false,
      refreshRequested: false, refreshPromise: null, refreshGeneration: 0, error: "",
      histories: new Map(), historyGenerations: new Map(), busyIds: new Set(),
    },
  };
  const context = {
    state,
    ENDPOINTS: { webMonitors: "/news/monitors" },
    asArray: (value) => Array.isArray(value) ? value : [],
    configErrorMessage: () => "",
    renderWebMonitors() {}, showToast() {},
    refreshWebMonitorNotifications: async () => undefined,
    requestJsonStrict: async (path, options = {}) => {
      if (path === "/news/monitors" && !options.method) {
        getCalls += 1;
        if (getCalls === 1) return oldList;
        return { items: [{ monitor_id: "m1", status: "paused" }], runtime: {} };
      }
      return { item: { monitor_id: "m1", status: "paused" } };
    },
    webMonitorRequestId: () => "request-1",
    window: { confirm: () => true },
    openHomePage() {}, eventWorkspaceController: null,
    encodeURIComponent,
  };
  vm.runInNewContext(`${refreshSource}\n${actionSource}\nglobalThis.__refresh = refreshWebMonitors; globalThis.__act = runWebMonitorAction;`, context);

  const inflight = context.__refresh({ silent: true });
  await new Promise(setImmediate);
  const action = context.__act("pause", "m1");
  await new Promise(setImmediate);
  resolveOldList({ items: [{ monitor_id: "m1", status: "active" }], runtime: {} });
  await Promise.all([inflight, action]);

  assert.equal(getCalls, 2);
  assert.equal(state.webMonitors.items[0].status, "paused");
  assert.equal(state.webMonitors.loading, false);
});

test("monitor check failure envelope never produces a success toast", async () => {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const refreshSource = between(source, "async function refreshWebMonitors", "async function createWebMonitor");
  const actionSource = between(source, "async function runWebMonitorAction", "const DESKTOP_CONTENT_LIBRARY_STORAGE_KEY");
  const toasts = [];
  const state = {
    newsclawMode: true,
    webMonitors: {
      items: [{ monitor_id: "m1", status: "active" }], runtime: {}, loading: false,
      refreshRequested: false, refreshPromise: null, refreshGeneration: 0, error: "",
      histories: new Map(), historyGenerations: new Map(), busyIds: new Set(),
    },
  };
  const context = {
    state,
    ENDPOINTS: { webMonitors: "/news/monitors" },
    asArray: (value) => Array.isArray(value) ? value : [],
    configErrorMessage: () => "",
    renderWebMonitors() {}, showToast(value) { toasts.push(value); },
    refreshWebMonitorNotifications: async () => undefined,
    requestJsonStrict: async (path, options = {}) => {
      if (path.endsWith("/check") && options.method === "POST") {
        return { status: "failed", error: "PDF 没有可提取正文", check: { status: "failed" } };
      }
      return { items: [{ monitor_id: "m1", status: "needs_attention" }], runtime: {} };
    },
    webMonitorRequestId: () => "request-1",
    window: { confirm: () => true },
    openHomePage() {}, eventWorkspaceController: null,
    encodeURIComponent,
  };
  vm.runInNewContext(`${refreshSource}\n${actionSource}\nglobalThis.__act = runWebMonitorAction;`, context);

  await context.__act("check", "m1");

  assert.deepEqual(toasts, ["PDF 没有可提取正文"]);
  assert.equal(state.webMonitors.items[0].status, "needs_attention");
});

test("closing monitor history fences a late history response", async () => {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const actionSource = between(source, "async function runWebMonitorAction", "const DESKTOP_CONTENT_LIBRARY_STORAGE_KEY");
  let resolveHistory;
  const delayedHistory = new Promise((resolvePromise) => { resolveHistory = resolvePromise; });
  const state = {
    newsclawMode: true,
    webMonitors: {
      items: [{ monitor_id: "m1", status: "active" }], runtime: {}, loading: false,
      refreshRequested: false, refreshPromise: null, refreshGeneration: 0, error: "",
      histories: new Map(), historyGenerations: new Map(), busyIds: new Set(),
    },
  };
  const context = {
    state,
    ENDPOINTS: { webMonitors: "/news/monitors" },
    asArray: (value) => Array.isArray(value) ? value : [],
    requestJsonStrict: async () => delayedHistory,
    renderWebMonitors() {}, showToast() {}, configErrorMessage: () => "",
    refreshWebMonitors: async () => undefined,
    refreshWebMonitorNotifications: async () => undefined,
    webMonitorRequestId: () => "request-1",
    window: { confirm: () => true }, openHomePage() {}, eventWorkspaceController: null,
    encodeURIComponent,
  };
  vm.runInNewContext(`${actionSource}\nglobalThis.__act = runWebMonitorAction;`, context);

  const opening = context.__act("history", "m1");
  await new Promise(setImmediate);
  await context.__act("history", "m1");
  resolveHistory({ versions: [{ snapshot_id: "v1" }] });
  await opening;

  assert.equal(state.webMonitors.histories.has("m1"), false);
});

test("handled monitor notifications cannot revive from a drawer snapshot or stale GET", async () => {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const refreshSource = between(source, "async function refreshWebMonitorNotifications", "async function respondWebMonitorNotification");
  const respondSource = between(source, "async function respondWebMonitorNotification", "function collapseMessageItem");
  const msg = { type: "web-monitor-notification", delivery_id: "d1", kind: "failure" };
  const state = {
    newsclawMode: true,
    newsclawModeGeneration: 0,
    webMonitorNotificationGeneration: 0,
    webMonitorNotificationActions: new Set(),
    messages: [msg],
    messageListSnapshot: [msg],
  };
  let resolveOldInbox;
  const oldInbox = new Promise((resolvePromise) => { resolveOldInbox = resolvePromise; });
  let removed = false;
  const context = {
    state,
    ENDPOINTS: { webMonitorNotifications: "/news/monitor-notifications" },
    requestJsonStrict: async (path, options = {}) => {
      if (path === "/news/monitor-notifications" && !options.method) return oldInbox;
      return { ok: true };
    },
    asArray: (value) => Array.isArray(value) ? value : [],
    mergeMessages: (items) => { state.messages.push(...items); },
    messageKey: (item) => String(item.delivery_id || ""),
    syncMessageCount() {}, renderMessages() {}, showToast() {},
    refreshWebMonitors: async () => undefined,
    configErrorMessage: () => "", openHomePage() {}, openContentLibraryPage() {},
    eventWorkspaceController: null, encodeURIComponent,
  };
  vm.runInNewContext(`${refreshSource}\n${respondSource}\nglobalThis.__refresh = refreshWebMonitorNotifications; globalThis.__respond = respondWebMonitorNotification;`, context);

  const staleRefresh = context.__refresh();
  await new Promise(setImmediate);
  await context.__respond(msg, "dismiss", { remove() { removed = true; } });
  resolveOldInbox({ items: [{ ...msg, delivery_state: "delivered" }] });
  await staleRefresh;

  assert.equal(removed, true);
  assert.equal(state.messages.length, 0);
  assert.equal(state.messageListSnapshot.length, 0);
});

test("one monitor delivery accepts only one in-flight user action", async () => {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const respondSource = between(source, "async function respondWebMonitorNotification", "function collapseMessageItem");
  const msg = { type: "web-monitor-notification", delivery_id: "d1", kind: "change", event_id: "e1" };
  let resolveAction;
  const pending = new Promise((resolvePromise) => { resolveAction = resolvePromise; });
  let calls = 0;
  let opened = 0;
  const state = {
    newsclawMode: true,
    newsclawModeGeneration: 0,
    webMonitorNotificationGeneration: 0,
    webMonitorNotificationActions: new Set(),
    messages: [msg],
    messageListSnapshot: [msg],
  };
  const context = {
    state,
    ENDPOINTS: { webMonitorNotifications: "/news/monitor-notifications" },
    requestJsonStrict: async () => { calls += 1; return pending; },
    messageKey: (item) => String(item.delivery_id || ""),
    syncMessageCount() {}, renderMessages() {}, showToast() {},
    refreshWebMonitors: async () => undefined,
    configErrorMessage: () => "", openHomePage() {}, openContentLibraryPage() {},
    eventWorkspaceController: { async openEvent() { opened += 1; } }, encodeURIComponent,
  };
  vm.runInNewContext(`${respondSource}\nglobalThis.__respond = respondWebMonitorNotification;`, context);

  const opening = context.__respond(msg, "open", null);
  await new Promise(setImmediate);
  await context.__respond(msg, "dismiss", null);
  resolveAction({ ok: true });
  await opening;

  assert.equal(calls, 1);
  assert.equal(opened, 1);
  assert.equal(state.webMonitorNotificationActions.size, 0);
});

test("a monitor notification action cannot navigate after leaving NewsClaw mode", async () => {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const respondSource = between(source, "async function respondWebMonitorNotification", "function collapseMessageItem");
  const msg = { type: "web-monitor-notification", delivery_id: "d1", kind: "change", event_id: "e1" };
  let resolveAction;
  const pending = new Promise((resolvePromise) => { resolveAction = resolvePromise; });
  let opened = 0;
  const state = {
    newsclawMode: true,
    newsclawModeGeneration: 2,
    webMonitorNotificationGeneration: 4,
    webMonitorNotificationActions: new Set(),
    messages: [msg],
    messageListSnapshot: [msg],
  };
  const context = {
    state,
    ENDPOINTS: { webMonitorNotifications: "/news/monitor-notifications" },
    requestJsonStrict: async () => pending,
    messageKey: (item) => String(item.delivery_id || ""),
    syncMessageCount() {}, renderMessages() {}, showToast() {},
    refreshWebMonitors: async () => undefined,
    configErrorMessage: () => "", openHomePage() {}, openContentLibraryPage() {},
    eventWorkspaceController: { async openEvent() { opened += 1; } }, encodeURIComponent,
  };
  vm.runInNewContext(`${respondSource}\nglobalThis.__respond = respondWebMonitorNotification;`, context);

  const opening = context.__respond(msg, "open", null);
  await new Promise(setImmediate);
  state.newsclawMode = false;
  state.newsclawModeGeneration += 1;
  resolveAction({ ok: true });
  await opening;

  assert.equal(opened, 0);
  assert.equal(state.webMonitorNotificationActions.size, 0);
});

test("ordinary product mode rejects the hidden monitoring route", async () => {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const normalizeSource = between(source, "function normalizeDesktopContentLibraryTab", "function storedDesktopContentLibraryTab");
  const context = { state: { newsclawMode: false } };
  vm.runInNewContext(`${normalizeSource}\nglobalThis.__normalize = normalizeDesktopContentLibraryTab;`, context);
  assert.equal(context.__normalize("monitoring"), "watchLater");
  context.state.newsclawMode = true;
  assert.equal(context.__normalize("monitoring"), "monitoring");
  assert.match(source, /state\.messages = state\.messages\.filter\([\s\S]*web-monitor-notification/);
  assert.match(source, /availableTabs = DESKTOP_CONTENT_LIBRARY_TABS\.filter/);
  assert.match(source, /if \(!newsclawMode\)[\s\S]*renderWebMonitors\(\)/);
});
