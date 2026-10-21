import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  INTEREST_OPTIONS,
  QUALITY_OPTIONS,
  relevanceScoreLabel,
} from "../src/components/paperEvaluation.ts";

const controlSource = readFileSync(new URL("../src/components/PaperEvaluationToggleButtons.tsx", import.meta.url), "utf8");
const metadataSource = readFileSync(new URL("../src/components/PaperMetadataDialog.tsx", import.meta.url), "utf8");
const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");

test("quality toggle exposes five discrete judgments", () => {
  assert.deepEqual(QUALITY_OPTIONS, [
    { value: "poor", label: "很差" },
    { value: "below_average", label: "较差" },
    { value: "normal", label: "一般" },
    { value: "good", label: "较好" },
    { value: "excellent", label: "优秀" },
  ]);
});

test("interest toggle exposes five levels", () => {
  assert.deepEqual(INTEREST_OPTIONS, [
    { value: "ignored", label: "不相关" },
    { value: "low", label: "较不相关" },
    { value: "neutral", label: "一般" },
    { value: "high", label: "较相关" },
    { value: "interested", label: "高度相关" },
  ]);
});

test("Agent relevance shows the five labels, including older continuous scores", () => {
  assert.deepEqual([0, .25, .5, .75, 1].map(relevanceScoreLabel), INTEREST_OPTIONS.map((option) => option.label));
  assert.equal(relevanceScoreLabel(.9688), "高度相关");
  assert.equal(relevanceScoreLabel(.683), "较相关");
  for (const missing of [undefined, null, NaN, Infinity, -1, 1.1]) {
    assert.equal(relevanceScoreLabel(missing), "待评估");
  }
});

test("daily cards hide quality while reading feedback retains it", () => {
  const cardSource = readFileSync(new URL("../src/components/PaperCard.tsx", import.meta.url), "utf8");
  assert.match(cardSource, /showQuality=\{false\}/);
  assert.match(controlSource, /showQuality = true/);
  assert.match(controlSource, /showQuality && <Grid/);
  assert.match(metadataSource, /showQuality=\{paper\.data\?\.status === "completed" \|\| paper\.data\?\.status === "feedback_pending"\}/);
});

test("all editable paper ratings use the shared compact toggle control", () => {
  assert.match(controlSource, /<ToggleButtonGroup/);
  assert.match(controlSource, /options\.map/);
  assert.match(controlSource, /value\.toFixed\(2\)/);
  assert.match(controlSource, /aria-pressed/);
  assert.match(controlSource, />Agent<\/Typography>/);
  assert.match(controlSource, />我的<\/Typography>/);
  assert.match(metadataSource, /<PaperEvaluationToggleButtons/);
  assert.match(readerSource, /<PaperEvaluationToggleButtons/);
  assert.doesNotMatch(metadataSource + readerSource + controlSource, /<Slider/);
});
