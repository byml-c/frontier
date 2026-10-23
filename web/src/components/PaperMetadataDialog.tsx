import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  Grid,
  Stack,
  Tab,
  Tabs,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { InterestLevel, Paper, QualityLevel, Tag } from "../types";
import { CompletionSummaryEditor } from "./CompletionSummaryEditor";
import { completionSummaryDraft, completionSummaryDraftIsComplete, type CompletionSummaryDraft } from "./completionSummary";
import { Modal } from "./Modal";
import { normalizeExactName } from "./exactName";
import { PaperEvaluationToggleButtons } from "./PaperEvaluationToggleButtons";
import { PaperTagSelector } from "./PaperTagSelector";
import { ErrorState, Loading } from "./States";

type MetadataForm = {
  title: string;
  authors: string;
  first_author: string;
  corresponding_author: string;
  primary_affiliation: string;
  research_group_summary: string;
  source: string;
  source_url: string;
  pdf_url: string;
  doi: string;
  published_at: string;
  abstract: string;
  model_short_name: string;
  search_aliases: string;
  summary: string;
  agent_comment: string;
  problem: string;
  innovation: string;
  experiment: string;
  limitations: string;
  open_questions: string;
  recommendation_reason: string;
  metadata_json: string;
};

const emptyForm: MetadataForm = {
  title: "",
  authors: "",
  first_author: "",
  corresponding_author: "",
  primary_affiliation: "",
  research_group_summary: "",
  source: "",
  source_url: "",
  pdf_url: "",
  doi: "",
  published_at: "",
  abstract: "",
  model_short_name: "",
  search_aliases: "",
  summary: "",
  agent_comment: "",
  problem: "",
  innovation: "",
  experiment: "",
  limitations: "",
  open_questions: "",
  recommendation_reason: "",
  metadata_json: "{}",
};
const metadataCardSx = { p: { xs: .75, sm: 0 }, border: 0, borderRadius: 0, bgcolor: "transparent" };

