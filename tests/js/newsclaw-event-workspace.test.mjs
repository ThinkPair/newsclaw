import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");

async function eventWorkspaceApi() {
  const source = await read("src/openbiliclaw/web/desktop/assets/js/event-workspace.js");
  const context = { globalThis: {} };
  vm.runInNewContext(source, context, { filename: "event-workspace.js" });
  return { api: context.globalThis.NewsClawEventWorkspace, source };
}

test("event workspace renders the selected inbox, pulse and evidence hierarchy", async () => {
  const [html, css, app] = await Promise.all([
    read("src/openbiliclaw/web/desktop/index.html"),
    read("src/openbiliclaw/web/desktop/assets/css/app.css"),
    read("src/openbiliclaw/web/desktop/assets/js/app.js"),
  ]);
  for (const id of [
    "eventWorkspace",
    "eventInboxList",
    "eventPulseTimeline",
    "eventEvidencePane",
    "eventConflictPanel",
    "eventMarkReadBtn",
    "eventAuditToggleBtn",
  ]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(html, /这里只放已经发生、能够沿证据反查的变化/);
  assert.match(html, /event-workspace\.js[\s\S]*app\.js/);
  assert.match(css, /\.event-workspace-grid\s*\{[\s\S]*grid-template-columns:[\s\S]*minmax\(270px,[\s\S]*minmax\(420px,[\s\S]*minmax\(310px,/);
  assert.match(css, /@container desktop-main \(max-width: 1040px\)[\s\S]*\.event-evidence-pane\s*\{[\s\S]*position:\s*fixed/);
  assert.match(app, /newsEvents:\s*"\/news\/events"/);
  assert.match(app, /newsChanges:\s*"\/news\/changes"/);
  assert.match(app, /newsConflicts:\s*"\/news\/conflicts"/);
  assert.match(app, /events:\s*"\/events"/);
  assert.doesNotMatch(app, /events:\s*"\/news\/events"/);
});

test("objective event copy is A to B and never imports model interpretation", async () => {
  const { api, source } = await eventWorkspaceApi();
  assert.ok(api);
  assert.deepEqual(
    { ...api.buildEventStatement({ previous_state: "维持利率", new_state: "下调25个基点" }) },
    {
      previous: "维持利率",
      next: "下调25个基点",
      plain: "维持利率 → 下调25个基点",
    },
  );
  assert.doesNotMatch(source, /\.inference\b|\.confidence\b|match[_-]?score|impact[_-]?score/i);
  assert.match(source, /现实生效/);
  assert.match(source, /新闻爪发现/);
  assert.match(source, /data-event-open-evidence>查看冲突证据/);
  assert.match(source, /syncNarrowEvidence\(true\)/);
  assert.match(source, /事实已核验/);
  assert.match(source, /存在相互冲突的事实版本/);
});

test("current state head follows event state instead of discovery order", async () => {
  const { api } = await eventWorkspaceApi();
  const changes = [
    { change_id: "late-old", lifecycle: "active", new_state: "旧状态" },
    { change_id: "actual-head", lifecycle: "active", new_state: "现实当前状态" },
  ];
  assert.equal(
    api.currentStateHeadId(changes, { current_state: "现实当前状态", conflict_count: 0 }),
    "actual-head",
  );
  assert.equal(
    api.currentStateHeadId(changes, { current_state: "现实当前状态", conflict_count: 1 }),
    "",
  );
  assert.equal(
    api.currentStateHeadId(
      [{ change_id: "audit-branch", lifecycle: "active", is_state_applied: false, new_state: "现实当前状态" }],
      { current_state: "现实当前状态", conflict_count: 0 },
    ),
    "",
  );
});

test("audit branches and unknown source scope stay explicit", async () => {
  const { api, source } = await eventWorkspaceApi();
  assert.equal(api.changeSourceScopeLabel("multi_source"), "多源佐证");
  assert.equal(api.changeSourceScopeLabel("single_source"), "单源佐证");
  assert.equal(api.changeSourceScopeLabel("unknown"), "来源范围待核验");
  assert.match(source, /已核验旁支，未进入当前状态链/);
  assert.match(source, /is_state_applied/);
  assert.match(source, /const change = event \? selectedChange\(\) : null/);
  assert.match(source, /Boolean\(selectedEventForView\(\)\) && state\.conflicts\.length > 0/);
  assert.match(source, /evidenceReturnChangeId/);
  assert.match(source, /querySelectorAll\("\[data-change-id\]"\)/);
  assert.match(source, /if \(openMobileDetail\) syncMobileDetail\(true\)/);
});

test("empty states distinguish no tracking, unread complete and conflicts", async () => {
  const { api } = await eventWorkspaceApi();
  const none = api.classifyInboxEmpty({ summary: { event_count: 0 }, attention: "all", topicGroup: "", query: "" });
  const read = api.classifyInboxEmpty({ summary: { event_count: 3 }, attention: "unread", topicGroup: "", query: "" });
  const conflict = api.classifyInboxEmpty({ summary: { event_count: 3 }, attention: "conflict", topicGroup: "", query: "" });
  assert.equal(none.title, "还没有持续跟踪的事件");
  assert.equal(none.action, "materials");
  assert.equal(read.title, "未读变化已处理完");
  assert.equal(conflict.title, "当前没有待看的证据冲突");
});

test("evidence labels never upgrade title-only or legacy material", async () => {
  const { api } = await eventWorkspaceApi();
  assert.equal(api.evidenceScopeLabel({ content_scope: "full_text" }), "正文级");
  assert.equal(api.evidenceScopeLabel({ content_scope: "title_only" }), "标题级");
  assert.equal(
    api.evidenceScopeLabel({ content_scope: "full_text", evidence_completeness: "partial_legacy" }),
    "历史资料不完整",
  );
});
