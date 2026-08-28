import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  escapeHtmlAttribute,
  getRecommendationCardKind,
  getRecommendationTagFingerprint,
  normalizeRecommendation,
  normalizeRecommendationDisplayTags,
} from "../../src/openbiliclaw/web/js/view-models.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFile(resolve(root, path), "utf8");

test("mobile recommendation normalizes the canonical display-tag contract", () => {
  const item = normalizeRecommendation({
    source_platform: "news",
    content_type: "article",
    topic_group: "宏观政策",
    franchise_key: "数字人民币",
    style_key: "deep_focus",
    content_scope: "full_text",
    freshness: "fresh",
    temporal_class: "breaking",
    temporal_state: "active",
    display_tags: [
      { type: "topic", label: " 宏观政策 ", origin: "model" },
      { type: "story", label: "数字人民币", origin: "model" },
      { type: "temporal", label: "新进展", origin: "rule" },
      { type: "evidence", label: "全文级", origin: "article" },
    ],
  });

  assert.deepEqual(item.display_tags, [
    { type: "topic", label: "宏观政策", origin: "model" },
    { type: "story", label: "数字人民币", origin: "model" },
    { type: "temporal", label: "新进展", origin: "rule" },
    { type: "evidence", label: "全文级", origin: "article" },
  ]);
  assert.equal(item.topic_group, "宏观政策");
  assert.equal(item.franchise_key, "数字人民币");
  assert.equal(item.style_key, "deep_focus");
  assert.equal(item.temporal_class, "breaking");
  assert.equal(item.temporal_state, "active");

  const fingerprint = getRecommendationTagFingerprint(item);
  assert.deepEqual(fingerprint.tags.map((tag) => tag.label), [
    "宏观政策",
    "数字人民币",
    "新进展",
  ]);
  assert.equal(fingerprint.overflowCount, 1);
  assert.equal(fingerprint.temporalLabel, "新进展");
  assert.equal(fingerprint.evidenceLabel, "全文级");
  assert.match(fingerprint.ariaLabel, /宏观政策/);
  assert.match(fingerprint.ariaLabel, /全文级/);
});

test("legacy no-cover recommendations fall back to structured fields, never title or body", () => {
  const legacy = normalizeRecommendation({
    source_platform: "news",
    content_type: "article",
    title: "这段标题不能再出现两遍",
    body_text: "正文也不能伪装成图片",
    cover_url: "",
    topic_group: "金融政策",
    franchise_key: "数字人民币",
    content_scope: "summary",
    freshness: "aging",
    temporal_class: "current",
  });

  assert.deepEqual(getRecommendationCardKind(legacy), {
    kind: "fingerprint",
    coverUrl: "",
    text: "",
  });
  const fingerprint = getRecommendationTagFingerprint(legacy);
  const serialized = JSON.stringify(fingerprint);
  assert.deepEqual(fingerprint.tags.map((tag) => tag.label), [
    "金融政策",
    "数字人民币",
    "近期",
  ]);
  assert.equal(fingerprint.overflowCount, 1);
  assert.equal(fingerprint.evidenceLabel, "摘要级");
  assert.doesNotMatch(serialized, /这段标题不能再出现两遍/);
  assert.doesNotMatch(serialized, /正文也不能伪装成图片/);
});

test("a valid publisher cover always wins over the fingerprint media slot", () => {
  const article = normalizeRecommendation({
    source_platform: "news",
    content_type: "article",
    cover_url: "http://example.com/news-cover.jpg",
    display_tags: [{ type: "topic", label: "科技", origin: "model" }],
  });
  assert.deepEqual(getRecommendationCardKind(article), {
    kind: "cover",
    coverUrl: "https://example.com/news-cover.jpg",
    text: "",
  });
});

test("non-news no-cover content keeps its established text-card behavior", () => {
  const reddit = normalizeRecommendation({
    source_platform: "reddit",
    content_type: "post",
    title: "Local-first agents",
    body_text: "A practical write-up.",
    cover_url: "",
  });
  assert.deepEqual(getRecommendationCardKind(reddit), {
    kind: "text",
    coverUrl: "",
    text: "A practical write-up.",
  });
});

test("attribute escaping blocks quote-based tag injection", () => {
  assert.equal(
    escapeHtmlAttribute('宏观" onmouseover="globalThis.pwned=1'),
    "宏观&quot; onmouseover=&quot;globalThis.pwned=1",
  );
  assert.equal(escapeHtmlAttribute("<&'\">"), "&lt;&amp;&#39;&quot;&gt;");
});

test("display tags accept bounded legacy aliases and remove duplicates", () => {
  const tags = normalizeRecommendationDisplayTags([
    "人工智能",
    { kind: "franchise", label: "AI Agent", origin: "legacy-cache" },
    { key: "scope", label: "摘要级", origin: "article" },
    { type: "topic", label: "人工智能", origin: "duplicate" },
    null,
  ]);
  assert.deepEqual(tags, [
    { type: "topic", label: "人工智能", origin: "legacy" },
    { type: "story", label: "AI Agent", origin: "legacy-cache" },
    { type: "evidence", label: "摘要级", origin: "article" },
  ]);
});

test("mobile card template renders readable tags, status, evidence, and overflow", async () => {
  const [recommend, css] = await Promise.all([
    read("src/openbiliclaw/web/js/views/recommend.js"),
    read("src/openbiliclaw/web/css/app.css"),
  ]);
  assert.match(recommend, /card-tag-fingerprint/);
  assert.match(recommend, /状态：\$\{esc\(fingerprint\.temporalLabel\)\}/);
  assert.match(recommend, /证据：\$\{esc\(fingerprint\.evidenceLabel\)\}/);
  assert.match(recommend, /card-info-tag-overflow/);
  assert.match(recommend, /另有 \$\{fingerprint\.overflowCount\} 个标签/);
  assert.match(recommend, /escapeHtmlAttribute\(fingerprint\.ariaLabel\)/);
  assert.match(recommend, /cardMedia\.kind === "cover"/);
  assert.match(recommend, /cardMedia\.kind === "text"/);
  assert.match(recommend, /card-cover-text/);
  assert.match(css, /\.card-cover-frame\.is-tag-card/);
  assert.match(css, /\.card-cover-frame\.is-text-card\s*\{[\s\S]*?max-height:\s*116px;/);
  assert.match(css, /\.card-cover-text\s*\{[\s\S]*?-webkit-line-clamp:\s*4;/);
  assert.match(css, /\.card-info-tag\[data-tag-type="topic"\]/);
  assert.match(css, /min-height:\s*26px/);
  assert.match(css, /\.card-meta\s*\{[\s\S]*?flex-wrap:\s*wrap;/);
});
