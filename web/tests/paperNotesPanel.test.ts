import assert from "node:assert/strict";
import test from "node:test";

import { noteEditorKeyboardAction } from "../src/components/noteEditorKeyboard.ts";

const keyEvent = (key: string, overrides: Partial<{ metaKey: boolean; ctrlKey: boolean; isComposing: boolean }> = {}) => ({
  key,
  metaKey: false,
  ctrlKey: false,
  isComposing: false,
  ...overrides,
});

test("note editor saves with a platform modifier and keeps plain Enter for Markdown", () => {
  assert.equal(noteEditorKeyboardAction(keyEvent("Enter")), null);
  assert.equal(noteEditorKeyboardAction(keyEvent("Enter", { ctrlKey: true })), "save");
  assert.equal(noteEditorKeyboardAction(keyEvent("Enter", { metaKey: true })), "save");
});

test("note editor cancels with Escape without interrupting IME composition", () => {
  assert.equal(noteEditorKeyboardAction(keyEvent("Escape")), "cancel");
  assert.equal(noteEditorKeyboardAction(keyEvent("Escape", { isComposing: true })), null);
  assert.equal(noteEditorKeyboardAction(keyEvent("Enter", { ctrlKey: true, isComposing: true })), null);
});
