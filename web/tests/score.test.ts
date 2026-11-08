import assert from "node:assert/strict";
import test from "node:test";
import { USER_SCORE_OPTIONS, userScoreLabel, userScoreLevel } from "../src/components/score.ts";

test("five user rating levels store distinct scalar scores", () => {
  assert.deepEqual(USER_SCORE_OPTIONS.map(({ value }) => value), [0, 0.25, 0.5, 0.75, 1]);
  assert.equal(new Set(USER_SCORE_OPTIONS.map(({ label }) => label)).size, 5);
  for (const option of USER_SCORE_OPTIONS) {
    assert.equal(userScoreLevel(option.value), option.value);
    assert.equal(userScoreLabel(option.value), option.label);
  }
});

test("zero is a rated level, while absent and invalid values remain unselected", () => {
  assert.equal(userScoreLevel(0), 0);
  assert.equal(userScoreLabel(0), "很低");
  for (const score of [null, undefined, NaN, Infinity, -0.01, 1.01]) {
    assert.equal(userScoreLevel(score), null);
    assert.equal(userScoreLabel(score), "未评分");
  }
});

test("existing continuous scores have a display level without rewriting the score", () => {
  const score = 0.71342;
  assert.equal(userScoreLevel(score), 0.75);
  assert.equal(score, 0.71342);
  assert.equal(userScoreLevel(0.1249), 0);
  assert.equal(userScoreLevel(0.125), 0.25);
  assert.equal(userScoreLevel(0.8749), 0.75);
  assert.equal(userScoreLevel(0.875), 1);
});
