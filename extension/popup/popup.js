const STORAGE_KEY = "newsclaw.backend.endpoint";
const DEFAULT_ENDPOINT = "http://127.0.0.1:8430";
const DEV_ENDPOINT = "http://127.0.0.1:8431";
const PROBE_TIMEOUT_MS = 3500;

const $ = (selector) => document.querySelector(selector);
const clientFrame = $("#clientFrame");
const stateCard = $("#stateCard");
const stateTitle = $("#stateTitle");
const stateMessage = $("#stateMessage");
const connectionText = $("#connectionText");
const connectionDot = $("#connectionDot");
const monitorButton = $("#monitorButton");
const monitorBadge = $("#monitorBadge");
const settingsPanel = $("#settingsPanel");
const endpointInput = $("#endpointInput");
let activeEndpoint = DEFAULT_ENDPOINT;
let connectionGeneration = 0;
let settingsOpener = null;

const SETTINGS_FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

function resetMonitorBadge() {
  monitorButton.hidden = true;
  monitorButton.classList.remove("degraded");
  monitorBadge.hidden = true;
  monitorBadge.textContent = "";
  monitorButton.setAttribute("aria-label", "打开网页监控");
  monitorButton.title = "打开网页监控";
}

function normalizeEndpoint(raw) {
  const value = String(raw || "").trim().replace(/\/+$/, "");
  try {
    const url = new URL(value || DEFAULT_ENDPOINT);
    if (url.protocol !== "http:") throw new Error("NewsClaw 仅连接本机 HTTP 服务");
    if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
      throw new Error("只允许 127.0.0.1 或 localhost");
    }
    return url.origin;
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : "地址格式不正确");
  }
}

async function storageGet() {
  if (chrome?.storage?.local) {
    const result = await chrome.storage.local.get(STORAGE_KEY);
    return result[STORAGE_KEY] || "";
  }
  return localStorage.getItem(STORAGE_KEY) || "";
}

async function storageSet(endpoint) {
  if (chrome?.storage?.local) {
    await chrome.storage.local.set({ [STORAGE_KEY]: endpoint });
    return;
  }
  localStorage.setItem(STORAGE_KEY, endpoint);
}

function setConnectionState(kind, text) {
  connectionText.textContent = text;
  connectionDot.className = `status-dot${kind ? ` ${kind}` : ""}`;
}

function showLoading(endpoint) {
  resetMonitorBadge();
  stateCard.hidden = false;
  stateTitle.textContent = "正在连接新闻爪";
  stateMessage.textContent = `正在检查 ${endpoint}。扩展只连接本机服务，不读取任何网页。`;
  setConnectionState("", "正在连接本机服务…");
}

function showOffline(endpoint, reason = "") {
  resetMonitorBadge();
  stateCard.hidden = false;
  stateTitle.textContent = "本机服务还没启动";
  stateMessage.textContent = `${endpoint} 暂时无法连接。请先启动 NewsClaw，再点重新连接。${reason ? `（${reason}）` : ""}`;
  setConnectionState("degraded", "离线 · 等待本机服务");
}

async function probe(endpoint) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    let response = await fetch(`${endpoint}/api/ping`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (response.status === 404) {
      response = await fetch(`${endpoint}/health`, {
        cache: "no-store",
        signal: controller.signal,
      });
    }
    if (!response.ok) return false;
    const health = await fetch(`${endpoint}/api/health`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!health.ok) return false;
    const snapshot = await health.json().catch(() => null);
    return String(snapshot?.service || "") === "newsclaw-api";
  } finally {
    clearTimeout(timeout);
  }
}

async function refreshMonitorBadge(
  endpoint = activeEndpoint,
  generation = connectionGeneration,
) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(`${endpoint}/api/news/monitor-notifications`, {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("monitor inbox unavailable");
    const payload = await response.json();
    if (generation !== connectionGeneration || endpoint !== activeEndpoint) return;
    const count = Math.max(0, Number(payload?.unread_count || 0));
    monitorButton.hidden = false;
    monitorButton.classList.remove("degraded");
    monitorBadge.textContent = count > 99 ? "99+" : String(count);
    monitorBadge.hidden = count <= 0;
    monitorButton.setAttribute("aria-label", count ? `网页监控，${count} 条未读变化` : "打开网页监控");
    monitorButton.title = "打开网页监控";
  } catch {
    if (generation !== connectionGeneration || endpoint !== activeEndpoint) return;
    monitorButton.hidden = false;
    monitorButton.classList.add("degraded");
    monitorBadge.hidden = true;
    monitorBadge.textContent = "";
    monitorButton.setAttribute("aria-label", "网页监控状态暂不可用，仍可打开监控页");
    monitorButton.title = "监控状态暂不可用，点击查看";
  } finally {
    clearTimeout(timeout);
  }
}

