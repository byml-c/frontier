import assert from "node:assert/strict";
import test from "node:test";

import { prepareMarkdown } from "../src/components/markdownNormalization.ts";

test("repairs model-generated strong delimiters with inner whitespace", () => {
  const result = prepareMarkdown(
    "还接收本体感觉状态和 ** 32 个可学习的 future tokens** 加粗又失效了",
  );

  assert.match(result, /\*\*32 个可学习的 future tokens\*\*/);
  assert.doesNotMatch(result, /\*\*\s+32/);
  assert.equal(prepareMarkdown("** 核心贡献**"), "**核心贡献**");
  assert.equal(
    prepareMarkdown("** 核心贡献**与** 实验结果 **都很重要"),
    "**核心贡献** 与**实验结果** 都很重要",
  );
});

test("repairs CJK text after strong punctuation without changing code", () => {
  assert.equal(prepareMarkdown("**重点。**下一句"), "**重点。** 下一句");
  assert.equal(prepareMarkdown("`** 不应加粗**`"), "`** 不应加粗**`");
});

test("an unmatched marker does not consume later model-generated strong text", () => {
  assert.equal(
    prepareMarkdown("**未闭合\n下一段 ** 核心贡献**：有效"),
    "**未闭合\n下一段 **核心贡献**：有效",
  );
  assert.equal(
    prepareMarkdown("`inline ** 核心贡献**`\n```md\n** 核心贡献**\n```"),
    "`inline ** 核心贡献**`\n```md\n** 核心贡献**\n```",
  );
});

test("renders separate page references as independent links", () => {
  assert.equal(
    prepareMarkdown("证据见 [[page:2, 12]]。"),
    "证据见 [第 2 页](#frontier-page-2)、[第 12 页](#frontier-page-12)。",
  );
});
