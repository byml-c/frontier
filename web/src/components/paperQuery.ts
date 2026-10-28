import type { BriefItem, Paper } from "../types";
import { INTEREST_OPTIONS, QUALITY_OPTIONS } from "./paperEvaluation.ts";
import { matchExcerpt, queryTerms } from "./textMatches.ts";

export type RatingSource = "interest" | "quality" | "agent" | "model";
export type PaperOrder = "default" | "rating_desc" | "rating_asc" | "published_desc" | "published_asc" | "title";
export type RatingQuery = { ratingSource: RatingSource; ratingLevel: string; sort: PaperOrder };

export const RATING_SOURCES = [
  { value: "interest", label: "我的相关度" },
  { value: "agent", label: "Agent 相关度" },
  { value: "model", label: "模型相关度" },
  { value: "quality", label: "我的质量评价" },
] as const;

export function ratingOptions(source: RatingSource) {
  return source === "quality" ? QUALITY_OPTIONS : INTEREST_OPTIONS;
}

export function paperRating(paper: Paper, source: RatingSource): number | null {
  if (source === "agent" || source === "model") {
    const value = source === "agent" ? paper.relevance_score : paper.model_relevance_score;
    return value == null || !Number.isFinite(value) ? null : value;
  }
  const value = source === "quality"
    ? paper.quality_level ?? paper.screening_quality_level
    : paper.interest_level ?? paper.screening_interest_level;
  const index = ratingOptions(source).findIndex((option) => option.value === value);
  return index < 0 ? null : index / 4;
}

export function ratingMatches(paper: Paper, source: RatingSource, level: string): boolean {
  if (level === "") return true;
  const score = paperRating(paper, source);
  if (level === "-1") return score == null;
  return score != null && Math.round(score * 4) === Number(level);
}

function compareNullable(a: number | string | null, b: number | string | null, descending: boolean): number {
  if (a == null) return b == null ? 0 : 1;
  if (b == null) return -1;
  const result = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
  return descending ? -result : result;
}

export function briefPaper(item: BriefItem): Paper {
  return { ...item.paper, status: item.current_status, interest_level: item.interest_level ?? item.paper.interest_level, quality_level: item.quality_level ?? item.paper.quality_level };
}

export function queryBriefItems(items: BriefItem[], query: RatingQuery): BriefItem[] {
  return items.filter((item) => ratingMatches(briefPaper(item), query.ratingSource, query.ratingLevel)).sort((a, b) => {
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
  published_from: "", published_to: "", scope: "all", ratingSource: "interest", ratingLevel: "", sort: "default",
};

export function libraryQueryParams(query: LibraryQuery, collection: boolean, page: number): URLSearchParams {
  const params = new URLSearchParams({ limit: "50", offset: String(page * 50), rating_source: query.ratingSource, sort: query.sort });
  for (const key of ["q", "title_q", "affiliation_q", "keyword_q", "tag_q", "published_from", "published_to"] as const) {
    if (query[key].trim()) params.set(key, query[key].trim());
  }
  if (query.ratingLevel !== "") params.set("rating_level", query.ratingLevel);
  if (collection) params.set("library", "true");
  if (query.scope === "quality") params.set("quality", "true");
  else if (query.scope === "interested") params.set("interested", "true");
  else if (query.scope !== "all") params.set("status", query.scope);
  return params;
}

export function libraryMatchSnippets(paper: Paper, query: TextQuery): Array<{ label: string; text: string; terms: string[] }> {
  const keywordTerms = queryTerms(query.q, query.keyword_q);
  const unitTerms = queryTerms(query.q, query.affiliation_q);
  const candidates = [
    { label: "摘要", text: paper.abstract, terms: keywordTerms },
    { label: "总结", text: paper.summary, terms: keywordTerms },
    { label: "目录摘要", text: paper.catalog_summary, terms: keywordTerms },
    { label: "模型 / 别名", text: [paper.model_short_name, ...(paper.search_aliases || [])].filter(Boolean).join(" · "), terms: keywordTerms },
    { label: "单位 / 课题组", text: [paper.primary_affiliation, paper.research_group?.name, paper.research_group?.institution, ...(paper.research_group?.aliases || [])].filter(Boolean).join(" · "), terms: unitTerms },
    { label: "简记", text: paper.screening_feedback, terms: queryTerms(query.q) },
    { label: "Agent 简评", text: paper.agent_comment, terms: keywordTerms },
    { label: "推荐理由", text: paper.recommendation_reason, terms: keywordTerms },
    { label: "关键词", text: Array.isArray(paper.metadata_json?.keywords) ? paper.metadata_json.keywords.join(" · ") : String(paper.metadata_json?.keywords || ""), terms: keywordTerms },
  ];
  return candidates.flatMap(({ label, text, terms }) => {
    const excerpt = text && terms.length ? matchExcerpt(text, terms) : null;
    return excerpt ? [{ label, text: excerpt, terms }] : [];
  }).slice(0, 5);
}
