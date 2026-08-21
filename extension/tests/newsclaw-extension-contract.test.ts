import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import vm from "node:vm";

import { collectManifestBuildAssets } from "../scripts/verify-build-assets.mjs";

type Manifest = {
  manifest_version?: number;
  name?: string;
  permissions?: string[];
  host_permissions?: string[];
  optional_host_permissions?: string[];
  background?: { service_worker?: string; scripts?: string[] };
  action?: { default_popup?: string };
  side_panel?: { default_path?: string };
  sidebar_action?: { default_panel?: string };
  content_scripts?: unknown;
  web_accessible_resources?: unknown;
  content_security_policy?: { extension_pages?: string };
};

const LOOPBACK_HOSTS = ["http://127.0.0.1/*", "http://localhost/*"];
const FORBIDDEN_PERMISSIONS = [
  "activeTab",
  "alarms",
  "cookies",
  "notifications",
  "scripting",
  "tabs",
  "webNavigation",
  "webRequest",
];

async function readJson<T>(path: string): Promise<T> {
  return JSON.parse(await readFile(path, "utf8")) as T;
}

test("all browser manifests keep the extension inside its loopback-only client boundary", async () => {
  const manifests = [
    await readJson<Manifest>("manifest.json"),
    await readJson<Manifest>("manifest.firefox.json"),
    await readJson<Manifest>("manifest.safari.json"),
  ];

  assert.deepEqual(manifests[0]?.permissions, ["sidePanel", "storage"]);
  assert.deepEqual(manifests[1]?.permissions, ["storage"]);
  assert.deepEqual(manifests[2]?.permissions, ["storage"]);

  for (const manifest of manifests) {
    assert.equal(manifest.manifest_version, 3);
    assert.match(manifest.name ?? "", /NewsClaw/);
    assert.deepEqual(manifest.host_permissions, LOOPBACK_HOSTS);
    assert.equal(manifest.optional_host_permissions, undefined);
    assert.equal(manifest.content_scripts, undefined);
    assert.equal(manifest.web_accessible_resources, undefined);
    for (const permission of FORBIDDEN_PERMISSIONS) {
      assert.equal(
        manifest.permissions?.includes(permission),
        false,
        `forbidden permission returned: ${permission}`,
      );
    }
  }
});

test("store metadata presents NewsClaw as an independent local news product", async () => {
  const [metadata, pkg, manifest] = await Promise.all([
    readJson<Record<string, unknown>>("amo-metadata.json"),
    readJson<{ name: string; version: string }>("package.json"),
    readJson<{ name?: string; version?: string }>("manifest.json"),
  ]);
  const serialized = JSON.stringify(metadata);

  assert.equal(pkg.name, "newsclaw-extension");
  assert.equal(pkg.version, "0.1.0");
  assert.equal(manifest.version, "0.1.0");
  assert.match(String((metadata.name as Record<string, string>)["zh-CN"]), /NewsClaw/);
  assert.match(String((metadata.summary as Record<string, string>)["zh-CN"]), /真实新闻/);
  assert.doesNotMatch(serialized, /whiteguo233|OpenBiliClaw|Bilibili|小红书|抖音/);
  assert.equal("homepage" in metadata, false);
});

test("Chrome, Firefox, and Safari expose the same local client through native side surfaces", async () => {
  const chrome = await readJson<Manifest>("manifest.json");
  const firefox = await readJson<Manifest>("manifest.firefox.json");
  const safari = await readJson<Manifest>("manifest.safari.json");

  assert.equal(chrome.side_panel?.default_path, "popup/popup.html");
  assert.equal(chrome.action?.default_popup, undefined);
  assert.equal(firefox.sidebar_action?.default_panel, "popup/popup.html");
  assert.equal(safari.action?.default_popup, "popup/popup.html");
  assert.equal(safari.side_panel, undefined);

  assert.deepEqual(collectManifestBuildAssets(chrome), [
    "dist/background/service-worker.js",
  ]);
  assert.deepEqual(collectManifestBuildAssets(firefox), [
    "background/service-worker.js",
  ]);
  assert.deepEqual(collectManifestBuildAssets(safari), [
    "background/service-worker.js",
  ]);
});

