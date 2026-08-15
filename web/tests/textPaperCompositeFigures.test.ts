import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/components/TextPaperReader.tsx", import.meta.url), "utf8");

test("text reader preserves responsive multi-panel arXiv figures", () => {
  assert.match(source, /"& \.ltx_flex_figure"/);
  assert.match(source, /"& \.ltx_flex_cell"/);
  assert.match(source, /\.\.\.arxivVisualSx/);
});

test("text reader permits sanitized inline SVG figure structure", () => {
  assert.match(source, /USE_PROFILES: \{ html: true, svg: true, svgFilters: true \}/);
  assert.match(source, /ADD_TAGS: \["foreignObject"\]/);
  assert.match(source, /"& svg\.ltx_picture"/);
});
