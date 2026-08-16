import assert from "node:assert/strict";
import test from "node:test";
import type { Conversation } from "../src/types.ts";
import { activeConversationTask, isRequestAbort } from "../src/components/conversationTask.ts";

function conversation(status: string, taskId: unknown = "task-1"): Conversation {
  return {
    id: "conversation-1",
    kind: "global",
    reference_id: null,
    title: "Test",
    status: "active",
    codex_session_id: null,
    created_at: "",
    updated_at: "",
    messages: [{
      id: "assistant-1",
      role: "assistant",
      content: "",
      citations_json: [],
      context_json: { stream_status: status, task_id: taskId },
      created_at: "",
    }],
  };
}

test("restores an active task from the persisted assistant draft", () => {
  assert.deepEqual(activeConversationTask(conversation("running")), {
    id: "task-1",
    status: "running",
  });
  assert.equal(activeConversationTask(conversation("cancelled")), null);
  assert.equal(activeConversationTask(conversation("running", null)), null);
});

test("prefers the server task state over a stale assistant draft", () => {
  const value = conversation("completed");
  value.active_task = { id: "task-from-server", status: "queued" };
  assert.deepEqual(activeConversationTask(value), {
    id: "task-from-server",
    status: "queued",
  });

  const terminal = conversation("running");
  terminal.active_task = null;
  assert.equal(activeConversationTask(terminal), null);
});

test("recognizes browser aborts without hiding unrelated errors", () => {
  assert.equal(isRequestAbort(new DOMException("stopped", "AbortError")), true);
  assert.equal(isRequestAbort(new Error("network failed")), false);
});
