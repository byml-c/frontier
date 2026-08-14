import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/pages/BriefPage.tsx", import.meta.url), "utf8");

test("brief membership is rendered independently from later paper status changes", () => {
  assert.doesNotMatch(source, /current_status\s*!==\s*["']ignored["']/);
  assert.match(source, /item\.tier === "featured"/);
  assert.match(source, /status: item\.current_status/);
});
