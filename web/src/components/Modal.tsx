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
          height: { xs: "calc(100dvh - 16px)", sm: "min(760px, calc(100dvh - 64px))" },
          width: { xs: "calc(100% - 16px)", sm: "calc(100% - 64px)" },
          m: { xs: 1, sm: 4 },
          maxHeight: "none",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        } : undefined,
      }}
    >
      <DialogTitle sx={{ px: { xs: 1.5, sm: 3 }, pr: 7, py: { xs: 1.25, sm: 2 }, fontWeight: 700 }}>{title}</DialogTitle>
      <IconButton onClick={onClose} aria-label="关闭" sx={{ position: "absolute", right: 12, top: 10 }}><CloseRounded /></IconButton>
      <DialogContent dividers sx={fixedHeight ? { display: "flex", flex: "1 1 0", height: 0, flexDirection: "column", minHeight: 0, overflow: "hidden", p: { xs: 1, sm: 3 } } : undefined}>{children}</DialogContent>
    </Dialog>
  );
}
