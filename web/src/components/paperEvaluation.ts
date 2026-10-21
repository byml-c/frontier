import type { InterestLevel, QualityLevel } from "../types";

export const QUALITY_OPTIONS: readonly { value: QualityLevel; label: string }[] = [
  { value: "poor", label: "很差" },
  { value: "below_average", label: "较差" },
  { value: "normal", label: "一般" },
  { value: "good", label: "较好" },
  { value: "excellent", label: "优秀" },
] as const;

export const INTEREST_OPTIONS: readonly { value: InterestLevel; label: string }[] = [
  { value: "ignored", label: "不相关" },
  { value: "low", label: "较不相关" },
  { value: "neutral", label: "一般" },
  { value: "high", label: "较相关" },
  { value: "interested", label: "高度相关" },
] as const;

export function relevanceScoreLabel(score?: number | null): string {
  if (score == null || !Number.isFinite(score) || score < 0 || score > 1) return "待评估";
  return INTEREST_OPTIONS[Math.round(score * 4)].label;
}
