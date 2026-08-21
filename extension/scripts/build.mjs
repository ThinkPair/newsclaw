import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { build } from "esbuild";

import { verifyBuildAssets } from "./verify-build-assets.mjs";

const root = resolve(import.meta.dirname, "..");
const targetFlagIndex = process.argv.indexOf("--target");
const requestedTarget = targetFlagIndex >= 0 ? process.argv[targetFlagIndex + 1] : process.env.TARGET;
const targetName =
  requestedTarget === "firefox"
    ? "firefox"
    : requestedTarget === "safari"
      ? "safari"
      : "chrome";
const isFirefox = targetName === "firefox";
const isSafari = targetName === "safari";
const buildTarget = isFirefox ? "firefox140" : isSafari ? "safari18" : "chrome120";
const outDir = isFirefox ? "dist-firefox" : isSafari ? "dist-safari" : "dist";

const target = {
  entry: resolve(root, "src/background/service-worker.ts"),
  outfile: resolve(root, `${outDir}/background/service-worker.js`),
};

await mkdir(dirname(target.outfile), { recursive: true });
await build({
  entryPoints: [target.entry],
  outfile: target.outfile,
  bundle: true,
  format: "iife",
  platform: "browser",
  target: buildTarget,
  sourcemap: true,
  logLevel: "info",
  footer: { js: "null;" },
});

if (isFirefox || isSafari) {
  const chromeManifest = JSON.parse(await readFile(resolve(root, "manifest.json"), "utf-8"));
  const targetManifestName = isFirefox ? "manifest.firefox.json" : "manifest.safari.json";
  const targetManifest = JSON.parse(
    await readFile(resolve(root, targetManifestName), "utf-8"),
  );
  const merged = {};
  for (const [key, value] of Object.entries(targetManifest)) {
    merged[key] = value;
    if (key === "name") merged.version = chromeManifest.version;
  }
  await writeFile(
    resolve(root, `${outDir}/manifest.json`),
    `${JSON.stringify(merged, null, 2)}\n`,
  );
  await cp(resolve(root, "popup"), resolve(root, `${outDir}/popup`), { recursive: true });
  await cp(resolve(root, "icons"), resolve(root, `${outDir}/icons`), { recursive: true });
}

await verifyBuildAssets({ root, target: targetName });
console.log(`NewsClaw ${targetName} extension built in ${outDir}/`);