test("extension-page CSP permits only the two local NewsClaw origins", async () => {
  for (const path of ["manifest.json", "manifest.firefox.json", "manifest.safari.json"]) {
    const manifest = await readJson<Manifest>(path);
    const csp = manifest.content_security_policy?.extension_pages ?? "";
    assert.match(csp, /connect-src http:\/\/127\.0\.0\.1:\* http:\/\/localhost:\*/);
    assert.match(csp, /frame-src http:\/\/127\.0\.0\.1:\* http:\/\/localhost:\*/);
    assert.doesNotMatch(csp, /https?:\/\/\*/);
    assert.doesNotMatch(csp, /https:\/\//);
  }
});

test("popup accepts loopback HTTP origins and rejects remote or HTTPS endpoints", async () => {
  const source = await readFile("popup/popup.js", "utf8");
  const start = source.indexOf("function normalizeEndpoint(raw)");
  const end = source.indexOf("async function storageGet", start);
  assert.ok(start >= 0 && end > start, "normalizeEndpoint must remain testable in popup.js");

  const context = vm.createContext({
    URL,
    Error,
    String,
    DEFAULT_ENDPOINT: "http://127.0.0.1:8430",
  });
  vm.runInContext(
    `${source.slice(start, end)}\nglobalThis.normalizeEndpoint = normalizeEndpoint;`,
    context,
  );
  const normalizeEndpoint = context.normalizeEndpoint as (raw: unknown) => string;

  assert.equal(normalizeEndpoint(""), "http://127.0.0.1:8430");
  assert.equal(normalizeEndpoint("http://127.0.0.1:8431/path/"), "http://127.0.0.1:8431");
  assert.equal(normalizeEndpoint("http://LOCALHOST:8430/"), "http://localhost:8430");
  assert.throws(() => normalizeEndpoint("https://127.0.0.1:8430"));
  assert.throws(() => normalizeEndpoint("http://news.example.com"));
  assert.throws(() => normalizeEndpoint("http://localhost.example.com:8430"));
});

test("popup is a local iframe client and contains no page-inspection or cookie hooks", async () => {
  const [html, source] = await Promise.all([
    readFile("popup/popup.html", "utf8"),
    readFile("popup/popup.js", "utf8"),
  ]);

  assert.match(html, /id="clientFrame"/);
  assert.match(html, /title="新闻爪侧栏"/);
  assert.match(source, /\/m\/\?surface=extension/);
  assert.match(source, /\/api\/ping/);
  assert.match(source, /\/health/);
  assert.match(source, /chrome\.storage\.local/);
  assert.doesNotMatch(html, /content_scripts|web_accessible_resources/i);
  assert.doesNotMatch(
    source,
    /chrome\.(?:cookies|scripting|webRequest|webNavigation)|executeScript|querySelectorAll\([^)]*(?:article|video)/,
  );
});

test("service worker only wires the side panel and migrates old delight storage", async () => {
  const removed: string[] = [];
  const updated: Record<string, unknown> = {};
  let resolveMigration!: () => void;
  const migrationDone = new Promise<void>((resolve) => { resolveMigration = resolve; });
  const stored = {
    activeDelights: [
      { source_platform: "bilibili", bvid: "BV1legacy" },
      { source_platform: "news", article_id: "news-1" },
    ],
    delightState: JSON.stringify({
      queue: [
        { source_platform: "douyin", content_id: "legacy-video" },
        { source_platform: "news", article_id: "news-2" },
      ],
      current: { source_platform: "bilibili", bvid: "BV2legacy" },
    }),
    delightLimit: 20,
    unrelatedPreference: { theme: "light" },
  };
  const listeners: Array<() => void> = [];
  const chromeMock = {
    runtime: {
      onInstalled: { addListener: (listener: () => void) => listeners.push(listener) },
      onStartup: { addListener: (listener: () => void) => listeners.push(listener) },
    },
    sidePanel: {
      setPanelBehavior: async () => undefined,
      setOptions: async () => undefined,
    },
    storage: {
      local: {
        get: async () => stored,
        remove: async (keys: string[]) => { removed.push(...keys); },
        set: async (values: Record<string, unknown>) => {
          Object.assign(updated, values);
          resolveMigration();
        },
      },
    },
  };

  const previousChrome = (globalThis as { chrome?: unknown }).chrome;
  (globalThis as { chrome?: unknown }).chrome = chromeMock;
  try {
    const serviceWorkerUrl = pathToFileURL("src/background/service-worker.ts");
    serviceWorkerUrl.searchParams.set("contract", `${Date.now()}-${Math.random()}`);
    await import(serviceWorkerUrl.href);
    await migrationDone;
  } finally {
    (globalThis as { chrome?: unknown }).chrome = previousChrome;
  }

  assert.equal(listeners.length, 2);
  assert.deepEqual(removed, []);
  assert.deepEqual(updated.activeDelights, [
    { source_platform: "news", article_id: "news-1" },
  ]);
  assert.equal(
    updated.delightState,
    JSON.stringify({
      queue: [{ source_platform: "news", article_id: "news-2" }],
    }),
  );
  assert.equal("delightLimit" in updated, false);
  assert.equal("unrelatedPreference" in updated, false);

  const source = await readFile("src/background/service-worker.ts", "utf8");
  assert.doesNotMatch(
    source,
    /chrome\.(?:cookies|scripting|tabs|webRequest|webNavigation)|runtime\.onMessage|\bfetch\s*\(/,
  );
});

test("build configuration bundles only the NewsClaw service worker", async () => {
  const [buildSource, packageJson] = await Promise.all([
    readFile("scripts/build.mjs", "utf8"),
    readJson<{ scripts: Record<string, string> }>("package.json"),
  ]);

  assert.match(buildSource, /src\/background\/service-worker\.ts/);
  assert.doesNotMatch(buildSource, /src\/content|src\/main|content_scripts|web_accessible_resources/);
  assert.equal(
    packageJson.scripts.test,
    "node --test --experimental-strip-types tests/newsclaw-extension-contract.test.ts",
  );
});
