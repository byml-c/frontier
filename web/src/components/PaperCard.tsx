import ArrowOutwardRounded from "@mui/icons-material/ArrowOutwardRounded";
import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import EditNoteRounded from "@mui/icons-material/EditNoteRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import MenuBookRounded from "@mui/icons-material/MenuBookRounded";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
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
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatDate, jsonBody, statusLabel } from "../api";
import type { InterestLevel, Paper, QualityLevel } from "../types";
import { PaperChoiceToggle, PaperEvaluationToggleButtons } from "./PaperEvaluationToggleButtons";
import { MarkdownMessage } from "./MarkdownMessage";
import { PaperMetadataDialog } from "./PaperMetadataDialog";
import { PaperTitle } from "./PaperTitle";
import { PaperAuthorSummary } from "./PaperAuthorSummary";

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
  preprocessed = false,
}: {
  paper: Paper;
  rank?: number;
  featured?: boolean;
  briefItemId?: string;
  briefId?: string;
  preprocessed?: boolean;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [commentAnchor, setCommentAnchor] = useState<HTMLButtonElement | null>(null);
  const [comment, setComment] = useState(paper.screening_feedback || "");
  const [language, setLanguage] = useState<"zh" | "en">("zh");
  const [quality, setQuality] = useState<QualityLevel | false>(
    paper.quality_level || paper.screening_quality_level || false,
  );
  const [interest, setInterest] = useState<InterestLevel | false>(
    paper.interest_level || paper.screening_interest_level || false,
  );
  const [agentQualityConfirmed, setAgentQualityConfirmed] = useState(
    Boolean(paper.agent_quality_confirmed ?? paper.agent_score_confirmed),
  );
  const [agentInterestConfirmed, setAgentInterestConfirmed] = useState(
    Boolean(paper.agent_interest_confirmed ?? paper.agent_score_confirmed),
  );
  const saveScreening = useMutation({
    mutationFn: (body: { quality_level?: QualityLevel; interest_level?: InterestLevel; screening_feedback?: string; agent_quality_confirmed?: boolean; agent_interest_confirmed?: boolean }) =>
      api<Paper>(`/api/papers/${paper.id}/metadata`, { ...jsonBody(body), method: "PATCH" }),
    onError: () => {
      setQuality(paper.quality_level || paper.screening_quality_level || false);
      setInterest(paper.interest_level || paper.screening_interest_level || false);
      setAgentQualityConfirmed(Boolean(paper.agent_quality_confirmed ?? paper.agent_score_confirmed));
      setAgentInterestConfirmed(Boolean(paper.agent_interest_confirmed ?? paper.agent_score_confirmed));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["brief"] });
      queryClient.invalidateQueries({ queryKey: ["paper", paper.id] });
      setCommentAnchor(null);
    },
  });
  const startReading = useMutation({
    mutationFn: () => api(`/api/papers/${paper.id}/start-reading`, jsonBody({ source_brief_item_id: briefItemId })),
    onSuccess: () => navigate(`/paper/${paper.id}`),
  });
  const retryTranslation = useMutation({
    mutationFn: () => api(`/api/briefs/${briefId}/abstract-translations/${paper.id}/retry`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["brief"] }),
  });
  const updateMetric = (next: { quality?: QualityLevel; interest?: InterestLevel }) => {
    if (next.quality) setQuality(next.quality);
    if (next.interest) setInterest(next.interest);
    saveScreening.mutate({
      ...(next.quality ? { quality_level: next.quality } : {}),
      ...(next.interest ? { interest_level: next.interest } : {}),
    });
  };
  const updateAgentScoreConfirmation = (metric: "quality" | "interest") => {
    const next = metric === "quality" ? !agentQualityConfirmed : !agentInterestConfirmed;
    if (metric === "quality") setAgentQualityConfirmed(next);
    else setAgentInterestConfirmed(next);
    saveScreening.mutate(metric === "quality"
      ? { agent_quality_confirmed: next }
      : { agent_interest_confirmed: next });
  };
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
            <Typography variant="caption" color="text.secondary">{paper.source.toUpperCase()} · {formatDate(paper.published_at)}</Typography>
          </Stack>
          <Chip size="small" variant="outlined" label={statusLabel(paper.status)} />
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
        {(saveScreening.error || retryTranslation.error || startReading.error) && <Alert severity="error" sx={{ mt: 1 }}>{(saveScreening.error || retryTranslation.error || startReading.error)?.message}</Alert>}
        <Typography component="div" variant="body2" color="text.secondary" sx={{ mt: 1 }}><MarkdownMessage content={abstract} /></Typography>

        <Stack direction="row" spacing={1} alignItems="flex-start" sx={{ mt: 2 }}>
          <AutoAwesomeRounded color="action" sx={{ fontSize: 17, mt: .15 }} />
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography variant="subtitle2">Agent 简评</Typography>
            <Typography component="div" variant="body2" color="text.secondary" sx={{ mt: .5 }}><MarkdownMessage content={paper.agent_comment || paper.recommendation_reason || "等待 Agent 判断。" } /></Typography>
          </Box>
        </Stack>

        <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1, pt: 1, borderTop: 1, borderColor: "divider" }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <PaperEvaluationToggleButtons
              researchScore={paper.research_score}
              relevanceScore={paper.relevance_score}
              qualityLevel={quality}
              interestLevel={interest}
              qualityTitle="质量"
              interestTitle="相关度"
              disabled={saveScreening.isPending}
              agentQualityConfirmed={agentQualityConfirmed}
              agentInterestConfirmed={agentInterestConfirmed}
              onQualityAgentScoreConfirm={() => updateAgentScoreConfirmation("quality")}
              onInterestAgentScoreConfirm={() => updateAgentScoreConfirmation("interest")}
              onQualityChange={(value) => updateMetric({ quality: value })}
              onInterestChange={(value) => updateMetric({ interest: value })}
            />
          </Box>
          <Tooltip title={paper.screening_feedback ? "编辑补充说明" : "补充偏好说明"}>
            <IconButton aria-label="补充筛选说明" aria-expanded={Boolean(commentAnchor)} aria-haspopup="dialog" color={paper.screening_feedback ? "primary" : "default"} onClick={(event) => { setComment(paper.screening_feedback || ""); setCommentAnchor(event.currentTarget); }}><EditNoteRounded /></IconButton>
          </Tooltip>
        </Stack>

        {!!paper.nodes?.length && <Stack direction="row" flexWrap="wrap" gap={.75} sx={{ mt: 1.25 }}>{paper.nodes.map((node) => <Chip size="small" key={node.id} clickable onClick={() => navigate(`/graph/${node.id}`)} label={node.name} variant="outlined" color="primary" />)}</Stack>}
      </CardContent>
      {preprocessed && (
        <Accordion disableGutters elevation={0} sx={{ mt: 1, borderTop: 1, borderColor: "divider", "&:before": { display: "none" } }}>
          <AccordionSummary expandIcon={<ExpandMoreRounded />} sx={{ px: 0 }}>
            <Typography variant="subtitle2">详细分析</Typography>
          </AccordionSummary>
          <AccordionDetails sx={{ px: 0, pt: 0 }}><Stack divider={<Divider flexItem />} spacing={2}>{[["解决的问题", paper.problem], ["创新内容", paper.innovation], ["实验结果", paper.experiment], ["局限", paper.limitations], ["未解决问题", paper.open_questions]].map(([label, value]) => <Box key={label}><Typography variant="subtitle2">{label}</Typography><Typography component="div" variant="body2" color="text.secondary" sx={{ mt: .5 }}><MarkdownMessage content={value || "暂未获得足够证据。"} /></Typography></Box>)}</Stack></AccordionDetails>
        </Accordion>
      )}
      <Divider sx={{ mt: preprocessed ? 0 : 1.5 }} />
      <CardActions sx={{ pt: 1.5, gap: .5 }}>
        <Button size="small" variant="contained" color={paper.status === "feedback_pending" ? "warning" : "primary"} startIcon={<MenuBookRounded />} disabled={startReading.isPending} onClick={() => startReading.mutate()}>
          {paper.status === "feedback_pending" ? "确认精读结论" : paper.status === "reading" ? "继续精读" : paper.status === "completed" ? "继续阅读" : "开始精读"}
        </Button>
        <Button size="small" color="inherit" onClick={() => setMetadataOpen(true)} sx={{ whiteSpace: "nowrap" }}>论文信息</Button>
        <Box sx={{ flex: 1 }} />
        {paper.source_url && <Button size="small" component={Link} href={paper.source_url} target="_blank" rel="noreferrer" endIcon={<ArrowOutwardRounded />} sx={{ whiteSpace: "nowrap" }}>查看原文</Button>}
      </CardActions>
      <Popover open={Boolean(commentAnchor)} anchorEl={commentAnchor} onClose={() => setCommentAnchor(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }} slotProps={{ paper: { sx: { p: 2, mt: 1, width: 360, maxWidth: "calc(100vw - 32px)" } } }}>
        <Box component="form" role="dialog" aria-label="补充筛选说明" onSubmit={(event) => { event.preventDefault(); saveScreening.mutate({ screening_feedback: comment }); }}>
          <Typography variant="subtitle2" sx={{ mb: 1.5 }}>补充偏好说明</Typography>
          <TextField fullWidth autoFocus multiline minRows={3} label="说明（可选）" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="例如：更关注真实机器人验证，暂不考虑纯仿真工作。" />
          {saveScreening.error && <Alert severity="error" sx={{ mt: 2 }}>{saveScreening.error.message}</Alert>}
          <Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 2 }}><Button size="small" onClick={() => setCommentAnchor(null)}>取消</Button><Button size="small" variant="contained" type="submit" disabled={saveScreening.isPending}>保存</Button></Stack>
        </Box>
      </Popover>
      <PaperMetadataDialog paperId={paper.id} open={metadataOpen} onClose={() => setMetadataOpen(false)} />
    </Card>
  );
}
