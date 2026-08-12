import assert from "node:assert/strict";
import test from "node:test";

import { prepareMarkdown } from "../src/components/markdownNormalization.ts";

test("repairs model-generated strong delimiters with inner whitespace", () => {
  const result = prepareMarkdown(
    "还接收本体感觉状态和 ** 32 个可学习的 future tokens** 加粗又失效了",
  );

  assert.match(result, /\*\*32 个可学习的 future tokens\*\*/);
  assert.doesNotMatch(result, /\*\*\s+32/);
});

test("repairs CJK text after strong punctuation without changing code", () => {
  assert.equal(prepareMarkdown("**重点。**下一句"), "**重点。** 下一句");
  assert.equal(prepareMarkdown("`** 不应加粗**`"), "`** 不应加粗**`");
});

test("renders separate page references as independent links", () => {
  assert.equal(
    prepareMarkdown("证据见 [[page:2, 12]]。"),
    "证据见 [第 2 页](#frontier-page-2)、[第 12 页](#frontier-page-12)。",
  );
});
