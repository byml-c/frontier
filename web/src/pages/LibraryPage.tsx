import AddRounded from "@mui/icons-material/AddRounded";
import CloudUploadRounded from "@mui/icons-material/CloudUploadRounded";
import HubRounded from "@mui/icons-material/HubRounded";
import MenuBookRounded from "@mui/icons-material/MenuBookRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Container,
  FormControl,
  InputAdornment,
  InputLabel,
  Link as MuiLink,
  MenuItem,
  Select,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useRef, useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import { api, formatDate, statusLabel } from "../api";
import { Modal } from "../components/Modal";
import { PageHeader } from "../components/PageHeader";
import { PaperMetadataDialog } from "../components/PaperMetadataDialog";
import { Empty, ErrorState, Loading } from "../components/States";
import { ExpandablePaperText } from "../components/ExpandablePaperText";
import type { Paper, Tag } from "../types";

type LibraryTab = "papers" | "collection";

const statusColors: Record<string, "default" | "primary" | "warning" | "success"> = {
  candidate: "default",
  reading: "primary",
  feedback_pending: "warning",
  completed: "success",
  ignored: "default",
};

export function LibraryPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<LibraryTab>("papers");
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState("");
  const [scope, setScope] = useState("all");
  const [ingestOpen, setIngestOpen] = useState(false);
  const [metadataId, setMetadataId] = useState<string | null>(null);

  const scopeFilter = scope === "quality"
    ? "&quality=true"
    : scope === "interested"
      ? "&interested=true"
      : scope === "all"
        ? ""
        : `&status=${scope}`;
  const libraryFilter = tab === "collection" ? "&library=true" : "";
  const papers = useQuery({
    queryKey: ["papers", tab, search, tag, scope],
    queryFn: () => api<{ items: Paper[]; total: number }>(
      `/api/papers?limit=100&q=${encodeURIComponent(search)}&tag_id=${encodeURIComponent(tag)}${libraryFilter}${scopeFilter}`,
    ),
  });
  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api<Tag[]>("/api/tags") });

  function changeTab(next: LibraryTab) {
    setTab(next);
    setScope("all");
  }

  return (
    <Container maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}>
      <PageHeader
        title="论文"
        description="浏览全部论文，并将完成精读的成果沉淀到积累库。"
        tabs={<Tabs value={tab} onChange={(_, value: LibraryTab) => changeTab(value)} aria-label="论文库分类" sx={{ minHeight: 40, "& .MuiTab-root": { minHeight: 40, py: 0 } }}><Tab value="papers" label="论文库" /><Tab value="collection" label="积累库" /></Tabs>}
        actions={<Button variant="contained" startIcon={<AddRounded />} onClick={() => setIngestOpen(true)}>添加论文</Button>}
      />

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
            <TextField
              size="small"
              fullWidth
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="搜索标题或作者"
              InputProps={{ startAdornment: <InputAdornment position="start"><SearchRounded /></InputAdornment> }}
            />
            <FormControl size="small" sx={{ minWidth: 156 }}>
              <InputLabel>{tab === "papers" ? "阅读状态" : "筛选"}</InputLabel>
              <Select
                label={tab === "papers" ? "阅读状态" : "筛选"}
                value={scope}
                onChange={(event) => setScope(event.target.value)}
              >
                <MenuItem value="all">全部</MenuItem>
                {tab === "papers" ? [
                  <MenuItem value="candidate" key="candidate">待阅读</MenuItem>,
                  <MenuItem value="reading" key="reading">精读中</MenuItem>,
                  <MenuItem value="feedback_pending" key="feedback_pending">待确认</MenuItem>,
                  <MenuItem value="completed" key="completed">已精读</MenuItem>,
                  <MenuItem value="ignored" key="ignored">已忽略</MenuItem>,
                ] : [
                  <MenuItem value="quality" key="quality">优质论文</MenuItem>,
                  <MenuItem value="interested" key="interested">感兴趣</MenuItem>,
                ]}
              </Select>
            </FormControl>
            <FormControl size="small" sx={{ minWidth: 150 }}>
              <InputLabel>标签</InputLabel>
              <Select label="标签" value={tag} onChange={(event) => setTag(event.target.value)}>
                <MenuItem value="">全部标签</MenuItem>
                {tags.data?.map((item) => <MenuItem value={item.id} key={item.id}>{item.name}</MenuItem>)}
              </Select>
            </FormControl>
            <Chip label={`${papers.data?.total ?? 0} 篇`} sx={{ alignSelf: { xs: "flex-start", md: "center" } }} />
          </Stack>
        </CardContent>
      </Card>

      {papers.isLoading ? <Loading /> : papers.error ? <ErrorState error={papers.error} /> : !papers.data?.items.length ? (
        <Empty
          title={tab === "papers" ? "没有匹配的论文" : "积累库还是空的"}
          body={tab === "papers" ? "调整筛选条件，或添加新的 arXiv、URL、PDF。" : "论文完成精读并确认反馈后，会自动进入这里。"}
        />
      ) : (
        <Stack spacing={1.5}>
          {papers.data.items.map((paper) => (
            <PaperCard
              key={paper.id}
              paper={paper}
              accumulated={tab === "collection"}
              onOpen={() => navigate(`/paper/${paper.id}`)}
              onTagClick={setTag}
              onMetadata={() => setMetadataId(paper.id)}
            />
          ))}
        </Stack>
      )}

      <IngestModal
        open={ingestOpen}
        onClose={() => setIngestOpen(false)}
        onSuccess={() => {
          setIngestOpen(false);
          setTab("papers");
          setScope("all");
          queryClient.invalidateQueries({ queryKey: ["papers"] });
        }}
      />
      {metadataId && <PaperMetadataDialog paperId={metadataId} open onClose={() => setMetadataId(null)} />}
    </Container>
  );
}

