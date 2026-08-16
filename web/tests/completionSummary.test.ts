import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { completionSummaryDraft, completionSummaryDraftIsComplete, completionSummarySections } from "../src/components/completionSummary.ts";

const dailySource = readFileSync(new URL("../src/pages/DailyPage.tsx", import.meta.url), "utf8");
const readerSource = readFileSync(new URL("../src/pages/ReaderPage.tsx", import.meta.url), "utf8");
const metadataSource = readFileSync(new URL("../src/components/PaperMetadataDialog.tsx", import.meta.url), "utf8");

const summary = {
  problem: "解决的问题",
  innovation: "创新内容",
  experiment: "实验结果",
  limitations: "局限",
  open_questions: "未解决问题",
  user_attitude: "用户很感兴趣",
};

test("private reading summary keeps all structured sections in order", () => {
  assert.deepEqual(
    completionSummarySections(summary, "private").map(({ key }) => key),
    ["problem", "innovation", "experiment", "limitations", "open_questions", "user_attitude"],
  );
});

test("public reading summary cannot expose user attitude", () => {
  const sections = completionSummarySections(summary, "public");
  assert.deepEqual(
    sections.map(({ key }) => key),
    ["problem", "innovation", "experiment", "limitations", "open_questions"],
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
  assert.equal(completionSummaryDraftIsComplete(draft && { ...draft, experiment: " " }), false);
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

test("paper details default to overview and toggle one shared reading editor", () => {
  assert.match(metadataSource, /label="概览"/);
  assert.match(metadataSource, /label="精读"/);
  assert.match(metadataSource, /if \(open\) setTab\("overview"\)/);
  assert.match(metadataSource, /<ToggleButtonGroup/);
  assert.match(metadataSource, /value=\{readingTab\}/);
  assert.match(metadataSource, /<ToggleButton value="daily">每日分析<\/ToggleButton>/);
  assert.match(metadataSource, /<ToggleButton value="reading" disabled=\{!summaryDraft\}>精读结论<\/ToggleButton>/);
  assert.match(metadataSource, /\{tab === "reading" && \(/);
  assert.match(metadataSource, /\{tab === "overview" && \(/);
  assert.match(metadataSource, /value=\{readingTab === "reading" && summaryDraft \? summaryDraft : dailyDraft\}/);
  assert.match(metadataSource, /disabledFields=\{readingTab === "daily" \? \["user_attitude"\] : \[\]\}/);
  assert.match(metadataSource, /title="论文信息" wide fixedHeight compact/);
  assert.match(metadataSource, /key=\{readingTab\}/);
  assert.doesNotMatch(metadataSource, /<Tab value="daily"/);
  assert.doesNotMatch(metadataSource, /<Typography variant="h3">概览<\/Typography>/);
  assert.match(metadataSource, /<PaperEvaluationToggleButtons/);
  assert.doesNotMatch(metadataSource, /field\("research_score"/);
  assert.doesNotMatch(metadataSource, /field\("relevance_score"/);
});
