import ErrorOutlineRounded from "@mui/icons-material/ErrorOutlineRounded";
import InboxRounded from "@mui/icons-material/InboxRounded";
import { Alert, Box, Card, CircularProgress, Stack, Typography } from "@mui/material";
import type { ReactNode } from "react";

export function Loading({ label = "正在加载" }: { label?: string }) {
  return <Stack alignItems="center" justifyContent="center" spacing={1.5} sx={{ minHeight: 180, color: "text.secondary" }}><CircularProgress size={26} /><Typography variant="body2">{label}</Typography></Stack>;
}

export function Empty({ title, body, action }: { title: string; body: string; action?: ReactNode }) {
  return <Card><Stack alignItems="center" textAlign="center" spacing={1.25} sx={{ py: 6, px: 1 }}><Box sx={{ width: 52, height: 52, borderRadius: 3, bgcolor: "action.hover", display: "grid", placeItems: "center", color: "text.secondary" }}><InboxRounded /></Box><Typography variant="h3">{title}</Typography><Typography color="text.secondary" sx={{ maxWidth: 480 }}>{body}</Typography>{action && <Box sx={{ pt: 1 }}>{action}</Box>}</Stack></Card>;
}

export function ErrorState({ error }: { error: unknown }) {
  return <Alert severity="error" icon={<ErrorOutlineRounded />} sx={{ my: 3 }}><strong>无法加载内容</strong><br />{error instanceof Error ? error.message : String(error)}</Alert>;
}
