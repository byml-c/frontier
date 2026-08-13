import CheckCircleOutlineRounded from "@mui/icons-material/CheckCircleOutlineRounded";
import ConstructionRounded from "@mui/icons-material/ConstructionRounded";
import ErrorOutlineRounded from "@mui/icons-material/ErrorOutlineRounded";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import PsychologyAltRounded from "@mui/icons-material/PsychologyAltRounded";
import { Box, CircularProgress, Collapse, Divider, Stack, Typography } from "@mui/material";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { templateSurfaces } from "../theme";
import { activityPhasesForDisplay, groupActivities, topLevelActivityStatus, type AgentActivityItem, type AgentActivityPhase } from "./agentActivityState";

type Props = { items: AgentActivityItem[]; children?: ReactNode; pending?: boolean; autoCollapseWhenComplete?: boolean };

export function AgentActivity({ items, children, pending = false, autoCollapseWhenComplete = false }: Props) {
  const groupedPhases = useMemo(() => groupActivities(items), [items]);
  const running = items.some((item) => item.status === "running");
  const failed = !running && items.some((item) => item.status === "failed");
  const active = running || pending;
  const phases = useMemo(() => activityPhasesForDisplay(groupedPhases, active), [active, groupedPhases]);
  const connectionStatus = useMemo(() => topLevelActivityStatus(items), [items]);
  const hasOutput = Boolean(children);
  const latestPhaseId = phases.at(-1)?.id;
  const [expanded, setExpanded] = useState(() => active || !hasOutput);
  const [expandedPhaseId, setExpandedPhaseId] = useState<string | null>(() => active ? latestPhaseId ?? null : null);

  useEffect(() => {
    if (active) setExpanded(true);
    else if (hasOutput || autoCollapseWhenComplete) setExpanded(false);
  }, [active, autoCollapseWhenComplete, hasOutput]);

  useEffect(() => {
    if (active && latestPhaseId) setExpandedPhaseId(latestPhaseId);
    else if (hasOutput || autoCollapseWhenComplete) setExpandedPhaseId(null);
  }, [active, autoCollapseWhenComplete, hasOutput, latestPhaseId]);

  if (!items.length && !hasOutput && !pending) return null;
  const statusLabel = active ? "处理中" : failed ? "处理未完成" : "处理完成";

  return (
    <Box sx={{ ...templateSurfaces.assistantMessage, borderRadius: 2, overflow: "hidden" }}>
      {(items.length > 0 || pending) && <>
        <Stack
          component="button"
          type="button"
          direction="row"
          alignItems="center"
          spacing={1}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          sx={{ width: "100%", border: 0, bgcolor: "transparent", color: "text.primary", px: 1.5, py: 1.1, cursor: "pointer", textAlign: "left" }}
        >
          {active ? <CircularProgress size={15} /> : failed ? <ErrorOutlineRounded color="error" sx={{ fontSize: 18 }} /> : <CheckCircleOutlineRounded color="success" sx={{ fontSize: 18 }} />}
          <Typography variant="body2" fontWeight={650}>{statusLabel}</Typography>
          {connectionStatus && <Typography variant="caption" color="text.secondary">· {connectionStatus.label}</Typography>}
          <Box sx={{ flex: 1, borderTop: 1, borderColor: "divider" }} />
          <KeyboardArrowDownRounded color="action" sx={{ fontSize: 20, transition: "transform .18s ease", transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }} />
        </Stack>
        <Collapse in={expanded}>
          <Stack spacing={.75} sx={{ px: 1.25, pb: 1.25 }}>
            {phases.map((phase) => (
              <ActivityPhase
                key={phase.id}
                phase={phase}
                pending={active && phase.id === latestPhaseId}
                expanded={expandedPhaseId === phase.id}
                onToggle={() => setExpandedPhaseId((value) => value === phase.id ? null : phase.id)}
              />
            ))}
            {!phases.length && pending && <Stack direction="row" alignItems="center" spacing={1} sx={{ px: .5, py: .75 }}><CircularProgress size={14} /><Typography variant="caption" color="text.secondary">正在启动 Agent…</Typography></Stack>}
          </Stack>
        </Collapse>
      </>}
      {hasOutput && <>
        {(items.length > 0 || pending) && <Divider />}
        <Box sx={{ px: 1.75, py: 1.25 }}>{children}</Box>
      </>}
    </Box>
  );
}

