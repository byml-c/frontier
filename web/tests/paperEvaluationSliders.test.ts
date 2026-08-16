import assert from "node:assert/strict";
import test from "node:test";
import {
  INTEREST_MARKS,
  QUALITY_MARKS,
  interestLevelToSlider,
  qualityLevelToSlider,
  sliderToInterestLevel,
  sliderToQualityLevel,
} from "../src/components/paperEvaluation.ts";

test("quality slider maps all three discrete judgments without collapsing normal into poor", () => {
  assert.deepEqual(QUALITY_MARKS.map((mark) => mark.label), ["较差", "正常", "优质"]);
  assert.deepEqual(["poor", "normal", "excellent"].map((value) => qualityLevelToSlider(value as never)), [0, 1, 2]);
  assert.deepEqual([0, 1, 2].map(sliderToQualityLevel), ["poor", "normal", "excellent"]);
});

test("interest slider uses related as the neutral state", () => {
  assert.deepEqual(INTEREST_MARKS.map((mark) => mark.label), ["忽略", "相关", "感兴趣"]);
  assert.deepEqual(["ignored", "neutral", "interested"].map((value) => interestLevelToSlider(value as never)), [0, 1, 2]);
  assert.deepEqual([0, 1, 2].map(sliderToInterestLevel), ["ignored", "neutral", "interested"]);
});
