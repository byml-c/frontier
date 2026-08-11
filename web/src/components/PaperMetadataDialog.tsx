import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { Paper, Tag } from "../types";
import { Modal } from "./Modal";
import { PaperTagSelector } from "./PaperTagSelector";
import { ErrorState, Loading } from "./States";

type MetadataForm = {
  title: string;
  authors: string;
  first_author: string;
  corresponding_author: string;
  primary_affiliation: string;
  source: string;
  source_url: string;
  pdf_url: string;
  doi: string;
  published_at: string;
  abstract: string;
  summary: string;
  agent_comment: string;
  problem: string;
  innovation: string;
  experiment: string;
  limitations: string;
  recommendation_reason: string;
  research_score: string;
  relevance_score: string;
  metadata_json: string;
};

const emptyForm: MetadataForm = {
  title: "",
  authors: "",
  first_author: "",
  corresponding_author: "",
  primary_affiliation: "",
  source: "",
  source_url: "",
  pdf_url: "",
  doi: "",
  published_at: "",
  abstract: "",
  summary: "",
  agent_comment: "",
  problem: "",
  innovation: "",
  experiment: "",
  limitations: "",
  recommendation_reason: "",
  research_score: "",
  relevance_score: "",
  metadata_json: "{}",
};

function paperToForm(paper: Paper): MetadataForm {
  return {
    title: paper.title || "",
    authors: (paper.authors ?? []).map((author) => author.name).filter(Boolean).join("\n"),
    first_author: paper.first_author || "",
    corresponding_author: paper.corresponding_author || "",
    primary_affiliation: paper.primary_affiliation || "",
    source: paper.source || "",
    source_url: paper.source_url || "",
    pdf_url: paper.pdf_url || "",
    doi: paper.doi || "",
    published_at: paper.published_at || "",
    abstract: paper.abstract || "",
    summary: paper.summary || "",
    agent_comment: paper.agent_comment || "",
    problem: paper.problem || "",
    innovation: paper.innovation || "",
    experiment: paper.experiment || "",
    limitations: paper.limitations || "",
    recommendation_reason: paper.recommendation_reason || "",
    research_score: paper.research_score == null ? "" : String(paper.research_score),
    relevance_score: paper.relevance_score == null ? "" : String(paper.relevance_score),
    metadata_json: JSON.stringify(paper.metadata_json ?? {}, null, 2),
  };
}

