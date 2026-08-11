import CheckCircleOutlineRounded from "@mui/icons-material/CheckCircleOutlineRounded";
import ConstructionRounded from "@mui/icons-material/ConstructionRounded";
import ErrorOutlineRounded from "@mui/icons-material/ErrorOutlineRounded";
import PsychologyAltRounded from "@mui/icons-material/PsychologyAltRounded";
import { Box, CircularProgress, Collapse, Stack, Typography } from "@mui/material";
import { useState } from "react";
import { templateSurfaces } from "../theme";
import type { AgentActivityItem } from "./agentActivityState";

export function AgentActivity({ items }: { items: AgentActivityItem[] }) {
  const [expanded, setExpanded] = useState(true);
  if (!items.length) return null;
  const running = items.filter((item) => item.status === "running").length;
  const latestRunning = [...items].reverse().find((item) => item.status === "running");
  return (
    <Box sx={{ ...templateSurfaces.assistantMessage, borderRadius: 2, overflow: "hidden" }}>
      <Stack
        component="button"
        type="button"
        direction="row"
        alignItems="center"
        spacing={1}
        onClick={() => setExpanded((value) => !value)}
        sx={{ width: "100%", border: 0, bgcolor: "transparent", color: "text.primary", px: 1.5, py: 1.1, cursor: "pointer", textAlign: "left" }}
      >
        {running ? <CircularProgress size={15} /> : <CheckCircleOutlineRounded color="success" sx={{ fontSize: 18 }} />}
        <Typography variant="body2" fontWeight={650} sx={{ flex: 1 }}>
          {running ? latestRunning?.label : `已完成 ${items.length} 个步骤`}
        </Typography>
        <Typography variant="caption" color="text.secondary">{expanded ? "收起" : "查看过程"}</Typography>
      </Stack>
      <Collapse in={expanded}>
        <Stack spacing={1} sx={{ px: 1.5, pb: 1.35 }}>
          {items.map((item) => (
            <Stack key={item.id} direction="row" spacing={1} alignItems="flex-start">
              {item.status === "running" ? <CircularProgress size={14} sx={{ mt: .3 }} /> : item.status === "failed" ? <ErrorOutlineRounded color="error" sx={{ fontSize: 16, mt: .15 }} /> : item.kind === "tool" ? <ConstructionRounded color="action" sx={{ fontSize: 16, mt: .15 }} /> : <PsychologyAltRounded color="action" sx={{ fontSize: 16, mt: .15 }} />}
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="caption" fontWeight={600} display="block">{item.label}</Typography>
                {item.detail && <Typography variant="caption" color="text.secondary" display="block" sx={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.detail}</Typography>}
              </Box>
            </Stack>
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
}
