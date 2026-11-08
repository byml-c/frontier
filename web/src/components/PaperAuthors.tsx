import { Link, Stack, TextField, Typography } from "@mui/material";
import type { ResearchGroup } from "../types";

export function PaperAuthors({ value, onChange, group }: {
  value: string; onChange: (value: string) => void; group?: ResearchGroup | null;
}) {
  return <Stack spacing={1}>
    <TextField label="作者" name="authors" size="small" fullWidth multiline minRows={2} maxRows={6}
      value={value} onChange={(event) => onChange(event.target.value)} />
    {group && <Stack direction="row" spacing={1} alignItems="baseline" flexWrap="wrap">
      <Typography variant="caption" color="text.secondary">课题组</Typography>
      <Link href="/research-groups" underline="hover" fontSize=".875rem">{group.name}</Link>
      <Typography variant="caption" color="text.secondary">{group.institution}</Typography>
    </Stack>}
  </Stack>;
}
