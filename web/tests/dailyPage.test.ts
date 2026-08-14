import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pageSource = readFileSync(new URL("../src/pages/DailyPage.tsx", import.meta.url), "utf8");
const calendarSource = readFileSync(new URL("../src/components/DailyCalendar.tsx", import.meta.url), "utf8");
const discussionSource = readFileSync(new URL("../src/components/DailyDiscussion.tsx", import.meta.url), "utf8");
const librarySource = readFileSync(new URL("../src/pages/LibraryPage.tsx", import.meta.url), "utf8");
const typesSource = readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

test("daily date picker uses the localized MUI calendar and marks available dates", () => {
  assert.match(calendarSource, /DateCalendar/);
  assert.match(calendarSource, /adapterLocale="zh-cn"/);
  assert.match(calendarSource, /availableDates\?\.has/);
  assert.match(calendarSource, /bgcolor: props\.selected \? "common\.white" : "primary\.main"/);
  assert.match(pageSource, /setSearchParams\(\{ date: value \}\)/);
});

test("daily papers use scrollable tabs and bind discussion to the active paper", () => {
  assert.match(pageSource, /daily\.data\?\.selections/);
  assert.match(pageSource, /variant="scrollable"/);
  assert.match(pageSource, /allowScrollButtonsMobile/);
  assert.match(pageSource, /paperId=\{activeSelection\.paper\.id\}/);
  assert.match(pageSource, /key=\{activeSelection\.paper\.id\}/);
});

test("library allows additional papers while disabling papers already selected today", () => {
  assert.match(librarySource, /daily\.data\?\.selections\?\.some/);
  assert.match(librarySource, /settingDaily=\{setDaily\.isPending\}/);
  assert.doesNotMatch(librarySource, /settingDaily=\{setDaily\.isPending &&/);
  assert.match(librarySource, /disabled=\{isDaily \|\| settingDaily \|\| !dailyReady\}/);
});

test("daily selection rank and anonymous identity updates stay consistent across paper tabs", () => {
  assert.match(typesSource, /export type DailySelection = \{[\s\S]*?rank: number;/);
  assert.equal(
    discussionSource.match(/invalidateQueries\(\{ queryKey: \["daily-discussion"\] \}\)/g)?.length,
    2,
  );
  assert.doesNotMatch(discussionSource, /invalidateQueries\(\{ queryKey: \["daily-discussion", paperId\]/);
});
