import { Box, Chip, Grid, Slider, Stack, Typography } from "@mui/material";
import type { InterestLevel, QualityLevel } from "../types";
import {
  INTEREST_MARKS,
  QUALITY_MARKS,
  interestLevelToSlider,
  qualityLevelToSlider,
  sliderToInterestLevel,
  sliderToQualityLevel,
} from "./paperEvaluation";

function AgentScore({ value }: { value?: number | null }) {
  return <Chip size="small" variant="outlined" label={`Agent 打分 ${value == null ? "—" : value.toFixed(2)}`} />;
}

const sliderSx = {
  mt: .5,
  mx: 1,
  width: "calc(100% - 16px)",
  "& .MuiSlider-markLabel": { fontSize: ".75rem" },
};

export function PaperEvaluationSliders({
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
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, md: 6 }}>
        <Box sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 2 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
            <Typography variant="subtitle1" fontWeight={750}>研究价值</Typography>
            <AgentScore value={researchScore} />
          </Stack>
          <Typography variant="caption" color="text.secondary">我的评价</Typography>
          <Slider
            min={0}
            max={2}
            step={1}
            marks={QUALITY_MARKS}
            value={qualityLevelToSlider(qualityLevel)}
            disabled={disabled}
            aria-label="我的文章质量评价"
            onChange={(_, value) => onQualityChange(sliderToQualityLevel(value as number))}
            sx={sliderSx}
          />
        </Box>
      </Grid>
      <Grid size={{ xs: 12, md: 6 }}>
        <Box sx={{ p: 2, border: 1, borderColor: "divider", borderRadius: 2 }}>
          <Stack direction="row" alignItems="center" justifyContent="space-between" gap={1}>
            <Typography variant="subtitle1" fontWeight={750}>个人相关性</Typography>
            <AgentScore value={relevanceScore} />
          </Stack>
          <Typography variant="caption" color="text.secondary">我的评价</Typography>
          <Slider
            min={0}
            max={2}
            step={1}
            marks={INTEREST_MARKS}
            value={interestLevelToSlider(interestLevel)}
            disabled={disabled}
            aria-label="我的兴趣度评价"
            onChange={(_, value) => onInterestChange(sliderToInterestLevel(value as number))}
            sx={sliderSx}
          />
        </Box>
      </Grid>
    </Grid>
  );
}
