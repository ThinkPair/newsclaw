import assert from "node:assert/strict";
import test from "node:test";

import {
  restoreDialogFocusAfterRender,
  runResumableMutationQueue,
} from "../../src/openbiliclaw/web/js/saved-sync-runtime.js";

test("resumable undo retains the uncertain mutation and every unattempted payload", async () => {
  const payloads = [{ id: "domain" }, { id: "specific-a" }, { id: "specific-b" }];
  const attempted = [];
  const result = await runResumableMutationQueue(payloads, async (payload) => {
    attempted.push(payload.id);
    if (payload.id === "specific-a") throw new Error("response lost");
  });

  assert.equal(result.complete, false);
  assert.deepEqual(attempted, ["domain", "specific-a"]);
  assert.deepEqual(result.remaining, [{ id: "specific-a" }, { id: "specific-b" }]);
  assert.match(result.error.message, /response lost/);
});

test("resumable undo completes an exact retry without replaying confirmed mutations", async () => {
  const attempted = [];
  const result = await runResumableMutationQueue(
    [{ id: "specific-a" }, { id: "specific-b" }],
    async (payload) => { attempted.push(payload.id); },
  );

  assert.equal(result.complete, true);
  assert.deepEqual(result.remaining, []);
  assert.deepEqual(attempted, ["specific-a", "specific-b"]);
});

test("dialog rerender restores focus only when the old focused node was detached", () => {
  let preferredFocused = 0;
  let fallbackFocused = 0;
  const preferred = { focus(options) { preferredFocused += 1; assert.equal(options.preventScroll, true); } };
  const fallback = { focus() { fallbackFocused += 1; } };
  const outside = {};
  const inside = {};
  const dialog = {
    contains(node) { return node === inside; },
    querySelector() { return fallback; },
    focus() { fallbackFocused += 1; },
  };

  assert.equal(restoreDialogFocusAfterRender(dialog, preferred, { activeElement: outside }), true);
  assert.equal(preferredFocused, 1);
  assert.equal(fallbackFocused, 0);
  assert.equal(restoreDialogFocusAfterRender(dialog, preferred, { activeElement: inside }), false);
  assert.equal(preferredFocused, 1);
});
