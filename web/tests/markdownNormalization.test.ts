import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkCjkFriendly from "remark-cjk-friendly/parseOnly";
import remarkCjkFriendlyGfmStrikethrough from "remark-cjk-friendly-gfm-strikethrough/parseOnly";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { prepareMarkdown } from "../src/components/markdownNormalization.ts";

function renderMarkdown(content: string): string {
  return renderToStaticMarkup(createElement(ReactMarkdown, {
    remarkPlugins: [remarkGfm, remarkCjkFriendly, remarkCjkFriendlyGfmStrikethrough, remarkMath],
    rehypePlugins: [rehypeKatex],
    children: content,
  }));
}

test("repairs model-generated strong delimiters with inner whitespace", () => {
  const result = prepareMarkdown(
    "还接收本体感觉状态和 ** 32 个可学习的 future tokens** 加粗又失效了",
  );

  assert.match(result, /\*\*32 个可学习的 future tokens\*\*/);
  assert.doesNotMatch(result, /\*\*\s+32/);
  assert.equal(prepareMarkdown("** 核心贡献**"), "**核心贡献**");
  assert.equal(
    prepareMarkdown("** 核心贡献**与** 实验结果 **都很重要"),
    "**核心贡献**与**实验结果**都很重要",
  );
});

test("pairs adjacent strong delimiters from left to right regardless of inner whitespace", () => {
  const result = prepareMarkdown("** x**其他文字** y**");
  const punctuated = prepareMarkdown("** x**，其他文字** y**");

  assert.equal(result, "**x**其他文字**y**");
  assert.equal(
    renderMarkdown(result),
    "<p><strong>x</strong>其他文字<strong>y</strong></p>",
  );
  assert.equal(punctuated, "**x**，其他文字**y**");
  assert.equal(
    renderMarkdown(punctuated),
    "<p><strong>x</strong>，其他文字<strong>y</strong></p>",
  );
});

test("does not treat escaped or triple asterisks as standalone strong delimiters", () => {
  assert.equal(prepareMarkdown(String.raw`\** x** 与 ** y**`), String.raw`\** x** 与 ** y**`);
  assert.equal(prepareMarkdown("*** x*** 与 ** y**"), "*** x*** 与 **y**");
});

test("does not normalize strong-looking delimiters inside code or math", () => {
  assert.equal(prepareMarkdown("`a ** b + c ** d`"), "`a ** b + c ** d`");
  assert.equal(prepareMarkdown("$a ** b + c ** d$"), "$a ** b + c ** d$");
  assert.equal(prepareMarkdown(String.raw`\(a ** b + c ** d\)`), "$a ** b + c ** d$");
});

test("parses CJK text after strong punctuation without changing visible content", () => {
  const result = prepareMarkdown("**重点。**下一句");

  assert.equal(result, "**重点。**下一句");
  assert.equal(renderMarkdown(result), "<p><strong>重点。</strong>下一句</p>");
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

test("preserves list indentation when converting display math delimiters", () => {
  const source = String.raw`- 公式：
  \[
  x_t = q_t + 1
  \]
- 其中 \(q_t\) 是关节位置`;
  const prepared = prepareMarkdown(source);
  const rendered = renderMarkdown(prepared);

  assert.equal(
    prepared,
    "- 公式：\n  $$\n  x_t = q_t + 1\n  $$\n- 其中 $q_t$ 是关节位置",
  );
  assert.doesNotMatch(rendered, /katex-error/);
  assert.match(rendered, /katex-display/);
  assert.match(rendered, /其中/);
  assert.equal(rendered.match(/<li>/g)?.length, 2);
});

test("preserves nested list and blockquote prefixes around display math", () => {
  const source = String.raw`1. 外层
   - 内层公式
     \[
     z_t = f(q_t)
     \]
> \[
> y_t = g(z_t)
> \]`;
  const prepared = prepareMarkdown(source);

  assert.match(prepared, / {5}\$\$\n {5}z_t = f\(q_t\)\n {5}\$\$/);
  assert.match(prepared, /> \$\$\n> y_t = g\(z_t\)\n> \$\$/);
  const rendered = renderMarkdown(prepared);
  assert.doesNotMatch(rendered, /katex-error/);
  assert.match(rendered, /<blockquote>/);
  assert.equal(rendered.match(/<li>/g)?.length, 2);
});

test("does not convert latex delimiters inside code or before they are closed", () => {
  const fenced = String.raw`~~~tex
\[
x_t
\]
~~~`;
  const inline = "`\\(q_t\\)`";
  const incompleteBlock = String.raw`- 公式：
  \[
  x_t`;

  assert.equal(prepareMarkdown(fenced), fenced);
  assert.equal(prepareMarkdown(inline), inline);
  assert.equal(prepareMarkdown(incompleteBlock), incompleteBlock);
  assert.equal(prepareMarkdown(String.raw`其中 \(q_t`), String.raw`其中 \(q_t`);
});

test("protects arbitrary-length markdown code markers and escaped literals", () => {
  const fenced = "````md\ninside \\(x_t\\) and an inner ``` marker\n````";
  const inline = "before ``\\(q_t\\)`` after \\(z_t\\)";
  const escaped = String.raw`literal \\(q_t\\), math \(z_t\)`;

  assert.equal(prepareMarkdown(fenced), fenced);
  assert.equal(prepareMarkdown(inline), "before ``\\(q_t\\)`` after $z_t$");
  assert.equal(prepareMarkdown(escaped), String.raw`literal \\(q_t\\), math $z_t$`);
});

test("converts a single-line display formula without breaking its list", () => {
  const source = String.raw`- \[x_t = q_t + 1\]
- 其中 \(q_t\) 是关节位置`;
  const prepared = prepareMarkdown(source);
  const rendered = renderMarkdown(prepared);

  assert.equal(
    prepared,
    "- $$\n  x_t = q_t + 1\n  $$\n- 其中 $q_t$ 是关节位置",
  );
  assert.doesNotMatch(rendered, /katex-error/);
  assert.equal(rendered.match(/<li>/g)?.length, 2);
  assert.equal(prepareMarkdown(prepared), prepared);
});
