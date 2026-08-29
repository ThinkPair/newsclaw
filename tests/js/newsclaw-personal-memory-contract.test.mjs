import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");

test("personal memory exposes four stable hash routes and one-panel tab semantics", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  for (const [tab, suffix] of [
    ["confirmed", "Confirmed"], ["research", "Research"],
    ["candidates", "Candidates"], ["history", "History"],
  ]) {
    assert.match(js, new RegExp(`"${tab}"`));
    assert.match(js, new RegExp(`memory${suffix}Tab`));
    assert.match(js, new RegExp(`memory${suffix}Panel`));
    assert.match(html, new RegExp(`id="memory${suffix}Tab"[^>]*role="tab"`));
    assert.match(html, new RegExp(`id="memory${suffix}Panel"[^>]*role="tabpanel"`));
  }
  assert.ok(js.includes('const nextHash = `#memory/${state.personalMemory.activeTab}`'));
  assert.match(js, /window\.addEventListener\("hashchange"/);
  assert.match(js, /panel\.hidden = !selected/);
  assert.match(js, /button\.setAttribute\("aria-current", "page"\)/);
  assert.match(js, /safeBind\("#profileBtn", "click", \(\) => openProfilePage\(\)\)/);
  const profileOpenStart = js.indexOf("function openProfilePage(");
  const profileOpenEnd = js.indexOf("function openChatPage", profileOpenStart);
  const profileOpen = profileOpenStart >= 0 && profileOpenEnd > profileOpenStart
    ? js.slice(profileOpenStart, profileOpenEnd)
    : "";
  assert.ok(profileOpen.includes("openPersonalMemoryPage(tab)"));
  assert.ok(!profileOpen.includes("clearDesktopContentLibraryRoute"), "opening memory must preserve the library entry for browser Back");
});

test("personal memory writes are versioned, idempotent and reconciled before success is shown", async () => {
  const js = await read("src/openbiliclaw/web/desktop/assets/js/app.js");
  assert.match(js, /personalMemory:\s*"\/personal-memory"/);
  assert.match(js, /personalMemoryHistory:\s*"\/personal-memory\/history"/);
  assert.match(js, /rememberPendingRequestId\(`personal-memory-\$\{namespace\}`/);
  assert.match(js, /JSON\.stringify\(\{ \.\.\.payload, request_id: pending\.requestId \}\)/);
  assert.match(js, /method: "PATCH"/);
  assert.match(js, /expected_version: item\.version/);
  assert.match(js, /expected_version: candidate\.version/);
  assert.match(js, /\/delete`/);
  assert.match(js, /\/restore`/);
  assert.match(js, /\/accept`/);
  assert.match(js, /\/reject`/);
  assert.match(js, /连接中断，正在核对本机记录，暂不显示假成功/);
  assert.match(js, /await refreshPersonalMemory\(\)/);
  assert.match(js, /personal_memory_version_conflict/);
  assert.match(js, /personal_memory_state_conflict/);
  assert.match(js, /personal_memory_request_conflict/);
  assert.match(js, /personal_memory_merge_required/);
  assert.match(js, /target_memory_id: target.memory_id/);
  assert.match(js, /expected_memory_version: target.version/);
  assert.match(js, /`现有记忆｜\$\{existingTitle\}`/);
  assert.match(js, /`准备写入｜\$\{proposedTitle\}`/);
  assert.match(js, /确认替换吗？旧版本仍会保留在修改记录中/);
  assert.match(js, /personalMemoryCandidateResolution\(candidateId\)/);
  assert.match(js, /resolved\?\.status === "accepted"/);
  assert.match(js, /resolved\?\.status === "rejected"/);
  assert.doesNotMatch(js, /reconciled: \(dashboard\) => !dashboardHasCandidate\(dashboard, candidateId\)/);
  assert.doesNotMatch(js, /state\.personalMemory\.dashboard\s*=\s*\{[^}]*optimistic/i);
});

test("personal memory renders honest states and escapes every persisted user field", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  for (const id of [
    "memoryLoadingState", "memoryErrorState", "memoryRetryBtn", "memoryRefreshBtn",
    "memoryConfirmedEmpty", "memoryResearchEmpty", "memoryCandidatesEmpty", "memoryHistoryEmpty",
  ]) assert.match(html, new RegExp(`id="${id}"`));

  for (const expression of [
    "item.title", "item.content", "candidate.title", "candidate.content",
    "candidate.proposal_reason", "revision.reason", "beforeContent", "afterContent",
    "item.excerpt",
  ]) {
    assert.match(js, new RegExp(`escapeHtml\\(${expression.replace(".", "\\.")}[^)]*\\)`), expression);
  }
  assert.match(js, /const ref = String\(item\.ref \|\| ""\)/);
  assert.match(js, /escapeHtml\(ref\)/);
  assert.match(js, /class="memory-evidence-list"/);
  assert.match(js, /class="memory-compare-grid"/);
  assert.match(js, /memoryState\.loadState !== "loading"/);
  assert.match(js, /memoryState\.loadState !== "error"/);
  assert.match(js, /memoryState\.loadState !== "ready" \|\| lists\[tab\]\.length > 0/);
  assert.match(js, /系统推断不会自动变成你的记忆|待你确认/);
});

test("product mode isolates legacy profile from P4 and mobile counts stay honest", async () => {
  const [html, js] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  assert.match(html, /id="legacyProfileMount" hidden aria-hidden="true"/);
  assert.match(html, /id="personalMemoryMount"/);
  assert.match(html, /id="mobilePersonalMemorySummary"/);
  assert.match(html, /id="mobileLegacyProfileSummary"[^>]*hidden/);
  assert.match(js, /function setNewsClawProductMode\(enabled\)/);
  assert.match(js, /personalMount\.hidden = !newsclawMode/);
  assert.match(js, /legacyMount\.hidden = newsclawMode/);
  assert.match(js, /service === "newsclaw-api"/);
  assert.match(js, /if \(state\.newsclawMode === false\) \{\s*openLegacyProfilePage\(\)/);
  assert.match(js, /if \(state\.newsclawMode === true\) \{\s*void refreshPersonalMemory\(\)/);
  for (const id of ["mobileMemoryConfirmedCount", "mobileMemoryResearchCount", "mobileMemoryPendingCount"]) {
    assert.match(html, new RegExp(`id="${id}"`));
    assert.match(js, new RegExp(`"${id}"`));
  }
  assert.match(html, /id="mobileMemoryConfirmedCount">—</);
  assert.match(js, /const countsKnown = memoryState\.dashboard !== null/);
  assert.match(js, /countElement\.textContent = "—"/);
  assert.match(js, /element\.textContent = "—"/);
  assert.match(js, /setAttribute\("aria-label", "尚未读取"\)/);
  assert.match(js, /const enteringPage = !personalMemoryVisible/);
  assert.match(js, /if \(forceLoad \|\| enteringPage/);
  const mobileMenuStart = js.indexOf("function openMobileMenu(");
  const mobileMenuEnd = js.indexOf("function closeMobileMenu", mobileMenuStart);
  assert.ok(js.slice(mobileMenuStart, mobileMenuEnd).includes("refreshPersonalMemory()"));
  assert.doesNotMatch(js, /偏好结构化解释、长新闻和跨学科桥接|反信息茧房/);
});
