import ArrowOutwardRounded from "@mui/icons-material/ArrowOutwardRounded";
import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import { Alert, Box, Button, Chip, Link, Stack, Tab, Tabs, TextField, Tooltip, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, paperStatusLabel } from "../api";
import type { Paper, Tag } from "../types";
import { Modal } from "./Modal";
import { PaperAuthors } from "./PaperAuthors";
import { authorsText, parseAuthors } from "./authorInput";
import { PaperScores } from "./PaperScores";
import { PaperQuickNotesFields } from "./PaperQuickNotesFields";
import { PaperTagSelector } from "./PaperTagSelector";
import { ErrorState, Loading } from "./States";

const emptyForm = {
  title: "", abstract: "", authors: "", first_author: "", corresponding_author: "", primary_affiliation: "",
  source: "", source_url: "", pdf_url: "", doi: "", published_at: "", model_short_name: "",
  search_aliases: "", pre_read_summary: "", post_read_summary: "", user_note: "",
};
type Form = typeof emptyForm;
type InfoTab = "key" | "analysis" | "other";
const infoTabs = [
  { value: "key", label: "关键信息" },
  { value: "analysis", label: "摘要和分析" },
  { value: "other", label: "其他信息" },
] as const;
const fieldGrid = { display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, gap: 1.5 };

function paperForm(paper: Paper): Form {
  return {
    ...Object.fromEntries(Object.keys(emptyForm).map((key) => [key, paper[key as keyof Form] ?? ""])) as Form,
    search_aliases: (paper.search_aliases ?? []).join(", "),
    authors: authorsText(paper.authors ?? []),
  };
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <Stack spacing={1.25}>
    <Typography variant="subtitle2" fontWeight={750}>{title}</Typography>
    {children}
  </Stack>;
}

