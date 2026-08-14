import PlayArrowRounded from "@mui/icons-material/PlayArrowRounded";
import { Box, Button, Container, Divider, Stack, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { BriefTabs } from "../components/BriefTabs";
import { PageHeader } from "../components/PageHeader";
import { PaperCard } from "../components/PaperCard";
import { Empty, ErrorState, Loading } from "../components/States";
import type { Brief } from "../types";

export function BriefPage() {
  const { briefId } = useParams();
  const queryClient = useQueryClient();
  const brief = useQuery({ queryKey: ["brief", briefId ?? "latest"], queryFn: async () => { if (briefId) return api<Brief>(`/api/briefs/${briefId}`); try { return await api<Brief>("/api/briefs/latest"); } catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; } }, retry: false });
  const run = useMutation({ mutationFn: () => api<{ id: string }>("/api/tasks/run-daily", { method: "POST" }), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tasks"] }) });
  if (brief.isLoading) return <Container sx={{ py: 6 }}><PageHeader title="研究简报" tabs={<BriefTabs value={briefId ? "archive" : "today"} />} /><Loading label="正在打开简报" /></Container>;
  if (!brief.data && !brief.error) return <Container sx={{ py: 6 }}><PageHeader title="研究简报" tabs={<BriefTabs value="today" />} /><Empty title="还没有简报" body="初始化论文库后，可以生成第一期研究简报。" action={<Button variant="contained" startIcon={<PlayArrowRounded />} onClick={() => run.mutate()} disabled={run.isPending}>生成简报</Button>} /></Container>;
  if (brief.error) return <Container sx={{ py: 6 }}><PageHeader title="研究简报" tabs={<BriefTabs value={briefId ? "archive" : "today"} />} /><ErrorState error={brief.error} /></Container>;
  const data = brief.data!;
  const featured = data.items.filter((item) => item.tier === "featured");
  const manual = data.items.filter((item) => item.tier === "manual");
  const related = data.items.filter((item) => !["featured", "manual"].includes(item.tier));
  const groups = related.reduce<Map<string, typeof related>>((result, item) => { const key = item.paper.nodes?.[0]?.name || "其他相关方向"; result.set(key, [...(result.get(key) ?? []), item]); return result; }, new Map());
  return <Container maxWidth="lg" sx={{ py: { xs: 4, md: 5 } }}>
    <PageHeader
      title={data.title}
      tabs={<BriefTabs value={briefId ? "archive" : "today"} />}
    />
    {featured.length > 0 && <Section title="重点推荐" description="优先考虑研究价值和与你当前方向的关联"><Stack spacing={1.5}>{featured.map((item) => <PaperCard key={item.id} paper={{ ...item.paper, status: item.current_status, is_interested: item.is_interested, is_quality: item.is_quality }} rank={item.rank} featured />)}</Stack></Section>}
    {manual.length > 0 && <Section title="手动添加" description="已加入今日推荐，不占每日推荐篇数"><Stack spacing={1.5}>{manual.map((item) => <PaperCard key={item.id} paper={{ ...item.paper, status: item.current_status, is_interested: item.is_interested, is_quality: item.is_quality }} />)}</Stack></Section>}
    {[...groups.entries()].map(([group, items]) => <Section title={group} key={group}><Stack spacing={1.5}>{items.map((item) => <PaperCard key={item.id} paper={{ ...item.paper, status: item.current_status, is_interested: item.is_interested, is_quality: item.is_quality }} rank={item.rank} />)}</Stack></Section>)}
  </Container>;
}

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) { return <Box component="section" sx={{ mb: 4 }}><Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ xs: "flex-start", sm: "baseline" }} sx={{ mb: 1 }}><Typography variant="h2">{title}</Typography>{description && <Typography variant="body2" color="text.secondary">{description}</Typography>}</Stack><Divider sx={{ mb: 1.5 }} />{children}</Box>; }
