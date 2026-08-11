import { Box, Card, CardContent, Stack, Typography } from "@mui/material";
import type { ReactNode } from "react";

export function PageHeader({ title, description, actions }: { eyebrow?: string; title: string; description?: string; actions?: ReactNode }) {
  return <Card component="header" sx={{ mb: 4 }}><CardContent sx={{ p: { xs: .5, md: 1.5 } }}><Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "stretch", sm: "flex-start" }} spacing={2}><Box><Typography variant="h1">{title}</Typography>{description && <Typography color="text.secondary" sx={{ mt: 1, maxWidth: 720 }}>{description}</Typography>}</Box>{actions && <Stack direction="row" spacing={1}>{actions}</Stack>}</Stack></CardContent></Card>;
}