export function PaperMetadataDialog({ paperId, open, onClose }: { paperId: string; open: boolean; onClose: () => void }) {
  const cache = useQueryClient();
  const paper = useQuery({
    queryKey: ["paper", paperId], queryFn: () => api<Paper>(`/api/papers/${paperId}`), enabled: open,
    refetchInterval: (query) => ["queued", "processing"].includes(query.state.data?.analysis_status ?? "") ? 2000 : false,
  });
  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api<Tag[]>("/api/tags"), enabled: open });
  const [tab, setTab] = useState<InfoTab>("key");
  const [form, setForm] = useState<Form>(emptyForm);
  const [score, setScore] = useState<number | null>(null);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [newTags, setNewTags] = useState<string[]>([]);
  const [baseline, setBaseline] = useState<string | null>(null);
  const loadedPaper = useRef<string | null>(null);
  const initialAuthors = useRef("");
  const dirty = baseline !== null && baseline !== JSON.stringify({ form, score, selectedTags, newTags });
  useEffect(() => { if (open) setTab("key"); }, [open, paperId]);
  useEffect(() => {
    if (!open) { setBaseline(null); loadedPaper.current = null; return; }
    if (!paper.data || (loadedPaper.current === paperId && dirty)) return;
    const nextForm = paperForm(paper.data);
    const nextScore = paper.data.user_score ?? null;
    const nextTags = paper.data.tags.map((tag) => tag.id);
    setForm(nextForm);
    initialAuthors.current = nextForm.authors;
    setScore(nextScore);
    setSelectedTags(nextTags);
    setNewTags([]);
    loadedPaper.current = paperId;
    setBaseline(JSON.stringify({ form: nextForm, score: nextScore, selectedTags: nextTags, newTags: [] }));
  }, [open, paperId, paper.data, dirty]);
  const save = useMutation({
    mutationFn: () => {
      const { search_aliases: aliases, authors, ...fields } = form;
      return api<Paper>(`/api/papers/${paperId}/metadata`, { method: "PATCH", body: JSON.stringify({
        ...Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, value.trim() || null])),
        title: form.title.trim(), search_aliases: aliases.split(/[,，\n]+/).map((value) => value.trim()).filter(Boolean),
        ...(authors !== initialAuthors.current ? { authors: parseAuthors(authors, paper.data?.authors ?? []) } : {}),
        user_score: score, tag_ids: selectedTags, new_tags: newTags,
      }) });
    },
    onSuccess: async () => {
      await Promise.all(["paper", "papers", "paper-authors", "brief", "daily", "research-groups"].map((key) => cache.invalidateQueries({ queryKey: key === "paper" ? [key, paperId] : [key] })));
      onClose();
    },
  });
  const generate = useMutation({
    mutationFn: () => api(`/api/papers/${paperId}/metadata/generate`, { method: "POST" }),
    onSuccess: () => Promise.all([
      cache.invalidateQueries({ queryKey: ["paper", paperId] }),
      cache.invalidateQueries({ queryKey: ["tasks"] }),
    ]),
  });
  const processing = ["queued", "processing"].includes(paper.data?.analysis_status ?? "");
  const arxivUrl = paper.data?.arxiv_id ? `https://arxiv.org/abs/${paper.data.arxiv_id}` : null;
  const field = (key: keyof Form, label: string, rows?: number) => <TextField key={key} label={label}
    size="small" value={form[key]} onChange={(event) => setForm((current) => ({ ...current, [key]: event.target.value }))}
    type={key === "published_at" ? "date" : undefined}
    slotProps={key === "published_at" ? { inputLabel: { shrink: true } } : undefined}
    required={key === "title" || key === "source"} fullWidth multiline={Boolean(rows)} minRows={rows} />;
  return <Modal open={open} onClose={onClose} title="论文信息" wide fixedHeight compact>
    <Tabs value={tab} onChange={(_, value: InfoTab) => setTab(value)} variant="fullWidth" aria-label="论文信息分类"
      sx={{ flexShrink: 0, minHeight: 44, borderBottom: 1, borderColor: "divider", mb: .5,
        "& .MuiTab-root": { minHeight: 44, minWidth: 0, fontSize: ".875rem", fontWeight: 650 } }}>
      {infoTabs.map((item) => <Tab key={item.value} value={item.value} label={item.label}
        id={`paper-info-tab-${item.value}`} aria-controls={`paper-info-panel-${item.value}`} />)}
    </Tabs>
    <Box key={tab} role="tabpanel" id={`paper-info-panel-${tab}`} aria-labelledby={`paper-info-tab-${tab}`}
      sx={{ flex: 1, minHeight: 0, overflowY: "auto", px: { xs: .75, sm: 1.5 }, py: 2 }}>
      {paper.isLoading ? <Loading /> : paper.error ? <ErrorState error={paper.error} /> : <Stack spacing={2.5}>
        {tab === "key" && <>
          <Stack direction="row" alignItems="center" flexWrap="wrap" gap={1.5}>
            {paper.data && <Chip size="small" variant="outlined" label={paperStatusLabel(paper.data.status)} />}
            {arxivUrl && <Link href={arxivUrl} target="_blank" rel="noopener noreferrer" underline="hover"
              sx={{ display: "inline-flex", alignItems: "center", gap: .35, fontSize: ".8125rem" }}>
              arXiv {paper.data?.arxiv_id}<ArrowOutwardRounded sx={{ fontSize: 14 }} />
            </Link>}
          </Stack>
          {field("title", "论文标题", 2)}
          <Section title="作者与单位">
            <PaperAuthors value={form.authors} group={paper.data?.research_group} onChange={(value) => setForm((current) => ({
              ...current, authors: value,
              first_author: !current.first_author || current.first_author === parseAuthors(current.authors)[0]?.name
                ? parseAuthors(value)[0]?.name ?? "" : current.first_author,
            }))} />
            <Box sx={fieldGrid}>{field("first_author", "第一作者")}{field("corresponding_author", "通讯作者")}</Box>
            {field("primary_affiliation", "主要完成单位")}
          </Section>
          <PaperScores userScore={score} agentScore={paper.data?.agent_score} modelScore={paper.data?.model_score} onChange={setScore} disabled={save.isPending} />
          <Box sx={fieldGrid}>{field("published_at", "发表日期")}{field("doi", "DOI")}</Box>
          {field("source_url", arxivUrl ? "arXiv 链接" : "原文链接")}
        </>}
        {tab === "analysis" && <>
          <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1}>
            <Typography variant="subtitle2" fontWeight={750}>摘要和分析</Typography>
            <Tooltip title={dirty ? "请先保存修改" : ""}>
              <span><Button size="small" variant="outlined" startIcon={<AutoAwesomeRounded />}
                disabled={generate.isPending || save.isPending || dirty || processing} onClick={() => generate.mutate()}>
                {processing ? "Agent 处理中" : "让 Agent 生成"}
              </Button></span>
            </Tooltip>
          </Stack>
          {processing && <Alert severity="info">Agent 正在整理总结。</Alert>}
          {paper.data?.analysis_status === "failed" && <Alert severity="error">{paper.data.analysis_error || "全文分析失败"}</Alert>}
          {generate.error && <Alert severity="error">{generate.error.message}</Alert>}
          {field("abstract", "原文摘要", 6)}
          {field("pre_read_summary", "读前简介", 5)}
          {field("post_read_summary", "读后总结", 12)}
        </>}
        {tab === "other" && <>
          <Section title="简记">
            <PaperQuickNotesFields value={form} onChange={(changes) => setForm((current) => ({ ...current, ...changes }))} />
          </Section>
          <Section title="标签">
            <PaperTagSelector tags={tags.data ?? []} selectedIds={selectedTags} newTags={newTags} onSelectedIdsChange={setSelectedTags} onNewTagsChange={setNewTags} />
          </Section>
          <Section title="来源">
            <Box sx={fieldGrid}>{field("source", "来源")}{field("pdf_url", "PDF 链接")}</Box>
          </Section>
        </>}
      </Stack>}
    </Box>
    <Box sx={{ flexShrink: 0, pt: 1.5, px: { xs: .75, sm: 1.5 }, pb: .25, borderTop: 1, borderColor: "divider" }}>
      {save.error && <Alert severity="error" sx={{ mb: 1 }}>{save.error.message}</Alert>}
      <Stack direction="row" justifyContent="flex-end" spacing={1}>
        <Button color="inherit" onClick={onClose}>取消</Button>
        <Button variant="contained" disabled={save.isPending || !dirty || !form.title.trim() || !form.source.trim()} onClick={() => save.mutate()}>保存</Button>
      </Stack>
    </Box>
  </Modal>;
}
