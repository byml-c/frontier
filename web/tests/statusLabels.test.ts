import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { paperStatusLabel, taskStatusLabel } from "../src/api.ts";

test("paper and task completion have separate meanings", () => {
  assert.equal(paperStatusLabel("reading"), "读过");
  assert.equal(paperStatusLabel("completed"), "已读完");
  assert.equal(taskStatusLabel("completed"), "已完成");
  assert.equal(taskStatusLabel("running"), "运行中");
  assert.equal(taskStatusLabel("queued"), "排队中");
  assert.equal(taskStatusLabel("failed"), "失败");
  assert.equal(taskStatusLabel("cancelled"), "已中断");
  assert.equal(paperStatusLabel("running"), "running");
  assert.equal(taskStatusLabel("reading"), "reading");
});

test("task center uses task labels and library reading entries are native links", () => {
  const tasks = readFileSync(new URL("../src/pages/TasksPage.tsx", import.meta.url), "utf8");
  assert.match(tasks, /taskStatusLabel\(task.status\)/);
  assert.doesNotMatch(tasks, /paperStatusLabel/);
  const library = readFileSync(new URL("../src/pages/LibraryPage.tsx", import.meta.url), "utf8");
  assert.match(library, /paperStatusLabel\(paper.status\)/);
  assert.match(library, /href=\{readingHref\} target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(library, /onOpen|openFromCard|role="link"/);
});
