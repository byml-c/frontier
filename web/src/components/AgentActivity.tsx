import CheckCircleOutlineRounded from "@mui/icons-material/CheckCircleOutlineRounded";
import ConstructionRounded from "@mui/icons-material/ConstructionRounded";
import ErrorOutlineRounded from "@mui/icons-material/ErrorOutlineRounded";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import PsychologyAltRounded from "@mui/icons-material/PsychologyAltRounded";
import { Box, CircularProgress, Collapse, Divider, Stack, Typography } from "@mui/material";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { templateSurfaces } from "../theme";
import { activityPhasesForDisplay, agentActivityOutcome, groupActivities, plainActivitySummary, topLevelActivityStatus, type AgentActivityItem, type AgentActivityPhase } from "./agentActivityState";

type Props = { items: AgentActivityItem[]; children?: ReactNode; pending?: boolean; autoCollapseWhenComplete?: boolean; turnStatus?: string };

export function AgentActivity({ items, children, pending = false, autoCollapseWhenComplete = false, turnStatus }: Props) {
  const groupedPhases = useMemo(() => groupActivities(items), [items]);
  const outcome = agentActivityOutcome(items, pending, turnStatus, autoCollapseWhenComplete);
  const active = outcome === "running";
  const failed = outcome === "failed";
  const phases = useMemo(() => activityPhasesForDisplay(groupedPhases, active), [active, groupedPhases]);
  const connectionStatus = useMemo(() => topLevelActivityStatus(items), [items]);
  const hasOutput = Boolean(children);
  const latestPhaseId = phases.at(-1)?.id;
  const [expanded, setExpanded] = useState(() => active || !hasOutput);
  const [collapsedPhaseIds, setCollapsedPhaseIds] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (active) setExpanded(true);
    else if (hasOutput || autoCollapseWhenComplete) setExpanded(false);
  }, [active, autoCollapseWhenComplete, hasOutput]);

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
                expanded={!collapsedPhaseIds.has(phase.id)}
                onToggle={() => setCollapsedPhaseIds((value) => {
                  const next = new Set(value);
                  if (next.has(phase.id)) next.delete(phase.id);
                  else next.add(phase.id);
                  return next;
                })}
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
  const summary = plainActivitySummary(phase.detail) || (phase.kind === "analysis" && running ? "Agent 正在分析，暂未提供可展示的摘要…" : status);
  const canCollapse = secondaryItems.length > 0;

  return (
    <Box sx={{ px: .5, py: .35 }}>
      <Stack
        component="button"
        type="button"
        direction="row"
        alignItems="center"
        spacing={1}
        aria-expanded={expanded}
        onClick={() => canCollapse && onToggle()}
        sx={{ width: "100%", border: 0, bgcolor: "transparent", color: "text.primary", p: 0, cursor: canCollapse ? "pointer" : "default", textAlign: "left" }}
      >
        {running ? <CircularProgress size={14} /> : failed ? <ErrorOutlineRounded color="error" sx={{ fontSize: 17 }} /> : <PsychologyAltRounded color="action" sx={{ fontSize: 17 }} />}
        <Typography variant="caption" fontWeight={700}>{phase.kind === "analysis" ? "分析" : "准备"}</Typography>
        {canCollapse && <KeyboardArrowDownRounded color="action" sx={{ ml: "0!important", fontSize: 17, transition: "transform .18s ease", transform: expanded ? "rotate(180deg)" : "rotate(0deg)" }} />}
        <Typography variant="caption" color="text.secondary" sx={{ minWidth: 0, flex: 1, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{summary}</Typography>
        <Typography variant="caption" color={failed ? "error.main" : "text.secondary"} sx={{ ml: "auto!important" }}>{status}</Typography>
      </Stack>
      <Collapse in={expanded && canCollapse}>
        <Box sx={{ ml: .85, mt: .55, pl: 2, borderLeft: 1, borderColor: "divider" }}>
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
        <Typography variant="caption" display="block"><Box component="span" fontWeight={700}>{item.kind === "tool" ? "工具：" : "进度："}</Box>{item.label}</Typography>
        {item.detail && <Typography variant="caption" color="text.secondary" display="block" sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.detail}</Typography>}
      </Box>
    </Stack>
  );
}