async function refreshConnectionState() {
  const generation = ++connectionGeneration;
  const endpoint = activeEndpoint;
  try {
    if (!(await probe(endpoint))) {
      if (generation !== connectionGeneration || endpoint !== activeEndpoint) return;
      clientFrame.removeAttribute("src");
      showOffline(endpoint, "健康检查未通过");
      return;
    }
    if (generation !== connectionGeneration || endpoint !== activeEndpoint) return;
    const frameTarget = `${endpoint}/m/?surface=extension`;
    if ((clientFrame.getAttribute?.("src") || "") !== frameTarget) {
      clientFrame.src = frameTarget;
    }
    stateCard.hidden = true;
    setConnectionState(
      "online",
      endpoint === DEV_ENDPOINT ? "在线 · 开发端口 8431" : "在线 · 本机服务",
    );
    await refreshMonitorBadge(endpoint, generation);
  } catch (error) {
    if (generation !== connectionGeneration || endpoint !== activeEndpoint) return;
    clientFrame.removeAttribute("src");
    showOffline(
      endpoint,
      error?.name === "AbortError" ? "连接超时" : "连接被拒绝",
    );
  }
}

async function connect({ allowDevelopmentFallback = true } = {}) {
  const generation = ++connectionGeneration;
  showLoading(activeEndpoint);
  const candidates = [activeEndpoint];
  if (allowDevelopmentFallback && activeEndpoint === DEFAULT_ENDPOINT) candidates.push(DEV_ENDPOINT);
  let lastReason = "";
  for (const candidate of candidates) {
    try {
      if (!(await probe(candidate))) {
        if (generation !== connectionGeneration) return;
        lastReason = "健康检查未通过";
        continue;
      }
      if (generation !== connectionGeneration) return;
      activeEndpoint = candidate;
      clientFrame.src = `${candidate}/m/?surface=extension`;
      stateCard.hidden = true;
      setConnectionState("online", candidate === DEV_ENDPOINT ? "在线 · 开发端口 8431" : "在线 · 本机服务");
      void refreshMonitorBadge(candidate, generation);
      return;
    } catch (error) {
      lastReason = error?.name === "AbortError" ? "连接超时" : "连接被拒绝";
    }
  }
  if (generation !== connectionGeneration) return;
  clientFrame.removeAttribute("src");
  showOffline(activeEndpoint, lastReason);
}

function openSettings(event) {
  settingsOpener = event?.currentTarget || document.activeElement;
  endpointInput.value = activeEndpoint;
  settingsPanel.hidden = false;
  endpointInput.focus();
}

function closeSettings({ restoreFocus = true } = {}) {
  settingsPanel.hidden = true;
  if (restoreFocus) settingsOpener?.focus?.();
  settingsOpener = null;
}

settingsPanel.addEventListener("click", (event) => {
  if (event.target === settingsPanel) closeSettings();
});

settingsPanel.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closeSettings();
    return;
  }
  if (event.key !== "Tab") return;
  const nodes = [...settingsPanel.querySelectorAll(SETTINGS_FOCUSABLE)].filter(node => !node.hidden);
  if (!nodes.length) {
    event.preventDefault();
    settingsPanel.focus();
    return;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

$("#retryButton").addEventListener("click", () => void connect());
$("#settingsButton").addEventListener("click", openSettings);
$("#stateSettingsButton").addEventListener("click", openSettings);
$("#cancelSettingsButton").addEventListener("click", closeSettings);
$("#openFullButton").addEventListener("click", () => {
  chrome.tabs.create({ url: `${activeEndpoint}/web` });
});
monitorButton.addEventListener("click", () => {
  chrome.tabs.create({ url: `${activeEndpoint}/web/#library/monitoring` });
});
$("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const endpoint = normalizeEndpoint(endpointInput.value);
    connectionGeneration += 1;
    await storageSet(endpoint);
    activeEndpoint = endpoint;
    // The connection panel remains alive in side-panel and standalone popup
    // contexts, so a successful save must return keyboard focus to its opener.
    closeSettings();
    await connect({ allowDevelopmentFallback: false });
  } catch (error) {
    stateCard.hidden = false;
    stateTitle.textContent = "连接地址不可用";
    stateMessage.textContent = error instanceof Error ? error.message : "请检查连接地址";
    closeSettings();
  }
});

void (async () => {
  try {
    activeEndpoint = normalizeEndpoint(await storageGet());
  } catch {
    activeEndpoint = DEFAULT_ENDPOINT;
  }
  await connect();
})();

setInterval(() => {
  void refreshConnectionState();
}, 60000);
