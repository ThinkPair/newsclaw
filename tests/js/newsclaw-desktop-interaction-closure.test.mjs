import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(path, "utf8");

function sliceBetween(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start + startMarker.length);
  assert.ok(start >= 0, `missing start marker: ${startMarker}`);
  assert.ok(end > start, `missing end marker: ${endMarker}`);
  return source.slice(start, end);
}

test("global filtering is available only on the visible new-materials page", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  assert.match(html, /id="searchForm" hidden aria-hidden="true"/);
  assert.match(html, /id="searchInput"[^>]+disabled/);
  const availability = sliceBetween(js, "function isNewMaterialsSearchAvailable", "function syncHomeSearch");
  assert.match(availability, /state\.homeMode === "materials"/);
  assert.match(availability, /!document\.getElementById\("homePage"\)\?\.hidden/);
  assert.match(availability, /form\.hidden = !available/);
  assert.match(availability, /control\.disabled = !available/);
  const apply = sliceBetween(js, "function applyHomeSearch", 'safeBind("#sideDrawerBtn"');
  assert.match(apply, /if \(!isNewMaterialsSearchAvailable\(\)\) return false/);
  assert.doesNotMatch(apply, /eventWorkspaceController\?\.applySearch/);
});

test("recommendation comments and chat submissions cannot report success early", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const cardAction = sliceBetween(js, "async function handleCardAction", "function renderRail");
  assert.match(cardAction, /card\.dataset\.commentSubmitting = "true"/);
  assert.ok(cardAction.indexOf("await submitFeedback") < cardAction.indexOf("finishRecommendationFeedback"));
  assert.ok(cardAction.indexOf("await submitFeedback") < cardAction.indexOf('input.value = ""'));
  assert.match(cardAction, /catch \(error\)[\s\S]+status\.setAttribute\("role", "alert"\)/);

  const chat = sliceBetween(js, "async function sendChat", "async function refreshRecommendations");
  assert.match(chat, /chatSubmissionInFlight\.has\(surface\)/);
  assert.match(chat, /turn_id: createClientTurnId\(`chat-\$\{surface\}`\)/);
  assert.match(chat, /if \(input\) input\.value = message/);
  assert.match(chat, /form\?\.setAttribute\("aria-busy", "true"\)/);
  assert.match(chat, /chatSubmissionInFlight\.delete\(surface\)/);
});

test("generic overlays and the collapsed side drawer have a complete focus lifecycle", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  for (const id of ["mobileMenu", "aboutOverlay", "messagesDrawer", "mobileQrDrawer", "activityDrawer"]) {
    assert.match(html, new RegExp(`id="${id}"[^>]+aria-hidden="true"`));
  }
  const lifecycle = sliceBetween(js, "function setModalBackgroundInert", "const MAIN_PAGE_IDS");
  assert.match(lifecycle, /element\.inert = true/);
  assert.match(lifecycle, /dialog\.setAttribute\("aria-modal", "true"\)/);
  assert.match(lifecycle, /if \(event\.key === "Escape"\)/);
  assert.match(lifecycle, /if \(event\.key !== "Tab"\) return/);
  assert.match(lifecycle, /opener\?\.isConnected/);
  assert.match(js, /if \(drawer\) drawer\.inert = !open/);
});

test("pending notifications and activity history preserve honest loading and retry states", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  const notification = sliceBetween(js, "async function refreshPendingNotification", "function hydrateInboxFromSpeculations");
  assert.match(notification, /pendingNotificationRefreshPromise/);
  assert.match(notification, /requestJsonStrict\(ENDPOINTS\.notificationPending/);
  assert.match(notification, /pendingNotificationLoadState = "error"/);
  const response = sliceBetween(js, "async function respondNotification", "async function refreshWebMonitorNotifications");
  assert.match(response, /rememberPendingRequestId\("notification-ack", articleId\)/);
  assert.match(response, /ENDPOINTS\.notificationAck/);
  assert.match(response, /article_id: articleId, request_id: pending\.requestId/);
  assert.doesNotMatch(response, /notificationSent|notifications\/sent/);

  const activity = sliceBetween(js, "function renderActivityHistory", "function formatPercent");
  assert.match(activity, /if \(state\.activityLoading\) return/);
  assert.match(activity, /seen\.has\(key\)/);
  assert.match(activity, /data-activity-retry/);
  assert.match(activity, /data-activity-summary/);
  assert.match(activity, /aria-expanded/);
});

test("monitoring, settings reconciliation, five tabs and theme notice avoid hidden or fake actions", async () => {
  const [html, css, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  assert.match(html, /id="monitoringAddBtn"[^>]+disabled/);
  const monitor = sliceBetween(js, "async function createWebMonitor", "function normalizeDesktopContentLibraryTab");
  assert.match(monitor, /safeHttpContentUrl\(url\)/);
  assert.match(monitor, /readOnlyAction = action === "event" \|\| action === "history"/);
  assert.match(monitor, /if \(!eventId\) return/);
  assert.match(monitor, /submit\.disabled = submit\.dataset\.submitting === "true" \|\| !safeHttpContentUrl/);

  const settings = sliceBetween(js, "function settingsDomainOutcome", "const delightBanner");
  assert.match(settings, /Promise\.allSettled/);
  assert.match(settings, /reconcileSettingsAfterPartialSave/);
  assert.match(settings, /requestJsonStrict\(ENDPOINTS\.config/);
  assert.match(settings, /requestJsonStrict\("\/news\/sources"/);
  assert.match(settings, /requestJsonStrict\("\/news\/settings"/);
  assert.match(settings, /部分设置未保存，已重新核账/);

  assert.match(css, /:has\(#contentLibraryMonitoringTab:not\(\[hidden\]\)\)[\s\S]+repeat\(5/);
  const noticeRule = css.match(/\.theme-notice \{[^}]+\}/)?.[0] || "";
  assert.match(noticeRule, /position: relative/);
  assert.doesNotMatch(noticeRule, /position: fixed/);
  const notice = sliceBetween(js, "function setupThemeNotice", "function showToast");
  assert.match(notice, /insertAdjacentElement\("afterend", notice\)/);
  assert.match(notice, /setTimeout\(\(\) => hide\(\), THEME_NOTICE_DURATION_MS\)/);
  assert.match(notice, /hide\(\{ remember: true \}\)/);
});
