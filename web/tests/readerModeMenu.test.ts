import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

test("reader modes share one circular dropdown instead of page-level tabs", () => {
  const menuSource = readFileSync(new URL("../src/components/ReaderModeMenu.tsx", import.meta.url), "utf8");
  const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");

  assert.match(menuSource, /value: "text", label: "文本模式"/);
  assert.match(menuSource, /value: "pdf", label: "PDF 模式"/);
  assert.match(menuSource, /value: "architecture", label: "架构模式"/);
  assert.match(menuSource, /borderRadius: "50%"/);
  assert.match(readerSource, /<ReaderModeMenu value=\{activeReaderMode\}/);
  assert.match(readerSource, /const toolbarModeControl = mobile \? undefined : modeControl/);
  assert.match(readerSource, /placeItems: "center" \}\}>\{modeControl\}<\/Box><Tabs value=\{mobileView\}/);
  assert.match(readerSource, /阅读\{!architectureMode && <KeyboardArrowDownRounded/);
  assert.doesNotMatch(readerSource, /architectureMode \? "架构" : "阅读"/);
  assert.doesNotMatch(readerSource, /<Tab component=\{Link\} to=\{`\/paper\/\$\{paperId\}/);
});