function PaperCard({
  paper,
  accumulated,
  onOpen,
  onTagClick,
  onMetadata,
}: {
  paper: Paper;
  accumulated: boolean;
  onOpen: () => void;
  onTagClick: (tagId: string) => void;
  onMetadata: () => void;
}) {
  return (
    <Card>
      <CardContent sx={{ pb: 1.25 }}>
        <Stack direction="row" alignItems="center" columnGap={1} rowGap={0.75} flexWrap="wrap">
          <Chip
            size="small"
            label={statusLabel(paper.status)}
            color={statusColors[paper.status] ?? "default"}
            sx={{ fontWeight: 700 }}
          />
          <Typography variant="caption" color="text.secondary">
            {formatDate(paper.published_at || paper.created_at)}
          </Typography>
          {paper.analysis_status && ["queued", "processing"].includes(paper.analysis_status) && <Chip size="small" color="primary" variant="outlined" label="处理中" />}
          {paper.analysis_status === "failed" && <Chip size="small" color="error" variant="outlined" label="处理失败" />}
          {paper.tags.map((item) => (
            <Box component="span" key={item.id} sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
              <Typography component="span" color="text.disabled">·</Typography>
              <MuiLink
                component="button"
                type="button"
                underline="hover"
                onClick={() => onTagClick(item.id)}
                sx={{ color: item.color || "primary.main", fontSize: "0.78rem", fontWeight: 650 }}
              >
                {item.name}
              </MuiLink>
            </Box>
          ))}
          <Button size="small" variant="outlined" color={paper.status === "feedback_pending" ? "warning" : "primary"} startIcon={<MenuBookRounded />} onClick={onOpen} sx={{ ml: "auto!important" }}>{paper.status === "feedback_pending" ? "确认结论" : "阅读"}</Button>
        </Stack>
        {accumulated && (
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ xs: "flex-start", sm: "center" }}
            spacing={1}
            sx={{ mt: 1.25, pt: 1.25, borderTop: 1, borderColor: "divider" }}
          >
            <Stack direction="row" spacing={0.75} alignItems="center" sx={{ color: "text.secondary", flexShrink: 0 }}>
              <HubRounded sx={{ fontSize: 17 }} />
              <Typography variant="caption" fontWeight={650}>所属节点</Typography>
            </Stack>
            {paper.nodes.length ? (
              <Stack direction="row" flexWrap="wrap" gap={0.75}>
                {paper.nodes.map((node) => (
                  <Button
                    component={RouterLink}
                    to={`/graph/${node.id}`}
                    variant="outlined"
                    size="small"
                    key={node.id}
                    sx={{ minHeight: 26, px: 1, py: 0.25, fontSize: "0.75rem" }}
                  >
                    {node.name}
                  </Button>
                ))}
              </Stack>
            ) : (
              <Typography variant="caption" color="text.disabled">尚未关联研究节点</Typography>
            )}
          </Stack>
        )}
      </CardContent>
      <CardContent
        role="button"
        tabIndex={0}
        aria-label={`编辑 ${paper.title} 的论文信息`}
        onClick={onMetadata}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onMetadata();
          }
        }}
        sx={{ pt: 1.25, cursor: "pointer", borderRadius: 1, "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: 2 } }}
      >
        <Typography variant="h3">{paper.title}</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          {paper.first_author || "作者信息待确认"}{paper.primary_affiliation ? ` · ${paper.primary_affiliation}` : ""} · {paper.source}
        </Typography>
        <Box sx={{ mt: 1 }}>
          <ExpandablePaperText lines={3}>
            {paper.summary || paper.abstract || "摘要与分析信息待补充。"}
          </ExpandablePaperText>
        </Box>
      </CardContent>
    </Card>
  );
}

function IngestModal({ open, onClose, onSuccess }: { open: boolean; onClose: () => void; onSuccess: () => void }) {
  const [value, setValue] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const ingest = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      form.set("recommend", "true");
      if (fileRef.current?.files?.[0]) {
        form.set("file", fileRef.current.files[0]);
        return api("/api/papers/ingest/pdf", { method: "POST", body: form });
      }
      const arxiv = value.includes("arxiv.org") || /^\d{4}\.\d{4,5}/.test(value);
      form.set(arxiv ? "value" : "url", value);
      return api(arxiv ? "/api/papers/ingest/arxiv" : "/api/papers/ingest/url", { method: "POST", body: form });
    },
    onSuccess,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    ingest.mutate();
  }

  return (
    <Modal open={open} onClose={onClose} title="添加论文">
      <Box component="form" onSubmit={submit}>
        <TextField
          fullWidth
          label="URL 或 arXiv ID"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="https://arxiv.org/abs/…"
        />
        <Button component="label" variant="outlined" startIcon={<CloudUploadRounded />} fullWidth sx={{ my: 2, py: 2 }}>
          选择 PDF
          <input ref={fileRef} hidden type="file" accept="application/pdf" />
        </Button>
        {ingest.error && <Alert severity="error">{ingest.error.message}</Alert>}
        <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2 }}>
          <Button onClick={onClose}>取消</Button>
          <Button variant="contained" type="submit" disabled={ingest.isPending || (!value.trim() && !fileRef.current?.files?.length)}>
            {ingest.isPending ? "正在添加…" : "添加"}
          </Button>
        </Stack>
      </Box>
    </Modal>
  );
}
