import { Box, Grid, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import type { InterestLevel, QualityLevel } from "../types";
import { INTEREST_OPTIONS, QUALITY_OPTIONS } from "./paperEvaluation";

function AgentScore({ value }: { value?: number | null }) {
  const label = value == null ? "—" : value.toFixed(2);
  return (
    <Box
      aria-label={`Agent 打分 ${label}`}
      sx={{
        alignItems: "center",
        bgcolor: "action.hover",
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        display: "flex",
        flexShrink: 0,
        gap: .5,
        height: 34,
        px: 1,
      }}
    >
      <Typography variant="caption" color="text.secondary">Agent</Typography>
      <Typography variant="body2" fontWeight={750} sx={{ fontVariantNumeric: "tabular-nums" }}>{label}</Typography>
    </Box>
  );
}

function RatingToggle<Value extends string>({
  ariaLabel,
  disabled,
  onChange,
  options,
  value,
}: {
  ariaLabel: string;
  disabled: boolean;
  onChange: (value: Value) => void;
  options: readonly { value: Value; label: string }[];
  value: Value;
}) {
  return (
    <ToggleButtonGroup
      exclusive
      fullWidth
      size="small"
      color="primary"
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(_, nextValue: Value | null) => { if (nextValue) onChange(nextValue); }}
      sx={{
        flex: 1,
        minWidth: 0,
        "& .MuiToggleButtonGroup-firstButton": { borderBottomLeftRadius: 0, borderTopLeftRadius: 0 },
        "& .MuiToggleButton-root": {
          height: 34,
          minWidth: 0,
          px: { xs: .625, sm: 1 },
          py: 0,
          whiteSpace: "nowrap",
          textTransform: "none",
        },
      }}
    >
      {options.map((option) => (
        <ToggleButton key={option.value} value={option.value}>{option.label}</ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}

function EvaluationMetric<Value extends string>({
  ariaLabel,
  disabled,
  onChange,
  options,
  score,
  title,
  value,
}: {
  ariaLabel: string;
  disabled: boolean;
  onChange: (value: Value) => void;
  options: readonly { value: Value; label: string }[];
  score?: number | null;
  title: string;
  value: Value;
}) {
  return (
    <Box sx={{ p: 1.25, border: 1, borderColor: "divider", borderRadius: 1.5 }}>
      <Typography variant="subtitle2" fontWeight={750} sx={{ mb: .75 }}>{title}</Typography>
      <Stack direction="row" alignItems="center" spacing={.75}>
        <AgentScore value={score} />
        <Stack direction="row" alignItems="stretch" sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ alignItems: "center", bgcolor: "action.hover", border: 1, borderColor: "divider", borderRadius: "8px 0 0 8px", borderRight: 0, display: "flex", flexShrink: 0, px: .75 }}>
            <Typography variant="caption" color="text.secondary">我的</Typography>
          </Box>
          <RatingToggle
            ariaLabel={ariaLabel}
            value={value}
            options={options}
            disabled={disabled}
            onChange={onChange}
          />
        </Stack>
      </Stack>
    </Box>
  );
}

export function PaperEvaluationToggleButtons({
  researchScore,
  relevanceScore,
  qualityLevel,
  interestLevel,
  onQualityChange,
  onInterestChange,
  disabled = false,
}: {
  researchScore?: number | null;
  relevanceScore?: number | null;
  qualityLevel: QualityLevel;
  interestLevel: InterestLevel;
  onQualityChange: (value: QualityLevel) => void;
  onInterestChange: (value: InterestLevel) => void;
  disabled?: boolean;
}) {
  return (
    <Grid container spacing={1.25}>
      <Grid size={{ xs: 12, md: 6 }}>
        <EvaluationMetric
          title="研究价值"
          score={researchScore}
          ariaLabel="我的文章质量评价"
          value={qualityLevel}
          options={QUALITY_OPTIONS}
          disabled={disabled}
          onChange={onQualityChange}
        />
      </Grid>
      <Grid size={{ xs: 12, md: 6 }}>
        <EvaluationMetric
          title="个人相关性"
          score={relevanceScore}
          ariaLabel="我的兴趣度评价"
          value={interestLevel}
          options={INTEREST_OPTIONS}
          disabled={disabled}
          onChange={onInterestChange}
        />
      </Grid>
    </Grid>
  );
}
