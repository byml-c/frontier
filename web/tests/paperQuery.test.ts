import assert from "node:assert/strict";
import test from "node:test";
import type { BriefItem, Paper } from "../src/types.ts";
import { EMPTY_LIBRARY_QUERY, libraryMatchSnippets, libraryQueryParams, paperRating, queryBriefItems, ratingMatches } from "../src/components/paperQuery.ts";
import { matchExcerpt, splitTextMatches } from "../src/components/textMatches.ts";

function item(id: string, rank: number, interest: Paper["screening_interest_level"], agent: number | null): BriefItem {
  return {
    id, rank, tier: "related", current_status: "candidate", is_interested: false, is_quality: false,
    paper: { id, title: id, status: "candidate", source: "arxiv", tags: [], nodes: [], is_interested: false, is_quality: false, screening_interest_level: interest, relevance_score: agent },
  };
}

test("brief rating filters distinguish zero, unrated, human and Agent scores", () => {
  const papers = [item("zero", 1, "ignored", .91), item("high", 2, "high", .125), item("none", 3, null, null)];
  const base = { ratingSource: "interest" as const, ratingLevel: "0", sort: "default" as const };
  assert.deepEqual(queryBriefItems(papers, base).map((p) => p.id), ["zero"]);
  assert.deepEqual(queryBriefItems(papers, { ...base, ratingLevel: "-1" }).map((p) => p.id), ["none"]);
  assert.deepEqual(queryBriefItems(papers, { ...base, ratingSource: "agent", ratingLevel: "1" }).map((p) => p.id), ["high"]);
  assert.equal(paperRating({ ...papers[0].paper, quality_level: "excellent" }, "quality"), 1);
  for (const [value, level] of [[0, "0"], [.1249, "0"], [.125, "1"], [.375, "2"], [.625, "3"], [.875, "4"], [1, "4"]] as const) {
    assert.ok(ratingMatches({ ...papers[0].paper, relevance_score: value }, "agent", level));
  }
});

test("brief ordering keeps missing scores last and preserves original recommendation ranks", () => {
  const papers = [item("none", 1, null, null), item("high", 2, "high", .9), item("zero", 3, "ignored", 0)];
  assert.deepEqual(queryBriefItems(papers, { ratingSource: "agent", ratingLevel: "", sort: "rating_asc" }).map((p) => p.id), ["zero", "high", "none"]);
  assert.deepEqual(queryBriefItems(papers, { ratingSource: "agent", ratingLevel: "", sort: "rating_desc" }).map((p) => p.id), ["high", "zero", "none"]);
  assert.deepEqual(papers.map((p) => p.rank), [1, 2, 3]);
});

test("library requests combine all dimensions and paginate on the server", () => {
  const params = libraryQueryParams({ ...EMPTY_LIBRARY_QUERY, title_q: "100% robot", affiliation_q: "清华", tag_q: "WAM", keyword_q: "tactile", published_from: "2026-10-01", published_to: "2026-10-28", ratingSource: "quality", ratingLevel: "4", sort: "rating_desc" }, true, 2);
  assert.equal(params.get("title_q"), "100% robot");
  assert.equal(params.get("affiliation_q"), "清华");
  assert.equal(params.get("rating_source"), "quality");
  assert.equal(params.get("rating_level"), "4");
  assert.equal(params.get("published_to"), "2026-10-28");
  assert.equal(params.get("offset"), "100");
  assert.equal(params.get("library"), "true");
});

test("text highlighting is case-insensitive, literal, and preserves original text", () => {
  const text = "World Model 清华 a+b <script> 100%";
  const parts = splitTextMatches(text, ["world model", "world", "清华", "a+b", "<script>", "100%"]);
  assert.equal(parts.map((p) => p.text).join(""), text);
  assert.deepEqual(parts.filter((p) => p.matched).map((p) => p.text), ["World Model", "清华", "a+b", "<script>", "100%"]);
  assert.match(matchExcerpt("x".repeat(200) + "TACTILE result", ["tactile"])!, /TACTILE/);
  assert.equal(matchExcerpt("no match", ["robot"]), null);
});

test("hidden abstract and institution matches are exposed in highlighted excerpts", () => {
  const paper = { ...item("paper", 1, null, null).paper, abstract: "a".repeat(200) + " tactile control", summary: "A summary without the query term", catalog_summary: "A tactile catalog entry", research_group: { name: "Robot Lab", institution: "清华大学", aliases: ["THU"] } } as Paper;
  const snippets = libraryMatchSnippets(paper, { q: "", title_q: "", affiliation_q: "THU", keyword_q: "tactile", tag_q: "" });
  assert.ok(snippets.some((s) => s.label === "摘要" && s.text.includes("tactile")));
  assert.ok(snippets.some((s) => s.label === "单位 / 课题组" && s.text.includes("THU")));
  assert.ok(snippets.some((s) => s.label === "目录摘要" && s.text.includes("tactile")));
});
