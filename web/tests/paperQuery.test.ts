import assert from "node:assert/strict";
import test from "node:test";
import type { BriefItem, Paper } from "../src/types.ts";
import { EMPTY_LIBRARY_QUERY, libraryQueryParams, queryBriefItems, paperRating, ratingMatches, libraryMatchSnippets } from "../src/components/paperQuery.ts";

function item(id: string, rank: number, user: number | null, agent: number | null): BriefItem {
  return { id, rank, tier: "related", paper: {
    id, title: id, status: "candidate", source: "arxiv", tags: [], nodes: [], user_score: user, agent_score: agent,
  } };
}

test("single-score filtering distinguishes zero, null and source", () => {
  const entries = [item("zero", 1, 0, .83), item("fraction", 2, .713, .22), item("none", 3, null, null)];
  const query = { ...EMPTY_LIBRARY_QUERY, scoreMin: "0", scoreMax: "0" };
  assert.deepEqual(queryBriefItems(entries, query).map((i) => i.id), ["zero"]);
  assert.deepEqual(queryBriefItems(entries, { ...EMPTY_LIBRARY_QUERY, rated: "false" }).map((i) => i.id), ["none"]);
  assert.equal(paperRating(entries[1].paper, "user"), .713);
  assert.equal(paperRating(entries[1].paper, "agent"), .22);
  assert.ok(ratingMatches(entries[1].paper, { ...EMPTY_LIBRARY_QUERY, scoreMin: ".712", scoreMax: ".714" }));
});

test("ordering uses exact values, keeps null last, and does not mutate rank", () => {
  const entries = [item("a", 1, .713, null), item("b", 2, .714, null), item("none", 3, null, null)];
  assert.deepEqual(queryBriefItems(entries, { ...EMPTY_LIBRARY_QUERY, sort: "rating_desc" }).map((i) => i.id), ["b", "a", "none"]);
  assert.deepEqual(entries.map((i) => i.id), ["a", "b", "none"]);
});

test("library and brief filters use the same continuous query contract", () => {
  const params = libraryQueryParams({ ...EMPTY_LIBRARY_QUERY, q: "robot", scoreMin: "0", scoreMax: ".734", rated: "true", ratingSource: "agent", sort: "rating_desc" }, true, 2);
  assert.equal(params.get("score_min"), "0");
  assert.equal(params.get("score_max"), ".734");
  assert.equal(params.get("rated"), "true");
  assert.equal(params.get("rating_source"), "agent");
  assert.equal(params.get("offset"), "100");
  assert.equal(params.get("rating_level"), null);
});

test("current Markdown and institution content remain searchable", () => {
  const paper = { ...item("p", 1, null, null).paper, abstract: "a".repeat(200)+" tactile control",
    post_read_summary: "Tactile evidence", pre_read_summary: "A tactile entry",
    research_group: { name: "Robot Lab", institution: "清华大学", aliases: ["THU"] } } as Paper;
  const snippets = libraryMatchSnippets(paper, { q: "", title_q: "", affiliation_q: "THU", keyword_q: "tactile", tag_q: "" });
  assert.ok(snippets.some((s) => s.text.includes("THU")));
  assert.ok(snippets.some((s) => s.text.includes("tactile")));
});

test("post-reading content is not shown in library search snippets", () => {
  const paper = { ...item("private-summary", 1, null, null).paper,
    abstract: "A robot paper", pre_read_summary: "Before reading",
    post_read_summary: "Detailed post-reading conclusion" } as Paper;
  const snippets = libraryMatchSnippets(paper, { q: "post-reading", title_q: "", affiliation_q: "", keyword_q: "", tag_q: "" });
  assert.deepEqual(snippets, []);
});
