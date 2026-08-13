import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../src/components/ConversationScroller.tsx", import.meta.url), "utf8");
const helperStart = source.indexOf("export function isNearConversationBottom");
const helperEnd = source.indexOf("export function ConversationScroller");
const compiled = ts.transpileModule(source.slice(helperStart, helperEnd), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const helpers = await import(`data:text/javascript,${encodeURIComponent(compiled)}`) as {
  isNearConversationBottom: (metrics: { scrollHeight: number; scrollTop: number; clientHeight: number }, threshold?: number) => boolean;
  shouldPositionForNewMessage: (previousChildCount: number | null, nextChildCount: number, wasAtBottom: boolean) => boolean;
};
const { isNearConversationBottom, shouldPositionForNewMessage } = helpers;

test("bottom proximity uses the remaining scroll distance", () => {
  assert.equal(isNearConversationBottom({ scrollHeight: 1000, scrollTop: 430, clientHeight: 500 }), true);
  assert.equal(isNearConversationBottom({ scrollHeight: 1000, scrollTop: 400, clientHeight: 500 }), false);
});

test("initial content and a newly appended message may position once", () => {
  assert.equal(shouldPositionForNewMessage(null, 4, false), true);
  assert.equal(shouldPositionForNewMessage(4, 5, true), true);
});

test("streamed changes inside an existing message never trigger positioning", () => {
  assert.equal(shouldPositionForNewMessage(5, 5, true), false);
  assert.equal(shouldPositionForNewMessage(5, 6, false), false);
});
