import assert from "node:assert/strict";
import test from "node:test";
import type { Conversation } from "../src/types.ts";
import {
  agentStateFromConversation,
  agentStateFromStreamEvent,
  contextUsageDetails,
  mergeAgentRuntimeStates,
  modelDisplayName,
  normalizeAgentUsage,
  updateAgentStateFromStream,
} from "../src/components/agentRuntimeState.ts";

test("normalizes Codex token usage aliases and derives the current context size", () => {
  assert.deepEqual(normalizeAgentUsage({
    input_tokens: 12_000,
    output_tokens: 800,
    model_context_window: 200_000,
    cached_input_tokens: 9_000,
  }), {
    current_tokens: 12_800,
    context_window: 200_000,
    total_tokens: undefined,
    input_tokens: 12_000,
    output_tokens: 800,
    cached_input_tokens: 9_000,
  });
});

test("live stream state overrides persisted state and the last service check", () => {
  const state = mergeAgentRuntimeStates(
    { runtime: { model: "detected-model", reasoning_effort: "medium" } },
    { runtime: { model: "persisted-model" }, usage: { context_window: 100_000 } },
    { runtime: { model: "live-model", reasoning_effort: "high" }, usage: { current_tokens: 25_000 } },
  );
  assert.deepEqual(state, {
    runtime: { model: "live-model", reasoning_effort: "high" },
    usage: { context_window: 100_000, current_tokens: 25_000 },
  });
});

test("reads runtime and usage from SSE events without changing state for unrelated deltas", () => {
  const runtime = agentStateFromStreamEvent({ type: "runtime", model: "gpt-5", model_reasoning_effort: "high" });
  const withUsage = updateAgentStateFromStream(runtime, { type: "usage", usage: { current_tokens: 20_000, total_tokens: 25_000, context_window: 200_000 } });
  assert.deepEqual(withUsage, {
    runtime: { model: "gpt-5", reasoning_effort: "high", model_provider: undefined },
    usage: { current_tokens: 20_000, context_window: 200_000, total_tokens: 25_000, input_tokens: undefined, output_tokens: undefined, cached_input_tokens: undefined },
  });
  assert.equal(updateAgentStateFromStream(withUsage, { type: "delta", delta: "hello" }), withUsage);
});

test("uses the newest assistant message that contains persisted runtime data", () => {
  const conversation = {
    id: "conversation-1",
    kind: "global",
    status: "active",
    messages: [
      { id: "a1", role: "assistant", content: "", citations_json: [], context_json: { agent_runtime: { model: "older" } }, created_at: "" },
      { id: "u1", role: "user", content: "question", citations_json: [], context_json: {}, created_at: "" },
      { id: "a2", role: "assistant", content: "", citations_json: [], context_json: { agent_runtime: { model: "newer", reasoning_effort: "low" }, agent_usage: { current_tokens: 4_000, context_window: 100_000 } }, created_at: "" },
    ],
  } as Conversation;
  assert.equal(agentStateFromConversation(conversation).runtime?.model, "newer");
});

test("context percentage is bounded while retaining exact token counts", () => {
  assert.deepEqual(contextUsageDetails({ current_tokens: 500, context_window: 100_000 }), {
    current: 500,
    window: 100_000,
    percent: 0.5,
    percentLabel: "<1%",
  });
  assert.equal(contextUsageDetails({ current_tokens: 120_000, context_window: 100_000 })?.percent, 100);
});

test("formats Codex model ids for the compact status label", () => {
  assert.equal(modelDisplayName("gpt-5.6-sol"), "GPT-5.6-Sol");
  assert.equal(modelDisplayName("gpt-5.6-terra"), "GPT-5.6-Terra");
  assert.equal(modelDisplayName(), "模型未检测");
});
