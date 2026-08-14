import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { architectureCardSize } from "../src/features/architecture/architectureCardSize.ts";
import { organizeModuleLayout } from "../src/features/architecture/architectureLayout.ts";
import type { ArchitectureNode } from "../src/features/architecture/types.ts";

function moduleNode(title: string, extra: Partial<ArchitectureNode> = {}): ArchitectureNode {
  return {
    id: "module",
    type: "module",
    title,
    position: { x: 0, y: 0 },
    ...extra,
  } as ArchitectureNode;
}

test("architecture cards grow from content but stay within a readable width", () => {
  const short = architectureCardSize(moduleNode("Encoder"));
  const long = architectureCardSize(moduleNode("Observation–Action Joint Attention With A Deliberately Long Functional Name"));
  const detailed = architectureCardSize(moduleNode("Encoder", { architecture: "DINOv2-B/14", keywords: ["shared weights", "fine-tuned", "high-resolution observation tokens"] }));

  assert.ok(short.width < long.width);
  assert.ok(long.width <= 320);
  assert.ok(long.height > short.height);
  assert.ok(detailed.height > short.height);
});

test("stored canvas size does not force an empty collapsed card to stay large", () => {
  const size = architectureCardSize(moduleNode("Encoder", { size: { width: 1200, height: 800 } }));

  assert.ok(size.width <= 320);
  assert.ok(size.height < 100);
});

test("expanded child edges render above the parent container", () => {
  const source = readFileSync(new URL("../src/features/architecture/ArchitectureReader.tsx", import.meta.url), "utf8");

  assert.match(source, /zIndex: expandedFlow \? 4 : 0/);
  assert.match(source, /fill=\{Boolean\(data\.expanded && data\.expandable\)\}/);
});

test("organize module layout follows dataflow and leaves room for tensor labels", () => {
  const parent = moduleNode("Backbone");
  const input = moduleNode("Input projection", { id: "input", position: { x: 500, y: 100 } });
  const attention = moduleNode("Joint attention", { id: "attention", position: { x: 0, y: 300 } });
  const output = moduleNode("Action stream", { id: "output", position: { x: 100, y: 20 } });
  const layout = organizeModuleLayout(parent, [output, attention, input], [
    { id: "first", source: "input", target: "attention" },
    { id: "second", source: "attention", target: "output" },
  ]);

  assert.ok(layout.positions.input.x < layout.positions.attention.x);
  assert.ok(layout.positions.attention.x < layout.positions.output.x);
  assert.ok(layout.positions.attention.x - layout.positions.input.x >= architectureCardSize(input).width + 180);
  assert.ok(layout.size.width > layout.positions.output.x + architectureCardSize(output).width);
  assert.ok(layout.size.height > layout.positions.input.y + architectureCardSize(input).height);
});
