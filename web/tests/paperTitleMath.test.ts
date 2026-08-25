import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { parsePaperTitleMath } from "../src/components/paperTitleMath.ts";

const titleSource = readFileSync(
  new URL("../src/components/PaperTitle.tsx", import.meta.url),
  "utf8",
);
const paperCardSource = readFileSync(
  new URL("../src/components/PaperCard.tsx", import.meta.url),
  "utf8",
);
const dailySource = readFileSync(new URL("../src/pages/DailyPage.tsx", import.meta.url), "utf8");
const librarySource = readFileSync(new URL("../src/pages/LibraryPage.tsx", import.meta.url), "utf8");
const graphSource = readFileSync(new URL("../src/pages/GraphPage.tsx", import.meta.url), "utf8");
const shellSource = readFileSync(new URL("../src/components/Shell.tsx", import.meta.url), "utf8");

test("paper titles parse explicit inline LaTeX without treating prose as Markdown", () => {
  assert.deepEqual(
    parsePaperTitleMath(String.raw`A *literal* title with $\pi_0$ and \(x_{t+1}\)`),
    [
      { kind: "text", value: "A *literal* title with " },
      { kind: "math", source: String.raw`\pi_0`, raw: String.raw`$\pi_0$` },
      { kind: "text", value: " and " },
      { kind: "math", source: String.raw`x_{t+1}`, raw: String.raw`\(x_{t+1}\)` },
    ],
  );
});

test("paper title math supports display delimiters inline and preserves malformed input", () => {
  assert.deepEqual(parsePaperTitleMath(String.raw`Method $$E=mc^2$$`), [
    { kind: "text", value: "Method " },
    { kind: "math", source: "E=mc^2", raw: "$$E=mc^2$$" },
  ]);
  assert.deepEqual(parsePaperTitleMath(String.raw`Price \$5 and unfinished $x`), [
    { kind: "text", value: String.raw`Price \$5 and unfinished $x` },
  ]);
  assert.deepEqual(parsePaperTitleMath(String.raw`Objective \[\mathcal{L}=0\]`), [
    { kind: "text", value: "Objective " },
    { kind: "math", source: String.raw`\mathcal{L}=0`, raw: String.raw`\[\mathcal{L}=0\]` },
  ]);
});

test("all visible paper-title surfaces use the safe shared KaTeX renderer", () => {
  assert.match(titleSource, /katex\.renderToString/);
  assert.match(titleSource, /trust: false/);
  assert.match(titleSource, /throwOnError: true/);
  for (const source of [paperCardSource, dailySource, librarySource, graphSource, shellSource]) {
    assert.match(source, /<PaperTitle/);
  }
  assert.doesNotMatch(dailySource, /shortPaperTitle/);
});
