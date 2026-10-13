import { ArrowOutwardRounded, SearchRounded } from "@mui/icons-material";
import { Alert, Box, Button, Card, CardContent, Chip, Container, Divider, Grid, InputAdornment, Link, Stack, TextField, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorState, Loading } from "../components/States";
import { PageHeader } from "../components/PageHeader";
import type { ResearchGroup } from "../types";

export function ResearchGroupsPage() {
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const groups = useQuery({
    queryKey: ["research-groups", search],
    queryFn: () => api<ResearchGroup[]>(`/api/research-groups?q=${encodeURIComponent(search)}`),
  });
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Record<string, unknown> }) => api<ResearchGroup>(`/api/research-groups/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ["research-groups"] });
      queryClient.invalidateQueries({ queryKey: ["research-group", variables.id] });
    },
  });
  const selected = useQuery({
    queryKey: ["research-group", selectedId],
    queryFn: () => api<ResearchGroup>(`/api/research-groups/${selectedId}`),
    enabled: Boolean(selectedId),
  });

  return <Container maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}>
    <PageHeader title="课题组" description="按课题组沉淀代表性工作、研究方向和同行信息。" />
    <TextField fullWidth size="small" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索课题组或机构" InputProps={{ startAdornment: <InputAdornment position="start"><SearchRounded /></InputAdornment> }} sx={{ mb: 2.5 }} />
    {groups.isLoading ? <Loading /> : groups.error ? <ErrorState error={groups.error} /> : !groups.data?.length ? <Empty title="还没有课题组档案" body="让 Agent 生成论文信息后，确认过的单位与课题组会自动沉淀到这里。" /> : <Grid container spacing={2.5}>
      <Grid size={{ xs: 12, md: 5 }}><Stack spacing={1}>{groups.data.map((group) => <Card key={group.id} variant={selectedId === group.id ? "elevation" : "outlined"} sx={{ cursor: "pointer", borderColor: selectedId === group.id ? "primary.main" : undefined }} onClick={() => setSelectedId(group.id)}><CardContent><Stack direction="row" justifyContent="space-between" gap={1}><Box><Typography variant="h3">{group.name}</Typography><Typography variant="body2" color="text.secondary">{group.institution || "机构待确认"}</Typography></Box><Chip size="small" label={`${group.paper_count} 篇`} /></Stack><Stack direction="row" flexWrap="wrap" gap={.5} sx={{ mt: 1 }}>{group.research_focus.slice(0, 4).map((focus) => <Chip key={focus} size="small" variant="outlined" label={focus} />)}</Stack><Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>{group.verification_status === "verified" ? "已确认" : "待确认"} · 置信度 {group.confidence}</Typography></CardContent></Card>)}</Stack></Grid>
      <Grid size={{ xs: 12, md: 7 }}>{selected.isLoading ? <Loading label="正在读取课题组档案" /> : selected.error ? <Alert severity="error">{selected.error.message}</Alert> : selected.data ? <GroupDetail group={selected.data} saving={update.isPending} onSave={(payload) => update.mutate({ id: selected.data!.id, payload })} /> : <Empty title="选择一个课题组" body="查看研究方向和代表性工作。" />}</Grid>
    </Grid>}
  </Container>;
}

function GroupDetail({ group, saving, onSave }: { group: ResearchGroup; saving: boolean; onSave: (payload: Record<string, unknown>) => void }) {
  const [summary, setSummary] = useState(group.representative_summary || "");
  const [focus, setFocus] = useState(group.research_focus.join("、"));
  useEffect(() => { setSummary(group.representative_summary || ""); setFocus(group.research_focus.join("、")); }, [group]);
  return <Card><CardContent><Stack spacing={2}><Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={2}><Box><Typography variant="h2">{group.name}</Typography><Typography color="text.secondary">{group.institution || "机构待确认"}</Typography></Box>{group.homepage_url && <Link href={group.homepage_url} target="_blank" rel="noreferrer" underline="hover">主页 <ArrowOutwardRounded sx={{ fontSize: 15, verticalAlign: "middle" }} /></Link>}</Stack><TextField label="课题组概览" multiline minRows={3} value={summary} onChange={(event) => setSummary(event.target.value)} /><TextField label="研究方向（用顿号分隔）" value={focus} onChange={(event) => setFocus(event.target.value)} /><Button variant="outlined" disabled={saving} onClick={() => onSave({ representative_summary: summary.trim() || null, research_focus: focus.split(/[、,，\n]+/).map((item) => item.trim()).filter(Boolean) })}>{saving ? "保存中…" : "保存课题组档案"}</Button><Divider /><Box><Typography variant="subtitle2">代表性研究主题</Typography><Stack spacing={.75} sx={{ mt: 1 }}>{group.representative_contributions.map((item, index) => <Box key={`${item.topic || "topic"}-${index}`}><Typography variant="body2" fontWeight={650}>{item.topic || "未命名主题"}</Typography>{item.summary && <Typography variant="body2" color="text.secondary">{item.summary}</Typography>}</Box>)}</Stack></Box><Divider /><Box><Typography variant="subtitle2">代表性工作与关联论文</Typography><Stack spacing={1} sx={{ mt: 1 }}>{group.papers?.map((paper) => <Box key={paper.id}><Typography variant="body2" fontWeight={paper.is_representative ? 700 : 400}>{paper.title || paper.id}{paper.is_representative ? " · 代表作" : ""}</Typography>{paper.contribution_summary && <Typography variant="body2" color="text.secondary">{paper.contribution_summary}</Typography>}<Typography component={RouterLink} to={`/paper/${paper.id}`} variant="caption" color="primary">打开论文</Typography></Box>)}</Stack></Box></Stack></CardContent></Card>;
}