export function PaperMetadataDialog({
  paperId,
  open,
  onClose,
}: {
  paperId: string;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState("bibliography");
  const [form, setForm] = useState<MetadataForm>(emptyForm);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [newTags, setNewTags] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);
  const [formError, setFormError] = useState("");
  const paper = useQuery({
    queryKey: ["paper", paperId],
    queryFn: () => api<Paper>(`/api/papers/${paperId}`),
    enabled: open && Boolean(paperId),
    refetchInterval: (query) => ["queued", "processing"].includes(query.state.data?.analysis_status ?? "") ? 2000 : false,
  });
  const tags = useQuery({
    queryKey: ["tags"],
    queryFn: () => api<Tag[]>("/api/tags"),
    enabled: open,
  });

  useEffect(() => {
    if (!open) {
      setDirty(false);
      return;
    }
    if (!paper.data || dirty) return;
    setForm(paperToForm(paper.data));
    setSelectedTags(paper.data.tags.map((tag) => tag.id));
    setNewTags([]);
    setFormError("");
    setDirty(false);
  }, [open, paper.data, dirty]);

  const save = useMutation({
    mutationFn: async () => {
      let metadata: Record<string, unknown>;
      try {
        metadata = JSON.parse(form.metadata_json) as Record<string, unknown>;
      } catch {
        throw new Error("扩展信息不是有效的 JSON");
      }
      return api<Paper>(`/api/papers/${paperId}/metadata`, {
        method: "PATCH",
        body: JSON.stringify({
          title: form.title.trim(),
          authors: form.authors.split("\n").map((name) => name.trim()).filter(Boolean).map((name) => ({ name })),
          first_author: form.first_author.trim() || null,
          corresponding_author: form.corresponding_author.trim() || null,
          primary_affiliation: form.primary_affiliation.trim() || null,
          source: form.source.trim(),
          source_url: form.source_url.trim() || null,
          pdf_url: form.pdf_url.trim() || null,
          doi: form.doi.trim() || null,
          published_at: form.published_at || null,
          abstract: form.abstract.trim() || null,
          summary: form.summary.trim() || null,
          agent_comment: form.agent_comment.trim() || null,
          problem: form.problem.trim() || null,
          innovation: form.innovation.trim() || null,
          experiment: form.experiment.trim() || null,
          limitations: form.limitations.trim() || null,
          recommendation_reason: form.recommendation_reason.trim() || null,
          research_score: form.research_score === "" ? null : Number(form.research_score),
          relevance_score: form.relevance_score === "" ? null : Number(form.relevance_score),
          metadata_json: metadata,
          tag_ids: selectedTags,
          new_tags: newTags,
        }),
      });
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["paper", paperId] }),
        queryClient.invalidateQueries({ queryKey: ["papers"] }),
        queryClient.invalidateQueries({ queryKey: ["brief"] }),
        queryClient.invalidateQueries({ queryKey: ["tags"] }),
      ]);
      onClose();
    },
  });

  const generate = useMutation({
    mutationFn: () => api<{ id: string; status: string }>(`/api/papers/${paperId}/metadata/generate`, { method: "POST" }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["paper", paperId] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
    },
  });

  function field(name: keyof MetadataForm, label: string, options: { multiline?: boolean; minRows?: number; type?: string } = {}) {
    return (
      <TextField
        label={label}
        value={form[name]}
        onChange={(event) => { setDirty(true); setForm((current) => ({ ...current, [name]: event.target.value })); }}
        fullWidth
        multiline={options.multiline}
        minRows={options.minRows}
        type={options.type}
      />
    );
  }

  const suggestedTags = paper.data?.suggested_tags ?? [];
  function acceptSuggestedTag(name: string) {
    const existing = tags.data?.find((tag) => tag.name === name);
    if (existing) {
      setSelectedTags((current) => current.includes(existing.id) ? current : [...current, existing.id]);
    } else if (!newTags.includes(name)) {
      setNewTags((current) => [...current, name]);
    }
    setDirty(true);
  }

  return (
    <Modal open={open} onClose={onClose} title="论文信息" wide>
      {paper.isLoading ? <Loading label="正在读取论文信息" /> : paper.error ? <ErrorState error={paper.error} /> : (
        <Stack spacing={2.5}>
          <Tabs value={tab} onChange={(_, value) => setTab(value)} variant="scrollable" allowScrollButtonsMobile>
            <Tab value="bibliography" label="文献信息" />
            <Tab value="analysis" label="分析与标签" />
            <Tab value="extended" label="扩展信息" />
          </Tabs>

          {tab === "bibliography" && (
            <Grid container spacing={2}>
              <Grid size={12}>{field("title", "标题")}</Grid>
              <Grid size={{ xs: 12, md: 6 }}>{field("authors", "全部作者（每行一位）", { multiline: true, minRows: 5 })}</Grid>
              <Grid size={{ xs: 12, md: 6 }}><Stack spacing={2}>{field("first_author", "第一作者")}{field("corresponding_author", "通讯作者")}{field("primary_affiliation", "第一单位")}</Stack></Grid>
              <Grid size={{ xs: 12, md: 4 }}>{field("source", "来源")}</Grid>
              <Grid size={{ xs: 12, md: 4 }}>{field("published_at", "发表日期", { type: "date" })}</Grid>
              <Grid size={{ xs: 12, md: 4 }}>{field("doi", "DOI")}</Grid>
              <Grid size={12}>{field("source_url", "原文链接")}</Grid>
              <Grid size={12}>{field("pdf_url", "PDF 链接")}</Grid>
              <Grid size={12}>{field("abstract", "摘要", { multiline: true, minRows: 6 })}</Grid>
            </Grid>
          )}

          {tab === "analysis" && (
            <Stack spacing={2}>
              <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" gap={1}>
                <Box>
                  <Typography variant="h3">Agent 分析</Typography>
                  <Typography variant="body2" color="text.secondary">重新读取论文全文并生成简介、评价、问题、创新、实验、局限和推荐标签。</Typography>
                </Box>
                <Button
                  variant="outlined"
                  startIcon={generate.isPending ? <CircularProgress size={16} /> : <AutoAwesomeRounded />}
                  disabled={generate.isPending || dirty || ["queued", "processing"].includes(paper.data?.analysis_status ?? "")}
                  onClick={() => generate.mutate()}
                >
                  {["queued", "processing"].includes(paper.data?.analysis_status ?? "") ? "Agent 处理中" : "让 Agent 生成"}
                </Button>
              </Stack>
              {dirty && <Alert severity="warning">请先保存当前修改，再调用 Agent，避免新生成的内容覆盖尚未保存的编辑。</Alert>}
              {paper.data?.analysis_status && ["queued", "processing"].includes(paper.data.analysis_status) && (
                <Alert severity="info" icon={<CircularProgress size={18} />}>Agent 正在读取全文并补充分析，当前内容仍可编辑。</Alert>
              )}
              {paper.data?.analysis_status === "failed" && <Alert severity="error">{paper.data.analysis_error || "全文分析失败"}</Alert>}
              {generate.error && <Alert severity="error">{generate.error.message}</Alert>}
              {field("summary", "一句话简介", { multiline: true, minRows: 2 })}
              {field("agent_comment", "Agent 一句话评语", { multiline: true, minRows: 2 })}
              {field("recommendation_reason", "推荐理由", { multiline: true, minRows: 3 })}
              <Grid container spacing={2}>
                <Grid size={{ xs: 12, md: 6 }}>{field("research_score", "研究价值（0–1）", { type: "number" })}</Grid>
                <Grid size={{ xs: 12, md: 6 }}>{field("relevance_score", "个人相关性（0–1）", { type: "number" })}</Grid>
              </Grid>
              {field("problem", "解决的问题", { multiline: true, minRows: 3 })}
              {field("innovation", "创新内容", { multiline: true, minRows: 3 })}
              {field("experiment", "实验结果", { multiline: true, minRows: 3 })}
              {field("limitations", "局限与遗留问题", { multiline: true, minRows: 3 })}
              <Divider />
              <Box>
                <Typography variant="h3">标签</Typography>
                {suggestedTags.length > 0 && <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1 }}><Typography variant="body2" color="text.secondary" sx={{ alignSelf: "center" }}>Agent 建议</Typography>{suggestedTags.map((name) => <Chip clickable color="primary" variant="outlined" label={name} key={name} onClick={() => acceptSuggestedTag(name)} />)}</Stack>}
                <PaperTagSelector
                  tags={tags.data ?? []}
                  selectedIds={selectedTags}
                  newTags={newTags}
                  onSelectedIdsChange={setSelectedTags}
                  onNewTagsChange={setNewTags}
                  onEdit={() => setDirty(true)}
                />
              </Box>
            </Stack>
          )}

          {tab === "extended" && (
            <Box>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>用于保留论文来源提供的其他结构化字段。</Typography>
              {field("metadata_json", "扩展信息（JSON）", { multiline: true, minRows: 18 })}
            </Box>
          )}

          {(formError || save.error) && <Alert severity="error">{formError || save.error?.message}</Alert>}
          <Stack direction="row" justifyContent="flex-end" spacing={1}>
            <Button onClick={onClose}>取消</Button>
            <Button variant="contained" disabled={save.isPending || !form.title.trim()} onClick={() => { setFormError(""); save.mutate(); }}>{save.isPending ? "正在保存…" : "保存"}</Button>
          </Stack>
        </Stack>
      )}
    </Modal>
  );
}
