import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  INTEREST_OPTIONS,
  QUALITY_OPTIONS,
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
