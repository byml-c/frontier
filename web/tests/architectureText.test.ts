import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { normalizeArchitectureInlineMath, normalizeArchitectureMathSource, parseArchitectureList } from "../src/features/architecture/architectureText.ts";

test("architecture symbols accept canonical TeX and common Markdown delimiters", () => {
  assert.equal(normalizeArchitectureMathSource(String.raw`g_\psi`), String.raw`g_\psi`);
  assert.equal(normalizeArchitectureMathSource(String.raw`$g_\psi$`), String.raw`g_\psi`);
  assert.equal(normalizeArchitectureMathSource(String.raw`\(A_t^*\)`), String.raw`A_t^*`);
  assert.equal(normalizeArchitectureMathSource("x_stage"), String.raw`x_{\rm stage}`);
  assert.equal(normalizeArchitectureMathSource("x_{stage}"), String.raw`x_{\rm stage}`);
  assert.equal(normalizeArchitectureMathSource("z_32"), String.raw`z_{32}`);
});

test("architecture list separators preserve commas inside formulas", () => {
  assert.deepEqual(parseArchitectureList("target $V_t=(x,y)$ · frozen", "module"), ["target $V_t=(x,y)$", "frozen"]);
  assert.deepEqual(parseArchitectureList("resize → normalize", "data"), ["resize", "normalize"]);
});

test("legacy architecture prose renders TeX-like identifiers without double wrapping", () => {
  assert.equal(normalizeArchitectureInlineMath("g_ψ · shared weights"), "$g_ψ$ · shared weights");
  assert.equal(normalizeArchitectureInlineMath("target $V_t^*$ · L_IDM"), String.raw`target $V_t^*$ · $L_{\rm IDM}$`);
  assert.equal(normalizeArchitectureInlineMath("stage $x_stage$"), String.raw`stage $x_{\rm stage}$`);
  assert.equal(normalizeArchitectureInlineMath("DINOv2-B/14 · fine-tuned"), "DINOv2-B/14 · fine-tuned");
});

test("architecture editor edits the visual card instead of a detached form", () => {
  const source = readFileSync(new URL("../src/features/architecture/ArchitectureWorkspace.tsx", import.meta.url), "utf8");
  assert.match(source, /<ArchitectureNodeCardEditor/);
  assert.match(source, /void beginEdit\(node\)/);
  assert.match(source, /annotation=\{annotations\.map/);
  assert.match(source, /添加到对话/);
  assert.match(source, /window\.setTimeout\(\(\) => \{ void persistDraftRef\.current\(draft\)/);
  assert.doesNotMatch(source, />保存<\/Button>/);
  assert.doesNotMatch(source, /<TextField label="名称"/);
  assert.doesNotMatch(source, /<Card key=\{item\.id\}/);
});

test("architecture card uses the restrained section style and name-first heading", () => {
  const source = readFileSync(new URL("../src/features/architecture/ArchitectureNodeCard.tsx", import.meta.url), "utf8");

  assert.match(source, /标题 · 名称 \+ 符号（TeX）/);
  assert.match(source, /bgcolor: "rgba\(0,111,230,\.06\)"/);
  assert.doesNotMatch(source, /borderLeft/);
});

test("architecture card references travel with the paper conversation context", () => {
  const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");

  assert.match(readerSource, /architectureCards\.length \? \{ cards: architectureCards \}/);
  assert.match(readerSource, /onClearArchitectureCards\(\)/);
  assert.match(readerSource, /architectureCardsFromContext/);
});
