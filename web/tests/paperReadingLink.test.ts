import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { paperReadingHref, readingSourceBriefItem } from "../src/components/paperReadingLink.ts";
import { paperStatusLabel } from "../src/api.ts";

test("reading destination preserves brief provenance for a copied or middle-clicked link", () => {
  const href = paperReadingHref("paper-1", "brief-item-2");
  const target = new URL(href, "https://frontier.example");
  assert.equal(target.pathname, "/paper/paper-1");
  assert.equal(readingSourceBriefItem(target.search), "brief-item-2");
  assert.equal(paperReadingHref("paper-1"), "/paper/paper-1");
  assert.equal(readingSourceBriefItem("?mode=text"), undefined);
});

test("reading entry is a native new-tab link without a click-time mutation", () => {
  const source = readFileSync(new URL("../src/components/PaperCard.tsx", import.meta.url), "utf8");
  assert.match(source, /<Link href=\{paperReadingHref\(paper.id, briefItemId\)\} target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(source, /startReading\.mutate|开始精读|继续精读/);
  assert.equal(paperStatusLabel("reading"), "读过");
});
