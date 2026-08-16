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

test("quality toggle exposes all three discrete judgments", () => {
  assert.deepEqual(QUALITY_OPTIONS, [
    { value: "poor", label: "较差" },
    { value: "normal", label: "正常" },
    { value: "excellent", label: "优质" },
  ]);
});

test("interest toggle uses related as the neutral state", () => {
  assert.deepEqual(INTEREST_OPTIONS, [
    { value: "ignored", label: "忽略" },
    { value: "neutral", label: "相关" },
    { value: "interested", label: "感兴趣" },
  ]);
});

test("all editable paper ratings use the shared compact toggle control", () => {
  assert.match(controlSource, /<ToggleButtonGroup/);
  assert.match(controlSource, /options\.map/);
  assert.match(controlSource, /value\.toFixed\(2\)/);
  assert.match(controlSource, />Agent<\/Typography>/);
  assert.match(controlSource, />我的<\/Typography>/);
  assert.match(metadataSource, /<PaperEvaluationToggleButtons/);
  assert.match(readerSource, /<PaperEvaluationToggleButtons/);
  assert.doesNotMatch(metadataSource + readerSource + controlSource, /<Slider/);
});
