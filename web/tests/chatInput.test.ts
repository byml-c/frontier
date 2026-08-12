import assert from "node:assert/strict";
import test from "node:test";

import { shouldSubmitChatOnEnter } from "../src/components/chatInput.ts";

test("Enter submits while Shift+Enter keeps a newline", () => {
  assert.equal(
    shouldSubmitChatOnEnter({ key: "Enter", shiftKey: false, keyCode: 13, isComposing: false }),
    true,
  );
  assert.equal(
    shouldSubmitChatOnEnter({ key: "Enter", shiftKey: true, keyCode: 13, isComposing: false }),
    false,
  );
});

test("IME confirmation never submits the chat", () => {
  assert.equal(
    shouldSubmitChatOnEnter({ key: "Enter", shiftKey: false, keyCode: 13, isComposing: true }),
    false,
  );
  assert.equal(
    shouldSubmitChatOnEnter({ key: "Enter", shiftKey: false, keyCode: 229, isComposing: false }),
    false,
  );
});
