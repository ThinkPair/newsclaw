/**
 * MAIN-world script — observes ``window.__INITIAL_STATE__`` and ships a
 * serialisable snapshot to the isolated content script via postMessage.
 *
 * Why this exists: MV3 content scripts run in an isolated JS world.
 * They cannot read ``window.__INITIAL_STATE__`` set by the page's own
 * MAIN-world JavaScript — ``doc.defaultView`` only exposes their own
 * isolated globals. Without this bridge, ``bootstrap.ts``'s
 * ``extractBootstrapStateFromDocument`` returns ``null`` on every
 * Xiaohongshu page (production logs from 2026-05-05 confirmed 0
 * ``self_info persisted`` events across 1h+ of activity).
 *
 * What it does:
 *  1. Polls for ``window.__INITIAL_STATE__`` to appear (Xiaohongshu's
 *     SPA assigns it after Vue mounts).
 *  2. Walks the state, unwraps Vue 3 ``ref`` objects, breaks cycles,
 *     drops functions / Vue-internal keys.
 *  3. ``window.postMessage({source: "obc-xhs-state", state})`` so the
 *     isolated-world listener inside ``bootstrap.ts`` caches it.
 *  4. Re-emits on ``popstate`` / ``visibilitychange`` because XHS is a
 *     SPA — landing on /explore vs /user/profile/X exposes different
 *     state subtrees.
 *
 * What we do NOT do: never mutate state, never exfiltrate beyond the
 * already-cooked ``__INITIAL_STATE__`` that any page-side script can
 * read, never run on non-XHS hosts (manifest matches restrict this).
 */

const POST_MESSAGE_SOURCE = "obc-xhs-state";

// Vue 3 reactive trees mark refs with __v_isRef and stash the underlying
// value in _rawValue / _value. Vue's reactivity machinery also creates
// circular references via dep <-> sub edges. Unwrap and break cycles so
// structuredClone (used by postMessage) doesn't throw.
//
// Whitelisted top-level state keys so the snapshot stays bounded —
// xhs's full __INITIAL_STATE__ can carry hundreds of feed items the
// bootstrap path doesn't consume. bootstrap.ts:notesForScope only
// reads from these subtrees.
const STATE_WHITELIST = new Set([
  "user",
  "saved",
  "collect",
  "collections",
  "liked",
  "likes",
  "history",
  "footprint",
  "browseHistory",
  "browsingHistory",
]);

// Hard cap on snapshot size (post-clone, pre-postMessage). Above this,
// we drop to a minimal {user: {loggedIn, userInfo}} subset so the bridge
// at least delivers self_info. 2 MB matches Chrome's structured-clone
// soft-limit for typical pages without memory pressure.
const MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024;

// Vue ref-detection helpers exported for unit tests.
export function isVueRef(value: unknown): boolean {
  if (value === null || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  return obj.__v_isRef === true || "_rawValue" in obj;
}

/**
 * Deep-clone a value into a structuredClone-safe shape.
 *
 * - Unwraps Vue 3 refs (``{__v_isRef: true, _rawValue: ...}``) into
 *   their inner values
 * - Recurses into plain objects and arrays
 * - Drops functions, Symbols, getter-throws, ``__v_*`` Vue internals,
 *   and ``dep`` / ``deps`` reactivity wires (these create cycles)
 * - Breaks cycles via WeakSet
 *
 * Exported for test coverage of edge cases.
 */
export function safeJsonClone(value: unknown, seen?: WeakSet<object>): unknown {
  if (value === null || value === undefined) return value;
  const t = typeof value;
  if (t === "function" || t === "symbol") return undefined;
  if (t !== "object") return value;

  // Vue 3 ref unwrap — walk through ref chains.
  if (isVueRef(value)) {
    const r = value as Record<string, unknown>;
    if ("_rawValue" in r) return safeJsonClone(r._rawValue, seen);
    if ("_value" in r) return safeJsonClone(r._value, seen);
  }

  const tracker = seen ?? new WeakSet<object>();
  if (tracker.has(value as object)) return undefined; // cycle
  tracker.add(value as object);

  if (Array.isArray(value)) {
    return value.map((item) => safeJsonClone(item, tracker));
  }

  const obj = value as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(obj)) {
    // Vue 3 reactivity wires + internal flags — these create cycles
    // and aren't useful to bootstrap.ts.
    if (key.startsWith("__v_")) continue;
    if (key === "dep" || key === "deps" || key === "sub" || key === "subs") continue;
    let val: unknown;
    try {
      val = obj[key];
    } catch {
      continue; // getter threw, skip
    }
    const cloned = safeJsonClone(val, tracker);
    if (cloned !== undefined) out[key] = cloned;
  }
  return out;
}

