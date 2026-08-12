import { Button, IconButton, Popover, Stack, Typography } from "@mui/material";
import type { IconButtonProps } from "@mui/material";
import type { MouseEvent, ReactNode } from "react";
import { useState } from "react";

export function MobileConfirmAction({ label, icon, onConfirm, disabled = false, color = "primary" }: { label: string; icon: ReactNode; onConfirm: () => void; disabled?: boolean; color?: IconButtonProps["color"] }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  function confirm() {
    setAnchor(null);
    onConfirm();
  }
  return <>
    <IconButton color={color} aria-label={label} aria-haspopup="dialog" aria-expanded={Boolean(anchor)} disabled={disabled} onClick={(event: MouseEvent<HTMLElement>) => setAnchor(event.currentTarget)} sx={{ display: { xs: "inline-flex", md: "none" }, width: 36, height: 36, border: 1, borderColor: color === "warning" ? "warning.main" : "divider" }}>
      {icon}
    </IconButton>
    <Popover open={Boolean(anchor)} anchorEl={anchor} onClose={() => setAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }} slotProps={{ paper: { sx: { mt: .75, p: 1 } } }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="body2" fontWeight={600}>{label}</Typography>
        <Button size="small" variant="contained" color={color === "warning" ? "warning" : "primary"} onClick={confirm}>确认</Button>
      </Stack>
    </Popover>
  </>;
}
