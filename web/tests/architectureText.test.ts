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
  assert.match(source, /onSave=\{\(\) => draft && void saveDraft\(draft\)\}/);
  assert.match(source, /放弃未保存的修改？/);
  assert.match(source, /有未保存修改/);
  assert.doesNotMatch(source, /persistDraftRef/);
  assert.doesNotMatch(source, /<TextField label="名称"/);
  assert.doesNotMatch(source, /<Card key=\{item\.id\}/);
});

test("every architecture node exposes an expand or Agent-expansion control", () => {
  const source = readFileSync(new URL("../src/features/architecture/ArchitectureReader.tsx", import.meta.url), "utf8");

  assert.match(source, /data\.expandable \? \(data\.expanded \? "收起模块" : "展开模块"\) : "请求 Agent 展开"/);
  assert.match(source, /data\.onRequestExpand\?\.\(entity\)/);
  assert.match(source, /aria-label="请求 Agent 展开节点"/);
});

test("architecture canvas explains solid and dashed edge semantics", () => {
  const source = readFileSync(new URL("../src/features/architecture/ArchitectureReader.tsx", import.meta.url), "utf8");

  assert.match(source, /aria-label="架构连线图例"/);
  assert.match(source, />主数据流</);
  assert.match(source, />条件 \/ 辅助输入</);
});

test("architecture card uses the restrained section style and name-first heading", () => {
  const source = readFileSync(new URL("../src/features/architecture/ArchitectureNodeCard.tsx", import.meta.url), "utf8");

  assert.match(source, /标题 · 名称 \+ 符号（TeX）/);
  assert.match(source, /bgcolor: "rgba\(0,111,230,\.06\)"/);
  assert.doesNotMatch(source, /borderLeft/);
  assert.match(source, /className=\{interactiveTitle \? "architecture-node-title"/);
  assert.match(source, /cursor: interactiveTitle \? "default"/);
  assert.match(source, /cursor: interactiveTitle \? "grab"/);
});

test("architecture card references travel with the paper conversation context", () => {
  const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");

  assert.match(readerSource, /const cards = options\?\.cards \?\? architectureCards/);
  assert.match(readerSource, /cards\.length \? \{ cards \}/);
  assert.match(readerSource, /setLiveRequestContext\(contextJson\)/);
  assert.match(readerSource, /onClearArchitectureCards\(\)/);
  assert.match(readerSource, /architectureCardsFromContext/);
});

test("requesting architecture expansion sends immediately without consuming the draft", () => {
  const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");

  assert.match(readerSource, /setArchitectureComposerRequest\(\{ id: Date\.now\(\), text: `请展开架构节点/);
  assert.match(readerSource, /sendContentRef\.current\?\.\(composerRequest\.text, \{ cards: \[composerRequest\.card\], excerpts: \[\], clearComposer: false, clearPendingReferences: false \}\)/);
  assert.match(readerSource, /handledComposerRequestId\.current === composerRequest\.id/);
  assert.doesNotMatch(readerSource, /composerRef\.current\?\.setValue\(current \? `\$\{current\}\\n\$\{composerRequest\.text\}`/);
});
