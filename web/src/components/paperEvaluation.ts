import type { InterestLevel, QualityLevel } from "../types";

export const QUALITY_OPTIONS: readonly { value: QualityLevel; label: string }[] = [
  { value: "poor", label: "较差" },
  { value: "normal", label: "正常" },
  { value: "excellent", label: "优质" },
] as const;

export const INTEREST_OPTIONS: readonly { value: InterestLevel; label: string }[] = [
  { value: "ignored", label: "忽略" },
  { value: "neutral", label: "相关" },
  { value: "interested", label: "感兴趣" },
] as const;
