import type { CompletionSummaryStructured } from "../types";

export type CompletionSummaryScope = "private" | "public";
export type CompletionSummarySection = {
  key: keyof CompletionSummaryStructured;
  title: string;
  content: string;
};

export const PUBLIC_COMPLETION_SUMMARY_FIELDS = [
  ["research_question", "研究问题"],
  ["evidence_and_contribution", "证据与贡献"],
  ["experiments", "实验"],
  ["limitations", "局限"],
] as const;

export const PRIVATE_COMPLETION_SUMMARY_FIELDS = [
  ...PUBLIC_COMPLETION_SUMMARY_FIELDS,
  ["user_attitude", "用户态度"] as const,
] as const;

export type CompletionSummaryField = typeof PRIVATE_COMPLETION_SUMMARY_FIELDS[number][0];
export type CompletionSummaryDraft = Record<CompletionSummaryField, string>;

export function completionSummaryDraft(
  summary: CompletionSummaryStructured | null | undefined,
): CompletionSummaryDraft | null {
  if (!summary || !PRIVATE_COMPLETION_SUMMARY_FIELDS.some(([key]) => typeof summary[key] === "string")) return null;
  return Object.fromEntries(
    PRIVATE_COMPLETION_SUMMARY_FIELDS.map(([key]) => [key, typeof summary[key] === "string" ? summary[key] : ""]),
  ) as CompletionSummaryDraft;
}

export function completionSummaryDraftIsComplete(summary: CompletionSummaryDraft | null): summary is CompletionSummaryDraft {
  return Boolean(summary && PRIVATE_COMPLETION_SUMMARY_FIELDS.every(([key]) => summary[key].trim()));
}

export function completionSummarySections(
  summary: CompletionSummaryStructured | null | undefined,
  scope: CompletionSummaryScope,
): CompletionSummarySection[] {
  if (!summary) return [];
  const fields = scope === "private"
    ? PRIVATE_COMPLETION_SUMMARY_FIELDS
    : PUBLIC_COMPLETION_SUMMARY_FIELDS;
  const sections = fields.flatMap(([key, title]) => {
    const content = summary[key];
    return typeof content === "string" && content.trim()
      ? [{ key, title, content: content.trim() }]
      : [];
  });
  if (scope === "private" && sections.length === 0 && summary.overview?.trim()) {
    return [{ key: "overview", title: "历史精读总结", content: summary.overview.trim() }];
  }
  return sections;
}
