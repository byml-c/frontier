import assert from "node:assert/strict";
import test from "node:test";
import { canCreateExactName, normalizeExactName } from "../src/components/exactName.ts";

test("partial tag matches never suppress an explicitly different new tag", () => {
  const tags = [{ id: "tag-1", name: "视觉语言动作模型" }];
  assert.equal(canCreateExactName("视觉语言", tags.map((tag) => tag.name), []), true);
  assert.equal(canCreateExactName(" 视觉语言动作模型 ", tags.map((tag) => tag.name), []), false);
  assert.equal(canCreateExactName("VISION", ["Vision"], []), false);
  assert.equal(normalizeExactName("  VLA "), "vla");
});

test("node creation is blocked only by an exact normalized name", () => {
  const nodes = ["机器人学习"];
  assert.equal(canCreateExactName("机器人学习策略", nodes, []), true);
  assert.equal(canCreateExactName(" 机器人学习 ", nodes, []), false);
  assert.equal(canCreateExactName("ROBOT", ["Robot"], []), false);
});
