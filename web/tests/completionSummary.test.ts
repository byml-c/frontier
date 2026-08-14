import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { completionSummaryDraft, completionSummaryDraftIsComplete, completionSummarySections } from "../src/components/completionSummary.ts";

const dailySource = readFileSync(new URL("../src/pages/DailyPage.tsx", import.meta.url), "utf8");
const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");
const metadataSource = readFileSync(new URL("../src/components/PaperMetadataDialog.tsx", import.meta.url), "utf8");

const summary = {
  research_question: "研究问题",
  evidence_and_contribution: "证据与贡献",
  experiments: "实验",
  limitations: "局限",
  user_attitude: "用户很感兴趣",
};

test("private reading summary keeps all structured sections in order", () => {
  assert.deepEqual(
    completionSummarySections(summary, "private").map(({ key }) => key),
    ["research_question", "evidence_and_contribution", "experiments", "limitations", "user_attitude"],
  );
});

test("public reading summary cannot expose user attitude", () => {
  const sections = completionSummarySections(summary, "public");
  assert.deepEqual(
    sections.map(({ key }) => key),
    ["research_question", "evidence_and_contribution", "experiments", "limitations"],
  );
  assert.equal(JSON.stringify(sections).includes("用户很感兴趣"), false);
});

test("daily replaces brief analysis with the public structured reading summary", () => {
  assert.match(dailySource, /scope="public"/);
  assert.match(dailySource, /paper\.completion_summary_structured/);
  assert.doesNotMatch(dailySource, /paper\.completion_summary(?!_structured)/);
  assert.doesNotMatch(dailySource, /paper\.agent_comment/);
  assert.doesNotMatch(dailySource, /paper\.recommendation_reason/);
});

test("structured summary drafts require every editable section", () => {
  const draft = completionSummaryDraft(summary);
  assert.equal(completionSummaryDraftIsComplete(draft), true);
  assert.equal(completionSummaryDraftIsComplete(draft && { ...draft, experiments: " " }), false);
});

test("generated and metadata reading summaries use the shared editor", () => {
  assert.match(readerSource, /<CompletionSummaryEditor/);
  assert.match(readerSource, /completion_summary_structured: summaryDraft/);
  assert.match(metadataSource, /<CompletionSummaryEditor/);
  assert.match(metadataSource, /completion_summary_structured: summaryDraft/);
  assert.doesNotMatch(metadataSource, /paper\.data\?\.completion_summary\b/);
  assert.doesNotMatch(readerSource, /overview: paper\.completion_summary/);
  assert.doesNotMatch(metadataSource + readerSource, /历史精读总结/);
});
