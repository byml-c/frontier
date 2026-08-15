import ArrowOutwardRounded from "@mui/icons-material/ArrowOutwardRounded";
import AutoAwesomeRounded from "@mui/icons-material/AutoAwesomeRounded";
import BlockRounded from "@mui/icons-material/BlockRounded";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import EditNoteRounded from "@mui/icons-material/EditNoteRounded";
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
  Link,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatDate, jsonBody, statusLabel } from "../api";
import type { Paper } from "../types";
import { Modal } from "./Modal";
import { MarkdownMessage } from "./MarkdownMessage";
import { PaperMetadataDialog } from "./PaperMetadataDialog";

export function PaperCard({ paper, rank, featured = false, briefItemId }: { paper: Paper; rank?: number; featured?: boolean; briefItemId?: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [ignoreOpen, setIgnoreOpen] = useState(false);
  const [ignoreReason, setIgnoreReason] = useState("");
  const [metadataOpen, setMetadataOpen] = useState(false);
  const ignore = useMutation({ mutationFn: () => api(`/api/papers/${paper.id}/ignore`, jsonBody({ reason: ignoreReason, source_brief_item_id: briefItemId })), onSuccess: () => { setIgnoreOpen(false); queryClient.invalidateQueries({ queryKey: ["brief"] }); queryClient.invalidateQueries({ queryKey: ["papers"] }); } });
  const startReading = useMutation({ mutationFn: () => api(`/api/papers/${paper.id}/start-reading`, jsonBody({ source_brief_item_id: briefItemId })), onSuccess: () => navigate(`/paper/${paper.id}`) });
  function submitIgnore(event: FormEvent) { event.preventDefault(); if (ignoreReason.trim()) ignore.mutate(); }

  return <Card>
    <CardContent sx={{ p: { xs: .25, md: .5 } }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={2} sx={{ mb: 1 }}>
        <Stack direction="row" spacing={1} alignItems="center"><Chip size="small" label={rank ? `#${rank}` : paper.source.toUpperCase()} color={featured ? "primary" : "default"} /><Typography variant="caption" color="text.secondary">{paper.source.toUpperCase()} · {formatDate(paper.published_at)}</Typography></Stack>
        <Chip size="small" variant="outlined" label={statusLabel(paper.status)} />
      </Stack>
      {paper.analysis_status && ["queued", "processing"].includes(paper.analysis_status) && <Alert severity="info" icon={<CircularProgress size={18} />} sx={{ mb: 1.25, py: .25 }}>正在读取全文并整理信息。</Alert>}
      {paper.analysis_status === "failed" && <Alert severity="error" sx={{ mb: 1.25, py: .25 }}>处理失败：{paper.analysis_error || "请在任务页查看原因"}</Alert>}
      <Typography variant="h3" component="h2">{paper.title}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>{paper.first_author || "作者信息待确认"}{paper.corresponding_author ? ` · 通讯作者 ${paper.corresponding_author}` : ""}{paper.primary_affiliation ? ` · ${paper.primary_affiliation}` : ""}</Typography>
      <Box color="text.secondary" sx={{ mt: 1, fontSize: ".875rem" }}><MarkdownMessage content={paper.summary || paper.abstract || "简介正在整理中。"} /></Box>
      <Divider sx={{ my: 1.25 }} />
      <Stack direction="row" spacing={1} alignItems="flex-start"><AutoAwesomeRounded color="action" sx={{ fontSize: 17, mt: .15 }} /><Box sx={{ minWidth: 0 }}><Typography variant="caption" fontWeight={700} color="text.secondary">推荐理由</Typography><Box sx={{ mt: .15, fontSize: ".875rem" }}><MarkdownMessage content={paper.agent_comment || paper.recommendation_reason || "等待全文分析后给出判断。"} /></Box></Box></Stack>
      {!!paper.nodes?.length && <Stack direction="row" flexWrap="wrap" gap={.75} sx={{ mt: 1.25 }}>{paper.nodes.map((node) => <Chip size="small" key={node.id} clickable onClick={() => navigate(`/graph/${node.id}`)} label={node.name} variant="outlined" color="primary" />)}</Stack>}
      <Accordion disableGutters elevation={0} sx={{ mt: .5, border: 0, bgcolor: "transparent", "&:before": { display: "none" } }}><AccordionSummary expandIcon={<ExpandMoreRounded />} sx={{ px: 0, minHeight: 36, "& .MuiAccordionSummary-content": { my: .5 } }}><Typography variant="body2" fontWeight={650}>问题、方法与实验</Typography></AccordionSummary><AccordionDetails sx={{ px: 0, pt: 0 }}><Stack divider={<Divider flexItem />} spacing={1.5}>{[["研究问题", paper.problem], ["主要创新", paper.innovation], ["实验结果", paper.experiment], ["局限与后续问题", paper.limitations]].map(([label, value]) => <Box key={label}><Typography variant="subtitle2">{label}</Typography><Box color="text.secondary" sx={{ mt: .25, fontSize: ".875rem" }}><MarkdownMessage content={value || "暂未获得足够证据。"} /></Box></Box>)}</Stack></AccordionDetails></Accordion>
    </CardContent>
    <Divider />
    <CardActions sx={{ px: 0, pt: 1, pb: 0, flexWrap: "wrap", gap: .5 }}>
      <Button
        variant="contained"
        color={paper.status === "feedback_pending" ? "warning" : "primary"}
        startIcon={<MenuBookRounded />}
        onClick={() => startReading.mutate()}
        disabled={startReading.isPending}
      >
        {paper.status === "feedback_pending" ? "确认精读结论" : paper.status === "reading" ? "继续精读" : paper.status === "completed" ? "继续阅读" : "开始精读"}
      </Button>
      {paper.status !== "completed" && paper.status !== "feedback_pending" && <Button color="inherit" startIcon={<BlockRounded />} onClick={() => setIgnoreOpen(true)}>忽略</Button>}
      <Button color="inherit" startIcon={<EditNoteRounded />} onClick={() => setMetadataOpen(true)}>论文信息</Button>
      <Box sx={{ flex: 1 }} />
      {paper.source_url && <Button component={Link} href={paper.source_url} target="_blank" rel="noreferrer" endIcon={<ArrowOutwardRounded />}>查看原文</Button>}
    </CardActions>
    <Modal open={ignoreOpen} title="忽略这篇论文" onClose={() => setIgnoreOpen(false)}><Box component="form" onSubmit={submitIgnore}><Typography color="text.secondary" variant="body2" sx={{ mb: 2 }}>请填写原因，后续推荐会据此调整。</Typography><TextField fullWidth autoFocus multiline minRows={4} value={ignoreReason} onChange={(event) => setIgnoreReason(event.target.value)} placeholder="例如：方向相关，但实验不足以支持结论" />{ignore.error && <Alert severity="error" sx={{ mt: 2 }}>{ignore.error.message}</Alert>}<Stack direction="row" justifyContent="flex-end" spacing={1} sx={{ mt: 3 }}><Button onClick={() => setIgnoreOpen(false)}>取消</Button><Button variant="contained" color="error" type="submit" disabled={!ignoreReason.trim() || ignore.isPending}>确认忽略</Button></Stack></Box></Modal>
    <PaperMetadataDialog paperId={paper.id} open={metadataOpen} onClose={() => setMetadataOpen(false)} />
  </Card>;
}
