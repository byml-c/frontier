import AddRounded from "@mui/icons-material/AddRounded";
import CloudUploadRounded from "@mui/icons-material/CloudUploadRounded";
import EditNoteRounded from "@mui/icons-material/EditNoteRounded";
import HubRounded from "@mui/icons-material/HubRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import StarRounded from "@mui/icons-material/StarRounded";
import UndoRounded from "@mui/icons-material/UndoRounded";
import {
  Alert,
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Container,
  FormControl,
  IconButton,
  InputAdornment,
  InputLabel,
  Link as MuiLink,
  MenuItem,
  Pagination,
  Select,
  Snackbar,
  Stack,
  Tab,
  Tabs,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, KeyboardEvent, MouseEvent, useEffect, useRef, useState } from "react";
import { Link as RouterLink, useNavigate, useSearchParams } from "react-router-dom";
import { api, formatDate, statusLabel } from "../api";
import { Modal } from "../components/Modal";
import { MobileConfirmAction } from "../components/MobileConfirmAction";
import { PageHeader } from "../components/PageHeader";
import { PaperMetadataDialog } from "../components/PaperMetadataDialog";
import { PaperTitle } from "../components/PaperTitle";
import { Empty, ErrorState, Loading } from "../components/States";
import { ExpandablePaperText } from "../components/ExpandablePaperText";
import { HighlightText } from "../components/HighlightText";
import { RatingQueryControls } from "../components/RatingQueryControls";
import { EMPTY_LIBRARY_QUERY, RATING_SOURCES, libraryMatchSnippets, libraryQueryParams, paperRating, ratingOptions, type LibraryQuery } from "../components/paperQuery";
import { queryTerms } from "../components/textMatches";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import type { DailyPayload, Paper, Tag } from "../types";

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
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const tab: LibraryTab = searchParams.get("view") === "collection" ? "collection" : "papers";
  const [filters, setFilters] = useState<LibraryQuery>({ ...EMPTY_LIBRARY_QUERY });
  const [queryFilters, setQueryFilters] = useState(filters);
  const [page, setPage] = useState(0);
  useEffect(() => {
    const timer = setTimeout(() => setQueryFilters(filters), 250);
    return () => clearTimeout(timer);
  }, [filters]);
  const updateFilters = (changes: Partial<LibraryQuery>) => {
    setFilters((current) => ({ ...current, ...changes }));
    setPage(0);
  };
  const dateError = Boolean(filters.published_from && filters.published_to && filters.published_from > filters.published_to);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [metadataId, setMetadataId] = useState<string | null>(null);
  const [cancelPaper, setCancelPaper] = useState<Paper | null>(null);
  const [dailyNoticeTitle, setDailyNoticeTitle] = useState("");
  const [dailyNoticeError, setDailyNoticeError] = useState("");

  const papers = useQuery({
    queryKey: ["papers", tab, queryFilters, page],
    enabled: !dateError && !(queryFilters.published_from && queryFilters.published_to && queryFilters.published_from > queryFilters.published_to),
    queryFn: () => api<{ items: Paper[]; total: number }>(
      `/api/papers?${libraryQueryParams(queryFilters, tab === "collection", page)}`,
    ),
  });
  const daily = useQuery({ queryKey: ["daily", "today"], queryFn: () => api<DailyPayload>("/api/daily") });
  const tags = useQuery({ queryKey: ["tags"], queryFn: () => api<Tag[]>("/api/tags") });
  const cancelReading = useMutation({
    mutationFn: (paperId: string) => api(`/api/papers/${paperId}/cancel-reading`, { method: "POST" }),
    onSuccess: () => {
      setCancelPaper(null);
      queryClient.invalidateQueries({ queryKey: ["papers"] });
    },
  });
  const setDaily = useMutation({
    mutationFn: (paper: Paper) => api(`/api/daily/${daily.data?.date}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paper_id: paper.id }),
    }),
    onSuccess: (_, paper) => {
      setDailyNoticeError("");
      setDailyNoticeTitle(paper.title);
      queryClient.invalidateQueries({ queryKey: ["daily"] });
    },
    onError: (error) => {
      setDailyNoticeTitle("");
      setDailyNoticeError(`设置失败：${error.message}`);
    },
  });

  function changeTab(next: LibraryTab) {
    setSearchParams(next === "collection" ? { view: "collection" } : {}, { replace: true });
    updateFilters({ scope: "all" });
  }

  return (
    <Container maxWidth="lg" sx={{ py: { xs: 4, md: 6 } }}>
      <PageHeader
        title="论文"
        description="浏览全部论文，并将完成精读的成果沉淀到积累库。"
        tabs={<Tabs value={tab} onChange={(_, value: LibraryTab) => changeTab(value)} aria-label="论文库分类" sx={{ minHeight: 40, "& .MuiTab-root": { minHeight: 40, py: 0 } }}><Tab value="papers" label="论文库" /><Tab value="collection" label="积累库" /></Tabs>}
        actions={<><MobileConfirmAction label="添加论文" icon={<AddRounded fontSize="small" />} onConfirm={() => setIngestOpen(true)} /><Button variant="contained" startIcon={<AddRounded />} onClick={() => setIngestOpen(true)} sx={{ display: { xs: "none", md: "inline-flex" } }}>添加论文</Button></>}
      />

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
            <TextField
              size="small"
              fullWidth
              value={filters.q}
              onChange={(event) => updateFilters({ q: event.target.value })}
              label="综合搜索"
              placeholder="作者、模型缩写、我的简记等"
              InputProps={{ startAdornment: <InputAdornment position="start"><SearchRounded /></InputAdornment> }}
            />
            <FormControl size="small" sx={{ minWidth: 156 }}>
              <InputLabel>{tab === "papers" ? "阅读状态" : "筛选"}</InputLabel>
              <Select
                label={tab === "papers" ? "阅读状态" : "筛选"}
                value={filters.scope}
                onChange={(event) => updateFilters({ scope: event.target.value })}
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
            <Chip label={`${papers.data?.total ?? 0} 篇`} sx={{ alignSelf: { xs: "flex-start", md: "center" } }} />
          </Stack>
          <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} sx={{ mt: 2 }}>
            <TextField size="small" type="date" label="发表日期从" value={filters.published_from} onChange={(event) => updateFilters({ published_from: event.target.value })} InputLabelProps={{ shrink: true }} sx={{ flex: 1 }} />
            <TextField size="small" type="date" label="发表日期至" value={filters.published_to} onChange={(event) => updateFilters({ published_to: event.target.value })} InputLabelProps={{ shrink: true }} sx={{ flex: 1 }} />
          </Stack>
          {dateError && <Alert severity="warning" sx={{ mt: 1 }}>开始日期不能晚于结束日期。</Alert>}
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5} sx={{ mt: 2 }}>
            <RatingQueryControls value={filters} onChange={updateFilters} defaultSortLabel="阅读状态优先" />
          </Stack>
          {(filters.ratingSource === "agent" || filters.ratingSource === "model") && <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>连续相关度按五档范围筛选，排序使用原始分数。</Typography>}
          <Accordion disableGutters elevation={0} sx={{ mt: 1.5, "&:before": { display: "none" } }}>
            <AccordionSummary expandIcon={<ExpandMoreRounded />} sx={{ px: 0 }}><Typography variant="body2">标题、单位、关键词、标签匹配{[filters.title_q, filters.affiliation_q, filters.keyword_q, filters.tag_q].filter(Boolean).length ? ` · ${[filters.title_q, filters.affiliation_q, filters.keyword_q, filters.tag_q].filter(Boolean).length} 项条件` : ""}</Typography></AccordionSummary>
            <AccordionDetails sx={{ px: 0 }}>
              <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" }, gap: 1.5 }}>
                <TextField size="small" label="标题包含" value={filters.title_q} onChange={(event) => updateFilters({ title_q: event.target.value })} />
                <TextField size="small" label="单位 / 课题组包含" value={filters.affiliation_q} onChange={(event) => updateFilters({ affiliation_q: event.target.value })} />
                <TextField size="small" label="关键词" placeholder="在标题、摘要、总结和模型别名中匹配" value={filters.keyword_q} onChange={(event) => updateFilters({ keyword_q: event.target.value })} />
                <Autocomplete freeSolo options={tags.data?.map((item) => item.name) || []} inputValue={filters.tag_q} onInputChange={(_, value) => updateFilters({ tag_q: value })}
                  renderInput={(params) => <TextField {...params} size="small" label="标签包含" />}
                  renderOption={(props, option) => { const { key, ...rest } = props; return <li key={key} {...rest} aria-label={option}><HighlightText text={option} terms={queryTerms(filters.tag_q)} /></li>; }} />
              </Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 1 }}>不同条件同时满足；这四项文本可用空格分隔多个词，匹配处会高亮。</Typography>
            </AccordionDetails>
          </Accordion>
          <Button size="small" onClick={() => updateFilters({ ...EMPTY_LIBRARY_QUERY })}>清除查询条件</Button>
        </CardContent>
      </Card>

      {dateError ? null : papers.isLoading ? <Loading /> : papers.error ? <ErrorState error={papers.error} /> : !papers.data?.items.length ? (
        <Empty
          title="没有匹配的论文"
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
              onTagClick={(name) => updateFilters({ tag_q: name })}
              query={queryFilters}
              onMetadata={() => setMetadataId(paper.id)}
              onCancelReading={() => setCancelPaper(paper)}
              isDaily={Boolean(
                daily.data?.selections?.some((selection) => selection.paper.id === paper.id)
                || daily.data?.selection?.paper.id === paper.id
              )}
              onSetDaily={() => setDaily.mutate(paper)}
              settingDaily={setDaily.isPending}
              dailyReady={Boolean(daily.data?.date)}
            />
          ))}
        </Stack>
      )}
      {!dateError && Boolean(papers.data?.total) && <Stack direction={{ xs: "column", sm: "row" }} alignItems="center" justifyContent="space-between" spacing={1} sx={{ my: 3 }}>
        <Typography variant="caption" color="text.secondary">第 {page * 50 + 1}–{Math.min((page + 1) * 50, papers.data?.total || 0)} 篇，共 {papers.data?.total} 篇</Typography>
        <Pagination count={Math.max(1, Math.ceil((papers.data?.total || 0) / 50))} page={page + 1} onChange={(_, value) => setPage(value - 1)} size="small" />
      </Stack>}

      <IngestModal
        open={ingestOpen}
        onClose={() => setIngestOpen(false)}
        onSuccess={() => {
          setIngestOpen(false);
          setSearchParams({}, { replace: true });
          updateFilters({ ...EMPTY_LIBRARY_QUERY });
          queryClient.invalidateQueries({ queryKey: ["papers"] });
        }}
      />
      {metadataId && <PaperMetadataDialog paperId={metadataId} open onClose={() => setMetadataId(null)} />}
      <Modal open={Boolean(cancelPaper)} onClose={() => setCancelPaper(null)} title="取消精读状态">
        <Stack spacing={2}>
          <Typography>论文将回到“待阅读”，并退出积累库。已有元数据、标签、笔记和对话会保留；已建立的研究节点关联会移除。</Typography>
          {cancelReading.error && <Alert severity="error">{cancelReading.error.message}</Alert>}
          <Stack direction="row" justifyContent="flex-end" spacing={1}>
            <Button onClick={() => setCancelPaper(null)}>保留当前状态</Button>
            <Button color="warning" variant="contained" onClick={() => cancelPaper && cancelReading.mutate(cancelPaper.id)} disabled={cancelReading.isPending}>确认取消</Button>
          </Stack>
        </Stack>
      </Modal>
      <Snackbar
        open={Boolean(dailyNoticeTitle || dailyNoticeError)}
        autoHideDuration={3200}
        onClose={() => { setDailyNoticeTitle(""); setDailyNoticeError(""); }}
        message={dailyNoticeError || <Box component="span">已将《<PaperTitle title={dailyNoticeTitle} />》设为今日精选</Box>}
      />
    </Container>
  );
}

function PaperCard({
  paper,
  accumulated,
  onOpen,
  onTagClick,
  onMetadata,
  onCancelReading,
  isDaily,
  onSetDaily,
  settingDaily,
  dailyReady,
  query,
}: {
  paper: Paper;
  accumulated: boolean;
  onOpen: () => void;
  onTagClick: (tagId: string) => void;
  onMetadata: () => void;
  onCancelReading: () => void;
  isDaily: boolean;
  onSetDaily: () => void;
  settingDaily: boolean;
  dailyReady: boolean;
  query: LibraryQuery;
}) {
  const titleTerms = queryTerms(query.q, query.title_q, query.keyword_q);
  const unitTerms = queryTerms(query.q, query.affiliation_q);
  const snippets = libraryMatchSnippets(paper, query);
  const score = paperRating(paper, query.ratingSource);
  const scoreLabel = score == null ? "未评分" : ratingOptions(query.ratingSource)[Math.round(score * 4)]?.label || "未评分";
  function openFromCard(event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>) {
    const target = event.target as Element;
    if (target.closest("button, a, input, textarea, select, [role=button]")) return;
    if ("key" in event && !["Enter", " "].includes(event.key)) return;
    if ("key" in event) event.preventDefault();
    onOpen();
  }
  return (
    <Card role="link" tabIndex={0} aria-label={`阅读 ${paper.title}`} onClick={openFromCard} onKeyDown={openFromCard} sx={{ cursor: "pointer", "&:focus-visible": { outline: "2px solid", outlineColor: "primary.main", outlineOffset: 2 } }}>
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
          <Chip size="small" variant="outlined" color={score == null ? "default" : "primary"} label={`${RATING_SOURCES.find((option) => option.value === query.ratingSource)?.label}：${scoreLabel}`} />
          {paper.analysis_status && ["queued", "processing"].includes(paper.analysis_status) && <Chip size="small" color="primary" variant="outlined" label="处理中" />}
          {paper.analysis_status === "failed" && <Chip size="small" color="error" variant="outlined" label="处理失败" />}
          {paper.tags.map((item) => (
            <Box component="span" key={item.id} sx={{ display: "inline-flex", alignItems: "center", gap: 1 }}>
              <Typography component="span" color="text.disabled">·</Typography>
              <MuiLink
                component="button"
                type="button"
                underline="hover"
                onClick={() => onTagClick(item.name)}
                sx={{ color: item.color || "primary.main", fontSize: "0.78rem", fontWeight: 650 }}
              >
                <HighlightText text={item.name} terms={queryTerms(query.q, query.tag_q)} />
              </MuiLink>
            </Box>
          ))}
          <Box sx={{ flex: 1 }} />
          <Tooltip title={isDaily ? "已是今日推荐" : "设为今日推荐"}><span><IconButton size="small" color={isDaily ? "warning" : "primary"} aria-label={isDaily ? "已是今日推荐" : "设为今日推荐"} disabled={isDaily || settingDaily || !dailyReady} onClick={onSetDaily} sx={{ display: { xs: "inline-flex", sm: "none" } }}><StarRounded fontSize="small" /></IconButton></span></Tooltip>
          <Button size="small" color={isDaily ? "warning" : "primary"} startIcon={<StarRounded />} disabled={isDaily || settingDaily || !dailyReady} onClick={onSetDaily} sx={{ display: { xs: "none", sm: "inline-flex" } }}>{isDaily ? "今日推荐" : "设为今日推荐"}</Button>
          {(["reading", "feedback_pending", "completed"] as string[]).includes(paper.status) && <Tooltip title="取消精读状态"><IconButton size="small" color="warning" aria-label="取消精读状态" onClick={onCancelReading}><UndoRounded fontSize="small" /></IconButton></Tooltip>}
          <Tooltip title="查看和编辑论文信息"><IconButton size="small" color="primary" aria-label="论文信息" onClick={onMetadata}><EditNoteRounded fontSize="small" /></IconButton></Tooltip>
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
        sx={{ pt: 1.25, borderRadius: 1 }}
      >
        <Typography variant="h3"><PaperTitle title={paper.title} highlightTerms={titleTerms} /></Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
          <HighlightText text={paper.first_author || "作者信息待确认"} terms={queryTerms(query.q)} />
          {paper.primary_affiliation ? <> · <HighlightText text={paper.primary_affiliation} terms={unitTerms} /></> : ""} · {paper.source}
        </Typography>
        <Box sx={{ mt: 1 }}>
          <ExpandablePaperText lines={3}>
            {paper.summary || paper.catalog_summary || paper.abstract || "摘要与分析信息待补充。"}
          </ExpandablePaperText>
        </Box>
        {!!snippets.length && <Stack spacing={.5} sx={{ mt: 1.5, p: 1.25, borderRadius: 1, bgcolor: "action.hover" }}>
          {snippets.map((match) => <Typography key={match.label} variant="body2" color="text.secondary"><Box component="span" fontWeight={650}>{match.label}：</Box><HighlightText text={match.text} terms={match.terms} /></Typography>)}
        </Stack>}
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
      form.set("analyze", "false");
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
