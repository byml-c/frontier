import { Box, Stack, Typography } from "@mui/material";
import type { CompletionSummaryStructured } from "../types";
import { MarkdownMessage } from "./MarkdownMessage";
import { completionSummarySections, type CompletionSummaryScope } from "./completionSummary";

export function CompletionSummarySections({
  summary,
  scope,
  onJump,
  emptyMessage = "尚无结构化精读总结。",
}: {
  summary?: CompletionSummaryStructured | null;
  scope: CompletionSummaryScope;
  onJump?: (page: number) => void;
  emptyMessage?: string;
}) {
  const sections = completionSummarySections(summary, scope);
  if (!sections.length) return <Typography color="text.secondary">{emptyMessage}</Typography>;

  return (
    <Stack spacing={2.25}>
      {sections.map((section) => (
        <Box key={section.key}>
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <Typography variant="subtitle2" fontWeight={750} sx={{ flexShrink: 0 }}>{section.title}</Typography>
            <Box aria-hidden sx={{ flex: 1, borderTop: 1, borderColor: "divider" }} />
          </Stack>
          <Box color="text.secondary" sx={{ mt: .75 }}>
            <MarkdownMessage content={section.content} onJump={onJump} />
          </Box>
        </Box>
      ))}
    </Stack>
  );
}
