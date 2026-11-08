export const USER_SCORE_OPTIONS = [
  { value: 0, label: "很低" },
  { value: 0.25, label: "较低" },
  { value: 0.5, label: "一般" },
  { value: 0.75, label: "较高" },
  { value: 1, label: "很高" },
] as const;

export function userScoreLevel(score: number | null | undefined): number | null {
  if (score == null || !Number.isFinite(score) || score < 0 || score > 1) return null;
  return Math.round(score * 4) / 4;
}

export function userScoreLabel(score: number | null | undefined): string {
  const level = userScoreLevel(score);
  return USER_SCORE_OPTIONS.find((option) => option.value === level)?.label ?? "未评分";
}
