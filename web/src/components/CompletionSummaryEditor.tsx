import { Box, Stack, TextField, Typography } from "@mui/material";
import {
  PRIVATE_COMPLETION_SUMMARY_FIELDS,
  type CompletionSummaryDraft,
  type CompletionSummaryField,
} from "./completionSummary";

export function CompletionSummaryEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: CompletionSummaryDraft;
  onChange: (value: CompletionSummaryDraft) => void;
  disabled?: boolean;
}) {
  function update(field: CompletionSummaryField, content: string) {
    onChange({ ...value, [field]: content });
  }

  return (
    <Stack spacing={2.25}>
      {PRIVATE_COMPLETION_SUMMARY_FIELDS.map(([field, title]) => (
        <Box key={field}>
          <Stack direction="row" alignItems="center" spacing={1.25}>
            <Typography variant="subtitle2" fontWeight={750} sx={{ flexShrink: 0 }}>{title}</Typography>
            <Box aria-hidden sx={{ flex: 1, borderTop: 1, borderColor: "divider" }} />
          </Stack>
          <TextField
            fullWidth
            multiline
            minRows={3}
            maxRows={10}
            value={value[field]}
            disabled={disabled}
            error={!value[field].trim()}
            helperText={!value[field].trim() ? "此项不能为空" : "支持 Markdown 和页码引用"}
            onChange={(event) => update(field, event.target.value)}
            inputProps={{ "aria-label": title }}
            sx={{ mt: .75 }}
          />
        </Box>
      ))}
    </Stack>
  );
}
