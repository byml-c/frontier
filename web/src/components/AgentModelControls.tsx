import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import CheckRounded from "@mui/icons-material/CheckRounded";
import { Box, Button, ListSubheader, Menu, MenuItem, Typography } from "@mui/material";
import { useState } from "react";
import { reasoningEffortLabel } from "./agentRuntimeState";
import { normalizeCodexSelection } from "./codexSelection";
import type { CodexSelectionControl } from "./useCodexSelection";

export function AgentModelControls({ control, disabled }: { control: CodexSelectionControl; disabled: boolean }) {
  const [modelAnchor, setModelAnchor] = useState<HTMLElement | null>(null);
  const [effortAnchor, setEffortAnchor] = useState<HTMLElement | null>(null);
  const { catalog, selection, onChange } = control;
  const backend = catalog?.backends.find((item) => item.name === selection?.backend);
  const model = backend?.models.find((item) => item.id === selection?.model);
  const buttonStyle = { color: "text.secondary", fontSize: 12, minWidth: 0, px: .75, py: .25, textTransform: "none", borderRadius: 1.5, whiteSpace: "nowrap", "& .MuiButton-endIcon": { ml: .25 } } as const;
  return <Box sx={{ display: "flex", alignItems: "center", minWidth: 0 }}>
    <Button size="small" aria-label="选择模型" aria-haspopup="menu" aria-expanded={Boolean(modelAnchor)} disabled={disabled || !selection} onClick={(event) => setModelAnchor(event.currentTarget)} endIcon={<ExpandMoreRounded sx={{ fontSize: 15 }} />} sx={{ ...buttonStyle, minWidth: 0, maxWidth: { xs: 160, sm: 220 } }} title={control.error ?? backend?.name}>
      <Box component="span" sx={{ overflow: "hidden", textOverflow: "ellipsis" }}>{model?.name ?? (control.error ? "模型不可用" : "加载模型…")}</Box>
    </Button>
    <Menu anchorEl={modelAnchor} open={Boolean(modelAnchor)} onClose={() => setModelAnchor(null)} slotProps={{ paper: { sx: { maxHeight: 420, minWidth: 240 } } }}>
      {catalog?.backends.flatMap((route) => [
        <ListSubheader key={`${route.name}-header`} sx={{ lineHeight: "32px", fontSize: 11, bgcolor: "background.paper" }}>{route.name}</ListSubheader>,
        ...(route.available ? route.models.map((option) => <MenuItem key={JSON.stringify([route.name, option.id])} selected={selection?.backend === route.name && selection?.model === option.id} onClick={() => {
          const next = normalizeCodexSelection(catalog, { backend: route.name, model: option.id, reasoning_effort: selection?.reasoning_effort });
          if (next) onChange(next);
          setModelAnchor(null);
        }} sx={{ gap: 2, fontSize: 13, justifyContent: "space-between" }}>{option.name}{selection?.backend === route.name && selection.model === option.id && <CheckRounded sx={{ fontSize: 16 }} />}</MenuItem>) : [<MenuItem disabled key={`${route.name}-unavailable`}><Typography variant="caption">未配置</Typography></MenuItem>]),
      ])}
    </Menu>
    {Boolean(model?.reasoning_efforts.length) && <>
      <Button size="small" aria-label="选择思考强度" aria-haspopup="menu" aria-expanded={Boolean(effortAnchor)} disabled={disabled} onClick={(event) => setEffortAnchor(event.currentTarget)} endIcon={<ExpandMoreRounded sx={{ fontSize: 15 }} />} sx={buttonStyle}>{reasoningEffortLabel(selection?.reasoning_effort ?? undefined)}</Button>
      <Menu anchorEl={effortAnchor} open={Boolean(effortAnchor)} onClose={() => setEffortAnchor(null)}>{model?.reasoning_efforts.map((effort) => <MenuItem key={effort} selected={selection?.reasoning_effort === effort} onClick={() => { if (selection) onChange({ ...selection, reasoning_effort: effort }); setEffortAnchor(null); }} sx={{ fontSize: 13 }}>{reasoningEffortLabel(effort)}</MenuItem>)}</Menu>
    </>}
  </Box>;
}
