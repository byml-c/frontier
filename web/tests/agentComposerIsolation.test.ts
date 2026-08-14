import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const composerSource = readFileSync(new URL("../src/components/AgentComposer.tsx", import.meta.url), "utf8");
const panelSource = readFileSync(new URL("../src/components/AgentPanel.tsx", import.meta.url), "utf8");
const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");
const markdownSource = readFileSync(new URL("../src/components/MarkdownMessage.tsx", import.meta.url), "utf8");
const runtimeSource = readFileSync(new URL("../src/components/AgentRuntimeStatus.tsx", import.meta.url), "utf8");

test("chat draft state is isolated inside AgentComposer", () => {
  assert.match(composerSource, /const \[value, setValue\] = useState\(""\)/);
  assert.match(composerSource, /export type AgentComposerHandle/);
  assert.match(composerSource, /getValue: \(\) => valueRef\.current/);
  assert.match(composerSource, /clear: \(\) => updateValue\(""\)/);
  assert.doesNotMatch(panelSource, /const \[text, setText\] = useState/);
  assert.doesNotMatch(readerSource, /const \[text, setText\] = useState/);
});

test("conversation parents use the isolated composer instead of subscribing to draft changes", () => {
  assert.match(panelSource, /composerRef\.current\?\.getValue\(\)/);
  assert.match(readerSource, /composerRef\.current\?\.getValue\(\)/);
  assert.doesNotMatch(panelSource, /<AgentComposer[^>]*\bonChange=/s);
  assert.doesNotMatch(readerSource, /<AgentComposer[^>]*\bonChange=/s);
  assert.match(panelSource, /const stopAgent = useCallback/);
  assert.match(readerSource, /const stopAgent = useCallback/);
});

test("expensive Markdown rendering has a memo boundary", () => {
  assert.match(markdownSource, /export const MarkdownMessage = memo\(function MarkdownMessage/);
  assert.match(runtimeSource, /export const AgentRuntimeStatus = memo\(function AgentRuntimeStatus/);
});
