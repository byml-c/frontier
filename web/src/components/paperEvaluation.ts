import type { InterestLevel, QualityLevel } from "../types";

const qualityLevels: QualityLevel[] = ["poor", "normal", "excellent"];
const interestLevels: InterestLevel[] = ["ignored", "neutral", "interested"];

export const QUALITY_MARKS = [
  { value: 0, label: "较差" },
  { value: 1, label: "正常" },
  { value: 2, label: "优质" },
] as const;

export const INTEREST_MARKS = [
  { value: 0, label: "忽略" },
  { value: 1, label: "相关" },
  { value: 2, label: "感兴趣" },
] as const;

export function qualityLevelToSlider(value: QualityLevel): number {
  return qualityLevels.indexOf(value);
}

export function sliderToQualityLevel(value: number): QualityLevel {
  return qualityLevels[Math.max(0, Math.min(2, Math.round(value)))];
}

export function interestLevelToSlider(value: InterestLevel): number {
  return interestLevels.indexOf(value);
}

export function sliderToInterestLevel(value: number): InterestLevel {
  return interestLevels[Math.max(0, Math.min(2, Math.round(value)))];
}
