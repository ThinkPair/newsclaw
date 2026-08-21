/**
 * NewsClaw's browser extension is intentionally a thin local side-panel
 * client. It does not inject scripts, inspect tabs, read page content, or
 * access cookies. All news and profile work remains in the local backend.
 */

const PANEL_PATH = "popup/popup.html";
const NEWS_DELIGHT_PLATFORM = "news";
const DELIGHT_STORAGE_SCOPE_RE = /(?:active[_-]?delights?|delight(?:[_-]?(?:queue|cache|state|batch|pending|candidates?))?|(?:queue|cache|batch|pending|candidates?)[_-]?delight)/i;
const DELIGHT_STORAGE_SETTING_RE = /(?:limit|setting|preference|config)/i;

type SanitizedValue = { value: unknown; changed: boolean };

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object";
}

function candidateValue(value: unknown): unknown {
  return isRecord(value) && isRecord(value.data) ? value.data : value;
}

function looksLikeDelightRecord(value: unknown): boolean {
  const candidate = candidateValue(value);
  return isRecord(candidate) && (
    "source_platform" in candidate
    || "bvid" in candidate
    || "article_id" in candidate
    || "content_id" in candidate
  );
}

function isNewsDelightItem(value: unknown): boolean {
  const candidate = candidateValue(value);
  return isRecord(candidate)
    && String(candidate.source_platform ?? "").trim().toLowerCase() === NEWS_DELIGHT_PLATFORM;
}

function sanitizeLegacyDelightPayload(
  value: unknown,
  { delightScope = false }: { delightScope?: boolean } = {},
): SanitizedValue {
  if (Array.isArray(value)) {
    if (!delightScope) return { value, changed: false };
    const filtered = value.filter(isNewsDelightItem);
    return { value: filtered, changed: filtered.length !== value.length };
  }
  if (!isRecord(value)) return { value, changed: false };
  if (delightScope && looksLikeDelightRecord(value)) {
    return isNewsDelightItem(value)
      ? { value, changed: false }
      : { value: null, changed: true };
  }

  let changed = false;
  const next: Record<string, unknown> = { ...value };
  for (const [key, child] of Object.entries(value)) {
    if (DELIGHT_STORAGE_SETTING_RE.test(key)) continue;
    const childScope = delightScope
      ? /^(?:items|queue|candidates|current|active|delights?)$/i.test(key)
        || DELIGHT_STORAGE_SCOPE_RE.test(key)
        || looksLikeDelightRecord(child)
      : DELIGHT_STORAGE_SCOPE_RE.test(key);
    if (!childScope) continue;
    const sanitized = sanitizeLegacyDelightPayload(child, { delightScope: true });
    if (!sanitized.changed) continue;
    if (sanitized.value === null) delete next[key];
    else next[key] = sanitized.value;
    changed = true;
  }
  return { value: next, changed };
}

async function purgeLegacyDelightStorage(): Promise<void> {
  let values: Record<string, unknown>;
  try { values = await chrome.storage.local.get(null); } catch { return; }
  const removeKeys: string[] = [];
  const updates: Record<string, unknown> = {};

  for (const [key, stored] of Object.entries(values)) {
    if (DELIGHT_STORAGE_SETTING_RE.test(key)) continue;
    const keyOwnsDelights = DELIGHT_STORAGE_SCOPE_RE.test(key);
    let value = stored;
    let wasJsonString = false;
    if (typeof stored === "string") {
      try { value = JSON.parse(stored); wasJsonString = true; }
      catch {
        if (keyOwnsDelights) removeKeys.push(key);
        continue;
      }
    }
    const sanitized = sanitizeLegacyDelightPayload(value, { delightScope: keyOwnsDelights });
    if (!sanitized.changed) continue;
    if (sanitized.value === null || (Array.isArray(sanitized.value) && sanitized.value.length === 0)) {
      removeKeys.push(key);
    } else {
      updates[key] = wasJsonString ? JSON.stringify(sanitized.value) : sanitized.value;
    }
  }

  try {
    if (removeKeys.length) await chrome.storage.local.remove(removeKeys);
    if (Object.keys(updates).length) await chrome.storage.local.set(updates);
  } catch { /* best-effort migration of old extension state */ }
}

async function enablePanelOnActionClick(): Promise<void> {
  if (!chrome.sidePanel?.setPanelBehavior) return;
  try {
    await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
  } catch (error) {
    console.warn(
      "[NewsClaw] Unable to enable click-to-open side panel:",
      error instanceof Error ? error.message : String(error),
    );
  }
}

chrome.runtime.onInstalled.addListener(() => {
  void enablePanelOnActionClick();
  void purgeLegacyDelightStorage();
});

chrome.runtime.onStartup.addListener(() => {
  void enablePanelOnActionClick();
  void purgeLegacyDelightStorage();
});

// Keep the path explicit when the extension is updated from an older build.
if (chrome.sidePanel?.setOptions) {
  void chrome.sidePanel.setOptions({ path: PANEL_PATH, enabled: true });
}
void enablePanelOnActionClick();
void purgeLegacyDelightStorage();
