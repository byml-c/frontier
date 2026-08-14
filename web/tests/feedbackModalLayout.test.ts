import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");
const modalSource = readFileSync(new URL("../src/components/Modal.tsx", import.meta.url), "utf8");

test("reading feedback keeps one bounded scroll region above fixed actions", () => {
  assert.match(readerSource, /data-feedback-scroll/);
  assert.match(readerSource, /overflowY: "auto"/);
  assert.match(readerSource, /scrollbarGutter: "stable"/);
  assert.match(readerSource, /flex: "1 1 0", height: 0/);
  assert.match(readerSource, /height: "100%", minHeight: 0, overflow: "hidden"/);
  assert.match(readerSource, /&::-webkit-scrollbar/);
  assert.match(readerSource, /flexShrink: 0/);
  assert.match(modalSource, /flex: "1 1 0", height: 0/);
  assert.match(modalSource, /overflow: "hidden"/);
});
