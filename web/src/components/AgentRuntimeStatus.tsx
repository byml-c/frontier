import { Box, CircularProgress, Stack, Tooltip, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { AgentRuntimeState, Conversation, LatestServiceChecks } from "../types";
import {
  agentStateFromConversation,
  agentStateFromServiceCheck,
  contextUsageDetails,
  mergeAgentRuntimeStates,
  modelDisplayName,
  reasoningEffortLabel,
} from "./agentRuntimeState";

export function AgentRuntimeStatus({ conversation, liveState }: { conversation?: Conversation; liveState?: AgentRuntimeState }) {
  const checks = useQuery({
    queryKey: ["service-checks"],
    queryFn: () => api<LatestServiceChecks>("/api/service-checks"),
    staleTime: 5 * 60 * 1000,
  });
  const state = mergeAgentRuntimeStates(
    agentStateFromServiceCheck(checks.data?.codex),
    agentStateFromConversation(conversation),
    liveState ?? {},
  );
  const model = modelDisplayName(state.runtime?.model);
  const effort = reasoningEffortLabel(state.runtime?.reasoning_effort);
  const usage = contextUsageDetails(state.usage);

  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ minWidth: 0, minHeight: 22, mt: 0.5, px: 0.25 }}>
      {usage && <ContextUsageRing {...usage} />}
      <Tooltip title={`模型：${model} · 思考程度：${effort}`}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {model} · {effort}
        </Typography>
      </Tooltip>
    </Stack>
  );
}

function ContextUsageRing({ current, window, percent, percentLabel }: { current: number; window: number; percent: number; percentLabel: string }) {
  const tokenLabel = `上下文 ${formatTokens(current)} / ${formatTokens(window)} tokens（${percentLabel}）`;
  return (
    <Tooltip title={tokenLabel} enterTouchDelay={0} leaveTouchDelay={2500}>
      <Box
        component="span"
        role="img"
        tabIndex={0}
        aria-label={tokenLabel}
        sx={{ position: "relative", display: "inline-flex", flex: "0 0 auto", width: 26, height: 26, alignItems: "center", justifyContent: "center", outline: 0 }}
      >
        <CircularProgress variant="determinate" value={100} size={26} thickness={3.5} sx={{ color: "action.disabledBackground", position: "absolute" }} />
        <CircularProgress variant="determinate" value={percent} size={26} thickness={3.5} sx={{ color: percent >= 85 ? "warning.main" : "primary.main", position: "absolute" }} />
      </Box>
    </Tooltip>
  );
}

function formatTokens(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(Math.round(value));
}
