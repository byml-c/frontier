import { Stack, TextField } from "@mui/material";

export type PaperQuickNotes = {
  model_short_name: string;
  search_aliases: string;
  user_note: string;
};

export function PaperQuickNotesFields({ value, onChange, autoFocus = false }: {
  value: PaperQuickNotes; onChange: (changes: Partial<PaperQuickNotes>) => void; autoFocus?: boolean;
}) {
  return <Stack spacing={1.5}>
    <TextField fullWidth autoFocus={autoFocus} label="模型简名" value={value.model_short_name}
      onChange={(event) => onChange({ model_short_name: event.target.value })} />
    <TextField fullWidth label="搜索别名" value={value.search_aliases}
      onChange={(event) => onChange({ search_aliases: event.target.value })} />
    <TextField fullWidth multiline minRows={3} label="我的简记" value={value.user_note}
      onChange={(event) => onChange({ user_note: event.target.value })} />
  </Stack>;
}
