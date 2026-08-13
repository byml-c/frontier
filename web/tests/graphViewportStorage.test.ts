import assert from "node:assert/strict";
import test from "node:test";
import { mergeGraphViewportState, parseGraphViewportState } from "../src/components/graphViewportStorage.ts";

test("restores valid graph positions and rejects malformed coordinates", () => {
  const state = parseGraphViewportState(JSON.stringify({
    version: 1,
    center: { x: 120, y: -40 },
    zoom: 1.25,
    nodes: { kept: { x: 1, y: 2 }, ignored: { x: "bad", y: 3 } },
  }));
  assert.deepEqual(state, { version: 2, center: { x: 120, y: -40 }, nodes: { kept: { x: 1, y: 2 } } });
});

test("falls back safely for corrupt or incompatible graph state", () => {
  assert.deepEqual(parseGraphViewportState("not-json"), { version: 2, center: { x: 0, y: 0 }, nodes: {} });
  assert.deepEqual(parseGraphViewportState(JSON.stringify({ version: 3 })), { version: 2, center: { x: 0, y: 0 }, nodes: {} });
});

test("merges newly observed positions without dropping unloaded nodes", () => {
  const state = mergeGraphViewportState(
    { version: 2, center: { x: 0, y: 0 }, nodes: { outside: { x: 8, y: 9 } } },
    { x: 40, y: 50 },
    { visible: { x: 1, y: 2 } },
  );
  assert.deepEqual(state, { version: 2, center: { x: 40, y: 50 }, nodes: { outside: { x: 8, y: 9 }, visible: { x: 1, y: 2 } } });
});
