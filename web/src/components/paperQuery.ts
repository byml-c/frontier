import type { BriefItem, Paper } from "../types";
import { matchExcerpt, queryTerms } from "./textMatches.ts";

export type RatingSource = "user" | "agent" | "model";
export type PaperOrder = "default" | "rating_desc" | "rating_asc" | "published_desc" | "published_asc" | "title";
export type RatingQuery = { ratingSource: RatingSource; scoreMin: string; scoreMax: string; rated: string; sort: PaperOrder };

export const RATING_SOURCES = [
  { value: "user", label: "人工评分" }, { value: "agent", label: "Agent 评分" },
  { value: "model", label: "模型评分" },
] as const;

export function paperRating(paper: Paper, source: RatingSource): number | null {
  const score = { user: paper.user_score, agent: paper.agent_score, model: paper.model_score }[source];
  return score == null || !Number.isFinite(score) ? null : score;
}

export function ratingMatches(paper: Paper, query: RatingQuery): boolean {
  const score = paperRating(paper, query.ratingSource);
  if (query.rated === "false") return score == null;
  if (score == null) return query.rated !== "true" && query.scoreMin === "" && query.scoreMax === "";
  return (query.scoreMin === "" || score >= Number(query.scoreMin)) &&
    (query.scoreMax === "" || score <= Number(query.scoreMax));
}

function compareNullable(a: number | string | null, b: number | string | null, descending: boolean): number {
  if (a == null) return b == null ? 0 : 1;
  if (b == null) return -1;
  const result = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
  return descending ? -result : result;
}

export function briefPaper(item: BriefItem): Paper { return item.paper; }

export function queryBriefItems(items: BriefItem[], query: RatingQuery): BriefItem[] {
  return items.filter((item) => ratingMatches(briefPaper(item), query)).sort((a, b) => {
    let result = 0;
    if (query.sort.startsWith("rating_")) {
      result = compareNullable(paperRating(briefPaper(a), query.ratingSource), paperRating(briefPaper(b), query.ratingSource), query.sort === "rating_desc");
    } else if (query.sort.startsWith("published_")) {
      result = compareNullable(a.paper.published_at || null, b.paper.published_at || null, query.sort === "published_desc");
    } else if (query.sort === "title") {
      result = a.paper.title.localeCompare(b.paper.title);
    }
    return result || (a.rank || 999) - (b.rank || 999) || a.id.localeCompare(b.id);
  });
}

export type TextQuery = { q: string; title_q: string; affiliation_q: string; keyword_q: string; tag_q: string };
export type LibraryQuery = TextQuery & { published_from: string; published_to: string; scope: string } & RatingQuery;
export const EMPTY_LIBRARY_QUERY: LibraryQuery = {
  q: "", title_q: "", affiliation_q: "", keyword_q: "", tag_q: "",
  published_from: "", published_to: "", scope: "all", ratingSource: "user", scoreMin: "", scoreMax: "", rated: "", sort: "default",
};

export function libraryQueryParams(query: LibraryQuery, collection: boolean, page: number): URLSearchParams {
  const params = new URLSearchParams({ limit: "50", offset: String(page * 50), rating_source: query.ratingSource, sort: query.sort });
  for (const key of ["q", "title_q", "affiliation_q", "keyword_q", "tag_q", "published_from", "published_to"] as const) {
    if (query[key].trim()) params.set(key, query[key].trim());
  }
  if (query.scoreMin !== "") params.set("score_min", query.scoreMin);
  if (query.scoreMax !== "") params.set("score_max", query.scoreMax);
  if (query.rated !== "") params.set("rated", query.rated);
  if (collection) params.set("library", "true");
  if (query.scope !== "all") params.set("status", query.scope);
  return params;
}

export function libraryMatchSnippets(paper: Paper, query: TextQuery): Array<{ label: string; text: string; terms: string[] }> {
  const keywordTerms = queryTerms(query.q, query.keyword_q);
  const unitTerms = queryTerms(query.q, query.affiliation_q);
  const candidates = [
    { label: "摘要", text: paper.abstract, terms: keywordTerms },
    { label: "读前简介", text: paper.pre_read_summary, terms: keywordTerms },
    { label: "模型 / 别名", text: [paper.model_short_name, ...(paper.search_aliases || [])].filter(Boolean).join(" · "), terms: keywordTerms },
    { label: "单位 / 课题组", text: [paper.primary_affiliation, paper.research_group?.name, paper.research_group?.institution, ...(paper.research_group?.aliases || [])].filter(Boolean).join(" · "), terms: unitTerms },
    { label: "简记", text: paper.user_note, terms: queryTerms(query.q) },
    { label: "关键词", text: Array.isArray(paper.metadata_json?.keywords) ? paper.metadata_json.keywords.join(" · ") : String(paper.metadata_json?.keywords || ""), terms: keywordTerms },
  ];
  return candidates.flatMap(({ label, text, terms }) => {
    const excerpt = text && terms.length ? matchExcerpt(text, terms) : null;
    return excerpt ? [{ label, text: excerpt, terms }] : [];
  }).slice(0, 5);
}