/**
 * Whitelist + clone the state subtrees bootstrap.ts actually reads.
 * Returns ``null`` if the input doesn't look like xhs state.
 */
export function buildStateSnapshot(rawState: unknown): Record<string, unknown> | null {
  if (rawState === null || typeof rawState !== "object") return null;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(rawState as Record<string, unknown>)) {
    if (!STATE_WHITELIST.has(key)) continue;
    const cloned = safeJsonClone((rawState as Record<string, unknown>)[key]);
    if (cloned !== undefined) out[key] = cloned;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/**
 * Last-resort minimal snapshot when the full one exceeds size budget.
 * bootstrap.ts can still extract self_info from this.
 */
function buildMinimalSnapshot(rawState: unknown): Record<string, unknown> | null {
  if (rawState === null || typeof rawState !== "object") return null;
  const user = (rawState as Record<string, unknown>).user;
  if (!user || typeof user !== "object") return null;
  const userObj = user as Record<string, unknown>;
  const userOut: Record<string, unknown> = {};
  for (const key of ["loggedIn", "userInfo", "userPageData"]) {
    const cloned = safeJsonClone(userObj[key]);
    if (cloned !== undefined) userOut[key] = cloned;
  }
  return Object.keys(userOut).length > 0 ? { user: userOut } : null;
}

function approximateByteSize(value: unknown): number {
  try {
    return JSON.stringify(value).length;
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

let lastSnapshotJson = "";

function emitOnce(): void {
  const win = window as Window & { __INITIAL_STATE__?: unknown };
  const raw = win.__INITIAL_STATE__;
  if (!raw) return;

  let snapshot = buildStateSnapshot(raw);
  if (snapshot === null) return;

  // Size-cap the payload — fall back to minimal subset on overrun.
  if (approximateByteSize(snapshot) > MAX_SNAPSHOT_BYTES) {
    const minimal = buildMinimalSnapshot(raw);
    if (minimal === null) return;
    snapshot = minimal;
  }

  // Skip re-emit when nothing changed (popstate fires twice / SPA route
  // updates that don't affect the user subtree don't need to re-post).
  const json = JSON.stringify(snapshot);
  if (json === lastSnapshotJson) return;
  lastSnapshotJson = json;

  try {
    window.postMessage(
      { source: POST_MESSAGE_SOURCE, state: snapshot },
      "*",
    );
  } catch {
    // structuredClone failure (rare after our own clone above) — swallow.
  }
}

// Poll early because XHS assigns __INITIAL_STATE__ after the Vue app
// mounts, which can be several hundred ms after document_start. After
// the first successful emit the polling stops; subsequent state changes
// fire via popstate / visibilitychange.
function startPolling(): void {
  let attempts = 0;
  const tick = (): void => {
    attempts += 1;
    emitOnce();
    if (attempts >= 60) return; // 60 * 250ms = 15s budget
    window.setTimeout(tick, 250);
  };
  tick();
}

if (typeof window !== "undefined") {
  startPolling();
  window.addEventListener("popstate", emitOnce);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") emitOnce();
  });
  // Re-emit on user interaction so SPA route changes that don't fire
  // popstate (Vue Router push) still bring the snapshot up to date.
  // Throttled in emitOnce by lastSnapshotJson check.
  window.addEventListener("click", emitOnce, { passive: true });

  // eslint-disable-next-line no-console
  console.debug("[OpenBiliClaw] xhs state bridge installed (MAIN world)");
}
