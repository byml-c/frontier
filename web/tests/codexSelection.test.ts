import assert from "node:assert/strict";
import test from "node:test";
import type { CodexModelCatalog } from "../src/types.ts";
import { normalizeCodexSelection } from "../src/components/codexSelection.ts";

const catalog: CodexModelCatalog = {
  default_backend: "codex",
  backends: [
    { name: "codex", available: true, default_model: "shared", models: [
      { id: "shared", name: "OpenAI", reasoning_efforts: ["low", "high", "ultra"], default_reasoning_effort: "high" },
    ] },
    { name: "deepseek", available: true, models: [
      { id: "shared", name: "DeepSeek", reasoning_efforts: ["low", "high"], default_reasoning_effort: "low" },
    ] },
  ],
};

test("chooses configured default and preserves supported selection", () => {
  assert.deepEqual(normalizeCodexSelection(catalog), { backend: "codex", model: "shared", reasoning_effort: "high" });
  assert.deepEqual(normalizeCodexSelection(catalog, { backend: "codex", model: "shared", reasoning_effort: "ultra" }), { backend: "codex", model: "shared", reasoning_effort: "ultra" });
});

test("same model identifier routes by backend and resets unsupported effort", () => {
  assert.deepEqual(normalizeCodexSelection(catalog, { backend: "deepseek", model: "shared", reasoning_effort: "ultra" }), { backend: "deepseek", model: "shared", reasoning_effort: "low" });
});

test("removed backend or model falls back to available configuration", () => {
  assert.equal(normalizeCodexSelection(catalog, { backend: "removed", model: "gone" })?.backend, "codex");
  assert.equal(normalizeCodexSelection({ ...catalog, backends: [] }), undefined);
  assert.equal(normalizeCodexSelection(undefined), undefined);
});