function paperToForm(paper: Paper): MetadataForm {
  return {
    title: paper.title || "",
    authors: (paper.authors ?? []).map((author) => author.name).filter(Boolean).join(", "),
    first_author: paper.first_author || "",
    corresponding_author: paper.corresponding_author || "",
    primary_affiliation: paper.primary_affiliation || "",
    research_group_summary: paper.research_group_summary || "",
    source: paper.source || "",
    source_url: paper.source_url || "",
    pdf_url: paper.pdf_url || "",
    doi: paper.doi || "",
    published_at: paper.published_at || "",
    abstract: paper.abstract || "",
    model_short_name: paper.model_short_name || "",
    search_aliases: (paper.search_aliases ?? []).join(", "),
    summary: paper.summary || "",
    agent_comment: paper.agent_comment || "",
    problem: paper.problem || "",
    innovation: paper.innovation || "",
    experiment: paper.experiment || "",
    limitations: paper.limitations || "",
    open_questions: paper.open_questions || "",
    recommendation_reason: paper.recommendation_reason || "",
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
  const [tab, setTab] = useState("overview");
  const [readingTab, setReadingTab] = useState("daily");
  const [form, setForm] = useState<MetadataForm>(emptyForm);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [newTags, setNewTags] = useState<string[]>([]);
  const [summaryDraft, setSummaryDraft] = useState<CompletionSummaryDraft | null>(null);
  const [interestLevel, setInterestLevel] = useState<InterestLevel>("neutral");
  const [qualityLevel, setQualityLevel] = useState<QualityLevel>("normal");
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
    setSummaryDraft(completionSummaryDraft(paper.data.completion_summary_structured));
    setInterestLevel(paper.data.interest_level ?? (paper.data.is_interested ? "interested" : "neutral"));
    setQualityLevel(paper.data.quality_level ?? (paper.data.is_quality ? "excellent" : "normal"));
    setReadingTab(paper.data.completion_summary_structured ? "reading" : "daily");
    setFormError("");
    setDirty(false);
  }, [open, paper.data, dirty]);

  useEffect(() => {
    if (open) setTab("overview");
  }, [open, paperId]);

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
          authors: form.authors.split(/[,，\n]+/).map((name) => name.trim()).filter(Boolean).map((name) => ({ name })),
          first_author: form.first_author.trim() || null,
          corresponding_author: form.corresponding_author.trim() || null,
          primary_affiliation: form.primary_affiliation.trim() || null,
          research_group_summary: form.research_group_summary.trim() || null,
          source: form.source.trim(),
          source_url: form.source_url.trim() || null,
          pdf_url: form.pdf_url.trim() || null,
          doi: form.doi.trim() || null,
          published_at: form.published_at || null,
          abstract: form.abstract.trim() || null,
          model_short_name: form.model_short_name.trim() || null,
          search_aliases: form.search_aliases.split(/[,，\n]+/).map((value) => value.trim()).filter(Boolean),
          summary: form.summary.trim() || null,
          agent_comment: form.agent_comment.trim() || null,
          problem: form.problem.trim() || null,
          innovation: form.innovation.trim() || null,
          experiment: form.experiment.trim() || null,
          limitations: form.limitations.trim() || null,
          open_questions: form.open_questions.trim() || null,
          recommendation_reason: form.recommendation_reason.trim() || null,
          ...(paper.data?.status === "completed" && (
            interestLevel !== (paper.data.interest_level ?? (paper.data.is_interested ? "interested" : "neutral"))
            || qualityLevel !== (paper.data.quality_level ?? (paper.data.is_quality ? "excellent" : "normal"))
          ) ? {
            interest_level: interestLevel,
            quality_level: qualityLevel,
          } : {}),
          metadata_json: metadata,
          completion_summary_structured: summaryDraft && completionSummaryDraftIsComplete(summaryDraft) ? summaryDraft : undefined,
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
        queryClient.invalidateQueries({ queryKey: ["daily"] }),
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
  const summaryComplete = !summaryDraft || completionSummaryDraftIsComplete(summaryDraft);
  const dailyDraft: CompletionSummaryDraft = {
    problem: form.problem,
    innovation: form.innovation,
    experiment: form.experiment,
    limitations: form.limitations,
    open_questions: form.open_questions,
    user_attitude: "每日分析不推断用户态度；完成精读后由精读 Agent 根据你的笔记与对话整理。",
  };
  function updateDailyDraft(value: CompletionSummaryDraft) {
    setDirty(true);
    setForm((current) => ({
      ...current,
      problem: value.problem,
      innovation: value.innovation,
      experiment: value.experiment,
      limitations: value.limitations,
      open_questions: value.open_questions,
    }));
  }
  function acceptSuggestedTag(name: string) {
    const existing = tags.data?.find(
      (tag) => normalizeExactName(tag.name) === normalizeExactName(name),
    );
    if (existing) {
      setSelectedTags((current) => current.includes(existing.id) ? current : [...current, existing.id]);
    } else if (!newTags.some((tag) => normalizeExactName(tag) === normalizeExactName(name))) {
      setNewTags((current) => [...current, name]);
    }
    setDirty(true);
  }

  return (
    <Modal open={open} onClose={onClose} title="论文信息" wide fixedHeight compact>
      {paper.isLoading ? <Loading label="正在读取论文信息" /> : paper.error ? <ErrorState error={paper.error} /> : (
        <Stack sx={{ width: "100%", height: "100%", minHeight: 0 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ flexShrink: 0, minWidth: 0 }}>
            <Tabs value={tab} onChange={(_, value) => setTab(value)} variant="scrollable" scrollButtons={false} sx={{ minWidth: 0, flexShrink: 1, "& .MuiTab-root": { minWidth: { xs: 64, sm: 80 }, px: { xs: 1, sm: 2 } } }}>
              <Tab value="overview" label="概览" />
              <Tab value="reading" label="精读" />
              <Tab value="metadata" label="文献信息" />
            </Tabs>
            <Box sx={{ flex: 1 }} />
            {tab === "reading" && (
              <ToggleButtonGroup
                exclusive
                size="small"
                value={readingTab}
                aria-label="切换每日分析与精读结论"
                onChange={(_, value: "daily" | "reading" | null) => { if (value) setReadingTab(value); }}
                sx={{ flexShrink: 0, "& .MuiToggleButton-root": { py: .375, px: { xs: 1, sm: 1.5 }, whiteSpace: "nowrap", textTransform: "none" } }}
              >
                <ToggleButton value="daily">每日分析</ToggleButton>
                <ToggleButton value="reading" disabled={!summaryDraft}>精读结论</ToggleButton>
              </ToggleButtonGroup>
            )}
            {tab === "overview" && (
              <Button
                size="small"
                variant="outlined"
                startIcon={generate.isPending ? <CircularProgress size={16} /> : <AutoAwesomeRounded />}
                disabled={generate.isPending || dirty || ["queued", "processing"].includes(paper.data?.analysis_status ?? "")}
                onClick={() => generate.mutate()}
                sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
              >
                {["queued", "processing"].includes(paper.data?.analysis_status ?? "") ? "Agent 处理中" : "让 Agent 生成"}
              </Button>
            )}
          </Stack>

          <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", pt: .75, pr: { xs: 0, sm: .5 } }}>
            {tab === "overview" && (
              <Stack spacing={{ xs: 1.25, sm: 2 }}>
                {dirty && <Alert severity="warning">请先保存当前修改，再调用 Agent，避免覆盖尚未保存的内容。</Alert>}
                {paper.data?.analysis_status && ["queued", "processing"].includes(paper.data.analysis_status) && (
                  <Alert severity="info" icon={<CircularProgress size={18} />}>Agent 正在读取全文并补充分析，当前内容仍可编辑。</Alert>
                )}
                {paper.data?.analysis_status === "failed" && <Alert severity="error">{paper.data.analysis_error || "全文分析失败"}</Alert>}
                {generate.error && <Alert severity="error">{generate.error.message}</Alert>}

                <Card variant="outlined" sx={metadataCardSx}>
                  <CardContent>
                    <Stack spacing={2}>
                      <Typography variant="h3">核心判断</Typography>
                      {field("model_short_name", "模型简名")}
                      {field("search_aliases", "搜索别名（逗号分隔）")}
                      {field("summary", "一句话简介", { multiline: true, minRows: 2 })}
                      {field("agent_comment", "Agent 评语", { multiline: true, minRows: 2 })}
                      {field("recommendation_reason", "推荐理由", { multiline: true, minRows: 3 })}
                      <PaperEvaluationToggleButtons
                        showQuality={paper.data?.status === "completed" || paper.data?.status === "feedback_pending"}
                        researchScore={paper.data?.research_score}
                        relevanceScore={paper.data?.relevance_score}
                        modelScore={paper.data?.model_relevance_score}
                        modelConfidence={paper.data?.model_relevance_confidence}
                        modelReviewRequired={paper.data?.model_review_required}
                        qualityLevel={qualityLevel}
                        interestLevel={interestLevel}
                        disabled={paper.data?.status !== "completed"}
                        onQualityChange={(value) => { setQualityLevel(value); setDirty(true); }}
                        onInterestChange={(value) => { setInterestLevel(value); setDirty(true); }}
                      />
                      {paper.data?.status !== "completed" && <Typography variant="caption" color="text.secondary">完成精读并确认结论后，可以编辑“我的评价”。</Typography>}
                    </Stack>
                  </CardContent>
                </Card>

                <Card variant="outlined" sx={metadataCardSx}>
                  <CardContent>
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
                  </CardContent>
                </Card>

                <Card variant="outlined" sx={metadataCardSx}>
                  <CardContent>
                    <Typography variant="h3">所属节点</Typography>
                    {paper.data?.nodes.length ? <Stack direction="row" flexWrap="wrap" gap={1} sx={{ mt: 1 }}>
                      {paper.data.nodes.map((node) => <Chip key={node.id} label={node.name} color="primary" variant="outlined" />)}
                    </Stack> : <Typography color="text.secondary" sx={{ mt: 1 }}>尚未归入研究节点。</Typography>}
                  </CardContent>
                </Card>
              </Stack>
            )}

            {tab === "metadata" && (
              <Card variant="outlined" sx={metadataCardSx}>
                <CardContent>
                  <Stack spacing={2}>
                    <Typography variant="h3">论文标识</Typography>
                    {field("title", "标题")}
                    <Grid container spacing={2}>
                      <Grid size={{ xs: 12, sm: 4 }}>{field("first_author", "第一作者")}</Grid>
                      <Grid size={{ xs: 12, sm: 4 }}>{field("corresponding_author", "通讯作者")}</Grid>
                      <Grid size={{ xs: 12, sm: 4 }}>{field("primary_affiliation", "第一单位")}</Grid>
                      <Grid size={12}>{field("authors", "全部作者（用逗号分隔）", { multiline: true, minRows: 3 })}</Grid>
                      <Grid size={12}>{field("research_group_summary", "单位与核心课题组（Agent 归纳）", { multiline: true, minRows: 2 })}</Grid>
                    </Grid>
                    <Divider />
                    <Typography variant="h3">出版信息</Typography>
                    <Grid container spacing={2}>
                      <Grid size={{ xs: 12, md: 4 }}>{field("source", "来源")}</Grid>
                      <Grid size={{ xs: 12, md: 4 }}>{field("published_at", "发表日期", { type: "date" })}</Grid>
                      <Grid size={{ xs: 12, md: 4 }}>{field("doi", "DOI")}</Grid>
                    </Grid>
                    {field("source_url", "原文链接")}
                    {field("pdf_url", "PDF 链接")}
                    <Divider />
                    <Typography variant="h3">摘要</Typography>
                    {field("abstract", "原文摘要", { multiline: true, minRows: 7 })}
                    <Divider />
                    <Typography variant="h3">扩展信息</Typography>
                    {field("metadata_json", "JSON", { multiline: true, minRows: 10 })}
                  </Stack>
                </CardContent>
              </Card>
            )}

            {tab === "reading" && (
              <Card variant="outlined" sx={metadataCardSx}>
                <CardContent>
                  <CompletionSummaryEditor
                    key={readingTab}
                    value={readingTab === "reading" && summaryDraft ? summaryDraft : dailyDraft}
                    onChange={readingTab === "reading"
                      ? (value) => { setSummaryDraft(value); setDirty(true); }
                      : updateDailyDraft}
                    disabledFields={readingTab === "daily" ? ["user_attitude"] : []}
                    required={readingTab === "reading"}
                  />
                </CardContent>
              </Card>
            )}
          </Box>

          {(formError || save.error) && <Alert severity="error" sx={{ mt: 1, flexShrink: 0 }}>{formError || save.error?.message}</Alert>}
          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ flexShrink: 0, pt: 2, borderTop: 1, borderColor: "divider" }}>
            <Button onClick={onClose}>取消</Button>
            <Button variant="contained" disabled={save.isPending || !form.title.trim()} onClick={() => { if (!summaryComplete) { setTab("reading"); setReadingTab("reading"); setFormError("请补全所有精读总结后再保存。"); return; } setFormError(""); save.mutate(); }}>{save.isPending ? "正在保存…" : "保存"}</Button>
          </Stack>
        </Stack>
      )}
    </Modal>
  );
}
