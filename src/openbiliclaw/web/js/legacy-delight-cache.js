/**
 * Remove stale OpenBiliClaw delight candidates from browser-owned caches.
 *
 * NewsClaw only accepts delight records that explicitly identify themselves as
 * `source_platform: "news"`.  The cleanup is intentionally narrow: it only
 * visits delight/queue-shaped keys and stores, and never touches settings,
 * limits, preferences, or config values.
 */

const NEWS_DELIGHT_PLATFORM = "news";
const DELIGHT_STORAGE_SCOPE_RE = /(?:active[_-]?delights?|delight(?:[_-]?(?:queue|cache|state|batch|pending|candidates?))?|(?:queue|cache|batch|pending|candidates?)[_-]?delight)/i;
const DELIGHT_STORAGE_SETTING_RE = /(?:limit|setting|preference|config)/i;
const LEGACY_DATABASE_RE = /(?:openbiliclaw|newsclaw|\bobc\b)/i;

function isObject(value) {
  return Boolean(value) && typeof value === "object";
}

function candidateValue(item) {
  return isObject(item?.data) ? item.data : item;
}

function looksLikeDelightRecord(value) {
  const candidate = candidateValue(value);
  return isObject(candidate) && (
    "source_platform" in candidate
    || "bvid" in candidate
    || "article_id" in candidate
    || "content_id" in candidate
  );
}

export function isNewsDelightItem(item) {
  const candidate = candidateValue(item);
  return String(candidate?.source_platform ?? "").trim().toLowerCase() === NEWS_DELIGHT_PLATFORM;
}

export function sanitizeLegacyDelightPayload(value, { delightScope = false } = {}) {
  if (Array.isArray(value)) {
    if (!delightScope) return { value, changed: false };
    const filtered = value.filter(isNewsDelightItem);
    return { value: filtered, changed: filtered.length !== value.length };
  }
  if (!isObject(value)) return { value, changed: false };

  if (delightScope && looksLikeDelightRecord(value)) {
    return isNewsDelightItem(value)
      ? { value, changed: false }
      : { value: null, changed: true };
  }

  let changed = false;
  const next = { ...value };
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

function shouldRemoveSanitizedValue(value) {
  return value === null || (Array.isArray(value) && value.length === 0);
}

export function purgeLegacyDelightLocalStorage(storage) {
  if (storage === undefined) {
    try { storage = globalThis.localStorage; } catch { return { removed: 0, updated: 0 }; }
  }
  if (!storage) return { removed: 0, updated: 0 };
  let removed = 0;
  let updated = 0;
  let length = 0;
  try { length = storage.length; } catch { return { removed, updated }; }

  for (let index = length - 1; index >= 0; index -= 1) {
    let key = "";
    try { key = storage.key(index) || ""; } catch { continue; }
    if (!key || DELIGHT_STORAGE_SETTING_RE.test(key)) continue;
    const keyOwnsDelights = DELIGHT_STORAGE_SCOPE_RE.test(key);

    let raw = "";
    try { raw = storage.getItem(key) || ""; } catch { continue; }
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // A malformed value under a delight queue key cannot be safely reused.
      if (keyOwnsDelights) {
        try { storage.removeItem(key); removed += 1; } catch { /* best-effort */ }
      }
      continue;
    }

    const sanitized = sanitizeLegacyDelightPayload(parsed, { delightScope: keyOwnsDelights });
    if (!sanitized.changed) continue;
    try {
      if (shouldRemoveSanitizedValue(sanitized.value)) {
        storage.removeItem(key);
        removed += 1;
      } else {
        storage.setItem(key, JSON.stringify(sanitized.value));
        updated += 1;
      }
    } catch { /* best-effort */ }
  }
  return { removed, updated };
}

function openExistingDatabase(dbApi, name) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolve();
    };
    let request;
    try { request = dbApi.open(name); } catch { finish(); return; }
    request.onerror = finish;
    request.onblocked = finish;
    request.onsuccess = () => {
      const database = request.result;
      const storeNames = Array.from(database.objectStoreNames).filter(
        (storeName) => DELIGHT_STORAGE_SCOPE_RE.test(storeName)
          && !DELIGHT_STORAGE_SETTING_RE.test(storeName),
      );
      if (!storeNames.length) {
        database.close();
        finish();
        return;
      }

      let transaction;
      try { transaction = database.transaction(storeNames, "readwrite"); }
      catch {
        database.close();
        finish();
        return;
      }
      const close = () => {
        try { database.close(); } catch { /* best-effort */ }
        finish();
      };
      transaction.oncomplete = close;
      transaction.onerror = close;
      transaction.onabort = close;

      for (const storeName of storeNames) {
        let cursorRequest;
        try { cursorRequest = transaction.objectStore(storeName).openCursor(); }
        catch { continue; }
        cursorRequest.onsuccess = () => {
          const cursor = cursorRequest.result;
          if (!cursor) return;
          const sanitized = sanitizeLegacyDelightPayload(cursor.value, { delightScope: true });
          if (sanitized.changed) {
            if (sanitized.value === null) cursor.delete();
            else cursor.update(sanitized.value);
          }
          cursor.continue();
        };
      }
    };
  });
}

export async function purgeLegacyDelightIndexedDb(dbApi) {
  if (dbApi === undefined) {
    try { dbApi = globalThis.indexedDB; } catch { return; }
  }
  if (!dbApi || typeof dbApi.databases !== "function") return;
  let databases;
  try { databases = await dbApi.databases(); } catch { return; }
  const names = databases
    .map((entry) => String(entry?.name || ""))
    .filter((name) => name && (
      LEGACY_DATABASE_RE.test(name) || DELIGHT_STORAGE_SCOPE_RE.test(name)
    ));
  await Promise.allSettled(names.map((name) => openExistingDatabase(dbApi, name)));
}
