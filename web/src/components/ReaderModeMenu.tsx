import AccountTreeRounded from "@mui/icons-material/AccountTreeRounded";
import KeyboardArrowDownRounded from "@mui/icons-material/KeyboardArrowDownRounded";
import PictureAsPdfRounded from "@mui/icons-material/PictureAsPdfRounded";
import TextFieldsRounded from "@mui/icons-material/TextFieldsRounded";
import { Box, IconButton, ListItemIcon, ListItemText, Menu, MenuItem, Tooltip } from "@mui/material";
import { useState, type ReactNode } from "react";
import { compactReaderControlSx, floatingReaderControlSx, readerControlSx } from "./readerToolbarStyles";

export type ReaderMode = "text" | "pdf" | "architecture";

const MODE_OPTIONS: Array<{ value: ReaderMode; label: string; icon: ReactNode }> = [
  { value: "text", label: "文本模式", icon: <TextFieldsRounded fontSize="small" /> },
  { value: "pdf", label: "PDF 模式", icon: <PictureAsPdfRounded fontSize="small" /> },
  { value: "architecture", label: "架构模式", icon: <AccountTreeRounded fontSize="small" /> },
];

export function ReaderModeMenu({ value, compact = false, floating = false, onChange }: { value: ReaderMode; compact?: boolean; floating?: boolean; onChange: (value: ReaderMode) => void }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const current = MODE_OPTIONS.find((option) => option.value === value) ?? MODE_OPTIONS[0];
  const controlSx = floating ? floatingReaderControlSx : compact ? compactReaderControlSx : readerControlSx;

  return <>
    <Tooltip title={`阅读模式：${current.label}`}>
      <IconButton aria-label={`选择阅读模式，当前为${current.label}`} aria-haspopup="menu" aria-expanded={Boolean(anchor)} onClick={(event) => setAnchor(event.currentTarget)} sx={{ ...controlSx, position: "relative" }}>
        {current.icon}
        <KeyboardArrowDownRounded sx={{ position: "absolute", right: -1, bottom: -1, fontSize: 13, bgcolor: "background.paper", borderRadius: "50%" }} />
      </IconButton>
    </Tooltip>
    <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)} MenuListProps={{ "aria-label": "选择阅读模式", sx: { p: .75 } }} slotProps={{ paper: { sx: { mt: .75, width: 132, borderRadius: 2, boxShadow: "0 8px 28px rgba(15,23,42,.14)" } } }} anchorOrigin={{ vertical: "bottom", horizontal: "left" }} transformOrigin={{ vertical: "top", horizontal: "left" }}>
      {MODE_OPTIONS.map((option) => <MenuItem selected={option.value === value} onClick={() => { setAnchor(null); if (option.value !== value) onChange(option.value); }} key={option.value} sx={{ minHeight: 42, px: 1, py: .5, borderRadius: 1.25, justifyContent: "center", "& + &": { mt: .25 } }}>
        <ListItemIcon sx={{ minWidth: "0!important", mr: 1 }}><Box sx={{ width: 28, height: 28, borderRadius: "50%", bgcolor: option.value === value ? "rgba(0,111,230,.09)" : "transparent", color: option.value === value ? "primary.main" : "text.secondary", display: "grid", placeItems: "center" }}>{option.icon}</Box></ListItemIcon>
        <ListItemText primary={option.label} sx={{ flex: "0 0 auto" }} primaryTypographyProps={{ variant: "body2", fontWeight: option.value === value ? 650 : 450 }} />
      </MenuItem>)}
    </Menu>
  </>;
}
