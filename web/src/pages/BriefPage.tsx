import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import { Button, Container, Stack, Tab, Tabs, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { BriefTabs } from "../components/BriefTabs";
import { PageHeader } from "../components/PageHeader";
import { PaperCard } from "../components/PaperCard";
import { Empty, ErrorState, Loading } from "../components/States";
import type { Brief } from "../types";

type ViewTab = "all" | "top" | "pending" | "rated";

export function BriefPage() {
  const { briefId } = useParams();
  const queryClient = useQueryClient();
  const [view, setView] = useState<ViewTab>("all");
  const brief = useQuery({
    queryKey: ["brief", briefId ?? "latest"],
    queryFn: async () => {
      if (briefId) return api<Brief>(`/api/briefs/${briefId}`);
      try { return await api<Brief>("/api/briefs/latest"); }
      catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }
    },
    retry: false,
    refetchInterval: (query) => query.state.data?.items.some((item) => item.paper.abstract_translation_status === "queued") ? 3000 : false,
  });
  const run = useMutation({
    mutationFn: () => api<{ id: string }>("/api/tasks/run-daily", { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tasks"] }),
  });
  if (brief.isLoading) return <Container sx={{ py: 6 }}><PageHeader title="研究简报" tabs={<BriefTabs value={briefId ? "archive" : "today"} />} /><Loading label="正在打开简报" /></Container>;
  if (!brief.data && !brief.error) return <Container sx={{ py: 6 }}><PageHeader title="研究简报" tabs={<BriefTabs value="today" />} /><Empty title="还没有简报" body="初始化论文库后，可以生成第一期研究简报。" action={<Button variant="contained" startIcon={<PlayArrowRounded />} onClick={() => run.mutate()} disabled={run.isPending}>生成简报</Button>} /></Container>;
  if (brief.error) return <Container sx={{ py: 6 }}><PageHeader title="研究简报" tabs={<BriefTabs value={briefId ? "archive" : "today"} />} /><ErrorState error={brief.error} /></Container>;
  const data = brief.data!;
  const ordered = [...data.items].sort((a, b) => (a.rank || 999) - (b.rank || 999));
  const visible = ordered.filter((item) => {
    if (view === "top") return item.rank > 0 && item.rank <= 10;
    if (view === "pending") return item.rank > 10;
    if (view === "rated") return Boolean(item.paper.screening_feedback || item.paper.screening_quality_level || item.paper.screening_interest_level);
    return true;
  });

  return <Container maxWidth="lg" sx={{ py: { xs: 4, md: 5 } }}>
    <PageHeader title={data.title} tabs={<BriefTabs value={briefId ? "archive" : "today"} />} />
    <Stack spacing={1} sx={{ mb: 2 }}>
      <Typography variant="body2" color="text.secondary">共 {data.items.length} 篇论文，前 10 篇附详细分析。</Typography>
      <Tabs value={view} onChange={(_, next: ViewTab) => setView(next)} variant="scrollable" scrollButtons="auto" aria-label="日报筛选" sx={{ borderBottom: 1, borderColor: "divider" }}>
        <Tab value="all" label={`全部 ${data.items.length}`} />
        <Tab value="top" label="前 10 篇" />
        <Tab value="pending" label="其余候选" />
        <Tab value="rated" label={`已评分 ${ordered.filter((item) => item.paper.screening_feedback || item.paper.screening_quality_level || item.paper.screening_interest_level).length}`} />
      </Tabs>
    </Stack>
    {!visible.length && <Empty title={view === "rated" ? "还没有评分记录" : "暂无论文"} body={view === "rated" ? "点击论文下方的质量或相关度标签，记录你的偏好。" : "切换其他分类继续阅读。"} />}
    <Stack spacing={1.5}>
      {visible.map((item) => (
        <PaperCard
          key={item.id}
          briefId={data.id}
          briefItemId={item.id}
          paper={{ ...item.paper, status: item.current_status, interest_level: item.interest_level, quality_level: item.quality_level, is_interested: item.is_interested, is_quality: item.is_quality }}
          rank={item.rank}
          featured={item.tier === "featured"}
          preprocessed={item.rank > 0 && item.rank <= 10}
        />
      ))}
    </Stack>
  </Container>;
}
