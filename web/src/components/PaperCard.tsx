import ArrowOutwardRounded from "@mui/icons-material/ArrowOutwardRounded";
import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import EditNoteRounded from "@mui/icons-material/EditNoteRounded";
import MenuBookRounded from "@mui/icons-material/MenuBookRounded";
import {
  Alert,
  Box,
  Button,
  Card,
  CardActions,
  CardContent,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Link,
  Popover,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatDate, jsonBody, paperStatusLabel } from "../api";
import type { Paper } from "../types";
import { PaperChoiceToggle, PaperScores } from "./PaperScores";
import { MarkdownMessage } from "./MarkdownMessage";
import { PaperMetadataDialog } from "./PaperMetadataDialog";
import { PaperQuickNotesFields } from "./PaperQuickNotesFields";
import { PaperTitle } from "./PaperTitle";
import { PaperAuthorSummary } from "./PaperAuthorSummary";
import { paperReadingHref } from "./paperReadingLink";

const ABSTRACT_LANGUAGES = [
  { value: "zh", label: "中文" },
  { value: "en", label: "英文" },
] as const;

export function PaperCard({
  paper,
  rank,
  featured = false,
  briefItemId,
  briefId,
}: {
  paper: Paper;
  rank?: number;
  featured?: boolean;
  briefItemId?: string;
  briefId?: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [commentAnchor, setCommentAnchor] = useState<HTMLButtonElement | null>(null);
  const [comment, setComment] = useState(paper.user_note || "");
  const [modelShortName, setModelShortName] = useState(paper.model_short_name || "");
  const [searchAliases, setSearchAliases] = useState((paper.search_aliases || []).join(", "));
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const saveScreening = useMutation({
    mutationFn: (body: { user_score?: number | null; user_note?: string; model_short_name?: string | null; search_aliases?: string[] }) =>
      api<Paper>(`/api/papers/${paper.id}/metadata`, { ...jsonBody(body), method: "PATCH" }),
    onSuccess: (saved) => {
      setComment(saved.user_note || "");
      queryClient.setQueryData(["paper", paper.id], saved);
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["daily"] });
      queryClient.invalidateQueries({ queryKey: ["brief"] });
      queryClient.invalidateQueries({ queryKey: ["paper", paper.id] });
      setCommentAnchor(null);
    },
  });
  const retryTranslation = useMutation({
    mutationFn: () => api(`/api/briefs/${briefId}/abstract-translations/${paper.id}/retry`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["brief"] }),
  });
  const score = saveScreening.isPending && saveScreening.variables && "user_score" in saveScreening.variables
    ? saveScreening.variables.user_score ?? null
    : paper.user_score ?? null;
  const translated = paper.abstract_translation;
  const abstract = language === "zh"
    ? translated || (paper.abstract_translation_status === "failed" ? "摘要翻译失败，可点击重试，或切换英文阅读。" : paper.abstract_translation_status === "missing" ? "摘要尚未翻译，可先切换英文阅读。" : "正在翻译摘要。")
    : paper.abstract || "暂无英文摘要。";

  return (
    <Card>
      <CardContent>
        <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={2} sx={{ mb: 1 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip size="small" label={rank ? `#${rank}` : paper.source.toUpperCase()} color={featured ? "primary" : "default"} />
            {paper.model_short_name && <Chip size="small" color="secondary" variant="outlined" label={paper.model_short_name} />}
            <Typography variant="caption" color="text.secondary">{paper.source.toUpperCase()} · {formatDate(paper.published_at)}</Typography>
          </Stack>
          <Chip size="small" variant="outlined" label={paperStatusLabel(paper.status)} />
        </Stack>
        {paper.analysis_status && ["queued", "processing"].includes(paper.analysis_status) && (
          <Alert severity="info" icon={<CircularProgress size={18} />} sx={{ mb: 1.25, py: .25 }}>正在读取全文并整理信息。</Alert>
        )}
        {paper.analysis_status === "failed" && <Alert severity="error" sx={{ mb: 1.25, py: .25 }}>处理失败：{paper.analysis_error || "请在任务页查看原因"}</Alert>}
        <Typography variant="h3" component="h2"><PaperTitle title={paper.title} /></Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>
          {[paper.first_author || paper.authors?.[0]?.name, paper.primary_affiliation, paper.corresponding_author].filter(Boolean).join(" · ") || "作者信息待确认"}
        </Typography>
        <PaperAuthorSummary paper={paper} />

        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.5 }}>
          <Typography variant="subtitle2">摘要</Typography>
          <PaperChoiceToggle
            value={language}
            options={ABSTRACT_LANGUAGES}
            onChange={setLanguage}
            ariaLabel="摘要语言"
            fullWidth={false}
          />
          {language === "zh" && !translated && ["failed", "missing"].includes(paper.abstract_translation_status || "missing") && briefId && (
            <Button size="small" onClick={() => retryTranslation.mutate()} disabled={retryTranslation.isPending}>{paper.abstract_translation_status === "failed" ? "重试" : "翻译"}</Button>
          )}
        </Stack>
        {(saveScreening.error || retryTranslation.error) && <Alert severity="error" sx={{ mt: 1 }}>{(saveScreening.error || retryTranslation.error)?.message}</Alert>}
        <Typography component="div" variant="body2" color="text.secondary" sx={{ mt: 1 }}><MarkdownMessage content={abstract} /></Typography>
        {(paper.model_short_name || !!paper.search_aliases?.length || paper.user_note) && (
          <Stack direction="row" flexWrap="wrap" alignItems="center" gap={.75} sx={{ mt: 1 }}>
            {paper.search_aliases?.map((alias) => <Chip key={alias} size="small" variant="outlined" label={alias} />)}
            {paper.user_note && <Typography variant="caption" color="text.secondary">我的简记：{paper.user_note}</Typography>}
          </Stack>
        )}

        <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ mt: 2 }}>
          <AutoAwesomeRounded color="action" sx={{ fontSize: 17, mt: .15 }} />
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography variant="subtitle2">Agent 简评</Typography>
            <Typography component="div" variant="body2" color="text.secondary" sx={{ mt: .5 }}><MarkdownMessage content={paper.pre_read_summary || "等待 Agent 判断。" } /></Typography>
          </Box>
        </Stack>

        <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ xs: "stretch", sm: "center" }} spacing={1} sx={{ mt: 1, pt: 1, borderTop: 1, borderColor: "divider" }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <PaperScores userScore={score} agentScore={paper.agent_score} modelScore={paper.model_score}
              onChange={(value) => saveScreening.mutate({ user_score: value })} disabled={saveScreening.isPending} />
          </Box>
          <Tooltip title="编辑简记">
            <IconButton aria-label="编辑简记" aria-expanded={Boolean(commentAnchor)} aria-haspopup="dialog" color={paper.user_note || paper.model_short_name ? "primary" : "default"} sx={{ alignSelf: { xs: "flex-end", sm: "center" } }} onClick={(event) => { setComment(paper.user_note || ""); setModelShortName(paper.model_short_name || ""); setSearchAliases((paper.search_aliases || []).join(", ")); setCommentAnchor(event.currentTarget); }}><EditNoteRounded /></IconButton>
          </Tooltip>
        </Stack>

        {!!paper.nodes?.length && <Stack direction="row" flexWrap="wrap" gap={.75} sx={{ mt: 1.25 }}>{paper.nodes.map((node) => <Chip size="small" key={node.id} clickable onClick={() => navigate(`/graph/${node.id}`)} label={node.name} variant="outlined" color="primary" />)}</Stack>}
      </CardContent>
      <Divider sx={{ mt: 1.5 }} />
      <CardActions sx={{ pt: 1.5, gap: .5 }}>
        <Link href={paperReadingHref(paper.id, briefItemId)} target="_blank" rel="noopener noreferrer" underline="hover" sx={{ display: "inline-flex", alignItems: "center", gap: .75, px: 1, py: .5, fontSize: "0.8125rem", fontWeight: 600, whiteSpace: "nowrap" }}>
          <MenuBookRounded sx={{ fontSize: 18 }} />阅读
        </Link>
        <Button size="small" color="inherit" onClick={() => setMetadataOpen(true)} sx={{ whiteSpace: "nowrap" }}>论文信息</Button>
        <Box sx={{ flex: 1 }} />
        {paper.source_url && <Button size="small" component={Link} href={paper.source_url} target="_blank" rel="noreferrer" endIcon={<ArrowOutwardRounded />} sx={{ whiteSpace: "nowrap" }}>查看原文</Button>}
      </CardActions>
      <Popover open={Boolean(commentAnchor)} anchorEl={commentAnchor} onClose={() => setCommentAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }} slotProps={{ paper: { sx: { p: 2, mt: 1, width: 360, maxWidth: "calc(100vw - 32px)" } } }}>
        <Box component="form" role="dialog" aria-label="编辑简记" onSubmit={(event) => { event.preventDefault(); saveScreening.mutate({ user_note: comment, model_short_name: modelShortName.trim() || null, search_aliases: searchAliases.split(/[,，\n]+/).map((value) => value.trim()).filter(Boolean) }); }}>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>简记</Typography>
          <PaperQuickNotesFields autoFocus value={{ model_short_name: modelShortName, search_aliases: searchAliases, user_note: comment }}
            onChange={(changes) => {
              if (changes.model_short_name !== undefined) setModelShortName(changes.model_short_name);
              if (changes.search_aliases !== undefined) setSearchAliases(changes.search_aliases);
              if (changes.user_note !== undefined) setComment(changes.user_note);
            }} />
          {saveScreening.error && <Alert severity="error" sx={{ mt: 2 }}>{saveScreening.error.message}</Alert>}
          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2 }}><Button size="small" onClick={() => setCommentAnchor(null)}>取消</Button><Button size="small" variant="contained" type="submit" disabled={saveScreening.isPending}>保存</Button></Stack>
        </Box>
      </Popover>
      <PaperMetadataDialog paperId={paper.id} open={metadataOpen} onClose={() => setMetadataOpen(false)} />
    </Card>
  );
}
