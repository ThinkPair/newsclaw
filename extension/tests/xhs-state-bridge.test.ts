/**
 * Tests for the MAIN-world state bridge helpers.
 *
 * The bridge module's auto-install side effects (polling, event
 * listeners) only run when ``window`` is defined; node:test imports
 * the module without ``window`` so we can exercise the pure helpers
 * in isolation.
 */

import test from "node:test";
import assert from "node:assert/strict";

import {
  buildStateSnapshot,
  isVueRef,
  safeJsonClone,
} from "../src/main/xhs-state-bridge.ts";

test("isVueRef detects Vue 3 ref objects", () => {
  assert.equal(isVueRef({ __v_isRef: true, _rawValue: true }), true);
  assert.equal(isVueRef({ _rawValue: 42 }), true);
  assert.equal(isVueRef({}), false);
  assert.equal(isVueRef(null), false);
  assert.equal(isVueRef(true), false);
  assert.equal(isVueRef("string"), false);
});

test("safeJsonClone unwraps Vue 3 refs to raw values", () => {
  const ref = { __v_isRef: true, _rawValue: true, _value: true, dep: { sub: null } };
  // Reproduce a circular ref like Vue's reactivity creates.
  (ref.dep as { sub: unknown }).sub = ref;
  assert.equal(safeJsonClone(ref), true);
});

test("safeJsonClone preserves nested plain values", () => {
  const value = {
    user: {
      loggedIn: { __v_isRef: true, _rawValue: true },
      userInfo: {
        userId: "uid-1",
        nickname: "测试昵称",
      },
    },
  };
  const cloned = safeJsonClone(value);
  assert.deepEqual(cloned, {
    user: {
      loggedIn: true,
      userInfo: {
        userId: "uid-1",
        nickname: "测试昵称",
      },
    },
  });
});

test("safeJsonClone breaks reference cycles", () => {
  const a: Record<string, unknown> = { name: "A" };
  const b: Record<string, unknown> = { name: "B", parent: a };
  a.child = b;
  // Should not stack-overflow or throw.
  const cloned = safeJsonClone(a) as Record<string, unknown>;
  assert.equal(cloned.name, "A");
  const child = cloned.child as Record<string, unknown>;
  assert.equal(child.name, "B");
  // Cycle broken — parent reference is dropped (undefined fields are omitted).
  assert.equal(child.parent, undefined);
});

test("safeJsonClone drops Vue internal keys (__v_*, dep, deps, sub, subs)", () => {
  const value = {
    visible: 1,
    __v_skip: "internal",
    __v_isReactive: true,
    dep: { sub: null },
    deps: [],
    sub: null,
    subs: [],
    keep: "yes",
  };
  const cloned = safeJsonClone(value) as Record<string, unknown>;
  assert.deepEqual(Object.keys(cloned).sort(), ["keep", "visible"]);
});

test("safeJsonClone drops functions and symbols", () => {
  const value = {
    fn: () => 1,
    sym: Symbol("x"),
    keep: 42,
  };
  const cloned = safeJsonClone(value) as Record<string, unknown>;
  assert.deepEqual(cloned, { keep: 42 });
});

test("safeJsonClone tolerates getters that throw", () => {
  const value: Record<string, unknown> = { keep: 1 };
  Object.defineProperty(value, "boom", {
    enumerable: true,
    get() {
      throw new Error("getter error");
    },
  });
  const cloned = safeJsonClone(value) as Record<string, unknown>;
  assert.equal(cloned.keep, 1);
  assert.equal("boom" in cloned, false);
});

test("safeJsonClone clones arrays element by element", () => {
  const arr = [
    { __v_isRef: true, _rawValue: "a" },
    { __v_isRef: true, _rawValue: "b" },
    "c",
  ];
  assert.deepEqual(safeJsonClone(arr), ["a", "b", "c"]);
});

test("buildStateSnapshot whitelists known top-level keys", () => {
  const state = {
    user: { loggedIn: true, userInfo: { nickname: "屎屎" } },
    saved: { notes: [{ id: "n1" }] },
    likes: { notes: [{ id: "n2" }] },
    // Not whitelisted — must be dropped.
    feed: { items: ["should not survive"] },
    config: { something: "drop" },
  };
  const snapshot = buildStateSnapshot(state) as Record<string, unknown>;
  assert.deepEqual(Object.keys(snapshot).sort(), ["likes", "saved", "user"]);
});

test("buildStateSnapshot returns null for non-objects or empty whitelist hits", () => {
  assert.equal(buildStateSnapshot(null), null);
  assert.equal(buildStateSnapshot("not an object"), null);
  assert.equal(buildStateSnapshot({ feed: 1, config: 2 }), null);
});

test("buildStateSnapshot survives Vue-ref-wrapped XHS-shaped state", () => {
  // Mirror what production XHS shipped on 2026-05-05:
  //   loggedIn: ec {__v_isRef: true, _rawValue: true, _value: true}
  // plus a circular dep edge.
  const loggedInRef: Record<string, unknown> = {
    __v_isRef: true,
    _rawValue: true,
    _value: true,
    dep: { sub: null },
  };
  (loggedInRef.dep as { sub: unknown }).sub = loggedInRef;

  const state = {
    user: {
      loggedIn: loggedInRef,
      userInfo: {
        userId: "5e7c1f9f000000000100abcd",
        nickname: "littlewish",
      },
      notes: [
        [{ id: "post1" }],
        [{ id: "saved1" }, { id: "saved2" }],
        [{ id: "liked1" }],
      ],
    },
  };

  const snapshot = buildStateSnapshot(state) as {
    user: {
      loggedIn: unknown;
      userInfo: { nickname: string };
      notes: unknown[][];
    };
  };
  assert.equal(snapshot.user.loggedIn, true);
  assert.equal(snapshot.user.userInfo.nickname, "littlewish");
  assert.equal(snapshot.user.notes.length, 3);
  assert.equal((snapshot.user.notes[1][0] as { id: string }).id, "saved1");
});
