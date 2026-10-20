import { ArrowOutwardRounded, CloseRounded, SearchRounded } from "@mui/icons-material";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Container,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  Grid,
  IconButton,
  InputAdornment,
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
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
    mutationFn: ({ id, payload }: { id: string; payload: Record<string, unknown> }) =>
      api<ResearchGroup>(`/api/research-groups/${id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
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

  return (
    <Container maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}>
      <PageHeader
        title="课题组"
        description="按课题组沉淀代表性工作、研究方向和同行信息。"
      />
      <TextField
        fullWidth
        size="small"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="搜索课题组或机构"
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <SearchRounded />
            </InputAdornment>
          ),
        }}
        sx={{ mb: 2.5 }}
      />
      {groups.isLoading ? (
        <Loading />
      ) : groups.error ? (
        <ErrorState error={groups.error} />
      ) : !groups.data?.length ? (
        <Empty
          title="还没有课题组档案"
          body="入库 Agent 会搜索已有组，确认或创建课题组后自动沉淀到这里。"
        />
      ) : (
        <Grid container spacing={2.5}>
          {groups.data.map((group) => (
            <Grid key={group.id} size={{ xs: 12, sm: 6, md: 4 }}>
              <Card
                variant="outlined"
                onClick={() => setSelectedId(group.id)}
                sx={{
                  cursor: "pointer",
                  height: "100%",
                  transition: "border-color .15s, box-shadow .15s",
                  "&:hover": { borderColor: "primary.main", boxShadow: 2 },
                }}
              >
                <CardContent>
                  <Stack direction="row" justifyContent="space-between" gap={1}>
                    <Box sx={{ minWidth: 0 }}>
                      <Typography variant="h6" noWrap>
                        {group.name}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" noWrap>
                        {group.institution || "机构待确认"}
                      </Typography>
                    </Box>
                    <Chip size="small" label={`${group.paper_count} 篇`} />
                  </Stack>
                  <Stack direction="row" flexWrap="wrap" gap={0.5} sx={{ mt: 1.5 }}>
                    {group.research_focus.slice(0, 4).map((focus) => (
                      <Chip key={focus} size="small" variant="outlined" label={focus} />
                    ))}
                  </Stack>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    display="block"
                    sx={{ mt: 1.5 }}
                  >
                    {group.verification_status === "verified" ? "已确认" : "待确认"} · 置信度{" "}
                    {group.confidence}
                  </Typography>
                </CardContent>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}

      <Dialog
        open={Boolean(selectedId)}
        onClose={() => setSelectedId(null)}
        fullWidth
        maxWidth="md"
      >
        <DialogTitle sx={{ pr: 7 }}>
          {selected.data?.name || "课题组详情"}
          <IconButton
            aria-label="关闭课题组详情"
            onClick={() => setSelectedId(null)}
            sx={{ position: "absolute", right: 12, top: 12 }}
          >
            <CloseRounded />
          </IconButton>
        </DialogTitle>
        <DialogContent dividers>
          {selected.isLoading ? (
            <Loading label="正在读取课题组档案" />
          ) : selected.error ? (
            <Alert severity="error">{selected.error.message}</Alert>
          ) : selected.data ? (
            <GroupDetail
              group={selected.data}
              saving={update.isPending}
              onSave={(payload) => update.mutate({ id: selected.data!.id, payload })}
            />
          ) : null}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelectedId(null)}>关闭</Button>
        </DialogActions>
      </Dialog>
    </Container>
  );
}

function GroupDetail({
  group,
  saving,
  onSave,
}: {
  group: ResearchGroup;
  saving: boolean;
  onSave: (payload: Record<string, unknown>) => void;
}) {
  const [summary, setSummary] = useState(group.representative_summary || "");
  const [focus, setFocus] = useState(group.research_focus.join("、"));
  useEffect(() => {
    setSummary(group.representative_summary || "");
    setFocus(group.research_focus.join("、"));
  }, [group]);

  return (
    <Stack spacing={2.5}>
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={2}>
        <Box>
          <Typography variant="h5">{group.name}</Typography>
          <Typography color="text.secondary">{group.institution || "机构待确认"}</Typography>
        </Box>
        {group.homepage_url && (
          <Link href={group.homepage_url} target="_blank" rel="noreferrer" underline="hover">
            主页 <ArrowOutwardRounded sx={{ fontSize: 15, verticalAlign: "middle" }} />
          </Link>
        )}
      </Stack>

      <Stack direction="row" flexWrap="wrap" gap={0.75}>
        <Chip size="small" label={`${group.paper_count} 篇论文`} />
        <Chip
          size="small"
          variant="outlined"
          label={group.verification_status === "verified" ? "已确认" : "待确认"}
        />
        <Chip size="small" variant="outlined" label={`置信度 ${group.confidence}`} />
      </Stack>

      <TextField
        label="课题组概览"
        multiline
        minRows={3}
        value={summary}
        onChange={(event) => setSummary(event.target.value)}
      />
      <TextField
        label="研究方向（用顿号分隔）"
        value={focus}
        onChange={(event) => setFocus(event.target.value)}
      />
      <Box>
        <Button
          variant="outlined"
          disabled={saving}
          onClick={() =>
            onSave({
              representative_summary: summary.trim() || null,
              research_focus: focus
                .split(/[、,，\n]+/)
                .map((item) => item.trim())
                .filter(Boolean),
            })
          }
        >
          {saving ? "保存中…" : "保存课题组档案"}
        </Button>
      </Box>

      <Divider />
      <Section title="代表性研究主题">
        {group.representative_contributions.length ? (
          group.representative_contributions.map((item, index) => (
            <Box key={`${item.topic || "topic"}-${index}`}>
              <Typography variant="body2" fontWeight={650}>
                {item.topic || "未命名主题"}
              </Typography>
              {item.summary && (
                <Typography variant="body2" color="text.secondary">
                  {item.summary}
                </Typography>
              )}
            </Box>
          ))
        ) : (
          <Typography variant="body2" color="text.secondary">
            暂无代表性主题。
          </Typography>
        )}
      </Section>

      <Divider />
      <Section title="关联论文">
        {group.papers?.length ? (
          <Stack spacing={1.25}>
            {group.papers.map((paper) => (
              <Box key={paper.id}>
                <Typography variant="body2" fontWeight={paper.is_representative ? 700 : 400}>
                  {paper.title || paper.id}
                  {paper.is_representative ? " · 代表作" : ""}
                </Typography>
                <Stack direction="row" spacing={1} alignItems="center">
                  {paper.published_at && (
                    <Typography variant="caption" color="text.secondary">
                      {paper.published_at}
                    </Typography>
                  )}
                  <Typography
                    component={RouterLink}
                    to={`/paper/${paper.id}`}
                    variant="caption"
                    color="primary"
                  >
                    打开论文
                  </Typography>
                </Stack>
                {paper.contribution_summary && (
                  <Typography variant="body2" color="text.secondary">
                    {paper.contribution_summary}
                  </Typography>
                )}
              </Box>
            ))}
          </Stack>
        ) : (
          <Typography variant="body2" color="text.secondary">
            暂无关联论文。
          </Typography>
        )}
      </Section>
    </Stack>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        {title}
      </Typography>
      <Stack spacing={0.75}>{children}</Stack>
    </Box>
  );
}
