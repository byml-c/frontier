import CloseRounded from "@mui/icons-material/CloseRounded";
import { Dialog, DialogContent, DialogTitle, IconButton } from "@mui/material";
import type { ReactNode } from "react";

export function Modal({ open, title, children, onClose, wide = false, fixedHeight = false }: { open: boolean; title: string; children: ReactNode; onClose: () => void; wide?: boolean; fixedHeight?: boolean }) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth={wide ? "md" : "sm"}
      PaperProps={{
        sx: fixedHeight ? {
          height: { xs: "calc(100dvh - 24px)", sm: "min(760px, calc(100dvh - 64px))" },
          maxHeight: "none",
        } : undefined,
      }}
    >
      <DialogTitle sx={{ pr: 7, fontWeight: 700 }}>{title}</DialogTitle>
      <IconButton onClick={onClose} aria-label="关闭" sx={{ position: "absolute", right: 12, top: 10 }}><CloseRounded /></IconButton>
      <DialogContent dividers sx={fixedHeight ? { display: "flex", flexDirection: "column", minHeight: 0 } : undefined}>{children}</DialogContent>
    </Dialog>
  );
}
