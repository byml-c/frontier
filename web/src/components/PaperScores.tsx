import { Box, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { USER_SCORE_OPTIONS, userScoreLevel } from "./score";

export function ScoreInput({ value, onChange, disabled = false }: {
  value: number | null; onChange: (value: number | null) => void; disabled?: boolean;
}) {
  return <Stack direction="row" alignItems="stretch" sx={{ flex: 1, minWidth: 0 }}>
    <Box sx={{ display: "flex", alignItems: "center", px: 1, bgcolor: "action.hover",
      border: 1, borderRight: 0, borderColor: "divider", borderRadius: "8px 0 0 8px" }}>
      <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: "nowrap" }}>我的</Typography>
    </Box>
    <ToggleButtonGroup size="small" exclusive fullWidth color="primary" disabled={disabled}
      value={userScoreLevel(value)} aria-label="我的评分"
      onChange={(_, next: number | null) => onChange(next)}
      sx={{ minWidth: 0, "& .MuiToggleButtonGroup-firstButton": { borderTopLeftRadius: 0, borderBottomLeftRadius: 0 },
        "& .MuiToggleButton-root": {
          height: { xs: 38, sm: 34 }, minWidth: 0, px: { xs: .75, sm: 1.25 }, py: 0,
          fontSize: { xs: ".75rem", sm: ".8125rem" }, whiteSpace: "nowrap", textTransform: "none",
          transition: "background-color .15s, color .15s, border-color .15s",
          "&.Mui-selected": { fontWeight: 750 },
          "&.Mui-focusVisible": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: 2 },
        },
      }}>
      {USER_SCORE_OPTIONS.map((option) => <ToggleButton key={option.value} value={option.value}>
        {option.label}
      </ToggleButton>)}
    </ToggleButtonGroup>
  </Stack>;
}

function SourceScore({ label, score }: { label: string; score?: number | null }) {
  return <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 1,
    minWidth: 84, height: { xs: 38, sm: 34 }, px: 1, border: 1, borderColor: "divider",
    borderRadius: 1, bgcolor: "action.hover" }}>
    <Typography variant="caption" color="text.secondary">{label}</Typography>
    <Typography variant="body2" fontWeight={750} color={score == null ? "text.disabled" : "text.primary"}
      sx={{ fontVariantNumeric: "tabular-nums" }}>{score == null ? "—" : score.toFixed(2)}</Typography>
  </Box>;
}

export function PaperScores({ userScore, agentScore, modelScore, onChange, disabled = false }: {
  userScore: number | null; agentScore?: number | null; modelScore?: number | null;
  onChange: (value: number | null) => void; disabled?: boolean;
}) {
  return <Box sx={{ p: 1.25, border: 1, borderColor: "divider", borderRadius: 1.5, width: "100%", minWidth: 0 }}>
    <Typography variant="subtitle2" fontWeight={750} sx={{ mb: .75 }}>评分</Typography>
    <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ xs: "stretch", sm: "center" }} spacing={.75}>
      <Stack direction="row" spacing={.75}>
        <SourceScore label="Agent" score={agentScore} />
        <SourceScore label="模型" score={modelScore} />
      </Stack>
      <ScoreInput value={userScore} onChange={onChange} disabled={disabled} />
    </Stack>
  </Box>;
}

export function PaperChoiceToggle<Value extends string>({ value, options, onChange, ariaLabel, fullWidth = true }: {
  value: Value; options: readonly { value: Value; label: string }[];
  onChange: (value: Value) => void; ariaLabel: string; fullWidth?: boolean;
}) {
  return <ToggleButtonGroup size="small" exclusive fullWidth={fullWidth} color="primary" value={value} aria-label={ariaLabel}
    onChange={(_, next: Value | null) => { if (next) onChange(next); }}
    sx={{ "& .MuiToggleButton-root": { height: 34, px: 1, py: 0, fontSize: ".8125rem", whiteSpace: "nowrap" } }}>
    {options.map((item) => <ToggleButton key={item.value} value={item.value}>{item.label}</ToggleButton>)}
  </ToggleButtonGroup>;
}
