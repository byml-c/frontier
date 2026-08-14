import assert from "node:assert/strict";
import test from "node:test";
import { linkifyDailyMentions, mentionDraft } from "../src/components/dailyMentions.ts";

test("links known four-character anonymous mentions but preserves code", () => {
  assert.equal(
    linkifyDailyMentions("感谢 @Ab3D，`@Ab3D` 不变；@Nope 不存在。", ["Ab3D"]),
    "感谢 [@Ab3D](#frontier-mention-Ab3D)，`@Ab3D` 不变；@Nope 不存在。",
  );
});

test("inserts a mention once into a reply draft", () => {
  assert.equal(mentionDraft("", "Ab3D"), "@Ab3D ");
  assert.equal(mentionDraft("同意", "Ab3D"), "同意 @Ab3D ");
  assert.equal(mentionDraft("@Ab3D 已提到", "Ab3D"), "@Ab3D 已提到");
});
