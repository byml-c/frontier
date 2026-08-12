import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import CalendarMonthRounded from "@mui/icons-material/CalendarMonthRounded";
import { Card, CardActionArea, CardContent, Chip, Container, Grid, Stack, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, formatDate } from "../api";
import { BriefTabs } from "../components/BriefTabs";
import { PageHeader } from "../components/PageHeader";
import { Empty, ErrorState, Loading } from "../components/States";
import type { Brief } from "../types";

export function ArchivePage() {
  const navigate = useNavigate();
  const query = useQuery({ queryKey: ["briefs"], queryFn: () => api<Brief[]>("/api/briefs?limit=365") });
  return <Container maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}><PageHeader title="往期简报" tabs={<BriefTabs value="archive" />} />{query.isLoading ? <Loading /> : query.error ? <ErrorState error={query.error} /> : !query.data?.length ? <Empty title="暂无往期简报" body="第一期简报发布后会保存在这里。" /> : <Grid container spacing={2.5}>{query.data.map((brief) => <Grid size={{ xs: 12, md: 6 }} key={brief.id}><Card sx={{ height: "100%" }}><CardActionArea onClick={() => navigate(`/briefs/${brief.id}`)} sx={{ height: "100%" }}><CardContent sx={{ p: 1 }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Chip icon={<CalendarMonthRounded />} label={formatDate(brief.brief_date)} size="small" /><ArrowForwardRounded color="action" /></Stack><Typography variant="h2" sx={{ mt: 2 }}>{brief.title}</Typography><Typography color="text.secondary" sx={{ mt: 1 }}>{brief.introduction || "研究方向增量简报"}</Typography><Typography variant="body2" color="primary" sx={{ mt: 3 }}>{brief.item_count ?? 0} 篇论文</Typography></CardContent></CardActionArea></Card></Grid>)}</Grid>}</Container>;
}
