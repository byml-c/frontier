import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import { Box, Collapse, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { useState } from "react";
import type { Paper } from "../types";

/** Compact author line with an optional Agent-generated group summary. */
export function PaperAuthorSummary({ paper }: { paper: Pick<Paper, "research_group_summary"> }) {
  const [open, setOpen] = useState(false);
  const summary = paper.research_group_summary?.trim();
  if (!summary) return null;

  return (
    <Box sx={{ mt: 0.25 }}>
      <Stack direction="row" alignItems="center" spacing={0.25}>
        <Tooltip title={open ? "收起单位与课题组" : "展开单位与课题组"}>
          <IconButton
            size="small"
            aria-label={open ? "收起单位与课题组" : "展开单位与课题组"}
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
            sx={{ p: 0.25 }}
          >
            <ExpandMoreRounded sx={{ transition: "transform .2s", transform: open ? "rotate(180deg)" : "none" }} />
          </IconButton>
        </Tooltip>
        <Typography variant="caption" color="text.secondary">单位与课题组</Typography>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.25, pl: 3 }}>
          {summary}
        </Typography>
      </Collapse>
    </Box>
  );
}