function ActivityPhase({ phase, pending, expanded, onToggle }: { phase: AgentActivityPhase; pending: boolean; expanded: boolean; onToggle: () => void }) {
  const running = phase.status === "running" || pending;
  const failed = !running && phase.status === "failed";
  const status = running ? "处理中" : failed ? "未完成" : "完成";
  const secondaryItems = phase.kind === "analysis" ? phase.items.filter((item) => item.kind !== "reasoning") : phase.items;

  return (
    <Box sx={{ border: 1, borderColor: "divider", borderRadius: 1.5, overflow: "hidden", bgcolor: "rgba(255,255,255,.45)" }}>
      <Stack
        component="button"
        type="button"
        direction="row"
        alignItems="center"
        spacing={1}
        aria-expanded={expanded}
        onClick={onToggle}
        sx={{ width: "100%", border: 0, bgcolor: "transparent", color: "text.primary", px: 1.25, py: .9, cursor: "pointer", textAlign: "left" }}
      >
        {running ? <CircularProgress size={14} /> : failed ? <ErrorOutlineRounded color="error" sx={{ fontSize: 17 }} /> : <PsychologyAltRounded color="action" sx={{ fontSize: 17 }} />}
        <Typography variant="caption" fontWeight={700}>{phase.kind === "analysis" ? "分析" : "准备"}</Typography>
        {phase.toolCount > 0 && <Typography variant="caption" color="text.secondary">{phase.toolCount} 次工具调用</Typography>}
        <Typography variant="caption" color={failed ? "error.main" : "text.secondary"} sx={{ ml: "auto!important" }}>{status}</Typography>
        <KeyboardArrowDownRounded color="action" sx={{ fontSize: 18, transition: "transform .18s ease", transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }} />
      </Stack>
      <Collapse in={expanded}>
        <Box sx={{ mx: 1.25, mb: 1.1, pl: 1.25, borderLeft: 2, borderColor: "divider" }}>
          {phase.detail && <Typography variant="caption" color="text.secondary" display="block" sx={{ pb: secondaryItems.length ? .85 : 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{phase.detail}</Typography>}
          {!phase.detail && phase.kind === "analysis" && running && <Typography variant="caption" color="text.secondary" display="block" sx={{ pb: secondaryItems.length ? .85 : 0 }}>正在梳理信息…</Typography>}
          <Stack spacing={.8}>
            {secondaryItems.map((item) => <ActivityStep item={item} key={item.id} />)}
          </Stack>
        </Box>
      </Collapse>
    </Box>
  );
}

function ActivityStep({ item }: { item: AgentActivityItem }) {
  return (
    <Stack direction="row" spacing={.8} alignItems="flex-start">
      {item.status === "running" ? <CircularProgress size={13} sx={{ mt: .25 }} /> : item.status === "failed" ? <ErrorOutlineRounded color="error" sx={{ fontSize: 15, mt: .1 }} /> : item.kind === "tool" ? <ConstructionRounded color="action" sx={{ fontSize: 15, mt: .1 }} /> : <CheckCircleOutlineRounded color="action" sx={{ fontSize: 15, mt: .1 }} />}
      <Box sx={{ minWidth: 0 }}>
        <Typography variant="caption" fontWeight={600} display="block">{item.label}</Typography>
        {item.detail && <Typography variant="caption" color="text.secondary" display="block" sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.detail}</Typography>}
      </Box>
    </Stack>
  );
}
