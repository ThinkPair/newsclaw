const STORAGE_KEY = "newsclaw.backend.endpoint";
const DEFAULT_ENDPOINT = "http://127.0.0.1:8430";
const DEV_ENDPOINT = "http://127.0.0.1:8431";

const $ = (selector) => document.querySelector(selector);
const clientFrame = $("#clientFrame");
const stateCard = $("#stateCard");
const stateTitle = $("#stateTitle");
const stateMessage = $("#stateMessage");
const connectionText = $("#connectionText");
const connectionDot = $("#connectionDot");
const settingsPanel = $("#settingsPanel");
const endpointInput = $("#endpointInput");
let activeEndpoint = DEFAULT_ENDPOINT;

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
  stateCard.hidden = false;
  stateTitle.textContent = "正在连接新闻爪";
  stateMessage.textContent = `正在检查 ${endpoint}。扩展只连接本机服务，不读取任何网页。`;
  setConnectionState("", "正在连接本机服务…");
}

function showOffline(endpoint, reason = "") {
  stateCard.hidden = false;
  stateTitle.textContent = "本机服务还没启动";
  stateMessage.textContent = `${endpoint} 暂时无法连接。请先启动 NewsClaw，再点重新连接。${reason ? `（${reason}）` : ""}`;
  setConnectionState("degraded", "离线 · 等待本机服务");
}

async function probe(endpoint) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3500);
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
    return response.ok;
  } finally {
    clearTimeout(timeout);
  }
}

async function connect({ allowDevelopmentFallback = true } = {}) {
  showLoading(activeEndpoint);
  const candidates = [activeEndpoint];
  if (allowDevelopmentFallback && activeEndpoint === DEFAULT_ENDPOINT) candidates.push(DEV_ENDPOINT);
  let lastReason = "";
  for (const candidate of candidates) {
    try {
      if (!(await probe(candidate))) {
        lastReason = "健康检查未通过";
        continue;
      }
      activeEndpoint = candidate;
      clientFrame.src = `${candidate}/m/?surface=extension`;
      stateCard.hidden = true;
      setConnectionState("online", candidate === DEV_ENDPOINT ? "在线 · 开发端口 8431" : "在线 · 本机服务");
      return;
    } catch (error) {
      lastReason = error?.name === "AbortError" ? "连接超时" : "连接被拒绝";
    }
  }
  clientFrame.removeAttribute("src");
  showOffline(activeEndpoint, lastReason);
}

function openSettings() {
  endpointInput.value = activeEndpoint;
  settingsPanel.hidden = false;
  endpointInput.focus();
}

function closeSettings() {
  settingsPanel.hidden = true;
}

$("#retryButton").addEventListener("click", () => void connect());
$("#settingsButton").addEventListener("click", openSettings);
$("#stateSettingsButton").addEventListener("click", openSettings);
$("#cancelSettingsButton").addEventListener("click", closeSettings);
$("#openFullButton").addEventListener("click", () => {
  chrome.tabs.create({ url: `${activeEndpoint}/web` });
});
$("#settingsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    activeEndpoint = normalizeEndpoint(endpointInput.value);
    await storageSet(activeEndpoint);
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
