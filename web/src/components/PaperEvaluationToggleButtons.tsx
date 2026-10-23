import CheckRounded from "@mui/icons-material/CheckRounded";
import { Box, ButtonBase, Grid, Stack, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from "@mui/material";
import type { InterestLevel, QualityLevel } from "../types";
import { INTEREST_OPTIONS, QUALITY_OPTIONS, relevanceScoreLabel } from "./paperEvaluation";

function AgentScore({ value, scoreLabel, confirmed, disabled = false, onConfirm }: { value?: number | null; scoreLabel?: string; confirmed: boolean; disabled?: boolean; onConfirm?: () => void }) {
  const label = scoreLabel ?? (value == null ? "—" : value.toFixed(2));
  const score = (
    <ButtonBase
      aria-label={confirmed ? `已认可 Agent 打分 ${label}` : `认可 Agent 打分 ${label}`}
      aria-pressed={confirmed}
      disabled={disabled || value == null || !onConfirm}
      onClick={onConfirm}
      sx={{
        alignItems: "center",
        bgcolor: confirmed ? "primary.light" : "action.hover",
        border: 1,
        borderColor: confirmed ? "primary.main" : "divider",
        borderRadius: 1,
        display: "flex",
        flexShrink: 0,
        gap: .5,
        height: 34,
        px: 1,
        color: confirmed ? "primary.main" : "text.secondary",
        "&.Mui-disabled": { color: "text.disabled", opacity: 1 },
      }}
    >
      {confirmed && <CheckRounded sx={{ fontSize: 16 }} />}
      <Typography variant="caption">Agent</Typography>
      <Typography variant="body2" fontWeight={750} sx={{ fontVariantNumeric: "tabular-nums" }}>{label}</Typography>
    </ButtonBase>
  );
  return <Tooltip title={confirmed ? "已看摘要并认可 Agent 评分（再次点击取消）" : "点击表示已看摘要并认可 Agent 评分"}>{score}</Tooltip>;
}

export function PaperChoiceToggle<Value extends string>({
  ariaLabel,
  disabled = false,
  fullWidth = true,
  attachedLabel = false,
  onChange,
  options,
  value,
}: {
  ariaLabel: string;
  disabled?: boolean;
  fullWidth?: boolean;
  attachedLabel?: boolean;
  onChange: (value: Value) => void;
  options: readonly { value: Value; label: string }[];
  value: Value | false;
}) {
  return (
    <ToggleButtonGroup
      exclusive
      fullWidth={fullWidth}
      size="small"
      color="primary"
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(_, nextValue: Value | null) => { if (nextValue) onChange(nextValue); }}
      sx={{
        flex: fullWidth ? 1 : undefined,
        minWidth: 0,
        ...(attachedLabel ? {
          "& .MuiToggleButtonGroup-firstButton": { borderBottomLeftRadius: 0, borderTopLeftRadius: 0 },
        } : {}),
        "& .MuiToggleButton-root": {
          height: 34,
          minWidth: 0,
          px: { xs: .375, sm: 1 },
          py: 0,
          fontSize: { xs: ".6875rem", sm: ".8125rem" },
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
  agentScoreConfirmed,
  disabled,
  onChange,
  onAgentScoreConfirm,
  options,
  score,
  scoreLabel,
  title,
  value,
  agentConfirmed,
}: {
  ariaLabel: string;
  disabled: boolean;
  onChange: (value: Value) => void;
  options: readonly { value: Value; label: string }[];
  score?: number | null;
  scoreLabel?: string;
  agentScoreConfirmed: boolean;
  agentConfirmed?: boolean;
  onAgentScoreConfirm?: () => void;
  title: string;
  value: Value | false;
}) {
  return (
    <Box sx={{ p: 1.25, border: 1, borderColor: "divider", borderRadius: 1.5 }}>
      <Typography variant="subtitle2" fontWeight={750} sx={{ mb: .75 }}>{title}</Typography>
      <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ xs: "stretch", sm: "center" }} spacing={.75}>
        <AgentScore
          value={score}
          scoreLabel={scoreLabel}
          confirmed={agentConfirmed ?? agentScoreConfirmed}
          disabled={disabled}
          onConfirm={onAgentScoreConfirm}
        />
        <Stack direction="row" alignItems="stretch" sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ alignItems: "center", bgcolor: "action.hover", border: 1, borderColor: "divider", borderRadius: "8px 0 0 8px", borderRight: 0, display: "flex", flexShrink: 0, px: .75 }}>
            <Typography variant="caption" color="text.secondary">我的</Typography>
          </Box>
          <PaperChoiceToggle
            attachedLabel
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
  modelScore,
  modelConfidence,
  modelReviewRequired = false,
  qualityLevel,
  interestLevel,
  onQualityChange,
  onInterestChange,
  agentScoreConfirmed = false,
  agentQualityConfirmed,
  agentInterestConfirmed,
  onAgentScoreConfirm,
  onQualityAgentScoreConfirm,
  onInterestAgentScoreConfirm,
  disabled = false,
  qualityTitle = "研究价值",
  interestTitle = "个人相关性",
  showQuality = true,
}: {
  researchScore?: number | null;
  relevanceScore?: number | null;
  modelScore?: number | null;
  modelConfidence?: number | null;
  modelReviewRequired?: boolean;
  qualityLevel: QualityLevel | false;
  interestLevel: InterestLevel | false;
  onQualityChange: (value: QualityLevel) => void;
  onInterestChange: (value: InterestLevel) => void;
  agentScoreConfirmed?: boolean;
  agentQualityConfirmed?: boolean;
  agentInterestConfirmed?: boolean;
  onAgentScoreConfirm?: () => void;
  onQualityAgentScoreConfirm?: () => void;
  onInterestAgentScoreConfirm?: () => void;
  disabled?: boolean;
  qualityTitle?: string;
  interestTitle?: string;
  showQuality?: boolean;
}) {
  return (
    <Grid container spacing={1.25}>
      {showQuality && <Grid size={{ xs: 12, md: 6 }}>
        <EvaluationMetric
          title={qualityTitle}
          score={researchScore}
          ariaLabel="我的文章质量评价"
          value={qualityLevel}
          options={QUALITY_OPTIONS}
          disabled={disabled}
          agentScoreConfirmed={agentScoreConfirmed}
          agentConfirmed={agentQualityConfirmed}
          onAgentScoreConfirm={onQualityAgentScoreConfirm ?? onAgentScoreConfirm}
          onChange={onQualityChange}
        />
      </Grid>}
      <Grid size={{ xs: 12, md: showQuality ? 6 : 12 }}>
        <EvaluationMetric
          title={interestTitle}
          score={relevanceScore}
          scoreLabel={
            relevanceScore == null
              ? undefined
              : `${relevanceScore.toFixed(2)} · ${relevanceScoreLabel(relevanceScore)}`
          }
          ariaLabel="我的兴趣度评价"
          value={interestLevel}
          options={INTEREST_OPTIONS}
          disabled={disabled}
          agentScoreConfirmed={agentScoreConfirmed}
          agentConfirmed={agentInterestConfirmed}
          onAgentScoreConfirm={onInterestAgentScoreConfirm ?? onAgentScoreConfirm}
          onChange={onInterestChange}
        />
        {modelScore != null && (
          <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: .75 }}>
            模型 {modelScore.toFixed(2)}
            {modelConfidence != null ? ` · 置信度 ${modelConfidence.toFixed(2)}` : ""}
            {modelReviewRequired ? " · 待 Agent 复核" : " · 高置信度"}
          </Typography>
        )}
      </Grid>
    </Grid>
  );
}
