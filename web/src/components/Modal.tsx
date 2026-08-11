import CloseRounded from "@mui/icons-material/CloseRounded";
import { Dialog, DialogContent, DialogTitle, IconButton } from "@mui/material";
import type { ReactNode } from "react";

export function Modal({ open, title, children, onClose, wide = false }: { open: boolean; title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth={wide ? "md" : "sm"}>
      <DialogTitle sx={{ pr: 7, fontWeight: 700 }}>{title}</DialogTitle>
      <IconButton onClick={onClose} aria-label="关闭" sx={{ position: "absolute", right: 12, top: 10 }}><CloseRounded /></IconButton>
      <DialogContent dividers>{children}</DialogContent>
    </Dialog>
  );
}
